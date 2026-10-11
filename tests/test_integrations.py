import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from atlas.config import Settings
from atlas.domain.models import Task
from atlas.models.backends import CompatibleBackend, parse_decision
from atlas.models.types import GenerateRequest
from atlas.storage.store import Store
from atlas.tools.registry import RepositoryTools


@pytest.mark.postgres
def test_postgres_events_claim_and_replay():
    url = os.environ.get("ATLAS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("ATLAS_TEST_DATABASE_URL is not configured")
    store = Store(url)
    run = store.create(
        Task(repository_path="fixture", instruction="Postgres integration " + uuid4().hex)
    )
    lease = store.claim("postgres-test")
    try:
        assert lease.run_id == run.id
        store.append(run.id, "run.started", {}, lease=lease)
        assert store.replay(run.id) == store.get(run.id)
        store.cancel(run.id)
    finally:
        store.close()


@pytest.mark.docker
@pytest.mark.asyncio
async def test_docker_sandbox_has_no_network_or_secret_env(tmp_path: Path):
    if (
        not shutil.which("docker")
        or subprocess.run(["docker", "info"], capture_output=True).returncode
    ):
        pytest.skip("Docker daemon is unavailable")
    if subprocess.run(
        ["docker", "image", "inspect", "atlas-sandbox:1.0"], capture_output=True
    ).returncode:
        pytest.skip("Build atlas-sandbox:1.0 before this integration test")
    source = """import os, socket
def test_isolation():
    assert 'ATLAS_MODEL_API_KEY' not in os.environ
    try:
        socket.create_connection(('1.1.1.1', 443), timeout=.2)
    except OSError:
        return
    raise AssertionError('Unexpected external network access')
"""
    (tmp_path / "test_isolation.py").write_text(source)
    result = await RepositoryTools(tmp_path, tmp_path / "artifacts", {}, Settings()).execute(
        "run_tests", {}
    )
    assert result.ok, result.output or result.error


@pytest.mark.model
@pytest.mark.asyncio
async def test_real_model_returns_valid_decision():
    if not os.environ.get("ATLAS_TEST_MODEL_URL"):
        pytest.skip("ATLAS_TEST_MODEL_URL is not configured")
    settings = Settings(
        model_url=os.environ["ATLAS_TEST_MODEL_URL"],
        model_name=os.environ.get("ATLAS_TEST_MODEL_NAME", "local-model"),
        model_api_key=os.environ.get("ATLAS_TEST_MODEL_API_KEY", ""),
        backend="compatible",
    )
    backend = CompatibleBackend(settings)
    try:
        result = await backend.generate(
            GenerateRequest(
                messages=[
                    {
                        "role": "user",
                        "content": 'Return exactly {"type":"tool","actions":[{"tool":"list_files","arguments":{}}]} as JSON.',
                    }
                ]
            )
        )
        assert parse_decision(result.text).actions[0].tool == "list_files"
    finally:
        await backend.close()
