# ESAM Quickstart (v0.2.1)

Copy-paste this on a fresh machine (Python 3.11+, no lab SSH, no pre-baked keys):

```bash
git clone https://github.com/EvolvingSoftware/ESAM.git esam && cd esam
./install.sh
esam init --template local
esam ask "ping"
```

What happens:
1. `git clone` pulls the public repo.
2. `./install.sh` creates a venv, installs ESAM + deps, starts the API on `http://127.0.0.1:8000`, waits for `/health`+`/ready`, and drops an `esam` launcher onto your PATH (no hand-edits).
3. `esam init --template local` scaffolds a working local agent in `.esam/config.json`.
4. `esam ask "ping"` sends a message to the configured agent and prints its reply.

Expected final output:

```
You asked: "ping". I am answering via the built-in offline responder because no local LLM endpoint was reachable.
(source: built-in offline responder (no cloud key))
```

Check the API directly:

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/ready
```
