# Pehle yeh padho

Yeh ZIP project ki complete source delivery hai: Python runtime, API, React dashboard, tests, benchmark results aur deployment files. PDF mein project ka final naam **Atlas Agent Runtime** tha; implementation us final scope par based hai.

## 1. Unzip aur demo run

Python **3.12** install hona chahiye. Frontend ka built version ZIP mein hai. Node **24** tab chahiye jab dashboard edit/rebuild karna ho.

Windows PowerShell mein project folder open karo:

```powershell
./scripts/setup.ps1
./scripts/start-demo.ps1
```

Agar PowerShell scripts block kare, direct commands:

```powershell
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements-dev.lock
./.venv/Scripts/python.exe -m pip install --no-deps -e .
$env:ATLAS_BACKEND = 'demo'
$env:ATLAS_SANDBOX = 'local'
$env:ATLAS_ALLOW_TRUSTED_LOCAL = 'true'
./.venv/Scripts/atlas.exe serve
```

Linux/macOS:

```bash
bash scripts/setup.sh
bash scripts/start-demo.sh
```

Browser mein **http://127.0.0.1:8000** open karo. Authentication demo launch karo. Run ke andar **Verification** aur **Patch** tabs mein actual test output aur downloadable diff milega. Dusre fixtures retry, SQL batching aur path traversal cover karte hain.

## 2. GitHub upload

Project folder ko repository root rakho: `pyproject.toml`, `Dockerfile`, `README.md`, `atlas/`, `apps/`, `frontend/` root par hone chahiye. GitHub par repository create karke upload/push karo. `.env`, local data, model weights aur `node_modules` upload mat karo; `.gitignore` already included hai.

GitHub CI Python tests, PostgreSQL/Docker integrations aur dashboard build ke jobs run karega. GitHub repository code host karti hai; live FastAPI server ke liye deployment host chahiye. GitHub Pages par full Python runtime nahi chalega.

## 3. Hosted demo

Docker host par:

```bash
python scripts/generate_secrets.py
docker compose config
docker compose up -d --build
```

API key generated `.env` mein milegi. Console ke **API access** dialog mein enter karo. Port localhost par bind hota hai; public domain ke liye [deployment guide](docs/DEPLOYMENT.md) ka HTTPS setup use karo. `render.yaml` bhi included hai for a GitHub-connected demo deployment with persistent storage.

## 4. Apne repository par agent

Default demos ko GPU ya model API ki zarurat nahi. General tasks ke liye apna model endpoint/name/API key configure karo aur Docker sandbox build karo. Model weights ya paid API credentials ZIP mein included nahi hain. Exact commands [README](README.md#real-repository-tasks) aur [deployment guide](docs/DEPLOYMENT.md) mein hain.

Source repository untouched rahegi. Agent ki edits isolated copy mein hoti hain. Manual mode mein patch approval tum karoge; final tests runtime dobara verify karega.

## 5. Check results

[TEST_REPORT.md](TEST_REPORT.md) actual tests aur unavailable live integrations batata hai. [BENCHMARKS.md](docs/BENCHMARKS.md) aur `benchmarks/results/` measured fixture results rakhte hain. Demo success ko real LLM accuracy ya GPU speed ka claim nahi banaya gaya hai.
