# ESAM Quickstart (v0.1.2)

One command on a fresh machine (no lab SSH or pre-baked keys):

```bash
curl -fsSL https://raw.githubusercontent.com/EvolvingSoftware/ESAM/v0.1.2/install.sh | bash
```

If `curl` is unavailable:

```bash
python3 -c "import urllib.request;urllib.request.urlretrieve('https://raw.githubusercontent.com/EvolvingSoftware/ESAM/v0.1.2/install.sh','install.sh')" && bash install.sh
```

The installer downloads the v0.1.2 release artifact, creates a local venv, starts the API on `http://127.0.0.1:8000`, and waits for `/health` + `/ready`.

Check it:

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/ready
```

Send one chat reply classification (no fleet keys required):

```bash
curl -fsS -X POST http://127.0.0.1:8000/api/tether/reply/classify \
  -H 'Content-Type: application/json' \
  -d '{"debtor_id":"quickstart","subject":"release check","body":"Hello ESAM, please acknowledge this clean-account release test."}'
```

Stop the server:

```bash
kill "$(cat ~/esam-v0.1.2/api.pid)"
```
