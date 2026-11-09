#!/usr/bin/env bash
# ESAM one-line installer — works on a machine that has NEVER seen our fleet:
# fresh user account, no lab SSH, no pre-baked keys. Needs only python3 + pip
# (or an internet connection to fetch them) and public GitHub access.
#
#   curl -fsSL https://raw.githubusercontent.com/EvolvingSoftware/ESAM/v0.1.2/install.sh | sh
#
# It downloads the tagged source, builds a venv, boots the API on :8000,
# seeds a demo debtor + workflow, registers a fresh user account, and proves
# ONE working chat reply through the tether classify endpoint.
#
# Optional env overrides (all optional):
#   ESAM_TAG  — git ref to install (tag/branch/commit; default v0.1.2)
#   ESAM_PORT — API port (default 8000)
#   ESAM_HOME — directory to extract into and launch from (created if missing;
#               default $PWD/esam-<tag> with any leading v stripped).
set -eu

TAG="${ESAM_TAG:-v0.1.2}"
REPO="EvolvingSoftware/ESAM"
PORT="${ESAM_PORT:-8000}"
BASE_DIR="${ESAM_HOME:-$(pwd)/esam-${TAG#v}}"   # default: ./esam-<tag> (no clone, no .env required)

say() { printf '\n\x1b[1;34m== %s ==\x1b[0m\n' "$*"; }
fatal() { printf '\n\x1b[1;31mERROR: %s\x1b[0m\n' "$*" >&2; exit 1; }
# Extract the "token" field from a JSON blob on stdin (empty string if absent/malformed).
tok() { ./.venv/bin/python -c 'import sys,json
try:
    print(json.loads(sys.stdin.read()).get("token",""))
except Exception:
    print("")' 2>/dev/null; }

# ── 1. Python 3.11+ ───────────────────────────────────────────────────────────
say "Checking for Python 3.11+"
PY=""
for cand in python3.12 python3.11 python3; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then PY="$cand"; break; fi
done
[ -n "$PY" ] || fatal "Python 3.11+ not found on PATH (looked for python3.12/3.11/python3)."
say "using $("$PY" --version)"

# ── 2. Fetch the source tarball for $TAG from the public repo ────────────────
say "Downloading ${REPO} @ ${TAG}"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
url="https://api.github.com/repos/${REPO}/tarball/${TAG}"
curl -fsSL -o "$tmp/esam.tgz" "$url" || fatal "download failed: $url"

# ── 3. Extract (GitHub tarballs nest under a single top-level dir) ───────────
say "Extracting to ${BASE_DIR}"
tar -xzf "$tmp/esam.tgz" -C "$tmp"
nested=""
for d in "$tmp"/*/; do [ -d "$d" ] && nested="${d%/}"; done
[ -n "$nested" ] || fatal "tarball did not contain a top-level directory."
mkdir -p "$BASE_DIR"
cp -a "$nested"/. "$BASE_DIR/"
say "Extracted to ${BASE_DIR}"
cd "$BASE_DIR"

# ── 4. venv + dependencies ───────────────────────────────────────────────────
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
    case "$body" in *'"paths"'*|*'"openapi"'*) up=1; break;; esac
    i=$((i+1)); sleep 1
done
[ -n "$up" ] || { echo "API did not serve an OpenAPI schema on :${PORT} — last log lines:" >&2; tail -n 40 api.log >&2 || true; exit 1; }
say "API is up (pid ${API_PID})"

# ── 6b. Persist runtime env for the ./esam CLI ───────────────────────────────
cat > esam.env <<ENVEOF
ESAM_API_BASE="http://localhost:${PORT}"
ESAM_PORT="${PORT}"
ENVEOF

# ── 6. Seed the demo workflow against the running API ────────────────────────
say "Seeding demo workflow"
ESAM_API_BASE="http://localhost:${PORT}" PYTHONPATH=src ./.venv/bin/python src/seed_demo_agents.py \
    || echo "[warn] seed reported issues (API still up) — see output above."

# ── 7. Ensure a demo debtor exists so the chat-reply path has data to act on ─
say "Ensuring demo debtor d-001 exists"
PYTHONPATH=src ./.venv/bin/python - <<'PYSEED'
import os, sys
sys.path.insert(0, "src")
os.environ.setdefault("ESAM_DB", "esam.db")
from database import get_connection
conn = get_connection()
cols = [r[1] for r in conn.execute("PRAGMA table_info(debtors)").fetchall()]
def val(c):
    if c == "id": return "d-001"
    if c.lower().endswith(("email",)): return "demo-debtor@example.com"
    if c in ("name","first_name"): return "Demo Debtor"
    if "amount" in c.lower(): return 250000
    if "state" in c.lower() or "status" in c.lower(): return "active"
    if c == "created_at": return "now"
    return None
to_insert = {}
for c in cols:
    v = val(c)
    if v is not None: to_insert[c] = v
if to_insert:
    ph = ",".join("?"*len(to_insert))
    sql = f"INSERT OR IGNORE INTO debtors ({','.join(to_insert)}) VALUES ({ph})"
    try:
        conn.execute(sql, list(to_insert.values())); conn.commit()
        print("  debtor d-001 present")
    except Exception as e:
        print(f"  (seed note: {e})")
else:
    print("  (debtors table not found — skipping)")
PYSEED

# ── 8. Prove ONE working chat reply through the tether classify endpoint ─────
say "Proving a working chat reply"
EMAIL="demo@example.com"; PASS="demo-pass-123"
REG=$(curl -fsS --max-time 10 -X POST "http://localhost:${PORT}/api/auth/register" \
    -H 'Content-Type: application/json' \
    -d "{\"email\":\"${EMAIL}\",\"name\":\"Demo User\",\"password\":\"${PASS}\"}" 2>/dev/null) || REG=""
TOKEN=$(printf '%s' "$REG" | tok)
if [ -z "$TOKEN" ]; then
    LOG=$(curl -fsS --max-time 10 -X POST "http://localhost:${PORT}/api/auth/login" \
        -H 'Content-Type: application/json' \
        -d "{\"email\":\"${EMAIL}\",\"password\":\"${PASS}\"}" 2>/dev/null) || LOG=""
    TOKEN=$(printf '%s' "$LOG" | tok)
fi
[ -n "$TOKEN" ] || fatal "could not obtain an auth token (register/login failed) — see api.log"
echo "  fresh user account ready (token acquired)"
REPLY=$(curl -fsS --max-time 60 -X POST "http://localhost:${PORT}/api/tether/reply/classify" \
    -H "Authorization: Bearer ${TOKEN}" -H 'Content-Type: application/json' \
    -d '{"debtor_id":"d-001","subject":"Re: Payment reminder","body":"I can pay half now and the rest next month.","email_from":"demo-debtor@example.com"}') || REPLY=""
echo "==> Chat reply (POST /api/tether/reply/classify):"
printf '%s\n' "$REPLY"
case "$REPLY" in *category*|*action*) say "Chat reply verified — one working classification returned.";; *) echo "[warn] classify did not return a recognizable result — see api.log";; esac

# ── 9. Report the served dashboard + how to stop ─────────────────────────────
say "Done — ESAM is live."
cat <<EOF
Dashboard : http://localhost:${PORT}/
Health    : http://localhost:${PORT}/health
Ready     : http://localhost:${PORT}/ready
OpenAPI   : http://localhost:${PORT}/openapi.json
Docs      : http://localhost:${PORT}/docs
Home dir  : $(pwd)
Installed : ${REPO} @ ${TAG}
API log   : $(pwd)/api.log (pid ${API_PID})
Stop it   : kill ${API_PID}
EOF
printf '\n--- served dashboard HTML (first 600 bytes of /) ---\n'
curl -fsS "http://localhost:${PORT}/" 2>/dev/null | head -c 600 || echo "(no HTML at / — check the docs/openapi routes above)"
printf '\n--- end ---\n'
say "Quickstart ready — run the demo workflow with a single command:"
cat <<QEOF
  cd ${BASE_DIR}
  ./esam demo        # seeds (if needed), runs one real workflow, prints the dashboard result
QEOF
