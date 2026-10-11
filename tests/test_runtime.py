import asyncio
import json
from pathlib import Path

import pytest

from atlas.context.compiler import ContextCompiler, ContextOverflow
from atlas.demo import DEMOS, prepare_demo
from atlas.domain.models import Memory, PlanNode, Run, Task
from atlas.memory.manager import contradictions
from atlas.models.backends import DemoBackend
from atlas.models.types import GenerateRequest, GenerateResult
from atlas.runtime.engine import RuntimeEngine
from atlas.runtime.graph import node_ready, validate_plan
from atlas.runtime.inference import InferenceScheduler, KVBudget, ModelKVConfig, estimate_kv_bytes
from atlas.telemetry.metrics import MetricsRegistry


@pytest.mark.asyncio
@pytest.mark.parametrize("demo_id", list(DEMOS))
async def test_end_to_end_demo_with_independent_verification(settings, store, demo_id):
    source = prepare_demo(settings.repo_root / demo_id, demo_id)
    original = (source / "logic.py").read_text()
    run = store.create(
        Task(
            repository_path=str(source),
            instruction=DEMOS[demo_id].instruction,
            demo_id=demo_id,
            approval_policy="workspace",
        )
    )
    metrics = MetricsRegistry()
    scheduler = InferenceScheduler(DemoBackend(), settings, metrics)
    try:
        result = await RuntimeEngine(store, settings, scheduler, metrics).execute(run.id)
        assert result.status == "completed", result.error
        assert result.report["passed"]
        assert result.report["tests_passed"] >= 3
        assert (source / "logic.py").read_text() == original
        assert (Path(result.workspace) / "logic.py").read_text() == DEMOS[demo_id].fixed
        assert (Path(result.workspace) / "test_regression.py").is_file()
        assert any(
            not item["result"]["ok"]
            for item in result.history
            if item["action"]["tool"] == "run_tests"
        )
        assert store.replay(run.id) == result
        assert scheduler.kv.used_bytes == 0
    finally:
        await scheduler.close()


@pytest.mark.asyncio
async def test_manual_approval_resume_on_fresh_engine(settings, store):
    demo_id = next(iter(DEMOS))
    source = prepare_demo(settings.repo_root / demo_id, demo_id)
    run = store.create(
        Task(repository_path=str(source), instruction=DEMOS[demo_id].instruction, demo_id=demo_id)
    )
    metrics = MetricsRegistry()
    scheduler = InferenceScheduler(DemoBackend(), settings, metrics)
    try:
        parked = await RuntimeEngine(store, settings, scheduler, metrics).execute(run.id)
        assert parked.status == "waiting_approval"
        assert "test_regression.py" not in {path.name for path in Path(parked.workspace).iterdir()}
        approval = store.approval_list(run.id)[0]
        store.decide_approval(run.id, approval["id"], True)
        result = await RuntimeEngine(store, settings, scheduler, metrics).execute(run.id)
        assert result.status == "completed"
        assert (
            len([item for item in result.history if item["action"]["tool"] == "apply_patch"]) == 1
        )
        assert store.replay(run.id) == result
    finally:
        await scheduler.close()


class ScriptBackend:
    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        return GenerateResult(text=next(self.decisions), latency_ms=1)

    async def close(self):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "decision,error",
    [
        (
            json.dumps({"type": "finish", "summary": "Everything passed", "evidence": ["made-up"]}),
            "evidence",
        ),
        (
            json.dumps({"type": "parallel", "actions": [{"tool": "run_tests", "arguments": {}}]}),
            "read-only",
        ),
        ("not JSON", "invalid decisions"),
    ],
)
async def test_model_cannot_invent_success_or_parallel_side_effects(
    settings, store, decision, error
):
    source = prepare_demo(settings.repo_root / "fixture", next(iter(DEMOS)))
    run = store.create(Task(repository_path=str(source), instruction="Repair the code"))
    backend, metrics = ScriptBackend([decision] * 3), MetricsRegistry()
    scheduler = InferenceScheduler(backend, settings, metrics)
    try:
        result = await RuntimeEngine(store, settings, scheduler, metrics).execute(run.id)
        assert result.status == "failed" and error in result.error
        assert result.report is None
    finally:
        await scheduler.close()


@pytest.mark.parametrize(
    "nodes",
    [
        [PlanNode(id="a", title="A", dependencies=["a"])],
        [
            PlanNode(id="a", title="A", dependencies=["b"]),
            PlanNode(id="b", title="B", dependencies=["a"]),
        ],
        [PlanNode(id="a", title="A"), PlanNode(id="a", title="Duplicate")],
        [PlanNode(id="a", title="A", dependencies=["missing"])],
    ],
)
def test_invalid_dependency_graph(nodes):
    with pytest.raises(ValueError):
        validate_plan(nodes)


def test_dependency_readiness():
    nodes = [PlanNode(id="a", title="A"), PlanNode(id="b", title="B", dependencies=["a"])]
    validate_plan(nodes)
    assert not node_ready(nodes, "b")
    nodes[0].status = "succeeded"
    assert node_ready(nodes, "b")


def history_entry(index, output):
    return {
        "event_id": str(index),
        "action": {"tool": "read_file", "arguments": {"path": "logic.py"}},
        "result": {"ok": True, "output": output},
    }


@pytest.mark.parametrize(
    "mode",
    [
        "recent_window",
        "keyword_memory",
        "summary_memory",
        "atlas_context",
        "atlas_runtime",
        "atlas_full",
    ],
)
def test_context_budget_preserves_policy_goal_and_contradictions(mode):
    run = Run(
        task=Task(
            repository_path="fixture",
            instruction="Repair expired authentication",
            context_mode=mode,
            context_tokens=8192,
        ),
        history=[history_entry(index, "token expiry " * 1500) for index in range(12)],
        memories=[
            Memory(kind="fact", content="expiry was checked", subject="expiry", value="yes"),
            Memory(kind="hypothesis", content="expiry was missing", subject="expiry", value="no"),
        ],
    )
    compiled = ContextCompiler().compile(run)
    assert compiled.compiled_tokens_estimate <= 8192 - 1024
    assert compiled.raw_tokens_estimate > compiled.compiled_tokens_estimate
    assert "untrusted" in compiled.messages[0]["content"]
    assert "Repair expired authentication" in compiled.messages[1]["content"]
    assert "contradictions" in compiled.messages[1]["content"]
    assert contradictions(run.memories)


def test_full_history_overflow_is_explicit():
    run = Run(
        task=Task(
            repository_path="fixture",
            instruction="Repair this repository",
            context_mode="full_history",
        ),
        history=[history_entry(1, "long evidence " * 10000)],
    )
    with pytest.raises(ContextOverflow):
        ContextCompiler().compile(run)


def test_stable_system_prefix_not_a_claim_of_gpu_reuse():
    compiler = ContextCompiler()
    first = compiler.compile(
        Run(task=Task(repository_path="x", instruction="Repair authentication"))
    )
    second = compiler.compile(
        Run(task=Task(repository_path="y", instruction="Repair database queries"))
    )
    assert first.prefix_hash == second.prefix_hash
    assert first.messages[1] != second.messages[1]


def test_kv_budget_formula_and_release():
    config = ModelKVConfig(2, 2, 8, 4)
    assert estimate_kv_bytes(config, 10) == 2560
    budget = KVBudget(config, 2560)
    budget.allocate("one", 10)
    with pytest.raises(MemoryError):
        budget.allocate("two", 1)
    budget.release("one")
    assert budget.used_bytes == 0 and budget.peak_bytes == 2560


@pytest.mark.asyncio
async def test_scheduler_cancellation_releases_allocation(settings):
    entered = asyncio.Event()

    class SlowBackend:
        async def generate(self, request):
            entered.set()
            await asyncio.Event().wait()

        async def close(self):
            pass

    scheduler = InferenceScheduler(SlowBackend(), settings, MetricsRegistry())
    pending = asyncio.create_task(scheduler.submit(GenerateRequest(messages=[]), 100))
    await entered.wait()
    assert scheduler.kv.used_bytes > 0
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    for _ in range(20):
        if not scheduler.kv.used_bytes:
            break
        await asyncio.sleep(0.01)
    assert scheduler.kv.used_bytes == 0
    await scheduler.close()
