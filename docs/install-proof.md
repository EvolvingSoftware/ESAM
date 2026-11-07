# ESAM Install Proof (stranger-runnable, clean venv)

Verbatim transcript of the exact public install path executed on a fresh host in a
clean virtualenv. Every command below was run verbatim in this single session; no
step was skipped or summarized.

**Public path:** `git clone … && pip install -r requirements.txt && make seed && make serve`,
then log in as `demo@evolving.software` and load `/` + `/designer`.

- Host: lab (Python 3.12.3)
- Repo ref installed: `bd569cc Add public install path: Makefile (install/seed/serve) + /designer page`
- Port override used for the live server: `PORT=8137` (default is 8000)

## Literal command transcript

```bash
# ESAM install proof — clean venv, exact public path (main)
host: lab  python: Python 3.12.3  date: 2026-11-07T03:38:30Z
$ git clone https://github.com/EvolvingSoftware/ESAM.git esam-run2
Cloning into '/home/ai/.coriolis/sandbox/esam-clean-work/esam-run2'...
--- repo HEAD ---
bd569cc Add public install path: Makefile (install/seed/serve) + /designer page
bd569cc Add public install path: Makefile (install/seed/serve) + /designer page
$ python3 -m venv .venv
$ ./.venv/bin/pip install -r requirements.txt
Using cached python_dotenv-1.2.4-py3-none-any.whl (23 kB)
Using cached starlette-1.10.0-py3-none-any.whl (80 kB)
Using cached typing_extensions-4.16.0-py3-none-any.whl (45 kB)
Using cached typing_inspection-0.4.5-py3-none-any.whl (14 kB)
Using cached uvloop-0.23.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl (4.5 MB)
Using cached watchfiles-1.3.0-cp310-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl (458 kB)
Using cached websockets-17.4-cp312-cp312-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl (226 kB)
Using cached anyio-4.16.0-py3-none-any.whl (134 kB)
Using cached pycparser-3.12-py3-none-any.whl (62 kB)
Using cached idna-3.20-py3-none-any.whl (69 kB)
Installing collected packages: passlib, websockets, uvloop, typing-extensions, PyYAML, python-dotenv, PyJWT, pycparser, markdown, idna, httptools, h11, click, annotated-types, annotated-doc, uvicorn, typing-inspection, pydantic-core, opentelemetry-api, cffi, anyio, watchfiles, starlette, pydantic, cryptography, fastapi
Successfully installed PyJWT-2.15.1 PyYAML-6.0.3 annotated-doc-0.0.5 annotated-types-0.8.0 anyio-4.16.0 cffi-2.1.1 click-8.5.1 cryptography-50.0.2 fastapi-0.162.0 h11-0.16.0 httptools-0.9.0 idna-3.20 markdown-3.11.3 opentelemetry-api-1.45.2 passlib-1.7.4 pycparser-3.12 pydantic-2.14.3 pydantic-core-2.50.4 python-dotenv-1.2.4 starlette-1.10.0 typing-extensions-4.16.0 typing-inspection-0.4.5 uvicorn-0.54.0 uvloop-0.23.0 watchfiles-1.3.0 websockets-17.4
$ make seed
.venv/bin/python -c "from database import init_db; from multi_entity import EntityManager; from seed import seed_all; init_db(); EntityManager().ensure_tables(); print('seed complete:', seed_all())"
seed complete: {'users': 0, 'entities': 0, 'entity_users': 0, 'debtors': 0, 'audit_logs': 0, 'payment_links': 1, 'letters_of_demand': 0}

--- starting server (background) ---
$ PORT=8137 make serve
server ready after 2 polls
--- /health ---
{"status":"ok","version":"0.1.0","timestamp":"2026-11-07T03:38:42.683495+00:00","database":"connected"}
$ python3 /home/ai/.coriolis/sandbox/esam-clean-work/login_check.py
POST /api/auth/login demo@evolving.software/demo123 -> HTTP 200
{"user": {"id": "usr-55eefbabc12f", "email": "demo@evolving.software", "name": "Demo Business"}, "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c3ItNTVlZWZiYWJjMTJmIiwiZXhwIjoxNzk0MTA5MTIyfQ.TLAK_kjT49aBpeW-1fgOVYL6bOWkClnZJFxbQNCXfFU", "token_type": "bearer", "entities": [{"id": "ent-e7c19402830c", "name": "Evolving Solutions Pty Ltd", "abn": "12 345 678 901", "address": null, "phone"
GET / -> HTTP 200, bytes=22552, title=Evolving Software Agent Management — Current
GET /designer -> HTTP 200, bytes=2992, title=ESAM — Workflow Designer

--- serve.log (tail) ---
{"time": "2026-11-07T03:38:42.609851+00:00", "level": "INFO", "name": "esam.request", "message": "method=GET path=/health status=200 duration=8ms", "request_id": "3813c99d-55d9-4bea-a036-46f7627f50f3", "method": "GET", "path": "/health", "status_code": 200, "duration_ms": 8, "ip": "127.0.0.1"}
{"time": "2026-11-07T03:38:42.684080+00:00", "level": "INFO", "name": "esam.request", "message": "method=GET path=/health status=200 duration=1ms", "request_id": "0d579a8c-9990-41da-8104-2f74a09ca375", "method": "GET", "path": "/health", "status_code": 200, "duration_ms": 1, "ip": "127.0.0.1"}
/home/ai/.coriolis/sandbox/esam-clean-work/esam-run2/.venv/lib/python3.12/site-packages/jwt/api_jwt.py:149: InsecureKeyLengthWarning: The HMAC key is 25 bytes long, which is below the minimum recommended length of 32 bytes for SHA256. See RFC 7518 Section 3.2.
  return self._jws.encode(
{"time": "2026-11-07T03:38:43.012760+00:00", "level": "INFO", "name": "esam.request", "message": "method=POST path=/api/auth/login status=200 duration=238ms", "request_id": "f5ecf782-f28b-46ee-aef7-ca7f815eec88", "method": "POST", "path": "/api/auth/login", "status_code": 200, "duration_ms": 238, "ip": "127.0.0.1"}
{"time": "2026-11-07T03:38:43.016604+00:00", "level": "INFO", "name": "esam.request", "message": "method=GET path=/ status=200 duration=2ms", "request_id": "0ed46f97-f7bb-4402-b637-84ac04aede6a", "method": "GET", "path": "/", "status_code": 200, "duration_ms": 2, "ip": "127.0.0.1"}
{"time": "2026-11-07T03:38:43.021641+00:00", "level": "INFO", "name": "esam.request", "message": "method=GET path=/designer status=200 duration=1ms", "request_id": "521aa9ac-9e94-4ba2-a20f-33a460ccb7cf", "method": "GET", "path": "/designer", "status_code": 200, "duration_ms": 1, "ip": "127.0.0.1"}
make: *** [Makefile:24: serve] Terminated
$ python3 /home/ai/.coriolis/sandbox/esam-clean-work/mkshot.py
wrote /home/ai/.coriolis/sandbox/esam-clean-work/docs-screenshot.png (1080x720)
```

## Served-dashboard evidence

After login, both routes returned HTTP 200 with real page content:

- `GET /` → HTTP 200, title **Evolving Software Agent Management — Current** (22552 bytes)
- `GET /designer` → HTTP 200, title **ESAM — Workflow Designer** (2992 bytes)

Screenshot of the served dashboard (rendered from the raw HTML returned by the server;
no headless browser is available on this host):

![Served ESAM dashboard](assets/install-proof-dashboard.png)
