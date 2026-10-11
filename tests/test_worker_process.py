import os
import socket
import subprocess
import sys
import time

import httpx

from atlas.demo import DEMOS, prepare_demo
from atlas.domain.models import Task


def test_standalone_worker_runs_job_and_exports_metrics(settings, store):
    demo_id = next(iter(DEMOS))
    repository = prepare_demo(settings.repo_root / "fixture", demo_id)
    run = store.create(
        Task(
            repository_path=str(repository),
            instruction=DEMOS[demo_id].instruction,
            demo_id=demo_id,
            approval_policy="workspace",
        )
    )
    with socket.socket() as temporary:
        temporary.bind(("127.0.0.1", 0))
        port = temporary.getsockname()[1]
    environment = {
        **os.environ,
        "ATLAS_DATABASE_URL": settings.database_url,
        "ATLAS_DATA_DIR": str(settings.data_dir),
        "ATLAS_REPO_ROOT": str(settings.repo_root),
        "ATLAS_BACKEND": "demo",
        "ATLAS_SANDBOX": "local",
        "ATLAS_ALLOW_TRUSTED_LOCAL": "true",
        "ATLAS_WORKER_METRICS_PORT": str(port),
        "ATLAS_ENVIRONMENT": "development",
        "ATLAS_API_KEY": "",
        "ATLAS_OTLP_ENDPOINT": "",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "apps.worker.main"],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and store.get(run.id).status not in {
            "completed",
            "failed",
        }:
            assert process.poll() is None, process.stderr.read().decode()
            time.sleep(0.03)
        assert store.get(run.id).status == "completed"
        with httpx.Client(trust_env=False) as client:
            response = client.get(f"http://127.0.0.1:{port}/metrics")
        assert response.status_code == 200
        assert "atlas_runs_completed_total 1.0" in response.text
        assert "atlas_tool_calls_total" in response.text
        assert store.replay(run.id) == store.get(run.id)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
