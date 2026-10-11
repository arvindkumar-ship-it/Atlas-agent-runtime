from __future__ import annotations

import asyncio
import json
from time import perf_counter

import httpx
from pydantic import ValidationError

from atlas.config import Settings
from atlas.context.compiler import ContextCompiler, estimate_tokens
from atlas.domain.models import TERMINAL, Action, Memory, Run
from atlas.memory.manager import from_observation
from atlas.models.backends import parse_decision
from atlas.models.types import GenerateRequest
from atlas.runtime.graph import node_ready, validate_plan
from atlas.runtime.inference import InferenceScheduler, ModelRouter
from atlas.storage.locks import workspace_lock
from atlas.storage.store import Lease, Store, digest
from atlas.telemetry.metrics import MetricsRegistry
from atlas.telemetry.tracing import tracer
from atlas.tools.registry import CONTRACTS, RepositoryTools, verify
from atlas.tools.workspace import WorkspaceManager


class RuntimeEngine:
    def __init__(
        self,
        store: Store,
        settings: Settings,
        scheduler: InferenceScheduler,
        metrics: MetricsRegistry,
    ):
        self.store, self.settings, self.scheduler, self.metrics = (
            store,
            settings,
            scheduler,
            metrics,
        )
        self.workspaces = WorkspaceManager(settings.data_dir)
        self.compiler = ContextCompiler()
        self.router = ModelRouter(settings.model_name, settings.small_model_name)

    async def _emit(self, run_id: str, kind: str, payload: dict, lease: Lease | None):
        return await asyncio.to_thread(self.store.append, run_id, kind, payload, None, lease)

    async def execute(self, run_id: str, lease: Lease | None = None) -> Run:
        from uuid import UUID

        UUID(run_id)
        async with workspace_lock(self.settings.data_dir / "locks" / f"{run_id}.lock"):
            return await self._execute(run_id, lease)

    async def _execute(self, run_id: str, lease: Lease | None = None) -> Run:
        started = perf_counter()
        with tracer().start_as_current_span("atlas.workflow") as span:
            span.set_attribute("atlas.run.id", run_id)
            run = await asyncio.to_thread(self.store.get, run_id)
            if run.status in TERMINAL:
                return run
            if not run.workspace:
                from pathlib import Path

                workspace = await asyncio.to_thread(
                    self.workspaces.create, run.id, Path(run.task.repository_path)
                )
                run = await self._emit(run.id, "workspace.created", {"path": str(workspace)}, lease)
            from pathlib import Path

            tools = RepositoryTools(
                Path(run.workspace),
                self.workspaces.artifacts / run.id,
                self.workspaces.baseline(run.id),
                self.settings,
            )
            await self._emit(run.id, "run.started", {}, lease)
            if lease and lease.attempts > 1:
                await self._emit(run.id, "run.recovered", {"attempt": lease.attempts}, lease)
                self.metrics.increment("atlas_workflow_recoveries_total")
            malformed = 0
            while True:
                run = await asyncio.to_thread(self.store.get, run_id)
                if run.status in TERMINAL:
                    return run
                if run.pending:
                    approved = await self._execute_pending(run, tools, lease)
                    if not approved:
                        return await asyncio.to_thread(self.store.get, run_id)
                    continue
                if run.step_count >= run.task.max_steps:
                    return await self._emit(
                        run.id, "run.failed", {"error": "Maximum model steps exceeded"}, lease
                    )
                context = self.compiler.compile(run)
                request = GenerateRequest(
                    messages=context.messages,
                    metadata={
                        "run": run.model_dump(mode="json"),
                        "prefix_hash": context.prefix_hash,
                    },
                )
                request.model = self.router.select(
                    "planning" if not run.plan else "action",
                    min(0.9, 0.2 + 0.2 * sum(not item["result"]["ok"] for item in run.history)),
                    context.compiled_tokens_estimate,
                )
                reserved = context.compiled_tokens_estimate + request.max_tokens
                if run.token_usage + reserved > run.task.total_tokens:
                    return await self._emit(
                        run.id, "run.failed", {"error": "Token ledger exhausted"}, lease
                    )
                await self._emit(
                    run.id,
                    "context.compiled",
                    {
                        "raw_tokens_estimate": context.raw_tokens_estimate,
                        "compiled_tokens_estimate": context.compiled_tokens_estimate,
                        "dropped_blocks": context.dropped_blocks,
                        "prefix_hash": context.prefix_hash,
                    },
                    lease,
                )
                self.metrics.observe(
                    "atlas_context_compiled_tokens_estimate", context.compiled_tokens_estimate
                )
                self.metrics.increment(
                    "atlas_model_calls_total", labels={"backend": self.settings.backend}
                )
                result = None
                for attempt in range(3):
                    try:
                        with tracer().start_as_current_span("atlas.inference"):
                            if run.task.context_mode in {"atlas_runtime", "atlas_full"}:
                                result = await self.scheduler.submit(
                                    request, context.compiled_tokens_estimate, run.task.priority
                                )
                            else:
                                result = await self.scheduler.backend.generate(request)
                        break
                    except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                        if (
                            isinstance(exc, httpx.HTTPStatusError)
                            and exc.response.status_code < 500
                            and exc.response.status_code != 429
                        ):
                            raise
                        await self._emit(run.id, "model.retry", {"attempt": attempt + 1}, lease)
                        if attempt == 2:
                            raise
                        await asyncio.sleep(0.25 * 2**attempt)
                charged = (
                    result.input_tokens
                    if result.input_tokens is not None
                    else context.compiled_tokens_estimate
                ) + (
                    result.output_tokens
                    if result.output_tokens is not None
                    else estimate_tokens(result.text)
                )
                await self._emit(
                    run.id,
                    "model.completed",
                    {
                        "charged_tokens": charged,
                        "input_tokens": result.input_tokens,
                        "output_tokens": result.output_tokens,
                        "output_tokens_estimate": estimate_tokens(result.text),
                        "usage_source": result.usage_source,
                        "latency_ms": result.latency_ms,
                        "ttft_ms": result.ttft_ms,
                        "tpot_ms": result.tpot_ms,
                        "cached_tokens": result.cached_tokens,
                        "model": request.model,
                    },
                    lease,
                )
                self.metrics.observe("atlas_model_latency_ms", result.latency_ms)
                if result.input_tokens is not None:
                    self.metrics.increment("atlas_model_input_tokens_total", result.input_tokens)
                if result.output_tokens is not None:
                    self.metrics.increment("atlas_model_output_tokens_total", result.output_tokens)
                if result.ttft_ms is not None:
                    self.metrics.observe("atlas_model_ttft_ms", result.ttft_ms)
                try:
                    decision = parse_decision(result.text)
                except (ValueError, ValidationError) as exc:
                    malformed += 1
                    await self._emit(
                        run.id,
                        "memory.created",
                        {
                            "memory": Memory(
                                kind="failure",
                                content=f"Invalid model decision: {str(exc)[:500]}",
                                importance=1,
                            ).model_dump(mode="json")
                        },
                        lease,
                    )
                    if malformed >= 3:
                        return await self._emit(
                            run.id,
                            "run.failed",
                            {"error": "Model returned invalid decisions three times"},
                            lease,
                        )
                    continue
                if decision.type == "plan":
                    validate_plan(decision.plan)
                    # Replanning preserves completed node state for matching IDs.
                    old = {node.id: node.status for node in run.plan}
                    for node in decision.plan:
                        node.status = old.get(node.id, "pending")
                    await self._emit(
                        run.id,
                        "plan.created",
                        {
                            "nodes": [node.model_dump() for node in decision.plan],
                            "summary": decision.summary,
                        },
                        lease,
                    )
                elif decision.type in {"tool", "parallel"}:
                    if decision.type == "parallel" and any(
                        not CONTRACTS.get(action.tool) or not CONTRACTS[action.tool].read_only
                        for action in decision.actions
                    ):
                        return await self._emit(
                            run.id,
                            "run.failed",
                            {"error": "Only read-only tools may run in parallel"},
                            lease,
                        )
                    for action in decision.actions:
                        tools.validate(action.tool, action.arguments)
                        if action.node_id and not node_ready(run.plan, action.node_id):
                            raise ValueError("Plan dependencies have not succeeded")
                    await self._emit(
                        run.id,
                        "actions.proposed",
                        {"actions": [action.model_dump() for action in decision.actions]},
                        lease,
                    )
                else:
                    run = await asyncio.to_thread(self.store.get, run_id)
                    evidence = {item["event_id"] for item in run.history}
                    if not decision.evidence or not set(decision.evidence) <= evidence:
                        return await self._emit(
                            run.id,
                            "run.failed",
                            {"error": "Final answer requires valid tool evidence references"},
                            lease,
                        )
                    report = await verify(tools)
                    await self._emit(run.id, "verification.completed", {"report": report}, lease)
                    if not report["passed"]:
                        await self._emit(
                            run.id,
                            "memory.created",
                            {
                                "memory": Memory(
                                    kind="failure",
                                    content="Final verification failed: "
                                    + json.dumps(report["checks"])[:2000],
                                    importance=1,
                                ).model_dump(mode="json")
                            },
                            lease,
                        )
                        malformed += 1
                        if malformed >= 3:
                            return await self._emit(
                                run.id, "run.failed", {"error": "Final verification failed"}, lease
                            )
                        continue
                    self.metrics.increment("atlas_runs_completed_total")
                    self.metrics.observe(
                        "atlas_workflow_duration_ms", (perf_counter() - started) * 1000
                    )
                    return await self._emit(
                        run.id,
                        "run.completed",
                        {"answer": decision.summary, "evidence": decision.evidence},
                        lease,
                    )

    async def _execute_pending(self, run: Run, tools: RepositoryTools, lease: Lease | None) -> bool:
        for action in run.pending:
            if (
                CONTRACTS[action.tool].approval
                and run.task.approval_policy == "manual"
                and not await asyncio.to_thread(self.store.approval_granted, run.id, action)
            ):
                await asyncio.to_thread(self.store.request_approval, run.id, action, lease)
                return False
        await self._emit(
            run.id,
            "session.parked",
            {
                "pending": len(run.pending),
                "state_tier": "durable_context",
                "gpu_cache_released": None,
            },
            lease,
        )
        if len(run.pending) > 1:
            if any(not CONTRACTS[action.tool].read_only for action in run.pending):
                raise ValueError("Side effects cannot run in parallel")
            await asyncio.gather(
                *(self._execute_action(run, action, tools, lease) for action in run.pending)
            )
        else:
            await self._execute_action(run, run.pending[0], tools, lease)
        await self._emit(run.id, "session.resumed", {}, lease)
        return True

    async def _execute_action(
        self, run: Run, action: Action, tools: RepositoryTools, lease: Lease | None
    ):
        key = digest({"run_id": run.id, "turn": run.step_count, "action": action.model_dump()})
        if await asyncio.to_thread(self.store.effect, key):
            return
        await self._emit(
            run.id,
            "tool.started",
            {"action": action.model_dump(), "node_id": action.node_id},
            lease,
        )
        started, contract = perf_counter(), CONTRACTS[action.tool]
        result = None
        for attempt in range(3 if contract.idempotent else 1):
            with tracer().start_as_current_span("atlas.tool") as span:
                span.set_attribute("atlas.tool", action.tool)
                result = await asyncio.wait_for(
                    tools.execute(action.tool, action.arguments),
                    timeout=max(contract.timeout, self.settings.test_timeout + 15),
                )
            if result.ok or not result.retryable:
                break
            await self._emit(
                run.id, "tool.retry", {"tool": action.tool, "attempt": attempt + 1}, lease
            )
            await asyncio.sleep(0.1 * 2**attempt)
        payload = {
            "action": action.model_dump(),
            "result": result.model_dump(),
            "duration_ms": (perf_counter() - started) * 1000,
            "effect_key": key,
        }
        await asyncio.to_thread(self.store.commit_tool, run.id, key, payload, lease)
        self.metrics.increment("atlas_tool_calls_total", labels={"tool": action.tool})
        self.metrics.observe(
            "atlas_tool_duration_ms", payload["duration_ms"], labels={"tool": action.tool}
        )
        if not result.ok:
            self.metrics.increment("atlas_tool_failures_total", labels={"tool": action.tool})
        latest = await asyncio.to_thread(self.store.get, run.id)
        source = next(
            item["event_id"] for item in reversed(latest.history) if item.get("effect_key") == key
        )
        memory = from_observation(action.tool, result.model_dump(), source)
        await self._emit(
            run.id, "memory.created", {"memory": memory.model_dump(mode="json")}, lease
        )
