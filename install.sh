#!/bin/sh
# ESAM one-line installer (POSIX sh).
#
#   curl -fsSL https://raw.githubusercontent.com/EvolvingSoftware/ESAM/v0.2.5/install.sh | sh
#
# What it does, with no prior ESAM config and no hand-edits:
#   1. Fetches the tagged source (git clone of the tag; codeload tarball fallback).
#   2. Builds an isolated venv and installs requirements + the esam package.
#   3. Drops an `esam` launcher onto every writable dir already on $PATH so the
#      command is resolvable immediately (no profile edits, no curl/wget needed).
#   4. Best-effort seeds the local database (non-fatal if it fails).
#
set -eu
OWNER="EvolvingSoftware"; REPO="ESAM"; TAG="${ESAM_TAG:-v0.2.5}"
PYBIN="${PYTHON:-python3}"
BASE="${ESAM_HOME:-$HOME/esam-$TAG}"
mkdir -p "$BASE"
SRC="$BASE/src"

# --- 1. get the tagged source -------------------------------------------------
if [ ! -f "$SRC/pyproject.toml" ]; then
  rm -rf "$SRC"
  if command -v git >/dev/null 2>&1; then
    if git clone --depth 1 --branch "$TAG" "https://github.com/$OWNER/$REPO.git" "$SRC" 2>/dev/null; then
      echo "[esam] cloned $TAG via git"
    else
      rm -rf "$SRC"; mkdir -p "$SRC"
      "$PYBIN" -c 'import sys,urllib.request as u;u.urlretrieve(sys.argv[1],sys.argv[2])' \
        "https://codeload.github.com/$OWNER/$REPO/tar.gz/refs/tags/$TAG" "$BASE/esam.tgz"
      tar xzf "$BASE/esam.tgz" -C "$SRC" --strip-components=1
      echo "[esam] fetched $TAG via codeload tarball"
    fi
  else
    mkdir -p "$SRC"
    "$PYBIN" -c 'import sys,urllib.request as u;u.urlretrieve(sys.argv[1],sys.argv[2])' \
      "https://codeload.github.com/$OWNER/$REPO/tar.gz/refs/tags/$TAG" "$BASE/esam.tgz"
    tar xzf "$BASE/esam.tgz" -C "$SRC" --strip-components=1
    echo "[esam] fetched $TAG via codeload tarball (no git)"
  fi
fi
if [ ! -f "$SRC/pyproject.toml" ]; then echo "ESAM source not found at $SRC" >&2; exit 1; fi

# --- 2. venv + deps + package -------------------------------------------------
"$PYBIN" -m venv "$BASE/.venv"
VPY="$BASE/.venv/bin/python"
"$VPY" -m pip install --upgrade pip >/dev/null 2>&1 || true
(cd "$SRC" && "$VPY" -m pip install -r requirements.txt)
(cd "$SRC" && ("$VPY" -m pip install . || "$VPY" -m pip install --no-build-isolation .))

# --- 3. put `esam` on PATH (every writable dir already on $PATH) --------------
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/esam" <<EOF
#!/bin/sh
exec "$VPY" -m scripts.esam_cli "\$@"
EOF
chmod +x "$HOME/.local/bin/esam"
OLDIFS=$IFS; IFS=':'
for d in $PATH; do
  [ -n "$d" ] || continue
  if [ -w "$d" ]; then
    printf '#!/bin/sh\nexec "%s" -m scripts.esam_cli "%%@"\n' "$VPY" > "$d/esam" 2>/dev/null && chmod +x "$d/esam" 2>/dev/null || true
  fi
done
IFS=$OLDIFS

# --- 4. best-effort seed (non-fatal) ------------------------------------------
(cd "$SRC" && PYTHONPATH=src "$VPY" -c "from database import init_db; from multi_entity import EntityManager; from seed import seed_all; init_db(); EntityManager().ensure_tables(); print('seed complete:', seed_all())") \
  || echo "[esam] seed skipped (non-fatal)"

echo "ESAM $TAG installed to $BASE"
echo "esam CLI ready: esam init --template local   (see https://github.com/$OWNER/$REPO/tree/$TAG)"
