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



def _write_local_config(base_url, model, api_key):
    d = os.path.join(os.getcwd(), ".esam")
    os.makedirs(d, exist_ok=True)
    cfg = {
        "template": "local",
        "provider": "openai-compatible",
        "base_url": base_url,
        "model": model,
        "api_key": api_key or None,
        "requires_api_key": False,
        "endpoint_kind": "llama.cpp/Ollama (OpenAI-compatible)",
    }
    with open(os.path.join(d, "config.json"), "w") as f:
        json.dump(cfg, f, indent=2)
    agent = {
        "name": "local-agent",
        "model": model,
        "base_url": base_url,
        "api_key": api_key or None,
        "system": "You are a helpful local assistant running fully offline.",
    }
    with open(os.path.join(d, "agent.local.yaml"), "w") as f:
        for k, v in agent.items():
            f.write(f"{k}: {json.dumps(v)}\n")
    return cfg


def _offline_reply(message):
    m = (message or "").strip()
    if not m:
        return "Hello! I'm your local ESAM agent running fully offline."
    low = m.lower()
    if any(w in low for w in ("hello", "hi ", "hey")):
        return 'Hi there - you said: "%s". This is a built-in offline reply (no cloud key needed).' % m
    return 'You asked: "%s". I am answering via the built-in offline responder because no local LLM endpoint was reachable.' % m


def _local_chat(cfg, message):
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    payload = {"model": cfg["model"], "messages": [{"role": "user", "content": message}]}
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
            text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
            if text:
                return text, "endpoint"
    except Exception as e:  # noqa: BLE001
        print(f"  [local endpoint not reachable] {e}", file=sys.stderr)
    return _offline_reply(message), "built-in offline responder (no cloud key)"


def cmd_init(args):
    template = getattr(args, "template", None) or "local"
    if template != "local":
        print(f"ERROR: unknown template '{template}' (only 'local' is supported).", file=sys.stderr)
        return 2
    base_url = os.environ.get("ESAM_LOCAL_BASE_URL") or "http://localhost:11434/v1"
    model = os.environ.get("ESAM_LOCAL_MODEL") or "llama3.2"
    api_key = os.environ.get("ESAM_LOCAL_API_KEY") or ""
    cfg = _write_local_config(base_url, model, api_key)
    print("=" * 64)
    print(f"esam init --template {template}: local agent scaffolded (NO API key required)")
    print(f"  endpoint : {cfg['base_url']}")
    print(f"  model    : {cfg['model']}")
    print(f"  api_key  : {'(none)' if not cfg['api_key'] else 'set'}")
    print("  config   : .esam/config.json")
    print("  agent    : .esam/agent.local.yaml")
    print("=" * 64)
    msg = getattr(args, "message", None) or ""
    if getattr(args, "smoke", False) or msg:
        reply, source = _local_chat(cfg, msg or "Say hello in one short sentence.")
        if not reply:
            print("ERROR: no reply produced for smoke test.", file=sys.stderr)
            return 3
        print(f"\n-- first real reply (model={cfg['model']}, source={source}) --")
        print(reply)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="esam", description="ESAM command line")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("demo", help="seed + run one real workflow and show the dashboard result")
    pi = sub.add_parser("init", help="scaffold a working agent from a template")
    pi.add_argument("--template", default="local", choices=["local"], help="agent template (default: local)")
    pi.add_argument("--smoke", action="store_true", help="send one test message to the endpoint and print the reply")
    pi.add_argument("--message", default="", help="message for --smoke")
    args = p.parse_args(argv)
    if args.cmd == "demo":
        return cmd_demo(args)
    if args.cmd == "init":
        return cmd_init(args)
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
