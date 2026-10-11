import asyncio
import signal

from prometheus_client import start_http_server

from atlas.config import Settings
from atlas.models.backends import make_backend
from atlas.runtime.engine import RuntimeEngine
from atlas.runtime.inference import InferenceScheduler
from atlas.runtime.worker import Worker
from atlas.storage.store import Store
from atlas.telemetry.metrics import MetricsRegistry
from atlas.telemetry.tracing import configure_tracing


async def main():
    settings = Settings.from_env()
    store = Store(settings.database_url)
    metrics = MetricsRegistry()
    provider = configure_tracing(settings.otlp_endpoint)
    metrics_server = None
    if settings.worker_metrics_port:
        metrics_server, _ = start_http_server(
            settings.worker_metrics_port, addr="127.0.0.1", registry=metrics.registry
        )
    scheduler = InferenceScheduler(make_backend(settings), settings, metrics)
    worker = Worker(
        store,
        RuntimeEngine(store, settings, scheduler, metrics),
        settings.worker_concurrency,
        settings.lease_seconds,
    )
    stop = asyncio.Event()
    for name in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(name, stop.set)
        except NotImplementedError:
            pass
    await worker.start()
    try:
        await stop.wait()
    finally:
        await worker.close()
        await scheduler.close()
        store.close()
        if metrics_server:
            await asyncio.to_thread(metrics_server.shutdown)
            metrics_server.server_close()
        if provider:
            await asyncio.to_thread(provider.shutdown)


if __name__ == "__main__":
    asyncio.run(main())
