import asyncio
from pathlib import Path

import pytest
from sqlalchemy import update

from atlas.demo import DEMOS, prepare_demo
from atlas.domain.models import Task
from atlas.models.backends import DemoBackend
from atlas.runtime.engine import RuntimeEngine
from atlas.runtime.inference import InferenceScheduler
from atlas.storage.store import jobs
from atlas.telemetry.metrics import MetricsRegistry
from atlas.tools.registry import RepositoryTools


@pytest.mark.asyncio
async def test_interrupted_worker_resumes_durable_pending_patch(settings, store, monkeypatch):
    demo_id = next(iter(DEMOS))
    source = prepare_demo(settings.repo_root / "fixture", demo_id)
    run = store.create(
        Task(
            repository_path=str(source),
            instruction=DEMOS[demo_id].instruction,
            demo_id=demo_id,
            approval_policy="workspace",
        )
    )
    entered = asyncio.Event()
    execute = RepositoryTools.execute

    async def interrupt_at_patch(self, name, arguments):
        if name == "apply_patch":
            entered.set()
            await asyncio.Event().wait()
        return await execute(self, name, arguments)

    monkeypatch.setattr(RepositoryTools, "execute", interrupt_at_patch)
    metrics = MetricsRegistry()
    scheduler = InferenceScheduler(DemoBackend(), settings, metrics)
    engine = RuntimeEngine(store, settings, scheduler, metrics)
    first_lease = store.claim("first")
    execution = asyncio.create_task(engine.execute(run.id, first_lease))
    try:
        await asyncio.wait_for(entered.wait(), 15)
        assert store.get(run.id).pending[0].tool == "apply_patch"
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution
        with store.engine.begin() as connection:
            connection.execute(
                update(jobs).where(jobs.c.id == first_lease.id).values(lease_until=0)
            )
        monkeypatch.setattr(RepositoryTools, "execute", execute)
        second_lease = store.claim("second")
        result = await RuntimeEngine(store, settings, scheduler, metrics).execute(
            run.id, second_lease
        )
        assert result.status == "completed"
        assert (
            len([item for item in result.history if item["action"]["tool"] == "apply_patch"]) == 1
        )
        assert any(event["type"] == "run.recovered" for event in store.event_list(run.id))
        assert store.replay(run.id) == result
        assert (Path(result.workspace) / "logic.py").read_text() == DEMOS[demo_id].fixed
    finally:
        execution.cancel()
        await scheduler.close()


@pytest.mark.asyncio
async def test_two_engine_process_handles_do_not_duplicate_filesystem_effects(settings, store):
    demo_id = next(iter(DEMOS))
    source = prepare_demo(settings.repo_root / "fixture", demo_id)
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
        engine = RuntimeEngine(store, settings, scheduler, metrics)
        results = await asyncio.gather(engine.execute(run.id), engine.execute(run.id))
        assert all(result.status == "completed" for result in results)
        assert len(store.get(run.id).history) == 6
        assert store.get(run.id).step_count == 6
    finally:
        await scheduler.close()
