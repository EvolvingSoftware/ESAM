# ESAM v0.2.0 — Stranger-Install Proof (verbatim transcript)

**Date:** 2026-11-13T02:17Z UTC
**Clone source:** `git clone --depth 1 https://github.com/EvolvingSoftware/ESAM.git esam` @ `24daf3e` (main)
**Host:** clean directory, no manual edits, Python 3.12 venv, network access only.

The commands below are the **exact** sequence from the README quickstart section,
run verbatim in a throwaway clone of `main`. No files were edited between steps.

---
=== ESAM stranger-install proof — 2026-11-13T02:16:57Z ===
clone @ 24daf3e314c7f0f1724537969589ddfe7b35ca73 (main)

$ python3 -m venv .venv
[exit=0]

$ ./.venv/bin/pip install -r requirements.txt 2>&1 | tail -5
Using cached websockets-17.4-cp312-cp312-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl (226 kB)
Using cached idna-3.20-py3-none-any.whl (69 kB)
Using cached pycparser-3.12-py3-none-any.whl (62 kB)
Installing collected packages: passlib, websockets, uvloop, typing-extensions, PyYAML, python-dotenv, PyJWT, pycparser, markdown, idna, httptools, h11, click, annotated-types, annotated-doc, uvicorn, typing-inspection, pydantic-core, opentelemetry-api, cffi, anyio, watchfiles, starlette, pydantic, cryptography, fastapi
Successfully installed PyJWT-2.15.1 PyYAML-6.0.3 annotated-doc-0.0.6 annotated-types-0.8.0 anyio-4.16.0 cffi-2.1.1 click-8.5.1 cryptography-50.0.2 fastapi-0.162.5 h11-0.16.0 httptools-0.9.0 idna-3.20 markdown-3.11.3 opentelemetry-api-1.45.2 passlib-1.7.4 pycparser-3.12 pydantic-2.14.3 pydantic-core-2.50.4 python-dotenv-1.2.4 starlette-1.10.0 typing-extensions-4.16.0 typing-inspection-0.4.5 uvicorn-0.54.0 uvloop-0.23.0 watchfiles-1.3.0 websockets-17.4
[exit=0]

$ ./.venv/bin/pip install . 2>&1 | tail -5
  Created wheel for esam: filename=esam-0.2.0-py3-none-any.whl size=16865 sha256=99fc8cfeca7f9dbc9f0474355c2116cff47c7ce4f8698e8ede46297d12d910a6
  Stored in directory: /tmp/pip-ephem-wheel-cache-kwz7rl_7/wheels/4f/0b/82/5548957fde075f6016fcf4f9add94c3513086b59d3ff69628a
Successfully built esam
Installing collected packages: esam
Successfully installed esam-0.2.0
[exit=0]

$ make seed
.venv/bin/python -c "from database import init_db; from multi_entity import EntityManager; from seed import seed_all; init_db(); EntityManager().ensure_tables(); print('seed complete:', seed_all())"
seed complete: {'users': 0, 'entities': 0, 'entity_users': 0, 'debtors': 0, 'audit_logs': 0, 'payment_links': 1, 'letters_of_demand': 0}
[exit=0]

$ PORT=8137 nohup make serve > /tmp/esam-serve.log 2>&1 &
[exit=0]

$ ./.venv/bin/python -c "import urllib.request as u; r=u.urlopen('http://localhost:8137/health'); print(r.status, r.read().decode())"
200 {"status":"ok","version":"0.2.0","timestamp":"2026-11-13T02:17:12.818858+00:00","database":"connected"}
[exit=0]

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
[exit=0]

=== serve log (last 5 lines) ===
{"time": "2026-11-13T02:17:10.163299+00:00", "level": "INFO", "name": "observability.watchdog", "message": "StepWatchdog started (default_timeout=300s, poll_interval=5s)", "request_id": "e6baf3ea-1825-4a28-9afd-20cf30d94be5"}
{"time": "2026-11-13T02:17:10.266403+00:00", "level": "INFO", "name": "api_server", "message": "Registered Hermes profiles as agents", "request_id": "e6baf3ea-1825-4a28-9afd-20cf30d94be5"}
{"time": "2026-11-13T02:17:10.266486+00:00", "level": "INFO", "name": "api_server", "message": "ES Agent Management API server started", "request_id": "e6baf3ea-1825-4a28-9afd-20cf30d94be5"}
{"time": "2026-11-13T02:17:12.729584+00:00", "level": "INFO", "name": "esam.request", "message": "method=GET path=/health status=200 duration=8ms", "request_id": "4bda7b73-3468-4dd0-8fd6-7774d4678cac", "method": "GET", "path": "/health", "status_code": 200, "duration_ms": 8, "ip": "127.0.0.1"}
{"time": "2026-11-13T02:17:12.819438+00:00", "level": "INFO", "name": "esam.request", "message": "method=GET path=/health status=200 duration=1ms", "request_id": "3f45637e-7b02-4f2b-a9cf-e41893e03dee", "method": "GET", "path": "/health", "status_code": 200, "duration_ms": 1, "ip": "127.0.0.1"}
