from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from atlas.config import Settings
from atlas.demo import DEMOS, prepare_demo
from atlas.domain.models import Task
from atlas.models.backends import make_backend
from atlas.runtime.engine import RuntimeEngine
from atlas.runtime.inference import InferenceScheduler
from atlas.storage.store import Store
from atlas.telemetry.metrics import MetricsRegistry


async def execute(args, settings: Settings):
    if args.command == "demo":
        repository = prepare_demo(settings.repo_root / f"cli-{args.name}", args.name).resolve()
        task = Task(
            repository_path=str(repository),
            instruction=DEMOS[args.name].instruction,
            demo_id=args.name if settings.backend == "demo" else None,
            approval_policy="workspace",
        )
    else:
        repository = Path(args.repo).resolve()
        if not repository.is_dir():
            raise ValueError("Repository does not exist")
        task = Task(
            repository_path=str(repository),
            instruction=args.task,
            approval_policy="workspace" if args.allow_workspace_edits else "manual",
        )
    store = Store(settings.database_url)
    run = store.create(task)
    metrics = MetricsRegistry()
    scheduler = InferenceScheduler(make_backend(settings), settings, metrics)
    try:
        result = await RuntimeEngine(store, settings, scheduler, metrics).execute(run.id)
        print(
            json.dumps(
                {
                    "run_id": result.id,
                    "status": result.status,
                    "answer": result.answer,
                    "error": result.error,
                    "workspace": result.workspace,
                    "checks": result.report["checks"] if result.report else None,
                },
                indent=2,
            )
        )
        if result.report:
            path = settings.data_dir / "artifacts" / run.id / "verified.patch"
            path.write_text(result.report["diff"], encoding="utf-8")
            print(f"Patch: {path}")
        if result.status == "waiting_approval":
            print(
                "Open the console to review the pending patch, or explicitly pass --allow-workspace-edits."
            )
        return 0 if result.status in {"completed", "waiting_approval"} else 1
    except Exception as exc:
        store.append(run.id, "run.failed", {"error": str(exc)[:800]})
        raise
    finally:
        await scheduler.close()
        store.close()


def main():
    parser = argparse.ArgumentParser(prog="atlas", description="Atlas Agent Runtime")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Run API and built dashboard")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    demo = commands.add_parser("demo", help="Run a bundled bug fixture")
    demo.add_argument("name", choices=DEMOS)
    demo.add_argument(
        "--trusted-local",
        action="store_true",
        help="Explicitly allow host pytest for bundled fixtures",
    )
    run = commands.add_parser("run", help="Investigate a local repository")
    run.add_argument("--repo", required=True)
    run.add_argument("--task", required=True)
    run.add_argument(
        "--allow-workspace-edits",
        action="store_true",
        help="Authorize patches in the isolated copy",
    )
    commands.add_parser("worker", help="Run a standalone durable worker")
    replay = commands.add_parser("replay", help="Verify and reconstruct an event stream")
    replay.add_argument("run_id")
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.command == "serve":
        import uvicorn

        uvicorn.run("apps.api.main:app", host=args.host, port=args.port)
    elif args.command == "worker":
        from apps.worker.main import main as worker_main

        asyncio.run(worker_main())
    elif args.command == "replay":
        store = Store(settings.database_url)
        try:
            print(store.replay(args.run_id).model_dump_json(indent=2))
        finally:
            store.close()
    else:
        if args.command == "demo" and args.trusted_local:
            settings = settings.model_copy(update={"sandbox": "local", "allow_trusted_local": True})
        raise SystemExit(asyncio.run(execute(args, settings)))


if __name__ == "__main__":
    main()
