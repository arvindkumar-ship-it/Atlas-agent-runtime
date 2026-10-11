from __future__ import annotations

import asyncio
import logging
import os
import socket
from uuid import uuid4

from atlas.domain.models import TERMINAL, RunStatus
from atlas.runtime.engine import RuntimeEngine
from atlas.storage.store import ConflictError, Lease, LeaseLost, Store

logger = logging.getLogger(__name__)


class Worker:
    def __init__(
        self, store: Store, engine: RuntimeEngine, concurrency: int = 2, lease_seconds: int = 45
    ):
        self.store, self.engine = store, engine
        self.concurrency, self.lease_seconds = concurrency, lease_seconds
        self.owner = f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:8]}"
        self.tasks = []
        self.closed = False

    async def start(self):
        self.tasks = [asyncio.create_task(self._poll()) for _ in range(self.concurrency)]

    async def _poll(self):
        while not self.closed:
            lease = await asyncio.to_thread(self.store.claim, self.owner, self.lease_seconds)
            if lease is None:
                await asyncio.sleep(0.2)
                continue
            await self._handle(lease)

    async def _heartbeat(self, lease: Lease, execution: asyncio.Task):
        while True:
            await asyncio.sleep(self.lease_seconds / 3)
            try:
                await asyncio.to_thread(self.store.heartbeat, lease, self.lease_seconds)
            except LeaseLost:
                execution.cancel()
                return

    async def _watch_cancel(self, lease: Lease, execution: asyncio.Task):
        while True:
            await asyncio.sleep(0.25)
            run = await asyncio.to_thread(self.store.get, lease.run_id)
            if run.status == RunStatus.CANCELLED:
                execution.cancel()
                return

    async def _handle(self, lease: Lease):
        execution = asyncio.create_task(self.engine.execute(lease.run_id, lease))
        heartbeat = asyncio.create_task(self._heartbeat(lease, execution))
        cancellation = asyncio.create_task(self._watch_cancel(lease, execution))
        try:
            run = await execution
            await asyncio.to_thread(
                self.store.finish_job,
                lease,
                "waiting" if run.status == RunStatus.WAITING_APPROVAL else "completed",
            )
        except asyncio.CancelledError:
            if self.closed:
                raise
        except LeaseLost:
            logger.info("Run lease reassigned: %s", lease.run_id)
        except ConflictError:
            # Cancellation/approval may finish the run while a worker is active.
            pass
        except Exception as exc:
            logger.exception("Run execution failed: %s", lease.run_id)
            try:
                run = await asyncio.to_thread(self.store.get, lease.run_id)
                if run.status not in TERMINAL:
                    await asyncio.to_thread(
                        self.store.append,
                        run.id,
                        "run.failed",
                        {"error": str(exc)[:800]},
                        None,
                        lease,
                    )
                await asyncio.to_thread(
                    self.store.finish_job, lease, "completed", type(exc).__name__
                )
            except ConflictError:
                pass
        finally:
            heartbeat.cancel()
            cancellation.cancel()
            if not execution.done():
                execution.cancel()
            await asyncio.gather(heartbeat, cancellation, execution, return_exceptions=True)

    async def close(self):
        self.closed = True
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
