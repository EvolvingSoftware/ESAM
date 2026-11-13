#!/usr/bin/env bash
# ESAM first-agent demo: scaffold a local agent and ask it something.
# Run this AFTER the quickstart (so `esam` is on PATH).
set -euo pipefail
cd "$(dirname "$0")"
esam init --template local >/dev/null 2>&1 || esam init --template local
echo '=== esam ask "ping" ==='
esam ask "ping"
