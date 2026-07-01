#!/usr/bin/env python3
"""ES Agent Registry — discover, register, track, and govern agents.

Provides identity management, state tracking, heartbeat monitoring,
and lifecycle management for all agents in the estate.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from database import (
    get_connection, transaction, new_id, utc_now,
    init_db,
)


class AgentRegistry:
    """Registry for managing agent identities and lifecycle."""

    def register(
        self,
        name: str,
        agent_type: str = "hermes",
        description: str = "",
        owner: str = "",
        profile: str = "default",
        identity_ref: str = "",
        permissions: dict | None = None,
        skills: list[str] | None = None,
        model_provider: str = "",
        model_name: str = "",
        host: str = "",
        platform: str = "",
    ) -> dict[str, Any]:
        """Register a new agent in the system. Returns the agent record."""
        agent_id = new_id("agent-")
        now = utc_now()

        with transaction() as conn:
            conn.execute("""
                INSERT INTO agents (id, name, agent_type, description, owner,
                    status, profile, identity_ref, permissions, skills,
                    model_provider, model_name, host, platform,
                    created_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                agent_id, name, agent_type, description, owner,
                profile, identity_ref,
                json.dumps(permissions or {}),
                json.dumps(skills or []),
                model_provider, model_name, host, platform,
                now, now,
            ))

        return self.get(agent_id)

    def get(self, agent_id: str) -> dict[str, Any] | None:
        """Get a single agent by ID."""
        conn = get_connection()
        row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if not row:
            return None
        return dict(row)

    def list(self, status: str | None = None, owner: str | None = None) -> list[dict[str, Any]]:
        """List agents, optionally filtered by status or owner."""
        conn = get_connection()
        query = "SELECT * FROM agents WHERE 1=1"
        params = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if owner:
            query += " AND owner = ?"
            params.append(owner)
        query += " ORDER BY created_at DESC"
        return [dict(r) for r in conn.execute(query, params).fetchall()]

    def heartbeat(self, agent_id: str, **fields) -> dict[str, Any]:
        """Record a heartbeat from an agent. Returns updated agent record."""
        now = utc_now()
        with transaction() as conn:
            updates = ["last_seen_at = ?"]
            params = [now]
            for key in ("status", "model_name", "model_provider", "host", "skills", "cron_jobs"):
                if key in fields:
                    val = fields[key]
                    if isinstance(val, (list, dict)):
                        val = json.dumps(val)
                    updates.append(f"{key} = ?")
                    params.append(val)
            params.append(agent_id)
            conn.execute(f"UPDATE agents SET {', '.join(updates)} WHERE id = ?", params)
        return self.get(agent_id)

    def update_status(self, agent_id: str, status: str) -> dict[str, Any]:
        """Update agent status. Status must be one of: offline, running, paused, error, retired."""
        valid = {"offline", "running", "paused", "error", "retired"}
        if status not in valid:
            raise ValueError(f"Invalid status '{status}'. Must be one of: {', '.join(sorted(valid))}")
        with transaction() as conn:
            now = utc_now()
            if status == "retired":
                conn.execute("UPDATE agents SET status = ?, retired_at = ? WHERE id = ?",
                             (status, now, agent_id))
            else:
                conn.execute("UPDATE agents SET status = ?, last_seen_at = ? WHERE id = ?",
                             (status, now, agent_id))
        return self.get(agent_id)

    def update_permissions(self, agent_id: str, permissions: dict) -> dict[str, Any]:
        """Update an agent's permission scope."""
        with transaction() as conn:
            conn.execute("UPDATE agents SET permissions = ? WHERE id = ?",
                         (json.dumps(permissions), agent_id))
        return self.get(agent_id)

    def retire(self, agent_id: str) -> dict[str, Any]:
        """Retire an agent — revoke access, mark as retired."""
        return self.update_status(agent_id, "retired")

    def get_summary(self) -> list[dict[str, Any]]:
        """Get agent summary view (with workflow counts, alerts, costs)."""
        conn = get_connection()
        return [dict(r) for r in conn.execute("SELECT * FROM agent_summary").fetchall()]

    def register_hermes_profiles(self) -> list[dict[str, Any]]:
        """Auto-discover and register local Hermes profiles as agents."""
        hermes_home = Path.home() / ".hermes"
        profiles_dir = hermes_home / "profiles"
        
        discovered = []
        
        # Default profile
        config_path = hermes_home / "config.yaml"
        if config_path.exists():
            agent = self.register(
                name="hermes-default",
                agent_type="hermes",
                description="Default Hermes Agent profile",
                owner="local",
                profile="default",
                host=os.uname().nodename,
                platform="macos",
            )
            discovered.append(agent)

        # Named profiles
        if profiles_dir.exists():
            for p_dir in sorted(profiles_dir.iterdir()):
                if p_dir.is_dir():
                    p_config = p_dir / "config.yaml"
                    if p_config.exists():
                        agent = self.register(
                            name=f"hermes-{p_dir.name}",
                            agent_type="hermes",
                            description=f"Hermes Agent profile: {p_dir.name}",
                            owner="local",
                            profile=p_dir.name,
                            host=os.uname().nodename,
                            platform="macos",
                        )
                        discovered.append(agent)

        return discovered


# ── CLI Demo ────────────────────────────────────────────────────────

def main():
    init_db()
    registry = AgentRegistry()

    print(f"\n{'='*60}")
    print(f"  ES AGENT REGISTRY — DEMO")
    print(f"{'='*60}\n")

    # Register a test agent
    agent = registry.register(
        name="tether-collections",
        description="Tether collections workflow agent",
        owner="evolving-software",
        skills=["tether-collections", "stripe-link-cli"],
        model_provider="local",
        model_name="gemma-4-12b-it",
        host=os.uname().nodename,
    )
    print(f"  Registered: {agent['name']} ({agent['id']})")
    print(f"  Status:     {agent['status']}")
    print(f"  Skills:     {json.loads(agent['skills'])}")
    print()

    # Discover local Hermes profiles
    print(f"  Discovering local Hermes profiles...")
    discovered = registry.register_hermes_profiles()
    print(f"  Found {len(discovered)} Hermes profile(s)\n")

    # List all agents
    agents = registry.list()
    print(f"  All agents ({len(agents)}):")
    for a in agents:
        print(f"    · {a['name']:<30}  {a['status']:<10}  {a['profile']:<15}  skills: {len(json.loads(a['skills']))}")
    print()

    # Summary
    summary = registry.get_summary()
    print(f"  Agent Summary:")
    for s in summary:
        print(f"    · {s['name']:<30}  workflows: {s['workflow_count']}  alerts: {s['alert_count']}  cost: ${s['total_cost']:.4f}")
    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
