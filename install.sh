#!/usr/bin/env bash
# ESAM one-line installer — pulls v0.1.0 from the public repo, builds a venv,
# boots the API on :8000 (override with ESAM_PORT), seeds the demo workflow.
#
#   curl -fsSL https://raw.githubusercontent.com/EvolvingSoftware/ESAM/v0.1.0/install.sh | sh
#
# POSIX-sh compatible: safe to pipe into `sh` (dash) or run directly under bash.
set -eu

TAG="v0.1.0"
REPO="EvolvingSoftware/ESAM"
PORT="${ESAM_PORT:-8000}"
BASE_DIR="$(pwd)/esam-${TAG#v}"   # ./esam-0.1.0  (no clone, no .env required)

say() { printf '\n\x1b[1;34m== %s ==\x1b[0m\n' "$*"; }
fatal() { printf '\n\x1b[1;31mERROR: %s\x1b[0m\n' "$*" >&2; exit 1; }

# ── 1. Python 3.11+ ───────────────────────────────────────────────────────────
say "Checking for Python 3.11+"
PY=""
for cand in python3.12 python3.11 python3; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then PY="$cand"; break; fi
done
[ -n "$PY" ] || fatal "Python 3.11+ not found on PATH (looked for python3.12/3.11/python3)."
say "using $("$PY" --version)"

# ── 2. Fetch the v0.1.0 source tarball from the public repo ───────────────────
# curl honours CURL_CA_BUNDLE / SSL_CERT_FILE automatically, so no extra flags needed.
say "Downloading ${REPO} @ ${TAG}"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
url="https://api.github.com/repos/${REPO}/tarball/${TAG}"
curl -fsSL -o "$tmp/esam.tgz" "$url" || fatal "download failed: $url"

# ── 3. Extract (GitHub tarballs nest under a single top-level dir) ────────────
say "Extracting to ${BASE_DIR}"
tar -xzf "$tmp/esam.tgz" -C "$tmp"
nested=""
for d in "$tmp"/*/; do [ -d "$d" ] && nested="${d%/}"; done
[ -n "$nested" ] || fatal "tarball did not contain a top-level directory."
mkdir -p "$BASE_DIR"
cp -a "$nested"/. "$BASE_DIR/"
say "Extracted to ${BASE_DIR}"
cd "$BASE_DIR"

# ── 4. venv + dependencies ────────────────────────────────────────────────────
say "Creating virtualenv (.venv) and installing dependencies"
"$PY" -m venv .venv
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt

# ── 5. Boot the API on :PORT (background; survives script exit in a shell) ───
say "Starting API on port ${PORT}"
if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE ":${PORT}\$"; then
    say "WARNING: something is already listening on :${PORT}; the API may fail to bind."
fi
PYTHONPATH=src nohup ./.venv/bin/python -m uvicorn api_server:app --host 0.0.0.0 --port "$PORT" >api.log 2>&1 < /dev/null &
API_PID=$!
echo "api pid ${API_PID} (log: $(pwd)/api.log)"

# Health check — strict: up only when the process is ALIVE and answers with a real
# FastAPI OpenAPI schema on :PORT. A foreign squatter or a dead uvicorn will not pass.
say "Waiting for the API to come up"
up=""; i=0
while [ "$i" -lt 90 ]; do
    if ! kill -0 "$API_PID" 2>/dev/null; then
        echo "API process exited before serving (could not bind :${PORT}?) — last log lines:" >&2
        tail -n 40 api.log >&2 || true
        exit 1
    fi
    body=$(curl -fsS --max-time 3 "http://localhost:${PORT}/openapi.json" 2>/dev/null) || body=""
    if printf '%s' "$body" | grep -q '"openapi"'; then up=1; break; fi
    i=$((i + 1)); sleep 1
done
[ -n "$up" ] || { echo "API did not come up on :${PORT} — last log lines:" >&2; tail -n 40 api.log >&2 || true; exit 1; }
say "API is serving (FastAPI OpenAPI schema confirmed on :${PORT})."

# ── 6. Seed the demo workflow against the running API ────────────────────────
say "Seeding demo workflow"
ESAM_API_BASE="http://localhost:${PORT}" PYTHONPATH=src ./.venv/bin/python src/seed_demo_agents.py \
    || echo "[warn] seed reported issues (API still up) — see output above."

# ── 7. Report the served dashboard ───────────────────────────────────────────
say "Done — ESAM is live."
cat <<EOF
Dashboard : http://localhost:${PORT}/
OpenAPI   : http://localhost:${PORT}/openapi.json
Docs      : http://localhost:${PORT}/docs
Home dir  : $(pwd)
API log   : $(pwd)/api.log (pid ${API_PID})
Stop it   : kill ${API_PID}
EOF
printf '\n--- served dashboard HTML (first 600 bytes of /) ---\n'
curl -fsS "http://localhost:${PORT}/" 2>/dev/null | head -c 600 || echo "(no HTML at / — check the docs/openapi routes above)"
printf '\n--- end ---\n'
