# ESAM Quickstart — your first run in under 5 minutes

New here? This is the shortest path from a fresh machine to a **running ESAM**
you can see and poke at: one install command, then verify it's live.

## What you'll get

- A live ESAM API + visual workflow designer on `http://localhost:8000`
- The demo workflow already seeded (so the designer isn't empty)
- Interactive API docs at `/docs`, OpenAPI schema at `/openapi.json`

No clone, no `.env`, no account. Everything runs locally in your current directory.

## Prerequisites

| Need | Why |
|------|-----|
| Linux or macOS (any POSIX box with a shell) | installer is plain `sh` + Python tooling |
| Python 3.11+ on PATH | the installer builds a virtualenv from it |
| Internet access | pulls the release tarball and dependencies |

That's the whole list — no Node, Go, or cloud credentials for a first run.

## Install (one line)

```bash
curl -fsSL https://raw.githubusercontent.com/EvolvingSoftware/ESAM/v0.1.0/install.sh | sh
```

What it does: extracts the release to `./esam-v0.1.0`, creates `.venv`, installs
dependencies, seeds the demo workflow and demo agents, boots the API on port 8000,
and health-checks `/openapi.json` before declaring success — so if it exits 0, the
API is actually serving.

## Verify your first run (what you should see)

1. The installer ends with `API is serving ...` and prints the dashboard URL.
2. In a browser: open **http://localhost:8000/designer** — the visual workflow
   designer loads with the seeded demo workflow visible.
3. Check from the shell (no browser needed):

```bash
curl -fsS http://localhost:8000/openapi.json | head -c 200; echo
# → JSON starting with {"openapi":"3.1..." — the API is live
```

4. Optional: open **http://localhost:8000/docs** for the interactive API explorer.

If all three hold, you've completed your first run.

## Options (all optional, all env vars)

| Var | Meaning | Default |
|-----|---------|---------|
| `ESAM_TAG` | release tag to install | `v0.1.0` |
| `ESAM_PORT` | API port | `8000` |
| `ESAM_HOME` | where the release is extracted/launched | `$PWD/esam-<tag>` |

Example — different port, pinned location:

```bash
ESAM_PORT=9000 ESAM_HOME=/opt/esam curl -fsSL \
  https://raw.githubusercontent.com/EvolvingSoftware/ESAM/v0.1.0/install.sh | sh
```

## If it doesn't work (first-run troubleshooting)

| Symptom | Likely cause / fix |
|---------|--------------------|
| `command not found: python3` or Python < 3.11 error | Install Python 3.11+ (`pyenv`, `apt install python3.11`, or your distro's package) and re-run the one-liner |
| Installer warns something is already listening on :8000 | Pick a free port: `ESAM_PORT=9000 <one-liner>` (or stop whatever holds 8000) |
| `API did not come up` + log lines printed | Read the last `api.log` lines it shows; usually a dependency or port issue — re-run after fixing |
| Dashboard URL opens but looks empty in your browser | The API may have been started from another directory/box — check `curl -fsS http://localhost:8000/openapi.json` first to confirm the API itself is live |

The installer fails loudly (non-zero exit) if the health check doesn't pass, so a
clean exit means your first run succeeded.
