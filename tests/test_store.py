from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select, update

from atlas.domain.models import Action, Task
from atlas.storage.store import ConflictError, LeaseLost, Store, events, jobs


def task():
    return Task(repository_path="fixture", instruction="Repair the failing test")


def test_replay_survives_database_reopen(store, settings):
    run = store.create(task())
    store.append(run.id, "run.started", {})
    store.append(run.id, "model.completed", {"charged_tokens": 42})
    expected = store.get(run.id)
    other = Store(settings.database_url)
    try:
        assert other.replay(run.id) == expected
        assert other.replay(run.id, through=1).status == "queued"
        assert expected.token_usage == 42
    finally:
        other.close()


def test_idempotency_is_bound_to_body(store):
    first = store.create(task(), "client-1")
    assert store.create(task(), "client-1").id == first.id
    with pytest.raises(ConflictError):
        store.create(task().model_copy(update={"instruction": "A different request"}), "client-1")
    assert len(store.list()) == 1


def test_optimistic_conflict_rolls_back_event(store):
    run = store.create(task())
    store.append(run.id, "run.started", {}, expected=run.sequence)
    with pytest.raises(ConflictError):
        store.append(run.id, "run.failed", {"error": "stale"}, expected=run.sequence)
    assert len(store.event_list(run.id)) == 2
    assert store.replay(run.id) == store.get(run.id)


def test_concurrent_appends_have_no_lost_updates(store):
    run = store.create(task())
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda _: store.append(run.id, "model.completed", {"charged_tokens": 1}), range(32)
            )
        )
    result = store.get(run.id)
    assert result.token_usage == 32
    assert result.step_count == 32
    assert result.sequence == 33
    assert store.replay(run.id) == result


def test_tampered_event_is_detected(store):
    run = store.create(task())
    with store.engine.begin() as connection:
        connection.execute(
            update(events).where(events.c.run_id == run.id).values(payload={"tampered": True})
        )
    with pytest.raises(ValueError, match="integrity"):
        store.replay(run.id)


def test_claim_is_exclusive_and_stale_generation_is_fenced(store):
    run = store.create(task())
    lease = store.claim("worker-a")
    assert lease.run_id == run.id
    assert store.claim("worker-b") is None
    store.heartbeat(lease, 45)
    with store.engine.begin() as connection:
        connection.execute(update(jobs).where(jobs.c.id == lease.id).values(lease_until=0))
    replacement = store.claim("worker-b")
    assert replacement.generation == lease.generation + 1
    for operation in [
        lambda: store.heartbeat(lease, 45),
        lambda: store.append(run.id, "run.started", {}, lease=lease),
        lambda: store.finish_job(lease),
    ]:
        with pytest.raises(LeaseLost):
            operation()
    store.append(run.id, "run.started", {}, lease=replacement)


def test_queue_priority_and_dead_letter(store):
    slow = store.create(task().model_copy(update={"priority": 900}))
    fast = store.create(task().model_copy(update={"priority": 1}))
    lease = store.claim("w")
    assert lease.run_id == fast.id
    store.finish_job(lease)
    lease = store.claim("w")
    assert lease.run_id == slow.id
    with store.engine.begin() as connection:
        connection.execute(
            update(jobs).where(jobs.c.id == lease.id).values(attempts=5, lease_until=0)
        )
    assert store.claim("w") is None
    assert store.get(slow.id).status == "failed"


def test_tool_effect_and_projection_are_one_commit(store):
    run = store.create(task())
    action = Action(tool="list_files")
    store.append(run.id, "actions.proposed", {"actions": [action.model_dump()]})
    payload = {
        "action": action.model_dump(),
        "result": {"ok": True, "output": "logic.py"},
        "effect_key": "effect",
    }
    store.commit_tool(run.id, "effect", payload)
    store.commit_tool(run.id, "effect", payload)
    result = store.get(run.id)
    assert len(result.history) == 1 and not result.pending
    assert store.effect("effect") == payload["result"]
    assert store.replay(run.id) == result


def test_approval_cannot_cross_runs_or_patches(store):
    first, second = store.create(task()), store.create(task())
    action = Action(tool="apply_patch", arguments={"patch": "exact patch"})
    approval = store.request_approval(first.id, action)
    with pytest.raises(KeyError):
        store.decide_approval(second.id, approval, True)
    store.decide_approval(first.id, approval, True)
    assert store.approval_granted(first.id, action)
    assert not store.approval_granted(second.id, action)
    assert not store.approval_granted(
        first.id, Action(tool="apply_patch", arguments={"patch": "modified"})
    )
    with pytest.raises(ConflictError):
        store.decide_approval(first.id, approval, False)
    with store.engine.connect() as connection:
        assert (
            connection.execute(select(jobs.c.status).where(jobs.c.run_id == first.id)).scalar_one()
            == "queued"
        )


def test_cancel_is_terminal_and_removes_queue_job(store):
    run = store.create(task())
    store.cancel(run.id)
    assert store.get(run.id).status == "cancelled"
    assert store.claim("w") is None
    with pytest.raises(ConflictError):
        store.append(run.id, "run.started", {})
