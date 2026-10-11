#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.lock
.venv/bin/python -m pip install --no-deps -e .
if command -v npm >/dev/null; then
  (cd frontend && npm ci && npm run build)
fi
echo 'Setup complete. Run: .venv/bin/atlas demo auth --trusted-local'
