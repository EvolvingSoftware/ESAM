# ESAM Quickstart (stranger edition)

Get ESAM running on a machine that has **never seen our fleet** — fresh user account,
no lab SSH, no pre-baked keys. One command:

```sh
curl -fsSL https://raw.githubusercontent.com/EvolvingSoftware/ESAM/v0.1.2/install.sh | sh
```

That's it.

## What that one command does

1. Checks for **Python 3.11+** (the only hard prerequisite; pip is used from the venv).
2. Downloads the tagged source (`v0.1.2`) from the public GitHub repo — no SSH, no keys.
3. Builds an isolated virtualenv and installs `requirements.txt`.
4. Boots the API on **port 8000** (override with `ESAM_PORT`).
5. Seeds a demo debtor + workflow against the running API.
6. Registers a fresh user account and proves **one working chat reply** through
   `POST /api/tether/reply/classify`.
7. Prints your dashboard URL, health endpoints, and how to stop it.

## Verify it worked

```sh
curl -s http://localhost:8000/health | python3 -m json.tool   # status: ok, database connected
curl -s http://localhost:8000/ready  | python3 -m json.tool   # status: ready
```

Then open the dashboard:

```sh
http://localhost:8000/
```

## Run a real workflow

From the install directory (default `./esam-0.1.2`):

```sh
cd esam-0.1.2
./esam demo     # seeds if needed, runs one real workflow, prints the dashboard result
```

## Chat reply path (what "one working chat reply" means)

The installer already proves this for you, but to do it by hand:

```sh
# 1. get a token (fresh account — no pre-baked keys anywhere)
curl -s -X POST http://localhost:8000/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","name":"You","password":"your-pass-123"}'
# -> { "user": {...}, "token": "<JWT>" }

# 2. classify a debtor reply (auth required)
curl -s -X POST http://localhost:8000/api/tether/reply/classify \
  -H 'Authorization: Bearer <JWT>' -H 'Content-Type: application/json' \
  -d '{"debtor_id":"d-001","subject":"Re: Payment reminder",
       "body":"I can pay half now and the rest next month.",
       "email_from":"demo-debtor@example.com"}'
# -> { "category": ..., "confidence": ..., "action": ... }
```

## Options (all optional)

| Env var | Default | Meaning |
|---|---|---|
| `ESAM_TAG` | `v0.1.2` | git ref to install (tag, branch or commit) |
| `ESAM_PORT` | `8000` | API port |
| `ESAM_HOME` | `$PWD/esam-<tag>` | directory to extract into and launch from |

Example — pin a different ref and port:

```sh
ESAM_TAG=v0.1.2 ESAM_PORT=9000 curl -fsSL https://raw.githubusercontent.com/EvolvingSoftware/ESAM/v0.1.2/install.sh | sh
```

## Stop it

The installer prints the API pid; stop with:

```sh
kill <pid>        # or: pkill -f 'uvicorn api_server:app'
```

Your data lives in `esam.db` (SQLite) inside the install directory — delete the
directory to wipe everything.
