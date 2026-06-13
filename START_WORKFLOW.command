#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$(dirname "$WORKSPACE")"
PYTHON="$WORKSPACE/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  PYTHON="$BASE_DIR/.venv/bin/python"
fi

if [ ! -x "$PYTHON" ]; then
  PYTHON="$(command -v python3 || true)"
fi

if [ -z "${PYTHON:-}" ]; then
  echo "Python 3.10-3.12 is required."
  echo "Run: python3.12 -m venv .venv && ./.venv/bin/python -m pip install -r requirements.txt"
  exit 1
fi

cd "$WORKSPACE"
exec "$PYTHON" "$WORKSPACE/START_WORKFLOW.py"
