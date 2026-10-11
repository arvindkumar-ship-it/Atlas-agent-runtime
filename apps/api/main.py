from __future__ import annotations

import asyncio
import hmac
import json
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from atlas.config import Settings
from atlas.demo import DEMOS, prepare_demo
from atlas.domain.models import TERMINAL, StrictModel, Task
from atlas.models.backends import make_backend
from atlas.runtime.engine import RuntimeEngine
from atlas.runtime.inference import InferenceScheduler
from atlas.runtime.worker import Worker
from atlas.security.uploads import extract_repository
from atlas.storage.store import ConflictError, Store
from atlas.telemetry.metrics import MetricsRegistry
from atlas.telemetry.tracing import configure_tracing


class RunRequest(StrictModel):
    repository_path: str = Field(min_length=1, max_length=1000)
    instruction: str = Field(min_length=3, max_length=12000)
    context_tokens: int = Field(default=8192, ge=2048, le=65536)
    total_tokens: int = Field(default=180000, ge=2048, le=2_000_000)
    approval_policy: str = "manual"
    priority: int = Field(default=100, ge=0, le=1000)


class ApprovalDecision(StrictModel):
    approved: bool


def create_app(settings: Settings | None = None, backend=None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.repo_root.mkdir(parents=True, exist_ok=True)
        app.state.store = Store(settings.database_url)
        app.state.metrics = MetricsRegistry()
        app.state.scheduler = InferenceScheduler(
            backend or make_backend(settings), settings, app.state.metrics
        )
        app.state.engine = RuntimeEngine(
            app.state.store, settings, app.state.scheduler, app.state.metrics
        )
        app.state.worker = Worker(
            app.state.store, app.state.engine, settings.worker_concurrency, settings.lease_seconds
        )
        provider = configure_tracing(settings.otlp_endpoint)
        if settings.embedded_worker:
            await app.state.worker.start()
        try:
            yield
        finally:
            await app.state.worker.close()
            await app.state.scheduler.close()
            app.state.store.close()
            if provider:
                provider.shutdown()

    app = FastAPI(title="Atlas Agent Runtime", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if (
            request.url.path.startswith(("/api", "/metrics"))
            and request.method != "OPTIONS"
            and settings.api_key
        ):
            key = request.headers.get("authorization", "").removeprefix("Bearer ")
            if not hmac.compare_digest(key, settings.api_key):
                return JSONResponse({"detail": "Valid bearer API key required"}, status_code=401)
        if request.method == "POST":
            limit = 8 * 1024**2 if request.url.path == "/api/repositories" else 160000
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > limit:
                    return JSONResponse({"detail": "Request body too large"}, status_code=413)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = (
            "no-store" if request.url.path.startswith("/api") else "no-cache"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(KeyError)
    async def missing(_, __):
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.exception_handler(ConflictError)
    async def conflict(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(PermissionError)
    async def forbidden(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=403)

    @app.exception_handler(ValueError)
    async def invalid(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "1.0.0"}

    @app.get("/readyz")
    async def ready(request: Request):
        try:
            await asyncio.to_thread(request.app.state.store.health)
        except Exception:
            raise HTTPException(503, "Database unavailable") from None
        return {"status": "ready"}

    @app.get("/api/config")
    async def config():
        return {
            "backend": settings.backend,
            "model": settings.model_name,
            "sandbox": settings.sandbox,
            "authentication": bool(settings.api_key),
            "embedded_worker": settings.embedded_worker,
            "gpu_measurements": False,
        }

    @app.get("/api/demos")
    async def demos():
        return [
            {"id": demo.id, "title": demo.title, "instruction": demo.instruction}
            for demo in DEMOS.values()
        ]

    async def quota(store: Store):
        recent = await asyncio.to_thread(store.list, 100)
        if sum(item.status not in TERMINAL for item in recent) >= 32:
            raise HTTPException(429, "At most 32 active runs are allowed")

    @app.post("/api/demos/{demo_id}/run", status_code=202)
    async def run_demo(demo_id: str, request: Request):
        if demo_id not in DEMOS:
            raise HTTPException(404, "Unknown demo")
        await quota(request.app.state.store)
        root = settings.repo_root / ("demo-" + uuid4().hex[:12])
        repository = await asyncio.to_thread(prepare_demo, root, demo_id)
        task = Task(
            repository_path=str(repository.resolve()),
            instruction=DEMOS[demo_id].instruction,
            demo_id=demo_id if settings.backend == "demo" else None,
            approval_policy="workspace",
        )
        return await asyncio.to_thread(request.app.state.store.create, task)

    @app.get("/api/repositories")
    async def repositories():
        return {
            "repositories": sorted(
                path.name
                for path in settings.repo_root.iterdir()
                if path.is_dir() and not path.is_symlink()
            )
        }

    @app.post("/api/repositories", status_code=201)
    async def upload_repository(request: Request):
        if request.headers.get("content-type", "").split(";")[0] != "application/zip":
            raise HTTPException(415, "Send the ZIP body with Content-Type: application/zip")
        name = await asyncio.to_thread(extract_repository, await request.body(), settings.repo_root)
        return {"repository_path": name}

    @app.post("/api/runs", status_code=202)
    async def create_run(body: RunRequest, request: Request):
        if settings.backend == "demo":
            raise HTTPException(
                409, "Use a bundled demo or configure a real model backend for repository tasks"
            )
        root = settings.repo_root.resolve()
        candidate = Path(body.repository_path)
        candidate = (candidate if candidate.is_absolute() else root / candidate).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_dir():
            raise HTTPException(403, "Repository must be inside ATLAS_REPO_ROOT")
        await quota(request.app.state.store)
        key = request.headers.get("idempotency-key")
        if key and len(key) > 128:
            raise HTTPException(422, "Idempotency key too long")
        task = Task(**{**body.model_dump(), "repository_path": str(candidate)})
        return await asyncio.to_thread(request.app.state.store.create, task, key)

    @app.get("/api/runs")
    async def list_runs(
        request: Request,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        runs = await asyncio.to_thread(request.app.state.store.list, limit, offset)
        return [
            {
                "id": item.id,
                "task": item.task,
                "status": item.status,
                "step_count": item.step_count,
                "token_usage": item.token_usage,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
                "error": item.error,
            }
            for item in runs
        ]

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: UUID, request: Request):
        return await asyncio.to_thread(request.app.state.store.get, str(run_id))

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: UUID, request: Request, after: int = Query(default=0, ge=0)):
        await asyncio.to_thread(request.app.state.store.get, str(run_id))
        return await asyncio.to_thread(request.app.state.store.event_list, str(run_id), after)

    @app.get("/api/runs/{run_id}/stream")
    async def stream(run_id: UUID, request: Request, after: int = Query(default=0, ge=0)):
        store = request.app.state.store
        await asyncio.to_thread(store.get, str(run_id))

        async def generate():
            sequence = after
            while not await request.is_disconnected():
                items = await asyncio.to_thread(store.event_list, str(run_id), sequence)
                for item in items:
                    sequence = item["sequence"]
                    yield f"id: {sequence}\ndata: {json.dumps(item)}\n\n"
                run = await asyncio.to_thread(store.get, str(run_id))
                if run.status in TERMINAL:
                    break
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(
            generate(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    @app.get("/api/runs/{run_id}/replay")
    async def replay(
        run_id: UUID, request: Request, through: int | None = Query(default=None, ge=1)
    ):
        return await asyncio.to_thread(request.app.state.store.replay, str(run_id), through)

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel(run_id: UUID, request: Request):
        await asyncio.to_thread(request.app.state.store.cancel, str(run_id))
        return {"status": "cancelled"}

    @app.get("/api/runs/{run_id}/approvals")
    async def get_approvals(run_id: UUID, request: Request):
        await asyncio.to_thread(request.app.state.store.get, str(run_id))
        return await asyncio.to_thread(request.app.state.store.approval_list, str(run_id))

    @app.post("/api/runs/{run_id}/approvals/{approval_id}")
    async def approval(run_id: UUID, approval_id: UUID, body: ApprovalDecision, request: Request):
        await asyncio.to_thread(
            request.app.state.store.decide_approval, str(run_id), str(approval_id), body.approved
        )
        return {"approved": body.approved}

    @app.get("/api/runs/{run_id}/patch")
    async def download_patch(run_id: UUID, request: Request):
        run = await asyncio.to_thread(request.app.state.store.get, str(run_id))
        if not run.report:
            raise HTTPException(409, "Verification report is not available")
        return Response(
            run.report["diff"],
            media_type="text/x-diff",
            headers={"Content-Disposition": f'attachment; filename="atlas-{run_id}.patch"'},
        )

    @app.get("/api/dashboard")
    async def dashboard(request: Request):
        store = request.app.state.store
        recent = await asyncio.to_thread(store.list, 100)
        model_calls, tool_calls, failures, contexts = [], [], [], []
        for run in recent:
            for item in await asyncio.to_thread(store.event_list, run.id):
                target = {
                    "model.completed": model_calls,
                    "tool.completed": tool_calls,
                    "run.failed": failures,
                    "context.compiled": contexts,
                }.get(item["type"])
                if target is not None:
                    target.append(item["payload"])
        raw = sum(item["raw_tokens_estimate"] for item in contexts)
        compiled = sum(item["compiled_tokens_estimate"] for item in contexts)
        return {
            "runs": len(recent),
            "completed": sum(item.status == "completed" for item in recent),
            "active": sum(item.status not in TERMINAL for item in recent),
            "failed": len(failures),
            "model_calls": len(model_calls),
            "tool_calls": len(tool_calls),
            "raw_tokens_estimate": raw,
            "compiled_tokens_estimate": compiled,
            "context_reduction": 1 - compiled / raw if raw else 0,
            "model_input_tokens": sum(item.get("input_tokens") or 0 for item in model_calls)
            if any(item.get("input_tokens") is not None for item in model_calls)
            else None,
            "scope": "latest 100 runs",
            "backend": settings.backend,
        }

    @app.get("/metrics")
    async def metrics(request: Request):
        return Response(
            request.app.state.metrics.prometheus(), media_type="text/plain; version=0.0.4"
        )

    @app.get("/api/metrics")
    async def metrics_json(request: Request):
        return request.app.state.metrics.snapshot()

    frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if (frontend / "assets").is_dir() and (frontend / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

        @app.get("/")
        async def index():
            return FileResponse(frontend / "index.html")

    return app


app = create_app()
