from __future__ import annotations

import math
import re
import threading
from contextlib import contextmanager
from time import perf_counter

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest


class MetricsRegistry:
    """Bounded label sets and standard cumulative Prometheus histogram buckets."""

    def __init__(self):
        self.registry = CollectorRegistry()
        self._lock = threading.RLock()
        self._instruments = {}

    def _instrument(self, kind: str, name: str, labels: dict | None):
        if not re.fullmatch(r"[a-zA-Z_:][a-zA-Z0-9_:]*", name):
            raise ValueError("Invalid metric name")
        labels = labels or {}
        if not set(labels) <= {"tool", "model", "mode", "status", "backend"}:
            raise ValueError("Unbounded metric label")
        names = tuple(sorted(labels))
        with self._lock:
            if name in self._instruments:
                existing_kind, existing_names, instrument = self._instruments[name]
                if existing_kind != kind or existing_names != names:
                    raise ValueError("Metric type or label schema changed")
            else:
                cls = {"counter": Counter, "gauge": Gauge, "histogram": Histogram}[kind]
                options = (
                    {"buckets": (1, 5, 10, 25, 50, 100, 250, 500, 1000, 5000, 30000)}
                    if kind == "histogram"
                    else {}
                )
                instrument = cls(
                    name, name.replace("_", " "), names, registry=self.registry, **options
                )
                self._instruments[name] = (kind, names, instrument)
            return instrument.labels(**labels) if labels else instrument

    def increment(self, name: str, value: int = 1, labels: dict | None = None):
        if value < 0:
            raise ValueError("Counter increment cannot be negative")
        self._instrument("counter", name, labels).inc(value)

    def set_gauge(self, name: str, value: float, labels: dict | None = None):
        if not math.isfinite(value):
            raise ValueError("Gauge must be finite")
        self._instrument("gauge", name, labels).set(value)

    def observe(self, name: str, value: float, labels: dict | None = None):
        if not math.isfinite(value) or value < 0:
            raise ValueError("Histogram observation must be finite and nonnegative")
        self._instrument("histogram", name, labels).observe(value)

    @contextmanager
    def time(self, name: str, labels: dict | None = None):
        started, result = perf_counter(), {}
        try:
            yield result
        finally:
            result["elapsed_ms"] = (perf_counter() - started) * 1000
            self.observe(name, result["elapsed_ms"], labels)

    def snapshot(self) -> dict:
        return {
            "samples": [
                {"name": sample.name, "labels": sample.labels, "value": sample.value}
                for metric in self.registry.collect()
                for sample in metric.samples
                if not sample.name.endswith("_created")
            ]
        }

    def prometheus(self) -> bytes:
        return generate_latest(self.registry)
