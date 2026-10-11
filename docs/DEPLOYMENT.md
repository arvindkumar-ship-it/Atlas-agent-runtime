# Manual deployment from GitHub

There are two supported setups: a hosted fixture demo and a dedicated Docker worker for your own repositories. Both use the same source and console. Keep model weights outside the Atlas image.

## Upload the repository

Unzip and push the inner `atlas-agent-runtime` folder as your repository root. The ZIP contains a built console for local use. Git ignores generated `frontend/dist`; Docker and CI rebuild it from the locked npm dependencies.

Run the delivery checks before deploying:

```bash
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
python -m pytest -q
cd frontend
npm ci
npm run build
```

The current Python lock was verified on Linux/Python 3.12. Windows scripts are supplied; live Windows deployment was not exercised in this environment.

## A. Docker fixture demo

On a Docker host, clone your repository, open its root and run:

```bash
python scripts/generate_secrets.py
docker compose config
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:8000/readyz
```

`generate_secrets.py` creates a private `.env` once. It refuses to overwrite an existing file. Read `ATLAS_API_KEY` locally and enter it in **API access**. Compose ignores the generic paths in `.env` and sets its database/data paths explicitly.

The stack has PostgreSQL, a persistent data volume and a persistent source volume. It uses the deterministic backend and explicitly enables local pytest inside the application container for the four known fixtures. It accepts no general repository runs in this mode. Keep this demo configuration when using the supplied Compose file; a live model needs setup B.

Port 8000 is bound to loopback. For a public domain, run Caddy on the same host, copy `deploy/Caddyfile`, set `ATLAS_DOMAIN` to your domain and point its DNS to the host. Caddy forwards to `127.0.0.1:8000`. Allow inbound HTTPS to the proxy, then visit `https://your-domain` and enter the operator key. Use the host's documented Caddy installation/service procedure.

Useful commands:

```bash
docker compose logs -f atlas
docker compose restart atlas
docker compose down
```

`down` preserves named volumes. Avoid `down -v` unless you intend to delete all stored runs/repositories. Restore the database and file volumes from the same backup checkpoint.

## GitHub-connected Render demo

`render.yaml` defines a Docker web service with a persistent disk, generated API key, SQLite and the four fixture demos. Create a Blueprint from your GitHub repository, review the proposed service/disk plan and deploy it. The blueprint uses a paid service with disk storage; check the provider's current cost before confirming deployment.

Render builds the Dockerfile, starts port 8000 and checks `/readyz`. The disk is mounted at `/app/data`; SQLite, workspaces, artifacts and repositories all live underneath it. After deployment, get `ATLAS_API_KEY` from the service's environment settings and enter it in the console. Preserve that key across redeployments.

Use one instance with the embedded worker. Attached disks are local to one service instance, so this blueprint does not split the API and worker. If the mount is not writable for UID 10001, adjust the disk ownership in the provider shell before starting the service; the application intentionally runs as a non-root user.

This deployment runs fixture demos. General uploaded-code execution needs a worker with a controlled Docker daemon; enabling host-local execution for arbitrary repositories is not the supported setup.

Provider references: [Docker services](https://render.com/docs/docker), [Blueprint YAML](https://render.com/docs/blueprint-spec), [persistent disks](https://render.com/docs/disks), [service ports](https://render.com/docs/web-services).

## B. Real repository worker on one Linux host

Use a dedicated host with Python 3.12, Node 24, Docker, an external model endpoint and PostgreSQL. A local SQLite deployment also works on one host at low concurrency. The two service processes must share absolute paths to the same repository/data files. Do not mount the Docker socket into the API.

1. Install the repository at `/opt/atlas`, create its `.venv` using the quick-start commands and build the frontend.
2. Create an `atlas` group, an `atlas` API account and an `atlas-worker` account in that group. Grant only `atlas-worker` Docker access. Keep Docker access on this dedicated worker host because it carries host-level authority.
3. Create `/opt/atlas/data` and `/opt/atlas/repositories` with group `atlas`, group write permission and setgid inheritance, for example mode `2770`. Files created inside run workspaces use group-sharing umask `0002`; their directories/files remain readable inside the read-only test-container bind mount.
4. Build `atlas-sandbox:1.0` using `deploy/sandbox.Dockerfile`. For additional pytest dependencies, build a custom image with pinned packages and configure its name.
5. Create `/etc/atlas/atlas.env`, accessible to the two service accounts but not public, with the settings below.

```dotenv
ATLAS_ENVIRONMENT=production
ATLAS_API_KEY=YOUR_RANDOM_HEX_KEY_OF_AT_LEAST_32_CHARACTERS
ATLAS_DATABASE_URL=postgresql://atlas:YOUR_DB_PASSWORD@127.0.0.1:5432/atlas
ATLAS_DATA_DIR=/opt/atlas/data
ATLAS_REPO_ROOT=/opt/atlas/repositories
ATLAS_BACKEND=compatible
ATLAS_MODEL_URL=http://127.0.0.1:8080/v1
ATLAS_MODEL_NAME=local-model
ATLAS_MODEL_API_KEY=
ATLAS_SANDBOX=docker
ATLAS_SANDBOX_IMAGE=atlas-sandbox:1.0
ATLAS_ALLOW_TRUSTED_LOCAL=false
ATLAS_EMBEDDED_WORKER=false
ATLAS_WORKER_CONCURRENCY=2
ATLAS_INFERENCE_CONCURRENCY=2
ATLAS_KV_BUDGET_BYTES=2147483648
```

Generate keys with `python -c "import secrets; print(secrets.token_hex(32))"`. Replace the database credentials and model endpoint/name; the shown uppercase values are placeholders. This file is a systemd environment file, not a committed project file. Use a proper secret manager if your host supplies one.

Copy `deploy/atlas-api.service` and `deploy/atlas-worker.service` into `/etc/systemd/system/`, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now atlas-api atlas-worker
sudo systemctl status atlas-api atlas-worker
curl http://127.0.0.1:8000/readyz
```

The API account can accept ZIPs and create runs. The standalone worker claims jobs from the same database and launches restricted test containers. Both services restart on failure. With absolute paths, the Docker daemon sees the same workspace path that the worker passes to `--mount`.

The standalone worker exports its process metrics at `http://127.0.0.1:9091/metrics` and sends traces when `ATLAS_OTLP_ENDPOINT` is configured. Keep that endpoint on loopback; it has no public bearer middleware. The API's `/metrics` contains the API process's own metrics, while `/api/dashboard` aggregates durable events from all runs. Set a different `ATLAS_WORKER_METRICS_PORT` for each additional worker process, or `0` to disable its exporter.

Put HTTPS in front of the loopback API as in setup A. Test model connectivity before submitting a repository:

```bash
ATLAS_TEST_MODEL_URL=http://127.0.0.1:8080/v1 ATLAS_TEST_MODEL_NAME=local-model python -m pytest -m model -q
python -m pytest -m docker -q
```

These commands need the matching environment, installed development dependencies and Docker access. Run Docker checks as the worker account. CI uses a disposable database; do not point its test variable at production.

After connecting the console, upload a small Python repository ZIP or place a repository inside `/opt/atlas/repositories`. Start with manual approvals. Inspect both the patch and the final tests before applying the downloaded diff to your original checkout.

## Local llama.cpp server

Place a compatible instruction GGUF in `models/` and set its filename:

```bash
export ATLAS_GGUF_FILENAME=your-model.gguf
docker compose -f deploy/model.compose.yaml up -d
curl http://127.0.0.1:8080/v1/models
```

The optional compose file uses the official `ggml-org/llama.cpp` server image, a read-only model mount, the alias `local-model` and loopback port 8080. Set `ATLAS_BACKEND=llamacpp`, `ATLAS_MODEL_URL=http://127.0.0.1:8080/v1` and `ATLAS_MODEL_NAME=local-model` for the host worker. Start with a model and context size that fit your hardware; the sample server reserves a 24,576-token context for benchmark compatibility.

Pin the server image digest after validating your chosen model/backend combination. The generic adapter does not call llama.cpp slot-management endpoints. It recompiles durable context and sends chat requests.

Official reference: [llama.cpp Docker documentation](https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md).

## vLLM or cloud-compatible endpoint

Start your server with its supported model name and OpenAI chat endpoint. Set `ATLAS_BACKEND=vllm` or `compatible`; both use the same tested HTTP contract. The adapter requests streaming usage and JSON object output. If a provider does not support that response format, adapt its server configuration or use a compatible endpoint; unsupported responses fail explicitly.

Configure automatic prefix caching or speculative decoding in the inference server itself. Atlas's queue/identity telemetry does not enable those server features. Use the server's measured cached-token usage, GPU telemetry and an independently controlled benchmark to evaluate them.

Official reference: [vLLM compatible server](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/).

## Backups and updates

Stop new work and gracefully stop the API/worker before taking a combined database/files snapshot. For PostgreSQL, use `pg_dump` plus copies of `data/` and `repositories/`; for SQLite, copy the entire data directory after services stop, including any WAL files. Encrypt private repository backups.

Pull the new GitHub revision, install its locks, rebuild the console/sandbox if their inputs changed and restart the services. Keep environment files and persistent state outside Git. Schema version changes need a reviewed migration and backup. Roll back code only with a compatible schema or restore the matching snapshot.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Console says valid bearer key required | Enter the deployment's API key in API access |
| Dashboard missing after Git clone | Run `npm ci` and `npm run build` inside `frontend`, then restart the API |
| General tasks rejected in fixture mode | Configure a real compatible model backend |
| Tests say Docker is required | Start the daemon and build `atlas-sandbox:1.0` |
| Bind-mounted workspace is missing | Use shared absolute host paths; keep worker and daemon filesystem paths consistent |
| Sandbox cannot import dependencies | Build a pinned custom sandbox image before the run |
| Model invalid JSON / 400 response | Verify chat-template and JSON response-format support, model name and endpoint |
| Context/KV admission failure | Set model geometry/context budgets that fit; estimates are not physical GPU capacity |
| Run recovered after restart | Expected after a lost lease; inspect recovery event and replay |
| State disappears after deployment | Persist database, workspaces, artifacts and repositories, not just the image |

Live hosted deployment, real GPU inference and Docker/PostgreSQL services require your environment. The delivery report separates prepared configuration from checks actually executed during packaging.
