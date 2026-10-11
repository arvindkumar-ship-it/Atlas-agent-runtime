import io
import time
import zipfile
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from atlas.demo import DEMOS


def test_auth_health_config_and_metrics(settings):
    settings = settings.model_copy(
        update={"api_key": "private-operator-key", "embedded_worker": False}
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/readyz").status_code == 200
        assert client.get("/api/config").status_code == 401
        assert client.get("/metrics").status_code == 401
        response = client.get(
            "/api/config", headers={"Authorization": "Bearer private-operator-key"}
        )
        assert response.status_code == 200
        assert "private-operator-key" not in response.text
        assert response.json()["authentication"]
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_api_validation_and_unknown_run(settings):
    settings = settings.model_copy(update={"embedded_worker": False})
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/runs/bad-id").status_code == 422
        assert client.get(f"/api/runs/{uuid4()}").status_code == 404
        assert client.get("/api/runs?limit=101").status_code == 422
        assert client.post("/api/demos/does-not-exist/run").status_code == 404
        assert (
            client.post(
                "/api/runs", json={"repository_path": ".", "instruction": "Repair code"}
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/api/repositories", content=b"zip", headers={"Content-Type": "text/plain"}
            ).status_code
            == 415
        )
        assert client.post("/api/runs", content=b"x" * 160001).status_code == 413


def test_zip_upload_and_repository_boundary(settings):
    settings = settings.model_copy(update={"backend": "compatible", "embedded_worker": False})
    with TestClient(create_app(settings)) as client:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("repo-main/logic.py", "answer = 42\n")
        response = client.post(
            "/api/repositories",
            content=buffer.getvalue(),
            headers={"Content-Type": "application/zip"},
        )
        assert response.status_code == 201, response.text
        name = response.json()["repository_path"]
        body = {
            "repository_path": name,
            "instruction": "Explain this code",
            "approval_policy": "manual",
        }
        response = client.post("/api/runs", json=body, headers={"Idempotency-Key": "once"})
        assert response.status_code == 202
        assert (
            client.post("/api/runs", json=body, headers={"Idempotency-Key": "once"}).json()["id"]
            == response.json()["id"]
        )
        body["instruction"] = "A different task"
        assert (
            client.post("/api/runs", json=body, headers={"Idempotency-Key": "once"}).status_code
            == 409
        )
        assert (
            client.post(
                "/api/runs", json={"repository_path": "/tmp", "instruction": "Repair code"}
            ).status_code
            == 403
        )


def test_live_worker_demo_sse_replay_and_patch(settings):
    with TestClient(create_app(settings)) as client:
        demo_id = next(iter(DEMOS))
        response = client.post(f"/api/demos/{demo_id}/run")
        assert response.status_code == 202, response.text
        run_id = response.json()["id"]
        for _ in range(200):
            state = client.get(f"/api/runs/{run_id}").json()
            if state["status"] in {"completed", "failed"}:
                break
            time.sleep(0.03)
        assert state["status"] == "completed", state
        assert client.get(f"/api/runs/{run_id}/replay").json() == state
        patch = client.get(f"/api/runs/{run_id}/patch")
        assert patch.status_code == 200 and "test_regression.py" in patch.text
        response = client.get(f"/api/runs/{run_id}/stream")
        assert response.status_code == 200
        assert '"type": "run.completed"' in response.text
        events = client.get(f"/api/runs/{run_id}/events?after=1").json()
        assert events[0]["sequence"] == 2
        dashboard = client.get("/api/dashboard").json()
        assert dashboard["completed"] == 1 and dashboard["model_calls"] == 6
        assert dashboard["model_input_tokens"] is None


def test_api_approval_binding_and_cancel(settings):
    settings = settings.model_copy(update={"embedded_worker": False})
    from atlas.domain.models import Action, Task

    with TestClient(create_app(settings)) as client:
        store = client.app.state.store
        run = store.create(Task(repository_path="fixture", instruction="Repair this fixture"))
        approval = store.request_approval(
            run.id, Action(tool="apply_patch", arguments={"patch": "exact patch"})
        )
        assert (
            client.post(
                f"/api/runs/{uuid4()}/approvals/{approval}", json={"approved": True}
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"/api/runs/{run.id}/approvals/{approval}", json={"approved": True}
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/api/runs/{run.id}/approvals/{approval}", json={"approved": True}
            ).status_code
            == 409
        )
        assert client.post(f"/api/runs/{run.id}/cancel").status_code == 200
        assert client.get(f"/api/runs/{run.id}").json()["status"] == "cancelled"


def test_production_config_requires_key_and_local_opt_in():
    from atlas.config import Settings

    with pytest.raises(ValueError):
        Settings(environment="production")
    with pytest.raises(ValueError):
        Settings(sandbox="local")
