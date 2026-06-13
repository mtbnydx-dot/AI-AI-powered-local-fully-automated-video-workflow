#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$(dirname "$WORKSPACE")"
PYTHON="$BASE_DIR/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  PYTHON="python3"
fi

cd "$WORKSPACE"
exec "$PYTHON" "$WORKSPACE/START_WORKFLOW.py"
