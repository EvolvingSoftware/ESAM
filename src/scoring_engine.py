"""Scoring Rules Engine — Configurable formula-based article scoring.

Provides deterministic, no-LLM scoring based on configurable rules
stored in the ``wf_scoring_rules`` table. Each rule has a weight, source
field, and transform function.  The final score is the weighted sum of
per-rule scores, each normalised to 0‑10, clamped to [0, 10].
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from database import get_connection

__all__ = ["ScoringEngine"]


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_TRANSFORM_MAP: dict[str, str] = {
    "identity": "_transform_identity",
    "normalize_0_10": "_transform_normalize_0_10",
    "tier_to_score": "_transform_tier_to_score",
    "inverse": "_transform_inverse",
    "boolean": "_transform_boolean",
}


class ScoringEngine:
    """Deterministic article scoring engine powered by configurable rules.

    Scores are computed as the weighted sum of per-rule scores, each
    normalised to 0‑10 via the configured transform.  The final score is
    clamped to ``[0, 10]``.

    Usage::

        engine = ScoringEngine()
        rules = engine.get_rules(agent_id)
        scored = engine.compute_batch(items, rules)
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_score(self, item: dict, rules: list[dict]) -> float:
        """Compute a single score for *item* using the given *rules*.

        Returns a ``float`` in ``[0, 10]`` representing the weighted
        total.
        """
        total = 0.0
        for rule in rules:
            weight = rule.get("weight", 0)
            if weight is None:
                weight = 0
            weight = float(weight)
            if weight <= 0:
                continue

            source_field = rule.get("source_field", "")
            raw_val = item.get(source_field)

            transform_name = rule.get("transform", "identity")
            fn_name = _TRANSFORM_MAP.get(transform_name, "_transform_identity")
            transform_fn = getattr(self, fn_name, self._transform_identity)

            try:
                score = transform_fn(raw_val)
            except (TypeError, ValueError):
                score = 0.0

            total += weight * score

        return max(0.0, min(10.0, total))

    def compute_batch(self, items: list[dict], rules: list[dict]) -> list[dict]:
        """Apply :meth:`compute_score` to each item in *items*.

        Adds a ``'score'`` key to each item dict and returns the items
        **sorted by score descending**.
        """
        scored: list[dict] = []
        for item in items:
            item_copy = dict(item)
            item_copy["score"] = self.compute_score(item_copy, rules)
            scored.append(item_copy)

        scored.sort(key=lambda x: x.get("score", 0), reverse=True)
        return scored

    def get_rules(
        self, agent_id: str, step_id: Optional[str] = None
    ) -> list[dict]:
        """Retrieve scoring rules for an agent, optionally filtered by step.

        When *step_id* is ``None`` only workflow‑level rules (those whose
        ``step_id`` column is NULL) are returned.
        """
        conn = get_connection()
        if step_id:
            rows = conn.execute(
                "SELECT * FROM wf_scoring_rules"
                " WHERE agent_id = ? AND step_id = ?"
                " ORDER BY rowid",
                (agent_id, step_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM wf_scoring_rules"
                " WHERE agent_id = ? AND step_id IS NULL"
                " ORDER BY rowid",
                (agent_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def set_rules(
        self,
        agent_id: str,
        rules: list[dict],
        step_id: Optional[str] = None,
    ) -> dict:
        """Replace scoring rules for an agent (and optionally a step).

        Deletes existing rules matching the scope, then inserts the new
        ones.  Returns a summary dict with the count of rules inserted.
        """
        conn = get_connection()
        now = _now()

        # Delete existing rules for this scope
        if step_id:
            conn.execute(
                "DELETE FROM wf_scoring_rules"
                " WHERE agent_id = ? AND step_id = ?",
                (agent_id, step_id),
            )
        else:
            conn.execute(
                "DELETE FROM wf_scoring_rules"
                " WHERE agent_id = ? AND step_id IS NULL",
                (agent_id,),
            )

        # Insert new rules
        inserted = 0
        for rule in rules:
            rid = _new_id()
            rule_key = rule.get("rule_key", "")
            weight = float(rule.get("weight", 0))
            source_field = rule.get("source_field", "")
            transform = rule.get("transform", "identity")

            conn.execute(
                """INSERT INTO wf_scoring_rules
                   (id, agent_id, step_id, rule_key, weight,
                    source_field, transform, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (rid, agent_id, step_id, rule_key, weight,
                 source_field, transform, now, now),
            )
            inserted += 1

        conn.commit()
        return {
            "agent_id": agent_id,
            "step_id": step_id,
            "rules_inserted": inserted,
        }

    def insert_default_rules(self, agent_id: str) -> dict:
        """Insert default newsletter scoring rules for *agent_id*.

        These defaults match the ``'newsletter'`` tag profile:

        ================== ====== ================ =================
        rule_key           weight source_field     transform
        ================== ====== ================ =================
        freshness          0.3    published_date   normalize_0_10
        authority          0.4    domain_tier      tier_to_score
        relevance          0.2    keyword_matches  normalize_0_10
        story_continuity   0.1    story_new_angle  boolean
        ================== ====== ================ =================
        """
        defaults = [
            {
                "rule_key": "freshness",
                "weight": 0.3,
                "source_field": "published_date",
                "transform": "normalize_0_10",
            },
            {
                "rule_key": "authority",
                "weight": 0.4,
                "source_field": "domain_tier",
                "transform": "tier_to_score",
            },
            {
                "rule_key": "relevance",
                "weight": 0.2,
                "source_field": "keyword_matches",
                "transform": "normalize_0_10",
            },
            {
                "rule_key": "story_continuity",
                "weight": 0.1,
                "source_field": "story_new_angle",
                "transform": "boolean",
            },
        ]
        return self.set_rules(agent_id, defaults)

    # ------------------------------------------------------------------
    # Transform Functions  (all static, return float in [0, 10])
    # ------------------------------------------------------------------

    @staticmethod
    def _transform_identity(val: Any) -> float:
        """Return the value as-is, coerced to float."""
        if val is None:
            return 0.0
        return float(val)

    @staticmethod
    def _transform_normalize_0_10(val: Any) -> float:
        """Clamp value to [0, 10]."""
        if val is None:
            return 0.0
        return max(0.0, min(10.0, float(val)))

    @staticmethod
    def _transform_tier_to_score(val: Any) -> float:
        """Map tier letters to scores: A=10, B=7, C=4, others=5."""
        mapping = {"A": 10, "B": 7, "C": 4,
                    "a": 10, "b": 7, "c": 4}
        if val is None:
            return 5.0
        return float(mapping.get(str(val), 5))

    @staticmethod
    def _transform_inverse(val: Any) -> float:
        """Inverse scoring: lower input = higher score.

        ``10 - min(10, val)`` — useful for things like ``days_old``
        where fresher (lower) should score higher.
        """
        if val is None:
            return 10.0
        return 10.0 - min(10.0, float(val))

    @staticmethod
    def _transform_boolean(val: Any) -> float:
        """Boolean scoring: 10 if truthy, 0 if falsy."""
        return 10.0 if val else 0.0
