# Atlas Agent Runtime

A durable coding-agent runtime with a React console. A run inspects an isolated repository copy, proposes a patch, executes tests and returns a diff with its verification evidence. Events, queue leases, approvals and tool results survive process restarts.

The default backend runs four deterministic bug fixtures. Their patches and tests are real. For your own repository, connect an OpenAI-compatible model server and use the Docker test sandbox.

Start with [START_HERE.md](START_HERE.md) for Windows/Linux setup and GitHub upload. See [deployment](docs/DEPLOYMENT.md) for a hosted demo or a real repository worker.

## Quick start

Use Python 3.12. The ZIP includes a built console, so Node is optional until you change the frontend.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
atlas demo auth --trusted-local
```

Start the console for the bundled fixtures:

```bash
export ATLAS_BACKEND=demo
export ATLAS_SANDBOX=local
export ATLAS_ALLOW_TRUSTED_LOCAL=true
atlas serve
```

Open http://127.0.0.1:8000. Launch a demo, inspect its timeline, open Verification and download the patch. Local execution is explicitly enabled here because the fixtures are part of this package. Uploaded or model-authored repositories should use Docker.

Windows PowerShell:

```powershell
./scripts/setup.ps1
./scripts/start-demo.ps1
```

## What works

- SQLite WAL for local use; PostgreSQL event/queue transactions for a server.
- Hash-linked events, deterministic replay and materialized run state.
- Durable pending actions, fenced leases, heartbeats and bounded crash recovery.
- Typed plans with dependency validation and parallel read-only inspection.
- Strict tool schemas, path confinement, bounded ZIP import and immutable original tests.
- Exact unified patches with a crash recovery journal and per-workspace process locks.
- Manual approval bound to the exact patch and run; optional prior workspace-edit authorization.
- Pytest execution in a restricted Docker container, independent final checks and downloadable diffs.
- Structured observations, contradiction tracking and context-budget compilation.
- Streaming model adapter with server-reported usage, bounded inference concurrency and estimated KV admission.
- Prometheus metrics, optional OTLP traces, a responsive console and measured benchmark output.
- NumPy and optional PyTorch transformer references, byte BPE, int8 quantization and a CPU paged KV experiment.

The runtime is for Python repositories with pytest. It does not provide arbitrary shell execution, package installation inside a run, multi-user tenancy or GPU allocator control. [Scope](docs/SPECIFICATION.md) maps the PDF's final project to the implementation and explains the research boundaries.

Test runner/collection settings and conftest hooks are fixed during a run. Tasks that require changes to those files need an operator edit outside the agent workspace.

## Real repository tasks

Start a local model server separately. Model weights are not bundled. The server must support streaming chat completions and JSON object responses.

```bash
docker build -f deploy/sandbox.Dockerfile -t atlas-sandbox:1.0 .
export ATLAS_BACKEND=compatible
export ATLAS_MODEL_URL=http://127.0.0.1:8080/v1
export ATLAS_MODEL_NAME=your-served-model-name
export ATLAS_SANDBOX=docker
atlas serve
```

Upload a small repository ZIP in **New agent run**, enter a task, then review the exact patch when it pauses for approval. The source remains unchanged. Repositories already placed under `repositories/` can be addressed by relative path.

CLI, with explicit permission to edit the isolated copy:

```bash
atlas run --repo ./repositories/example --task "Repair the authentication bug and add regression tests" --allow-workspace-edits
```

Without that flag, use the console to approve the patch. CLI and API must use the same database and data directory. The backend's output still passes runtime validation and independent verification; a model's final message cannot mark a failing run successful.

Repositories requiring additional dependencies need an operator-built sandbox image. Extend `deploy/sandbox.Dockerfile`, install pinned dependencies while building the image, and set `ATLAS_SANDBOX_IMAGE`. Runtime containers have no network or host secrets.

## Development and checks

```bash
python -m pytest -q
python -m ruff check atlas apps tests benchmarks scripts
python -m ruff format --check atlas apps tests benchmarks scripts
```

```bash
cd frontend
npm ci
npm run format:check
npm run build
```

`npm run dev` serves the console at http://127.0.0.1:5173 and proxies API requests to port 8000. A normal Git clone rebuilds `frontend/dist`; the delivery ZIP already contains it.

Browser checks (activate the Python environment first and leave port 8000 free):

```bash
cd frontend
npx playwright install chromium
npm run test:e2e
```

Playwright starts an isolated fixture API and checks the verification/patch/download flow on desktop and mobile. Set `ATLAS_E2E_URL` to check an already running fixture server instead; `ATLAS_E2E_API_KEY` supplies its operator key when needed.

Optional numerical parity checks and a toy training experiment:

```bash
python -m pip install ".[reference]"
python -m pytest tests/test_reference.py -q
python -m scripts.train_reference --steps 100
```

The reference decoder starts with random weights. Toy training checks autograd and optimization, not agent capability. It never replaces the external planner.

## Benchmarks

```bash
python -m benchmarks.run_all --trusted-local --repetitions 3
```

Seven context/runtime variants run against the same four fixtures. JSONL contains individual outcomes; CSV contains the aggregate. Included results use the deterministic fixture driver. [Benchmark notes](docs/BENCHMARKS.md) explain the measurements and the limits of comparing model quality with this driver.

For model-backed evaluation, set the real backend variables and omit `--trusted-local`; keep Docker enabled. Write results to another path to preserve the supplied fixture measurements:

```bash
python -m benchmarks.run_all --repetitions 3 --output benchmarks/results/live-model.jsonl
```

Integration checks opt in via environment:

```bash
ATLAS_TEST_DATABASE_URL=postgresql://atlas:password@localhost:5432/atlas_test pytest -m postgres -q
pytest -m docker -q
ATLAS_TEST_MODEL_URL=http://127.0.0.1:8080/v1 ATLAS_TEST_MODEL_NAME=your-model pytest -m model -q
```

Use a disposable PostgreSQL test database. CI provisions one and builds the sandbox image. Optional integrations skip with a reason when their services are unavailable. See [TEST_REPORT.md](TEST_REPORT.md) for the actual delivery checks.

## Repository map

| Path | Purpose |
| --- | --- |
| `atlas/domain` | Validated task, plan, action, memory and run models |
| `atlas/storage` | Transactional event store, queue leases and process locks |
| `atlas/runtime` | Reducer, engine, worker, scheduling and KV estimates |
| `atlas/tools`, `atlas/security` | Confined repository operations and execution controls |
| `atlas/context`, `atlas/memory` | Budgeted context and source-linked observations |
| `atlas/models` | Deterministic fixtures and streaming compatible-server adapter |
| `atlas/reference` | Numerical/educational model and cache implementations |
| `atlas/telemetry` | Prometheus and OpenTelemetry |
| `apps/api`, `apps/worker` | HTTP service and standalone durable worker |
| `frontend` | React/TypeScript console and production build |
| `tests` | Unit, integration, recovery and numerical checks |
| `benchmarks` | Reproducible fixture runner and measured results |
| `deploy`, `scripts` | Sandbox, services, setup and deployment helpers |

## Configuration

Settings come from `ATLAS_*` environment variables. Compose reads `.env`; local Python commands read their shell environment. `.env.example` lists common values. Production requires an API key of at least 32 characters.

| Variable | Default / meaning |
| --- | --- |
| `ATLAS_BACKEND` | `demo`, `compatible`, `vllm` or `llamacpp` |
| `ATLAS_MODEL_URL` | `http://127.0.0.1:8080/v1` |
| `ATLAS_MODEL_NAME` | `local-model`; must match the server |
| `ATLAS_MODEL_API_KEY` | Optional bearer key for the model server |
| `ATLAS_SMALL_MODEL_NAME` | Optional served model for low-uncertainty short action turns |
| `ATLAS_API_KEY` | Operator API bearer key |
| `ATLAS_ENVIRONMENT` | `development` or `production` |
| `ATLAS_DATABASE_URL` | `sqlite:///./data/atlas.db`; PostgreSQL URLs also supported |
| `ATLAS_DATA_DIR`, `ATLAS_REPO_ROOT` | `data`, `repositories` |
| `ATLAS_SANDBOX`, `ATLAS_SANDBOX_IMAGE` | `docker`, `atlas-sandbox:1.0` |
| `ATLAS_ALLOW_TRUSTED_LOCAL` | `false`; required for host pytest |
| `ATLAS_EMBEDDED_WORKER` | `true`; set false for a separate worker |
| `ATLAS_WORKER_CONCURRENCY` | `2`; workflows per worker process |
| `ATLAS_WORKER_METRICS_PORT` | `9091`; standalone worker's loopback Prometheus endpoint; `0` disables it |
| `ATLAS_INFERENCE_CONCURRENCY` | `2`; simultaneous calls per process |
| `ATLAS_QUEUE_SIZE` | `128`; bounded inference queue |
| `ATLAS_LEASE_SECONDS` | `45`; heartbeat every one-third of this period |
| `ATLAS_MODEL_TIMEOUT`, `ATLAS_TEST_TIMEOUT` | `120`, `45` seconds |
| `ATLAS_KV_BUDGET_BYTES` | `2147483648`; admission estimate, not measured GPU capacity |
| `ATLAS_KV_LAYERS`, `ATLAS_KV_HEADS`, `ATLAS_KV_HEAD_DIM` | `32`, `8`, `128`; set to your model's KV configuration |
| `ATLAS_CORS_ORIGINS` | Comma-separated allowed browser origins; empty for same-origin deployment |
| `ATLAS_OTLP_ENDPOINT` | Optional OTLP HTTP collector trace endpoint |

## Operational notes

Protect the console with HTTPS and the operator key before exposing it publicly. The browser stores that key in tab-scoped session storage. Authentication is shared-operator access; there are no per-user repository permissions.

Keep the database, workspaces, artifacts and repository directory together in backups. Event replay reconstructs run state; it does not re-execute external model calls or recover a lost workspace disk. Schema version 1 is recorded on startup; unknown versions fail rather than silently replacing data. Runtime changes need an explicit migration before changing that version.

Model servers and Docker run independently of Atlas. GPU measurements, slot save/restore and draft decoding require backend-specific support. The runtime records unavailable fields as `null`; its prefix identity and KV estimates never imply actual server cache ownership.
