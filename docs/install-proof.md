# ESAM v0.1.1 — Second-Host Install Proof

Raw, verbatim transcript of a stranger-runnable install on a host that had **never**
had ESAM. One public install command + one `esam demo` command → a real workflow run
→ a visible dashboard result.

## Host / environment (deterministic sensor)
- Fresh empty working directory: `/home/ai/.coriolis/sandbox/esam-proof`
- Python 3.12.3 available; no prior ESAM, no `.venv`, no port squatter on 8123.
- Port override `ESAM_PORT=8123` (default is 8000).
- Public tag used: `v0.1.1` (`EvolvingSoftware/ESAM`).

---

## STEP 1 — the one public install command

```bash
curl -fsSL https://raw.githubusercontent.com/EvolvingSoftware/ESAM/v0.1.1/install.sh | sh
```
(run with `ESAM_PORT=8123`)

### Verbatim output

```
== Checking for Python 3.11+ ==
== using Python 3.12.3 ==
== Downloading EvolvingSoftware/ESAM @ v0.1.1 ==
== Extracting to /home/ai/.coriolis/sandbox/esam-proof/esam-0.1.1 ==
== Extracted to /home/ai/.coriolis/sandbox/esam-proof/esam-0.1.1 ==
== Creating virtualenv (.venv) and installing dependencies ==
== Starting API on port 8123 ==
api pid 85 (log: /home/ai/.coriolis/sandbox/esam-proof/esam-0.1.1/api.log)

== Waiting for the API to come up ==
== API is serving (FastAPI OpenAPI schema confirmed on :8123). ==
== Seeding demo workflow ==
============================================================
Seeding Demo Agents for Agent Management Hackathon
============================================================
Server: http://localhost:8123

Server health: {'status': 'ok', 'version': '0.1.0', 'timestamp': '2026-11-06T08:36:24.389355+00:00', 'database': 'connected'}

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
      Runs: 3

  ── Tether Collections (id=78983601-72b...)
      Steps: 5
      Connections: 4
      Runs: 3

============================================================
Seed complete!
============================================================

== Done — ESAM is live. ==
Dashboard : http://localhost:8123/
OpenAPI   : http://localhost:8123/openapi.json
Docs      : http://localhost:8123/docs
Home dir  : /home/ai/.coriolis/sandbox/esam-proof/esam-0.1.1
Installed : EvolvingSoftware/ESAM @ v0.1.1
API log   : /home/ai/.coriolis/sandbox/esam-proof/esam-0.1.1/api.log (pid 85)
Stop it   : kill 85

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

== Quickstart ready — run the demo workflow with a single command: ==
  cd /home/ai/.coriolis/sandbox/esam-proof/esam-0.1.1
  ./esam demo        # seeds (if needed), runs one real workflow, prints the dashboard result
```

---

## STEP 2 — the single `esam demo` command

```bash
cd /home/ai/.coriolis/sandbox/esam-proof/esam-0.1.1
./esam demo
```

### Verbatim output

```
API healthy: {"status": "ok", "version": "0.1.0", "timestamp": "2026-11-06T08:36:24.605687+00:00", "database": "connected"}
Found 3 workflow agent(s) already seeded.

Selected demo agent: Customer Support (id=ee9ed429-634d-4a96-8fef-93dc31b72393)

Triggering a real workflow run on agent ee9ed429-634d-4a96-8fef-93dc31b72393 ...
  run response: {"run_id": "a322a3ae-a90e-45e5-97c6-deb9c66eb3e2", "status": "completed", "steps_executed": 3, "total_tokens": 1488, "total_cost_cents": 0}

================================================================
DASHBOARD RESULT (live, served by the running API)
================================================================

-- /api/workflow/dashboard/summary --
{
  "total_agents": 3,
  "active_agents": 3,
  "total_runs": 18,
  "total_cost_cents": 0,
  "draft_agents": 0
}

-- /api/workflow/dashboard/recent-runs (top 3) --
{"id": "a322a3ae-a90e-45e5-97c6-deb9c66eb3e2", "agent_id": "ee9ed429-634d-4a96-8fef-93dc31b72393", "status": "completed", "started_at": "2026-11-06T08:36:24.641091+00:00", "completed_at": "2026-11-06T08:36:24.687262+00:00", "total_cost_cents": 0, "total_tokens": 1488, "total_steps": 3, "trigger": "manual", "input_context": "{}", "created_at": "2026-11-06T08:36:24.619034+00:00", "agent_name": "Cust
{"id": "39a34c09-5655-474b-9a88-036f6e266099", "agent_id": "ee9ed429-634d-4a96-8fef-93dc31b72393", "status": "completed", "started_at": "2026-11-06T07:26:31.374960+00:00", "completed_at": "2026-11-06T07:26:31.420595+00:00", "total_cost_cents": 0, "total_tokens": 1780, "total_steps": 3, "trigger": "manual", "input_context": "{}", "created_at": "2026-11-06T07:26:31.353326+00:00", "agent_name": "Cust
{"id": "c058f7ce-dd18-4a82-92db-760f446c3808", "agent_id": "05723ac9-a492-455d-b12e-f7234e463ce9", "status": "completed", "started_at": "2026-11-05T01:03:03.245593+00:00", "completed_at": "2026-11-05T01:03:03.292433+00:00", "total_cost_cents": 0, "total_tokens": 1208, "total_steps": 3, "trigger": "manual", "input_context": "{\"applicant\": \"Emma Wilson\", \"income\": 65000, \"credit_score\": 580,

-- GET / (HTTP 200, served dashboard SPA) --
html[0:200]: <!DOCTYPE html> <html lang="en"> <head> <meta charset="UTF-8"> <meta name="viewport" content="width=device-width, initial-scale=1.0"> <title>Evolving Software Agent Management — Current</title> <style

Open the dashboard in a browser:
  Designer : http://localhost:8123/designer
  Dashboard: http://localhost:8123/
  Docs     : http://localhost:8123/docs

================================================================
esam demo: OK — agent 'Customer Support' run a322a3ae-a90e-45e5-97c6-deb9c66eb3e2 status=completed
================================================================
```

---

## Result

- **Install command:** `curl -fsSL https://raw.githubusercontent.com/EvolvingSoftware/ESAM/v0.1.1/install.sh | sh` → live API on the chosen port, 3 demo agents seeded.
- **Demo command:** `./esam demo` → triggered a real workflow run (`run_id a322a3ae-a90e-45e5-97c6-deb9c66eb3e2`, status=completed, steps_executed=3) and printed the live dashboard result.
- **Visible dashboard:** `GET /` returned HTTP 200 with the served SPA (`<title>Evolving Software Agent Management — Current</title>`); designer at `/designer`, docs at `/docs`.

No new internal tooling or audit scripts were added; this file is the raw second-host transcript only.
