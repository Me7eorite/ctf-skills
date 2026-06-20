#!/usr/bin/env bash
# Start the dashboard server with a clean bytecode cache.
#
# Why: importing a freshly-edited module while a stale .pyc exists has been
# observed to race against background-thread startup, leaving the running
# process executing partially-old code. Wiping __pycache__ and disabling
# bytecode writes makes the load deterministic across redeploys.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

find src -type d -name __pycache__ -prune -exec rm -rf {} +
find src -name '*.pyc' -delete

export PYTHONDONTWRITEBYTECODE=1
exec .venv/bin/challenge-factory serve "$@"
