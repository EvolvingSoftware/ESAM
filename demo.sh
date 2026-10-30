#!/usr/bin/env bash
# One-command ESAM demo — boots the Intelligence Newsletter workflow end-to-end.
#
# Usage:  ./demo.sh
#   Optionally point at a real OpenAI-compatible model:
#     ESAM_LLM_ENDPOINT=http://localhost:8001/v1/chat/completions \
#     ESAM_LLM_MODEL=gemma-4-12B ./demo.sh
#
# With no LLM endpoint reachable it falls back to deterministic offline synthesis,
# so the run is green on any host from a clean clone.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 scripts/run_newsletter.py "$@"
