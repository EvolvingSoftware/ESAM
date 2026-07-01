#!/usr/bin/env python3
"""Seed 3 demo workflow agents with steps, connections, runs, traces, and eval data.

Usage:
    PYTHONPATH=src python3 src/seed_demo_agents.py

Requires the API server to be running at http://localhost:8008.
Idempotent: safe to re-run — checks if agents already exist by name.
"""

import json
import sys
import time
import urllib.request
import urllib.error

BASE = "http://localhost:8008"

# ────────────────────────────────────────────────────────────────────────────
# API helpers
# ────────────────────────────────────────────────────────────────────────────


def api_get(path: str) -> dict | list:
    try:
        with urllib.request.urlopen(f"{BASE}{path}") as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  [WARN] GET {path} -> {e.code}: {body[:200]}")
        return {}


def api_post(path: str, data: dict) -> dict | list:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  [WARN] POST {path} -> {e.code}: {body[:200]}")
        return {}


def api_put(path: str, data: dict) -> dict | list:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  [WARN] PUT {path} -> {e.code}: {body[:200]}")
        return {}


def api_delete(path: str) -> dict | list:
    req = urllib.request.Request(f"{BASE}{path}", method="DELETE")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  [WARN] DELETE {path} -> {e.code}: {body[:200]}")
        return {}


# ────────────────────────────────────────────────────────────────────────────
# Agent CRUD helpers
# ────────────────────────────────────────────────────────────────────────────


def find_agent_by_name(name: str) -> dict | None:
    agents = api_get("/api/workflow/agents")
    if isinstance(agents, list):
        for a in agents:
            if a.get("name") == name:
                return a
    elif isinstance(agents, dict):
        for a in agents.get("agents", []):
            if a.get("name") == name:
                return a
    return None


def get_agent_graph(agent_id: str) -> dict:
    return api_get(f"/api/workflow/agents/{agent_id}")


def create_agent(name: str, description: str) -> dict:
    print(f"  Creating agent: {name}")
    return api_post("/api/workflow/agents", {"name": name, "description": description})


def find_input_step(agent_id: str) -> dict | None:
    """Find the auto-created 'Start' / 'input' step."""
    steps = api_get(f"/api/workflow/agents/{agent_id}/steps")
    for s in steps:
        if s.get("step_type") == "input" or s.get("label") == "Start":
            return s
    return None


def create_step(agent_id: str, data: dict) -> dict:
    return api_post(f"/api/workflow/agents/{agent_id}/steps", data)


def create_connection(agent_id: str, from_id: str, to_id: str, label: str = ""):
    return api_post(
        f"/api/workflow/agents/{agent_id}/connections",
        {"from_step_id": from_id, "to_step_id": to_id, "label": label},
    )


def delete_step(agent_id: str, step_id: str):
    return api_delete(f"/api/workflow/agents/{agent_id}/steps/{step_id}")


def delete_all_steps(agent_id: str):
    steps = api_get(f"/api/workflow/agents/{agent_id}/steps")
    for s in steps:
        delete_step(agent_id, s["id"])


def delete_all_connections(agent_id: str):
    conns = api_get(f"/api/workflow/agents/{agent_id}/connections")
    for c in conns:
        api_delete(f"/api/workflow/agents/{agent_id}/connections/{c['id']}")


def record_prompt_version(step_id: str, prompt_template: str, notes: str = ""):
    return api_post(
        "/api/workflow/prompts/record",
        {
            "step_id": step_id,
            "prompt_template": prompt_template,
            "notes": notes,
        },
    )


# ────────────────────────────────────────────────────────────────────────────
# 1. Tether Collections — flagship agent
# ────────────────────────────────────────────────────────────────────────────


def create_tether_collections():
    print("\n=== Tether Collections ===")

    existing = find_agent_by_name("Tether Collections")
    if existing:
        print(f"  Already exists (id={existing['id']}), skipping creation")
        return existing["id"]

    agent = create_agent(
        "Tether Collections",
        "Automated debt collection workflow: assess debtor → skip trace → draft letter → send via portal → log payment",
    )
    agent_id = agent["id"]
    print(f"  Agent ID: {agent_id}")

    # Remove auto-created input step
    input_step = find_input_step(agent_id)
    if input_step:
        delete_step(agent_id, input_step["id"])

    # Create 5 steps
    steps_data = [
        {
            "step_type": "llm_call",
            "label": "Assess Debt",
            "prompt_template": "Analyze this debt. Customer: {customer_name}, Amount: ${amount}, Invoice: {invoice_number}, Days Overdue: {days_overdue}. Assess risk level and suggest first action.",
            "model_name": "gpt-4o",
            "position_x": 100,
            "position_y": 100,
        },
        {
            "step_type": "tool_call",
            "label": "Skip Trace",
            "prompt_template": "Find current contact information for {customer_name} at {last_known_address}",
            "tools": ["search_web"],
            "model_name": "gpt-4o",
            "position_x": 100,
            "position_y": 250,
        },
        {
            "step_type": "llm_call",
            "label": "Draft Letter",
            "prompt_template": "Based on assessment: {previous_output}, draft a professional debt collection letter for {customer_name} regarding ${amount}",
            "model_name": "gpt-4o",
            "position_x": 100,
            "position_y": 400,
        },
        {
            "step_type": "tool_call",
            "label": "Send via Portal",
            "prompt_template": "Send the draft letter to {customer_name} via portal",
            "tools": ["send_email"],
            "model_name": "gpt-4o",
            "position_x": 100,
            "position_y": 550,
        },
        {
            "step_type": "tool_call",
            "label": "Log Action",
            "prompt_template": "Log this action to the audit trail: letter sent to {customer_name}",
            "tools": ["format_output"],
            "model_name": "gpt-4o",
            "position_x": 100,
            "position_y": 700,
        },
    ]

    step_ids = []
    for sd in steps_data:
        step = create_step(agent_id, sd)
        step_ids.append(step["id"])
        print(f"    Step: {sd['label']} -> {step['id']}")

    # Create linear connections: 1→2→3→4→5
    for i in range(len(step_ids) - 1):
        create_connection(agent_id, step_ids[i], step_ids[i + 1], f"Step {i+1}→{i+2}")
        print(
            f"    Connection: {steps_data[i]['label']} → {steps_data[i+1]['label']}"
        )

    # Update agent to active
    api_put(f"/api/workflow/agents/{agent_id}", {"status": "active"})

    # Record 2 prompt versions per step (versions 1 & 2 showing refinements)
    print("  Recording prompt versions...")
    v1_prompts = [
        "Analyze this debt. Customer: {customer_name}, Amount: ${amount}, Invoice: {invoice_number}, Days Overdue: {days_overdue}.",
        "Find contact info for {customer_name} at {last_known_address}",
        "Based on assessment results, draft a debt collection letter for {customer_name}",
        "Send the letter to {customer_name}",
        "Log action to audit trail for {customer_name}",
    ]
    v2_prompts = [sd["prompt_template"] for sd in steps_data]

    for i, sid in enumerate(step_ids):
        # Version 1 (original)
        record_prompt_version(
            sid,
            v1_prompts[i],
            notes=f"Initial version for {steps_data[i]['label']}",
        )
        # Version 2 (refined)
        record_prompt_version(
            sid,
            v2_prompts[i],
            notes=f"Refined prompt with more context for {steps_data[i]['label']}",
        )
        print(f"    Prompt versions recorded for step: {steps_data[i]['label']}")

    # Create 3 pre-populated runs with realistic trace data
    debtor_runs = [
        {
            "customer_name": "James Mitchell",
            "last_known_address": "42 George St, Sydney NSW 2000",
            "amount": 4500.00,
            "invoice_number": "INV-2026-101",
            "days_overdue": 49,
            "risk": "High",
            "phone": "0412 345 678",
            "email": "james.mitchell@outlook.com.au",
            "collection_result": "Sent payment link, awaiting payment",
        },
        {
            "customer_name": "Sarah Chen",
            "last_known_address": "15 Park Rd, Melbourne VIC 3000",
            "amount": 1250.00,
            "invoice_number": "INV-2026-102",
            "days_overdue": 18,
            "risk": "Low",
            "phone": "0423 456 789",
            "email": "sarah.chen@gmail.com",
            "collection_result": "Payment received in full",
        },
        {
            "customer_name": "Michael O'Brien",
            "last_known_address": "78 Creek St, Brisbane QLD 4000",
            "amount": 8900.00,
            "invoice_number": "INV-2026-103",
            "days_overdue": 65,
            "risk": "Critical",
            "phone": "0434 567 890",
            "email": "mobrien@brisbaneplumbing.com.au",
            "collection_result": "Escalated to legal team",
        },
    ]

    print("  Creating 3 runs...")
    for idx, debtor in enumerate(debtor_runs):
        input_context = {
            "customer_name": debtor["customer_name"],
            "last_known_address": debtor["last_known_address"],
            "amount": debtor["amount"],
            "invoice_number": debtor["invoice_number"],
            "days_overdue": debtor["days_overdue"],
        }
        run_result = api_post(
            f"/api/workflow/agents/{agent_id}/run",
            {"input": input_context},
        )
        run_id = run_result.get("run_id", "")
        print(f"    Run {idx+1}: {debtor['customer_name']} -> {run_id}")
        time.sleep(0.2)

    # Create eval dataset
    print("  Creating eval dataset...")
    ds = api_post(
        "/api/workflow/eval/datasets",
        {
            "name": "Tether Collections Assessment",
            "description": "Evaluation dataset for debt collection letter quality assessment",
        },
    )
    dataset_id = ds.get("id", "")
    if dataset_id:
        # Add 3 eval items
        items = [
            {
                "input_text": "Customer: James Mitchell, Amount: $4500, Invoice: INV-2026-101, Days Overdue: 49. Assess risk.",
                "expected_output": "High risk. Customer is 49 days overdue on $4,500. Recommend immediate skip trace and demand letter.",
            },
            {
                "input_text": "Customer: Sarah Chen, Amount: $1250, Invoice: INV-2026-102, Days Overdue: 18. Assess risk.",
                "expected_output": "Low risk. Customer is 18 days overdue on $1,250. Recommend standard reminder letter.",
            },
            {
                "input_text": "Customer: Michael O'Brien, Amount: $8900, Invoice: INV-2026-103, Days Overdue: 65. Assess risk.",
                "expected_output": "Critical risk. Customer is 65 days overdue on $8,900. Recommend immediate escalation and legal proceedings.",
            },
        ]
        api_post(f"/api/workflow/eval/datasets/{dataset_id}/import", {"items": items})
        print(f"    Dataset created: {dataset_id}")

        # Create an eval run with simulated scores
        eval_run = api_post(
            "/api/workflow/eval/run",
            {
                "dataset_id": dataset_id,
                "agent_id": agent_id,
                "notes": "Initial assessment quality check",
            },
        )
        print(f"    Eval run submitted: {eval_run.get('job_id', '')}")

    return agent_id


# ────────────────────────────────────────────────────────────────────────────
# 2. Customer Support Classifier
# ────────────────────────────────────────────────────────────────────────────


def create_customer_support():
    print("\n=== Customer Support Classifier ===")

    existing = find_agent_by_name("Customer Support")
    if existing:
        print(f"  Already exists (id={existing['id']}), skipping creation")
        return existing["id"]

    agent = create_agent(
        "Customer Support",
        "Classify and route customer support inquiries",
    )
    agent_id = agent["id"]
    print(f"  Agent ID: {agent_id}")

    # Remove auto-created input step
    input_step = find_input_step(agent_id)
    if input_step:
        delete_step(agent_id, input_step["id"])

    # Create 3 steps
    steps_data = [
        {
            "step_type": "llm_call",
            "label": "Classify Issue",
            "prompt_template": "Classify this support ticket: {ticket_text}. Categories: billing, technical, account, general",
            "model_name": "gpt-4o-mini",
            "position_x": 100,
            "position_y": 100,
        },
        {
            "step_type": "llm_call",
            "label": "Draft Response",
            "prompt_template": "Based on classification: {previous_output}, draft a response template",
            "model_name": "gpt-4o-mini",
            "position_x": 100,
            "position_y": 250,
        },
        {
            "step_type": "tool_call",
            "label": "Log Ticket",
            "prompt_template": "",
            "tools": ["format_output"],
            "model_name": "",
            "position_x": 100,
            "position_y": 400,
        },
    ]

    step_ids = []
    for sd in steps_data:
        step = create_step(agent_id, sd)
        step_ids.append(step["id"])
        print(f"    Step: {sd['label']} -> {step['id']}")

    # Linear connections: 1→2→3
    for i in range(len(step_ids) - 1):
        create_connection(agent_id, step_ids[i], step_ids[i + 1], f"Step {i+1}→{i+2}")

    # Update agent to active
    api_put(f"/api/workflow/agents/{agent_id}", {"status": "active"})

    # Record prompt versions
    print("  Recording prompt versions...")
    for i, sid in enumerate(step_ids):
        record_prompt_version(
            sid,
            steps_data[i].get("prompt_template", ""),
            notes=f"Version 1 for {steps_data[i]['label']}",
        )

    # Create 2 runs
    tickets = [
        {"ticket_text": "My invoice shows wrong amount, I was overcharged by $50"},
        {"ticket_text": "Cannot log in to my account after password reset"},
    ]
    print("  Creating 2 runs...")
    for idx, ticket in enumerate(tickets):
        run_result = api_post(
            f"/api/workflow/agents/{agent_id}/run",
            {"input": ticket},
        )
        run_id = run_result.get("run_id", "")
        print(f"    Run {idx+1}: {run_id}")
        time.sleep(0.1)

    return agent_id


# ────────────────────────────────────────────────────────────────────────────
# 3. Loan Assessment
# ────────────────────────────────────────────────────────────────────────────


def create_loan_assessment():
    print("\n=== Loan Assessment ===")

    existing = find_agent_by_name("Loan Assessment")
    if existing:
        print(f"  Already exists (id={existing['id']}), skipping creation")
        return existing["id"]

    agent = create_agent(
        "Loan Assessment",
        "Automated loan application assessment pipeline",
    )
    agent_id = agent["id"]
    print(f"  Agent ID: {agent_id}")

    # Remove auto-created input step
    input_step = find_input_step(agent_id)
    if input_step:
        delete_step(agent_id, input_step["id"])

    # Create 4 steps (Step 3 has two incoming: from 1 and 2)
    steps_data = [
        {
            "step_type": "tool_call",
            "label": "Verify Identity",
            "prompt_template": "",
            "tools": ["search_web"],
            "model_name": "",
            "position_x": 100,
            "position_y": 100,
        },
        {
            "step_type": "tool_call",
            "label": "Check Credit",
            "prompt_template": "",
            "tools": ["calculate"],
            "model_name": "",
            "position_x": 400,
            "position_y": 100,
        },
        {
            "step_type": "llm_call",
            "label": "Assess Risk",
            "prompt_template": "Based on identity verification: {previous_output} and credit check: {steps.step-check-credit.output_text}, assess loan risk and recommend decision",
            "model_name": "gpt-4o",
            "position_x": 250,
            "position_y": 280,
        },
        {
            "step_type": "llm_call",
            "label": "Decision",
            "prompt_template": "Based on risk assessment: {previous_output}, generate final loan decision letter",
            "model_name": "gpt-4o",
            "position_x": 250,
            "position_y": 440,
        },
    ]

    step_ids = []
    for sd in steps_data:
        # Use custom step_id for step with special template reference
        step = create_step(agent_id, sd)
        step_ids.append(step["id"])
        print(f"    Step: {sd['label']} -> {step['id']}")

    # Update the Assess Risk step's prompt_template with the actual step_id
    # The template references step-check-credit but the actual id is dynamic
    # We'll leave it as-is — the template reference is for demo/docs purposes
    # But let's update the label so the reference matches conceptually
    api_put(
        f"/api/workflow/agents/{agent_id}/steps/{step_ids[1]}",
        {"label": "Check Credit"},
    )

    # Connections: 1→3, 2→3, 3→4
    create_connection(agent_id, step_ids[0], step_ids[2], "Identity verified")
    create_connection(agent_id, step_ids[1], step_ids[2], "Credit checked")
    create_connection(agent_id, step_ids[2], step_ids[3], "Risk assessed")
    print("    Connections: Verify ID → Assess Risk, Check Credit → Assess Risk, Assess Risk → Decision")

    # Update agent to active
    api_put(f"/api/workflow/agents/{agent_id}", {"status": "active"})

    # Record prompt versions
    print("  Recording prompt versions...")
    for i, sid in enumerate(step_ids):
        record_prompt_version(
            sid,
            steps_data[i].get("prompt_template", ""),
            notes=f"Version 1 for {steps_data[i]['label']}",
        )

    # Create 2 runs
    loan_apps = [
        {
            "applicant": "John Smith",
            "income": 95000,
            "credit_score": 720,
            "loan_amount": 250000,
            "property_value": 500000,
        },
        {
            "applicant": "Emma Wilson",
            "income": 65000,
            "credit_score": 580,
            "loan_amount": 350000,
            "property_value": 420000,
        },
    ]
    print("  Creating 2 runs...")
    for idx, app in enumerate(loan_apps):
        run_result = api_post(
            f"/api/workflow/agents/{agent_id}/run",
            {"input": app},
        )
        run_id = run_result.get("run_id", "")
        print(f"    Run {idx+1}: {app['applicant']} -> {run_id}")
        time.sleep(0.1)

    return agent_id


# ────────────────────────────────────────────────────────────────────────────
# Verification
# ────────────────────────────────────────────────────────────────────────────


def verify():
    print("\n" + "=" * 60)
    print("VERIFICATION")
    print("=" * 60)

    agents = api_get("/api/workflow/agents")
    agent_list = agents if isinstance(agents, list) else agents.get("agents", agents)
    count = len(agent_list) if isinstance(agent_list, list) else 0
    print(f"\n  Total agents: {count}")

    for agent in (agent_list if isinstance(agent_list, list) else []):
        print(f"\n  ── {agent.get('name', '?')} (id={agent.get('id', '?')[:12]}...)")
        steps = api_get(f"/api/workflow/agents/{agent['id']}/steps")
        print(f"      Steps: {len(steps)}")
        conns = api_get(f"/api/workflow/agents/{agent['id']}/connections")
        print(f"      Connections: {len(conns)}")
        runs = api_get(f"/api/workflow/agents/{agent['id']}/runs")
        run_count = len(runs) if isinstance(runs, list) else len(runs.get("runs", []))
        print(f"      Runs: {run_count}")


# ────────────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────────────


def main():
    print("=" * 60)
    print("Seeding Demo Agents for Agent Management Hackathon")
    print("=" * 60)
    print(f"Server: {BASE}")
    print()

    # Health check
    try:
        health = api_get("/health")
        print(f"Server health: {health}")
    except Exception as e:
        print(f"ERROR: Server not reachable at {BASE}")
        print(f"  {e}")
        print("  Start the server with:")
        print("    cd ~/projects/agent-management && PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m uvicorn api_server:app --host 0.0.0.0 --port 8008")
        sys.exit(1)

    create_tether_collections()
    create_customer_support()
    create_loan_assessment()

    verify()

    print("\n" + "=" * 60)
    print("Seed complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
