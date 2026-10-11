from __future__ import annotations

import asyncio
import itertools
from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic
from uuid import uuid4

from atlas.config import Settings
from atlas.models.backends import ModelBackend
from atlas.models.types import GenerateRequest, GenerateResult
from atlas.telemetry.metrics import MetricsRegistry


@dataclass(frozen=True)
class ModelKVConfig:
    layers: int = 32
    kv_heads: int = 8
    head_dim: int = 128
    bytes_per_element: int = 2


def estimate_kv_bytes(config: ModelKVConfig, tokens: int) -> int:
    if (
        tokens < 0
        or min(config.layers, config.kv_heads, config.head_dim, config.bytes_per_element) <= 0
    ):
        raise ValueError("Invalid KV configuration")
    return 2 * config.layers * config.kv_heads * config.head_dim * config.bytes_per_element * tokens


class KVBudget:
    def __init__(self, config: ModelKVConfig, max_bytes: int):
        self.config, self.max_bytes = config, max_bytes
        self.allocations = {}
        self.peak_bytes = 0

    @property
    def used_bytes(self):
        return sum(self.allocations.values())

    def allocate(self, key: str, tokens: int):
        amount = estimate_kv_bytes(self.config, tokens)
        if self.used_bytes - self.allocations.get(key, 0) + amount > self.max_bytes:
            raise MemoryError("KV estimate exceeds admission budget")
        self.allocations[key] = amount
        self.peak_bytes = max(self.peak_bytes, self.used_bytes)

    def release(self, key: str):
        self.allocations.pop(key, None)


class ModelRouter:
    def __init__(self, primary: str, small: str = ""):
        self.primary, self.small = primary, small

    def select(self, phase: str, uncertainty: float, tokens: int) -> str:
        if self.small and phase == "action" and uncertainty < 0.4 and tokens < 6000:
            return self.small
        return self.primary


class SpeculationPolicy:
    """A policy hook; server-side draft decoding must be configured separately."""

    def should_enable(
        self, accepted: int, proposed: int, free_bytes: int, strict_json: bool
    ) -> bool:
        if strict_json or free_bytes < 128 * 1024**2:
            return False
        return proposed == 0 or accepted / proposed >= 0.55


class InferenceScheduler:
    def __init__(self, backend: ModelBackend, settings: Settings, metrics: MetricsRegistry):
        self.backend, self.settings, self.metrics = backend, settings, metrics
        self.queue = asyncio.PriorityQueue(settings.queue_size)
        self.workers = []
        self.counter = itertools.count()
        self.futures = {}
        self.active = {}
        self.prefixes = OrderedDict()
        self.condition = asyncio.Condition()
        self.kv = KVBudget(
            ModelKVConfig(settings.kv_layers, settings.kv_heads, settings.kv_head_dim),
            settings.kv_budget_bytes,
        )
        self.closed = False

    async def start(self):
        if not self.workers:
            self.workers = [
                asyncio.create_task(self._worker())
                for _ in range(self.settings.inference_concurrency)
            ]

    async def submit(
        self, request: GenerateRequest, tokens: int, priority: int = 100
    ) -> GenerateResult:
        if self.closed:
            raise RuntimeError("Scheduler is closed")
        if estimate_kv_bytes(self.kv.config, tokens + request.max_tokens) > self.kv.max_bytes:
            raise MemoryError("Single request exceeds configured KV estimate budget")
        await self.start()
        key = uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.futures[key] = future
        try:
            self.queue.put_nowait((priority, next(self.counter), key, monotonic(), request, tokens))
            return await future
        except asyncio.QueueFull:
            raise RuntimeError("Inference queue is full") from None
        finally:
            self.futures.pop(key, None)
            if key in self.active:
                self.active[key].cancel()

    async def _worker(self):
        while True:
            _, _, key, submitted, request, tokens = await self.queue.get()
            future = self.futures.get(key)
            try:
                if future is None or future.cancelled():
                    continue
                async with self.condition:
                    requested_bytes = estimate_kv_bytes(self.kv.config, tokens + request.max_tokens)
                    await self.condition.wait_for(
                        lambda amount=requested_bytes, pending=future: (
                            self.kv.used_bytes + amount <= self.kv.max_bytes or pending.cancelled()
                        )
                    )
                    if future.cancelled():
                        continue
                    self.kv.allocate(key, tokens + request.max_tokens)
                self.metrics.observe(
                    "atlas_inference_queue_wait_ms", (monotonic() - submitted) * 1000
                )
                self.metrics.set_gauge("atlas_kv_estimated_used_bytes", self.kv.used_bytes)
                self.metrics.set_gauge("atlas_kv_estimated_peak_bytes", self.kv.peak_bytes)
                prefix = request.metadata.get("prefix_hash")
                if prefix:
                    hit = prefix in self.prefixes
                    self.metrics.increment(
                        "atlas_prefix_identity_hits_total"
                        if hit
                        else "atlas_prefix_identity_misses_total"
                    )
                    self.prefixes[prefix] = True
                    self.prefixes.move_to_end(prefix)
                    if len(self.prefixes) > 128:
                        self.prefixes.popitem(last=False)
                call = asyncio.create_task(self.backend.generate(request))
                self.active[key] = call
                try:
                    result = await call
                finally:
                    self.active.pop(key, None)
                if not future.done():
                    future.set_result(result)
            except asyncio.CancelledError:
                if self.closed:
                    if future and not future.done():
                        future.cancel()
                    raise
                if future and not future.done():
                    future.cancel()
            except Exception as exc:
                if future and not future.done():
                    future.set_exception(exc)
            finally:
                async with self.condition:
                    self.kv.release(key)
                    self.condition.notify_all()
                self.metrics.set_gauge("atlas_kv_estimated_used_bytes", self.kv.used_bytes)
                self.queue.task_done()

    async def close(self):
        self.closed = True
        for future in self.futures.values():
            if not future.done():
                future.cancel()
        for call in self.active.values():
            call.cancel()
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers.clear()
        await self.backend.close()
