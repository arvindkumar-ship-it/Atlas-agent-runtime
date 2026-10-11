#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export ATLAS_BACKEND=demo
export ATLAS_SANDBOX=local
export ATLAS_ALLOW_TRUSTED_LOCAL=true
exec .venv/bin/atlas serve
