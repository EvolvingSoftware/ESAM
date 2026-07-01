"""Entity Dictionary — dictionary-based entity definitions.

Manages the wf_entity_dictionary table for known entities
(companies, products, people, concepts, organizations).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from database import get_connection

__all__ = ["EntityDictionary"]


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


VALID_TYPES = frozenset({"company", "product", "person", "concept", "org"})


class EntityDictionary:
    """Manages known entities in the wf_entity_dictionary table."""

    def __init__(self, db_conn=None):
        self.db = db_conn or get_connection()
        self._ensure_table()

    # ── Schema ──────────────────────────────────────────────────────────

    TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS wf_entity_dictionary (
            id              TEXT PRIMARY KEY,
            entity          TEXT NOT NULL,
            type            TEXT NOT NULL CHECK(type IN ('company','product','person','concept','org')),
            aliases         TEXT DEFAULT '',
            category        TEXT DEFAULT '',
            authority_tier  TEXT DEFAULT 'B',
            source          TEXT DEFAULT '',
            created_at      TEXT,
            updated_at      TEXT
        );
    """

    def _ensure_table(self):
        self.db.execute(self.TABLE_SQL)
        self.db.commit()

    # ── CRUD ────────────────────────────────────────────────────────────

    def add(
        self,
        entity: str,
        entity_type: str,
        aliases: str = "",
        category: str = "",
        authority_tier: str = "B",
    ) -> dict:
        """Add an entity to the dictionary. Returns the created record."""
        if entity_type not in VALID_TYPES:
            raise ValueError(
                f"Invalid entity_type '{entity_type}'. Must be one of: {', '.join(sorted(VALID_TYPES))}"
            )
        if authority_tier not in ("A", "B", "C"):
            raise ValueError("authority_tier must be 'A', 'B', or 'C'")

        entity_id = _new_id()
        now = _now()
        self.db.execute(
            """INSERT INTO wf_entity_dictionary (id, entity, type, aliases, category, authority_tier, source, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, '', ?, ?)""",
            (entity_id, entity, entity_type, aliases, category, authority_tier, now, now),
        )
        self.db.commit()
        return self.get(entity_id)

    def get(self, entity_id: str) -> dict | None:
        """Get an entity by ID. Returns None if not found."""
        row = self.db.execute(
            "SELECT id, entity, type, aliases, category, authority_tier, source, created_at, updated_at FROM wf_entity_dictionary WHERE id = ?",
            (entity_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def find(self, name: str) -> list[dict]:
        """Fuzzy find entities by name (case-insensitive LIKE match on entity and aliases)."""
        pattern = f"%{name}%"
        rows = self.db.execute(
            """SELECT id, entity, type, aliases, category, authority_tier, source, created_at, updated_at
               FROM wf_entity_dictionary
               WHERE LOWER(entity) LIKE LOWER(?) OR LOWER(aliases) LIKE LOWER(?)
               ORDER BY entity""",
            (pattern, pattern),
        ).fetchall()
        return [dict(r) for r in rows]

    def list(self, entity_type: str | None = None, category: str | None = None) -> list[dict]:
        """List entities, optionally filtered by type and/or category."""
        query = """SELECT id, entity, type, aliases, category, authority_tier, source, created_at, updated_at
                   FROM wf_entity_dictionary"""
        params: list[str] = []
        filters: list[str] = []
        if entity_type:
            filters.append("type = ?")
            params.append(entity_type)
        if category:
            filters.append("category = ?")
            params.append(category)
        if filters:
            query += " WHERE " + " AND ".join(filters)
        query += " ORDER BY entity"
        rows = self.db.execute(query, tuple(params) if params else None).fetchall() if params else self.db.execute(query).fetchall()
        if not params:
            rows = self.db.execute(query).fetchall()
        return [dict(r) for r in rows]

    def delete(self, entity_id: str) -> bool:
        """Delete an entity by ID. Returns True if a row was deleted."""
        cur = self.db.execute("DELETE FROM wf_entity_dictionary WHERE id = ?", (entity_id,))
        self.db.commit()
        return cur.rowcount > 0

    # ── Seeds ──────────────────────────────────────────────────────────

    def seed_defaults(self) -> None:
        """Seed the dictionary with default entities for the AI domain."""
        defaults = [
            # Companies
            ("OpenAI", "company", "", "AI", "A"),
            ("Anthropic", "company", "", "AI", "A"),
            ("Google DeepMind", "company", "DeepMind", "AI", "A"),
            ("Meta AI", "company", "", "AI", "A"),
            ("Microsoft", "company", "", "AI", "A"),
            ("xAI", "company", "", "AI", "A"),
            ("Mistral AI", "company", "Mistral", "AI", "A"),
            ("Cohere", "company", "", "AI", "B"),
            ("Hugging Face", "company", "HuggingFace", "AI", "B"),
            # Products
            ("ChatGPT", "product", "", "AI", "A"),
            ("Claude", "product", "", "AI", "A"),
            ("Gemini", "product", "", "AI", "A"),
            ("Copilot", "product", "", "AI", "A"),
            ("Grok", "product", "", "AI", "B"),
            ("Llama", "product", "", "AI", "A"),
            ("Mistral", "product", "", "AI", "B"),
            ("Perplexity", "product", "", "AI", "B"),
            # Concepts
            ("RAG", "concept", "Retrieval-Augmented Generation", "AI Technique", "B"),
            ("Multi-Agent Systems", "concept", "Multi-Agent,Multi-Agent System", "AI Technique", "B"),
            ("RLHF", "concept", "Reinforcement Learning from Human Feedback", "AI Technique", "B"),
            ("Chain-of-Thought", "concept", "Chain of Thought,CoT", "AI Technique", "B"),
            ("Fine-Tuning", "concept", "Fine-Tuning,Finetuning", "AI Technique", "B"),
        ]

        for entity, entity_type, aliases, category, tier in defaults:
            existing = self.db.execute(
                "SELECT id FROM wf_entity_dictionary WHERE entity = ? AND type = ?",
                (entity, entity_type),
            ).fetchone()
            if existing:
                continue
            self.add(entity, entity_type, aliases=aliases, category=category, authority_tier=tier)
