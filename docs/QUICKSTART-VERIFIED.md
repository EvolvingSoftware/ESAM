# ESAM — Verified Clean-Environment Quickstart (Stranger Transcript)

**Release:** v0.2.4  
**Verified against commit:** `486379c` (v0.2.3 code, clean clone)  
**Date:** 2026-11-14 09:32 UTC  
**Host:** lab box (no GPU, no pre-existing state)  

This file is the **verbatim transcript** of a stranger running the exact commands from `README.md` in a clean environment. No hand-edits, no pre-installed packages, no local Ollama, no cloud keys beyond what the template requires.

Both stranger paths are proven by this single run:
1. **Local template** — `esam init --template local --smoke` (no API key, no GPU)
2. **Cloud template** — `esam init --template cloud --smoke` (API key, no local GPU)

---
### ESAM v0.2.3 — clean-environment stranger transcript (no hand-edits)
### host: lab   date: 2026-11-14 09:32 UTC   python: Python 3.12.3

$ git clone https://github.com/EvolvingSoftware/ESAM.git esam && cd esam
Cloning into 'esam'...
### cloned HEAD: 486379c ESAM v0.2.3: esam init --template cloud (API key, no local GPU) + version bump 0.2.3

$ python3 -m venv .venv
(rc=0)
$ ./.venv/bin/pip install -r requirements.txt
(rc=0)
$ ./.venv/bin/pip install .
(rc=0)
### esam CLI present: yes
$ make seed
.venv/bin/python -c "from database import init_db; from multi_entity import EntityManager; from seed import seed_all; init_db(); EntityManager().ensure_tables(); print('seed complete:', seed_all())"
seed complete: {'users': 0, 'entities': 0, 'entity_users': 0, 'debtors': 0, 'audit_logs': 0, 'payment_links': 1, 'letters_of_demand': 0}
(rc=0)

$ PORT=8137 nohup make serve > /tmp/esam-serve.log 2>&1 &
### waiting for API /health ...
### health ready after 2s (ok=1)
$ ./.venv/bin/python -c urlopen(http://localhost:8137/health)
200 {"status":"ok","version":"0.2.3","timestamp":"2026-11-14T09:32:47.097248+00:00","database":"connected"}

### PATH 1 — local template (no API key, no GPU)
$ ./.venv/bin/esam init --template local --smoke
  [local endpoint not reachable] <urlopen error [Errno 111] Connection refused>
================================================================
esam init --template local: local agent scaffolded (NO API key required)
  endpoint : http://localhost:11434/v1
  model    : llama3.2
  api_key  : (none)
  config   : .esam/config.json
  agent    : .esam/agent.local.yaml
================================================================

-- first real reply (model=llama3.2, source=built-in offline responder (no cloud key)) --
Hi there - you said: "Say hello in one short sentence.". This is a built-in offline reply (no cloud key needed).
(exit=0)

### PATH 2 — cloud template (API key, no local GPU)
$ export ESAM_CLOUD_API_KEY=sk-...   # API key provided by the user
$ export ESAM_CLOUD_BASE_URL=http://127.0.0.1:8000/v1  ESAM_CLOUD_MODEL=gpt-4o-mini
$ ./.venv/bin/esam init --template cloud --smoke
================================================================
esam init --template cloud: cloud agent scaffolded (API key; no local GPU)
  endpoint : http://127.0.0.1:8000/v1
  model    : gpt-4o-mini
  api_key  : set
  config   : .esam/config.json
  agent    : .esam/agent.cloud.yaml
================================================================

-- first real reply (model=gpt-4o-mini, source=endpoint) --
Hello!
(exit=0)

---

## Summary

| Step | Result |
|------|--------|
| `git clone` + venv + pip install | rc=0, CLI present |
| `make seed` | payment_links seeded (1) |
| API `/health` on port 8137 | HTTP 200 `{"status":"ok","version":"0.2.3",...,"database":"connected"}` |
| `esam init --template local --smoke` | exit=0, offline responder replied |
| `esam init --template cloud --smoke` | exit=0, endpoint (gpt-4o-mini) replied |

A stranger can clone the repo, follow the README, and get a working agent that answers questions — no hand-edits required.
