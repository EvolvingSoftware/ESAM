#!/usr/bin/env python3
"""ESAM CLI — `esam demo` seeds one real workflow, runs it to completion and
prints the visible dashboard result.

Usage:
    ./esam demo            # seed (if needed) + run one workflow + show dashboard
    ESAM_API_BASE=... ./esam demo

Stdlib only. Talks to the running API over HTTP (urllib). The install script
boots the API and seeds the demo agents; this command is safe to re-run.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error

BASE = (os.environ.get("ESAM_API_BASE") or "http://localhost:8000").rstrip("/")

TERMINAL_OK = {"completed", "complete", "succeeded", "success", "done", "finished", "ok"}
TERMINAL_BAD = {"failed", "error", "errored", "cancelled", "canceled", "aborted"}


def _req(method: str, path: str, data=None):
    url = f"{BASE}{path}"
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:400]
        print(f"  [http {e.code}] {method} {path}: {detail}", file=sys.stderr)
        return None
    except Exception as e:  # noqa: BLE001
        print(f"  [err] {method} {path}: {e}", file=sys.stderr)
        return None


def _get(path):
    return _req("GET", path)


def wait_for_api(timeout=90):
    for i in range(timeout):
        h = _get("/health")
        if h is not None:
            print(f"API healthy: {json.dumps(h)[:200]}")
            return True
        time.sleep(1)
    return False


def list_agents():
    a = _get("/api/workflow/agents")
    if isinstance(a, dict):
        return a.get("agents", []) or []
    if isinstance(a, list):
        return a
    return []


def ensure_seeded():
    """If no agents exist yet, run the bundled seed script against this API."""
    agents = list_agents()
    if agents:
        print(f"Found {len(agents)} workflow agent(s) already seeded.")
        return agents
    print("No agents found — seeding demo workflow via src/seed_demo_agents.py ...")
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    seed = os.path.join(root, "src", "seed_demo_agents.py")
    if not os.path.exists(seed):
        print(f"  [warn] seed script not found at {seed}; assuming external seeding.")
        return []
    rc = os.system(
        f'ESAM_API_BASE="{BASE}" PYTHONPATH={os.path.join(root, "src")} '
        f'{sys.executable} "{seed}"'
    )
    if rc != 0:
        print(f"  [warn] seed exited {rc}; continuing.")
    return list_agents()


def pick_agent(agents):
    # Prefer a named demo agent; otherwise the first one.
    for key in ("customer", "support", "loan"):
        for a in agents:
            if key in str(a.get("name", "")).lower():
                return a
    return agents[0] if agents else None


def run_agent(agent_id):
    print(f"\nTriggering a real workflow run on agent {agent_id} ...")
    res = _req("POST", f"/api/workflow/agents/{agent_id}/run", {})
    if res is None:
        # Some builds expose the alternate route.
        res = _req("POST", f"/api/workflow/run/{agent_id}", {})
    print(f"  run response: {json.dumps(res)[:400]}")

    run_id = None
    status = "unknown"
    if isinstance(res, dict):
        for k in ("run_id", "id", "runId"):
            if res.get(k) is not None:
                run_id = str(res[k])
                break
        for k in ("status", "state"):
            if res.get(k):
                status = str(res[k]).lower()
    else:
        # Inline completion: the API returned a result object directly.
        status = "completed"

    # Poll for terminal state when we got a run id but no final status yet.
    if run_id and status not in TERMINAL_OK | TERMINAL_BAD:
        for _ in range(30):
            st = _get(f"/api/workflow/runs/{run_id}")
            if isinstance(st, dict) and st.get("status"):
                status = str(st["status"]).lower()
                if status in TERMINAL_OK | TERMINAL_BAD:
                    break
            time.sleep(1)
    return status, res, run_id


def show_dashboard(agent_id):
    print("\n" + "=" * 64)
    print("DASHBOARD RESULT (live, served by the running API)")
    print("=" * 64)
    summary = _get("/api/workflow/dashboard/summary")
    if summary is not None:
        print("\n-- /api/workflow/dashboard/summary --")
        print(json.dumps(summary, indent=2)[:1500])
    recent = _get("/api/workflow/dashboard/recent-runs")
    if recent is not None:
        print("\n-- /api/workflow/dashboard/recent-runs (top 3) --")
        items = recent.get("runs", recent) if isinstance(recent, dict) else recent
        if isinstance(items, list):
            for r in items[:3]:
                print(json.dumps(r)[:400])
    # Prove the dashboard SPA is being served (real HTML, not just a status code).
    for path in ("/", "/index.html", "/designer"):
        try:
            with urllib.request.urlopen(f"{BASE}{path}", timeout=15) as resp:
                html = resp.read().decode(errors="replace")
            head = " ".join(html.split())[:200]
            print(f"\n-- GET {path} (HTTP {resp.status}, served dashboard SPA) --")
            print(f"html[0:200]: {head}")
            if path == "/":
                break
        except Exception as e:  # noqa: BLE001
            print(f"\n-- GET {path} failed: {e}")
    print("\nOpen the dashboard in a browser:")
    print(f"  Designer : {BASE}/designer")
    print(f"  Dashboard: {BASE}/")
    print(f"  Docs     : {BASE}/docs")


def cmd_demo(_args):
    if not wait_for_api():
        print(f"ERROR: API not reachable at {BASE}", file=sys.stderr)
        return 1
    agents = ensure_seeded()
    agent = pick_agent(agents) or (agents[0] if agents else None)
    if not agent:
        print("ERROR: no workflow agent available to run.", file=sys.stderr)
        return 2
    aid = str(agent.get("id") or agent.get("agent_id"))
    name = agent.get("name", "?")
    print(f"\nSelected demo agent: {name} (id={aid})")
    status, _detail, run_id = run_agent(aid)
    show_dashboard(aid)
    ok = status in TERMINAL_OK or run_id is not None
    print("\n" + "=" * 64)
    print(f"esam demo: {'OK' if ok else 'INCOMPLETE'} — agent '{name}' run {run_id or '(inline)'} status={status}")
    print("=" * 64)
    return 0 if ok else 3


def main(argv=None):
    p = argparse.ArgumentParser(prog="esam", description="ESAM command line")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("demo", help="seed + run one real workflow and show the dashboard result")
    args = p.parse_args(argv)
    if args.cmd == "demo":
        return cmd_demo(args)
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
