#!/usr/bin/env sh
# ────────────────────────────────────────────────────────────────────────────
# ESAM one-line installer.
#
#   curl -fsSL https://raw.githubusercontent.com/EvolvingSoftware/ESAM/v0.1.0/install.sh | sh
#
# On a fresh box (no clone, no .env, Python 3.11+) this:
#   1. downloads the v0.1.0 source tarball and extracts it
#   2. verifies Python >= 3.11
#   3. creates a virtualenv and installs dependencies
#   4. boots the API on :8000 (no .env required)
#   5. seeds the demo workflow against the running API
#   6. prints the served dashboard URL + HTML so you can verify it live
#
# Env overrides (all optional):
#   ESAM_TAG        tag to install            [v0.1.0]
#   ESAM_PORT       port for the API          [8000]
#   ESAM_HOME       where to extract/launch   [$PWD/esam-<tag>]
#   ESAM_TARBALL_URL full tarball URL (test)  [github archive of tag]
# ────────────────────────────────────────────────────────────────────────────
set -eu

TAG="${ESAM_TAG:-v0.1.0}"
REPO="EvolvingSoftware/ESAM"
PORT="${ESAM_PORT:-8000}"
TARBALL_URL="${ESAM_TARBALL_URL:-https://github.com/${REPO}/archive/refs/tags/${TAG}.tar.gz}"
BASE_DIR="${ESAM_HOME:-$PWD/esam-${TAG}}"

say() { printf '\n\x1b[1;34m== %s ==\x1b[0m\n' "$*" >&2; }
fail() { printf '\n\x1b[1;31mERROR: %s\x1b[0m\n' "$*" >&2; exit 1; }

# ── 1. Python >= 3.11 ───────────────────────────────────────────────────────
say "Checking for Python 3.11+"
PYBIN="$(command -v python3 || command -v python || true)"
[ -n "$PYBIN" ] || fail "no python3/python found on PATH — install Python 3.11+ first."
"$PYBIN" - <<'PYEOF'
import sys
if sys.version_info < (3, 11):
    print(f"Python {sys.version} is too old; ESAM requires 3.11+.", file=sys.stderr)
    raise SystemExit(2)
print(f"using Python {sys.version.split()[0]}")
PYEOF

# ── 2. Fetch + extract source ───────────────────────────────────────────────
say "Downloading ${REPO} @ ${TAG}"
TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT
command -v curl >/dev/null 2>&1 || fail "curl is required."
curl -fsSL "$TARBALL_URL" -o "$TMP/esam.tgz" || fail "could not download tarball from $TARBALL_URL"
mkdir -p "$BASE_DIR"
tar -xzf "$TMP/esam.tgz" -C "$TMP"

# GitHub archives wrap files in a top-level dir (ESAM-<tag>/); git archive may not.
ENTRIES="$(find "$TMP" -mindepth 1 -maxdepth 1 | wc -l)"
TOPDIR="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -n1 || true)"
if [ "$ENTRIES" = "1" ] && [ -n "$TOPDIR" ]; then
    SRC="$TOPDIR"
else
    SRC="$TMP"
fi
cp -a "$SRC"/. "$BASE_DIR"/
say "Extracted to ${BASE_DIR}"
cd "$BASE_DIR"
[ -f requirements.txt ] || fail "requirements.txt missing from tarball — bad archive."
[ -f src/api_server.py ] || fail "src/api_server.py missing from tarball — bad archive."

# ── 3. Virtualenv + dependencies ────────────────────────────────────────────
say "Creating virtualenv (.venv) and installing dependencies"
"$PYBIN" -m venv .venv || fail "failed to create venv (on Debian/Ubuntu try: apt-get install python3-venv)."
./.venv/bin/python -m pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt || fail "pip install failed — see output above."
say "Dependencies installed."

# ── 4. Boot the API on :PORT (no .env required) ─────────────────────────────
say "Booting ESAM API on http://localhost:${PORT}"
PYTHONPATH=src nohup ./.venv/bin/python -m uvicorn api_server:app \
    --host 0.0.0.0 --port "$PORT" >"$BASE_DIR/api.log" 2>&1 &
API_PID=$!
echo "api pid ${API_PID} (log: ${BASE_DIR}/api.log)"

# Health check: the API is up once it answers HTTP on any known route.
up=""
i=0
while [ "$i" -lt 90 ]; do
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:${PORT}/openapi.json" || echo 000)
    if [ "$code" = "000" ]; then
        code=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:${PORT}/docs" || echo 000)
    fi
    if [ "$code" != "000" ] && [ -n "$code" ]; then up=1; break; fi
    i=$((i + 1)); sleep 1
done
[ -n "$up" ] || { fail "API did not come up on :${PORT} — last log lines:"; tail -n 40 "$BASE_DIR/api.log" >&2 || true; }
say "API is serving (HTTP ${code})."

# ── 5. Seed the demo workflow against the running API ───────────────────────
say "Seeding demo workflow"
ESAM_API_BASE="http://localhost:${PORT}" PYTHONPATH=src ./.venv/bin/python src/seed_demo_agents.py \
    || echo "[warn] seed reported issues (API still up) — see output above."

# ── 6. Report the served dashboard ──────────────────────────────────────────
say "Done — ESAM is live."
cat <<EOF
Dashboard : http://localhost:${PORT}/
OpenAPI   : http://localhost:${PORT}/openapi.json
Docs      : http://localhost:${PORT}/docs
Home dir  : ${BASE_DIR}
API log   : ${BASE_DIR}/api.log (pid ${API_PID})
Stop it   : kill ${API_PID}
EOF
printf '\n--- served dashboard HTML (first 600 bytes of /) ---\n'
curl -fsS "http://localhost:${PORT}/" 2>/dev/null | head -c 600 || echo "(no HTML at / — check the docs/openapi routes above)"
printf '\n--- end ---\n'
