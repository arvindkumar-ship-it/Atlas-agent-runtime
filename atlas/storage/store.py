from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import (
    JSON,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    and_,
    create_engine,
    event,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from atlas.domain.models import TERMINAL, Action, Run, Task, new_id, utc_now
from atlas.runtime.reducer import reduce_event

metadata = MetaData()
runs = Table(
    "runs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("state", JSON, nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("hash", String(64), nullable=False),
    Column("request_key", String(128), unique=True),
    Column("request_hash", String(64)),
    Column("created_at", String(40), nullable=False),
)
events = Table(
    "events",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("run_id", String(36), nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("type", String(80), nullable=False),
    Column("at", String(40), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("previous_hash", String(64), nullable=False),
    Column("hash", String(64), nullable=False),
    UniqueConstraint("run_id", "sequence"),
)
jobs = Table(
    "jobs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("run_id", String(36), nullable=False, unique=True),
    Column("status", String(20), nullable=False),
    Column("priority", Integer, nullable=False),
    Column("owner", String(80)),
    Column("generation", Integer, nullable=False),
    Column("available_at", Float, nullable=False),
    Column("lease_until", Float, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("error", Text),
)
effects = Table(
    "effects",
    metadata,
    Column("key", String(64), primary_key=True),
    Column("run_id", String(36), nullable=False),
    Column("result", JSON, nullable=False),
)
approvals = Table(
    "approvals",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("run_id", String(36), nullable=False),
    Column("action_hash", String(64), nullable=False),
    Column("action", JSON, nullable=False),
    Column("status", String(20), nullable=False),
    Column("at", String(40), nullable=False),
)
schema_versions = Table("schema_versions", metadata, Column("version", Integer, primary_key=True))


def canonical(data: object) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(data: object) -> str:
    return hashlib.sha256(canonical(data).encode()).hexdigest()


@dataclass(frozen=True)
class Lease:
    id: str
    run_id: str
    owner: str
    generation: int
    attempts: int


class ConflictError(RuntimeError):
    pass


class LeaseLost(ConflictError):
    pass


class Store:
    """Events, projections and queue changes share one database transaction."""

    def __init__(self, url: str):
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url.removeprefix("postgres://")
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
        sqlite = url.startswith("sqlite")
        if sqlite and not url.endswith(":memory:"):
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        options = {"connect_args": {"check_same_thread": False, "timeout": 30}} if sqlite else {}
        self.engine = create_engine(url, pool_pre_ping=True, **options)
        if sqlite:

            @event.listens_for(self.engine, "connect")
            def configure(connection, _):
                connection.isolation_level = None
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA foreign_keys=ON")

            @event.listens_for(self.engine, "begin")
            def begin(connection):
                connection.exec_driver_sql("BEGIN IMMEDIATE")

        with self.engine.begin() as connection:
            if not sqlite:
                connection.exec_driver_sql("SELECT pg_advisory_xact_lock(734213432)")
            metadata.create_all(connection)
            versions = connection.execute(select(schema_versions.c.version)).scalars().all()
            if versions and versions != [1]:
                raise RuntimeError("Unsupported database schema; do not overwrite it")
            if not versions:
                connection.execute(insert(schema_versions).values(version=1))

    def close(self):
        self.engine.dispose()

    def health(self) -> bool:
        with self.engine.connect() as connection:
            return connection.exec_driver_sql("SELECT 1").scalar() == 1

    def create(self, task: Task, request_key: str | None = None) -> Run:
        try:
            with self.engine.begin() as connection:
                if request_key:
                    row = (
                        connection.execute(select(runs).where(runs.c.request_key == request_key))
                        .mappings()
                        .first()
                    )
                    if row:
                        if row["request_hash"] != digest(task.model_dump(mode="json")):
                            raise ConflictError("Idempotency key was used for another task")
                        return Run.model_validate(row["state"])
                run = Run(task=task)
                connection.execute(
                    insert(runs).values(
                        id=run.id,
                        state=run.model_dump(mode="json"),
                        sequence=0,
                        hash="",
                        request_key=request_key,
                        request_hash=digest(task.model_dump(mode="json")),
                        created_at=run.created_at,
                    )
                )
                run = self._append(
                    connection, run.id, "run.created", {"run": run.model_dump(mode="json")}
                )
                connection.execute(
                    insert(jobs).values(
                        id=new_id(),
                        run_id=run.id,
                        status="queued",
                        priority=task.priority,
                        owner=None,
                        generation=0,
                        available_at=time.time(),
                        lease_until=0,
                        attempts=0,
                        error=None,
                    )
                )
                return run
        except IntegrityError:
            if request_key:
                with self.engine.connect() as connection:
                    row = (
                        connection.execute(select(runs).where(runs.c.request_key == request_key))
                        .mappings()
                        .first()
                    )
                if row:
                    if row["request_hash"] != digest(task.model_dump(mode="json")):
                        raise ConflictError("Idempotency key was used for another task") from None
                    return Run.model_validate(row["state"])
            raise

    def get(self, run_id: str) -> Run:
        with self.engine.connect() as connection:
            row = connection.execute(select(runs.c.state).where(runs.c.id == run_id)).first()
        if row is None:
            raise KeyError(run_id)
        return Run.model_validate(row[0])

    def list(self, limit: int = 50, offset: int = 0) -> list[Run]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(runs.c.state).order_by(runs.c.created_at.desc()).limit(limit).offset(offset)
            ).all()
        return [Run.model_validate(row[0]) for row in rows]

    def event_list(self, run_id: str, after: int = 0) -> list[dict]:
        with self.engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    select(events)
                    .where(and_(events.c.run_id == run_id, events.c.sequence > after))
                    .order_by(events.c.sequence)
                ).mappings()
            ]

    def replay(self, run_id: str, through: int | None = None) -> Run:
        state, previous_hash = None, ""
        for expected, item in enumerate(self.event_list(run_id), 1):
            if through is not None and expected > through:
                break
            body = {key: value for key, value in item.items() if key != "hash"}
            if (
                item["sequence"] != expected
                or item["previous_hash"] != previous_hash
                or digest(body) != item["hash"]
            ):
                raise ValueError("Event integrity check failed")
            state = reduce_event(state, item)
            previous_hash = item["hash"]
        if state is None:
            raise KeyError(run_id)
        return state

    def _check_lease(self, connection: Connection, lease: Lease):
        row = (
            connection.execute(select(jobs).where(jobs.c.id == lease.id).with_for_update())
            .mappings()
            .one()
        )
        if (
            row["owner"] != lease.owner
            or row["generation"] != lease.generation
            or row["status"] != "running"
            or row["lease_until"] <= time.time()
        ):
            raise LeaseLost("Worker lease expired or was reassigned")

    def _append(
        self,
        connection: Connection,
        run_id: str,
        kind: str,
        payload: dict,
        expected: int | None = None,
        lease: Lease | None = None,
    ) -> Run:
        if lease:
            self._check_lease(connection, lease)
        row = (
            connection.execute(select(runs).where(runs.c.id == run_id).with_for_update())
            .mappings()
            .first()
        )
        if not row:
            raise KeyError(run_id)
        if expected is not None and expected != row["sequence"]:
            raise ConflictError("Run changed since it was read")
        state = Run.model_validate(row["state"])
        if state.status in TERMINAL and kind != "run.created":
            raise ConflictError("Run is already terminal")
        item = dict(
            id=new_id(),
            run_id=run_id,
            sequence=row["sequence"] + 1,
            type=kind,
            at=utc_now(),
            payload=payload,
            previous_hash=row["hash"],
        )
        item["hash"] = digest(item)
        state = reduce_event(state, item)
        connection.execute(insert(events).values(**item))
        connection.execute(
            update(runs)
            .where(runs.c.id == run_id)
            .values(
                state=state.model_dump(mode="json"),
                sequence=state.sequence,
                hash=item["hash"],
            )
        )
        return state

    def append(
        self,
        run_id: str,
        kind: str,
        payload: dict,
        expected: int | None = None,
        lease: Lease | None = None,
    ) -> Run:
        with self.engine.begin() as connection:
            return self._append(connection, run_id, kind, payload, expected, lease)

    def claim(self, owner: str, lease_seconds: int = 45) -> Lease | None:
        now = time.time()
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    select(jobs)
                    .where(
                        or_(
                            and_(jobs.c.status == "queued", jobs.c.available_at <= now),
                            and_(jobs.c.status == "running", jobs.c.lease_until <= now),
                        )
                    )
                    .order_by(jobs.c.priority, jobs.c.available_at)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if not row:
                return None
            run = Run.model_validate(
                connection.execute(
                    select(runs.c.state).where(runs.c.id == row["run_id"])
                ).scalar_one()
            )
            if run.status in TERMINAL or row["attempts"] >= 5:
                connection.execute(update(jobs).where(jobs.c.id == row["id"]).values(status="dead"))
                if run.status not in TERMINAL:
                    self._append(
                        connection, run.id, "run.failed", {"error": "Job retry limit exceeded"}
                    )
                return None
            generation = row["generation"] + 1
            connection.execute(
                update(jobs)
                .where(jobs.c.id == row["id"])
                .values(
                    status="running",
                    owner=owner,
                    generation=generation,
                    lease_until=now + lease_seconds,
                    attempts=row["attempts"] + 1,
                )
            )
            return Lease(row["id"], row["run_id"], owner, generation, row["attempts"] + 1)

    def heartbeat(self, lease: Lease, lease_seconds: int):
        with self.engine.begin() as connection:
            self._check_lease(connection, lease)
            connection.execute(
                update(jobs)
                .where(jobs.c.id == lease.id)
                .values(lease_until=time.time() + lease_seconds)
            )

    def finish_job(self, lease: Lease, status: str = "completed", error: str | None = None):
        with self.engine.begin() as connection:
            self._check_lease(connection, lease)
            connection.execute(
                update(jobs)
                .where(jobs.c.id == lease.id)
                .values(
                    status=status,
                    owner=None,
                    lease_until=0,
                    error=error,
                    available_at=time.time() + min(2**lease.attempts, 30),
                )
            )

    def effect(self, key: str) -> dict | None:
        with self.engine.connect() as connection:
            return connection.execute(
                select(effects.c.result).where(effects.c.key == key)
            ).scalar_one_or_none()

    def commit_tool(self, run_id: str, key: str, payload: dict, lease: Lease | None = None):
        with self.engine.begin() as connection:
            if lease:
                self._check_lease(connection, lease)
            existing = connection.execute(select(effects.c.key).where(effects.c.key == key)).first()
            if existing:
                return
            connection.execute(
                insert(effects).values(key=key, run_id=run_id, result=payload["result"])
            )
            self._append(connection, run_id, "tool.completed", payload, lease=lease)

    def request_approval(self, run_id: str, action: Action, lease: Lease | None = None) -> str:
        action_data = action.model_dump(mode="json")
        action_hash = digest(action_data)
        with self.engine.begin() as connection:
            found = (
                connection.execute(
                    select(approvals).where(
                        and_(approvals.c.run_id == run_id, approvals.c.action_hash == action_hash)
                    )
                )
                .mappings()
                .first()
            )
            if found:
                return found["id"]
            approval_id = new_id()
            connection.execute(
                insert(approvals).values(
                    id=approval_id,
                    run_id=run_id,
                    action_hash=action_hash,
                    action=action_data,
                    status="pending",
                    at=utc_now(),
                )
            )
            self._append(
                connection,
                run_id,
                "approval.requested",
                {"approval_id": approval_id, "action": action_data},
                lease=lease,
            )
            return approval_id

    def approval_granted(self, run_id: str, action: Action) -> bool:
        with self.engine.connect() as connection:
            return bool(
                connection.execute(
                    select(approvals.c.id).where(
                        and_(
                            approvals.c.run_id == run_id,
                            approvals.c.action_hash == digest(action.model_dump(mode="json")),
                            approvals.c.status == "approved",
                        )
                    )
                ).first()
            )

    def approval_list(self, run_id: str) -> list[dict]:
        with self.engine.connect() as connection:
            return [
                dict(item)
                for item in connection.execute(
                    select(approvals).where(approvals.c.run_id == run_id)
                ).mappings()
            ]

    def decide_approval(self, run_id: str, approval_id: str, approved: bool):
        with self.engine.begin() as connection:
            connection.execute(
                select(jobs.c.id).where(jobs.c.run_id == run_id).with_for_update()
            ).first()
            row = (
                connection.execute(
                    select(approvals)
                    .where(and_(approvals.c.id == approval_id, approvals.c.run_id == run_id))
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if not row:
                raise KeyError(approval_id)
            if row["status"] != "pending":
                raise ConflictError("Approval is already decided")
            self._append(
                connection,
                run_id,
                "approval.decided",
                {"approval_id": approval_id, "approved": approved},
            )
            connection.execute(
                update(approvals)
                .where(approvals.c.id == approval_id)
                .values(status="approved" if approved else "rejected")
            )
            connection.execute(
                update(jobs)
                .where(jobs.c.run_id == run_id)
                .values(
                    status="queued" if approved else "completed",
                    owner=None,
                    lease_until=0,
                    available_at=time.time(),
                )
            )

    def cancel(self, run_id: str):
        with self.engine.begin() as connection:
            connection.execute(
                select(jobs.c.id).where(jobs.c.run_id == run_id).with_for_update()
            ).first()
            self._append(connection, run_id, "run.cancelled", {})
            connection.execute(
                update(jobs)
                .where(jobs.c.run_id == run_id)
                .values(status="cancelled", lease_until=0)
            )
