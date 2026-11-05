# E2E — ESAM v0.1.1 one-liner in an EMPTY directory (clean box)
# date: 2026-11-05T06:55:19Z   host python: Python 3.12.3 (satisfies installer's 'Python 3.11+')
# note: shared lab box squats :8000, so the documented ESAM_PORT override is used (=8123).

\$ curl -fsSL https://raw.githubusercontent.com/EvolvingSoftware/ESAM/v0.1.1/install.sh | ESAM_PORT=8123 sh
INSTALL_EXIT=0

--- install.sh output (stdout+stderr) ---

\x1b[1;34m== Checking for Python 3.11+ ==\x1b[0m

\x1b[1;34m== using Python 3.12.3 ==\x1b[0m

\x1b[1;34m== Downloading EvolvingSoftware/ESAM @ v0.1.1 ==\x1b[0m

\x1b[1;34m== Extracting to /home/ai/.coriolis/sandbox/e2e_v011/esam-0.1.1 ==\x1b[0m

\x1b[1;34m== Extracted to /home/ai/.coriolis/sandbox/e2e_v011/esam-0.1.1 ==\x1b[0m

\x1b[1;34m== Creating virtualenv (.venv) and installing dependencies ==\x1b[0m

\x1b[1;34m== Starting API on port 8123 ==\x1b[0m
api pid 77 (log: /home/ai/.coriolis/sandbox/e2e_v011/esam-0.1.1/api.log)

\x1b[1;34m== Waiting for the API to come up ==\x1b[0m

\x1b[1;34m== API is serving (FastAPI OpenAPI schema confirmed on :8123). ==\x1b[0m

\x1b[1;34m== Seeding demo workflow ==\x1b[0m
============================================================
Seeding Demo Agents for Agent Management Hackathon
============================================================
Server: http://localhost:8123

Server health: {'status': 'ok', 'version': '0.1.0', 'timestamp': '2026-11-05T06:55:29.457354+00:00', 'database': 'connected'}

=== Tether Collections ===
  Already exists (id=78983601-72b3-44cd-94e2-5443d68c0334), skipping creation

=== Customer Support Classifier ===
  Already exists (id=ee9ed429-634d-4a96-8fef-93dc31b72393), skipping creation

=== Loan Assessment ===
  Already exists (id=05723ac9-a492-455d-b12e-f7234e463ce9), skipping creation

============================================================
VERIFICATION
============================================================

  Total agents: 3

  ── Loan Assessment (id=05723ac9-a49...)
      Steps: 4
      Connections: 3
      Runs: 2

  ── Customer Support (id=ee9ed429-634...)
      Steps: 3
      Connections: 2
      Runs: 2

  ── Tether Collections (id=78983601-72b...)
      Steps: 5
      Connections: 4
      Runs: 3

============================================================
Seed complete!
============================================================

\x1b[1;34m== Done — ESAM is live. ==\x1b[0m
Dashboard : http://localhost:8123/
OpenAPI   : http://localhost:8123/openapi.json
Docs      : http://localhost:8123/docs
Home dir  : /home/ai/.coriolis/sandbox/e2e_v011/esam-0.1.1
Installed : EvolvingSoftware/ESAM @ v0.1.1
API log   : /home/ai/.coriolis/sandbox/e2e_v011/esam-0.1.1/api.log (pid 77)
Stop it   : kill 77

--- served dashboard HTML (first 600 bytes of /) ---
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Evolving Software Agent Management — Current</title>
<style>
  :root {
    --bg: #0d0d0d;
    --surface: #1a1a1a;
    --surface-hover: #252525;
    --border: #2a2a2a;
    --text: #e0e0e0;
    --text-dim: #808080;
    --red: #e53935;
    --red-dim: #b71c1c;
    --green: #4caf50;
    --amber: #ffb300;
    --blue: #42a5f5;
    --accent: #e53935;
    --node-w: 180px;
    --node-h: 64px;
    --transition: all 0.25s ease;
  }
  * { margin: 0; padding: 0; box-
--- end ---

--- real workflow result: GET /api/runs (seeded demo runs) ---
curl: (7) Failed to connect to localhost port 8123 after 0 ms: Couldn't connect to server

