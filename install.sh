#!/usr/bin/env bash
set -euo pipefail
OWNER="EvolvingSoftware"; REPO="ESAM"; TAG="v0.2.1"
PORT="${PORT:-8000}"
PYBIN="${PYTHON:-python3}"
BASE="${ESAM_HOME:-$HOME/esam-$TAG}"
mkdir -p "$BASE"
cd "$BASE"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
APPDIR=""
if [ -f "$SCRIPT_DIR/src/api_server.py" ] || [ -f "$SCRIPT_DIR/api_server.py" ]; then APPDIR="$SCRIPT_DIR"; fi
if [ -z "$APPDIR" ] && { [ -f "$BASE/src/api_server.py" ] || [ -f "$BASE/api_server.py" ]; }; then APPDIR="$BASE"; fi
if [ -z "$APPDIR" ]; then
  ASSET="https://github.com/$OWNER/$REPO/releases/download/$TAG/ESAM-$TAG.tar.gz"
  "$PYBIN" -c 'import sys,urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])' "$ASSET" "$BASE/esam-release.tgz"
  tar xzf "$BASE/esam-release.tgz" -C "$BASE"
  for d in "$BASE"/*/; do
    if [ -f "${d}src/api_server.py" ] || [ -f "${d}api_server.py" ]; then APPDIR="${d%/}"; break; fi
  done
fi
if [ -z "$APPDIR" ] || { [ ! -f "$APPDIR/src/api_server.py" ] && [ ! -f "$APPDIR/api_server.py" ]; }; then echo "ESAM source not found" >&2; exit 1; fi
"$PYBIN" -m venv "$BASE/.venv"
VPY="$BASE/.venv/bin/python"
"$VPY" -m pip install --upgrade pip >/dev/null
if [ -f "$APPDIR/requirements.txt" ]; then "$VPY" -m pip install -r "$APPDIR/requirements.txt"; fi
if [ -f "$APPDIR/pyproject.toml" ] || [ -f "$APPDIR/setup.py" ]; then "$VPY" -m pip install "$APPDIR" || "$VPY" -m pip install --no-build-isolation "$APPDIR"; fi
"$VPY" -m pip install uvicorn fastapi >/dev/null

# Make the `esam` CLI resolvable on PATH without hand-edits (stranger-friendly).
install_esam_shim() {
  local d="$1"; mkdir -p "$d" 2>/dev/null || return 0
  [ -w "$d" ] || return 0
  printf '#!/usr/bin/env bash
exec "%s" -m scripts.esam_cli "$@"
' "$VPY" > "$d/esam" 2>/dev/null && chmod +x "$d/esam" 2>/dev/null || true
}
install_esam_shim "$HOME/.local/bin"
OLDIFS=$IFS; IFS=':'
for d in $PATH; do [ -n "$d" ] && install_esam_shim "$d"; done
IFS=$OLDIFS
cat > "$BASE/run_api.py" <<'PYEOF'
import sys, importlib
appdir = sys.argv[1]
port = int(sys.argv[2])
sys.path.insert(0, appdir)
app=None
for mod in ("src.api_server","api_server","main","app"):
    try:
        m=importlib.import_module(mod)
    except Exception:
        continue
    for n in ("app","application"):
        o=getattr(m,n,None)
        if o is not None and type(o).__name__ in ("FastAPI","Starlette"):
            app=o; break
    if app is None:
        for n in dir(m):
            o=getattr(m,n,None)
            if type(o).__name__ == "FastAPI":
                app=o; break
    if app is not None: break
if app is None:
    sys.exit("no FastAPI app found")
from uvicorn import run
run(app, host="127.0.0.1", port=port)
PYEOF
cd "$APPDIR"
export PYTHONPATH="$APPDIR${PYTHONPATH:+:$PYTHONPATH}"
nohup "$VPY" "$BASE/run_api.py" "$APPDIR" "$PORT" >"$BASE/api.log" 2>&1 &
echo $! > "$BASE/api.pid"
"$PYBIN" - <<PYEOF
import sys, time, json, urllib.request
port = int("$PORT")
def get(p):
    return json.loads(urllib.request.urlopen("http://127.0.0.1:" + str(port) + p, timeout=3).read())
h=None; rdy=None
for _ in range(180):
    try:
        h=get("/health")
    except Exception:
        h=None
    if h and h.get("status") == "ok": break
    time.sleep(1)
if not (h and h.get("status") == "ok"): sys.exit("health not ok: %r" % (h,))
for _ in range(90):
    try:
        rdy=get("/ready")
    except Exception:
        rdy=None
    if rdy and rdy.get("status") == "ready": break
    time.sleep(1)
if not (rdy and rdy.get("status") == "ready"): sys.exit("ready not ready: %r" % (rdy,))
print(json.dumps({"health": h, "ready": rdy}))
PYEOF
echo "ESAM $TAG running at http://127.0.0.1:$PORT pid $(cat "$BASE/api.pid")"
