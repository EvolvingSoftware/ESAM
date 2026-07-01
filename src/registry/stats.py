"""Edition Stats — computes per-edition statistics and cross-edition trends."""

from __future__ import annotations

import json
from typing import Any, Optional

from database import get_connection

from .engine import EditionRegistry, WFE_TABLE

__all__ = ["EditionStats"]


class EditionStats:
    """Computes edition-level statistics and cross-edition trends.

    Typical usage::

        stats = EditionStats()
        s = stats.compute("edition-id")
        trend = stats.compute_trend()
    """

    def __init__(self, db_conn: Any = None) -> None:
        self._conn = db_conn
        self._registry = EditionRegistry(db_conn=db_conn)

    def compute(self, edition_id: str) -> dict:
        """Compute statistics for a single edition.

        Returns::

            {
                "edition_number": N,
                "source_count": N,
                "item_count": N,
                "total_tokens": N,
                "duration_seconds": N.N,
                "avg_tokens_per_source": N.N,
                "signal_count": N,
                "top_signals": [...],
                "quality_score": N.N or None,
            }
        """
        edition = self._registry.get(edition_id)
        if not edition:
            return {"error": f"Edition not found: {edition_id}"}

        source_count = edition.get("source_count", 0) or 0
        item_count = edition.get("item_count", 0) or 0
        total_tokens = edition.get("total_tokens", 0) or 0
        duration_seconds = edition.get("duration_seconds", 0.0) or 0.0

        avg_tokens_per_source = 0.0
        if source_count > 0:
            avg_tokens_per_source = round(total_tokens / source_count, 2)

        # Signal count
        signal_count = 0
        top_signals: list[dict] = []
        try:
            signal_ids = json.loads(edition.get("signal_ids", "[]") or "[]")
            signal_count = len(signal_ids)
            # Look up top signals from wf_stories
            if signal_ids:
                conn = self._conn or get_connection()
                placeholders = ",".join("?" for _ in signal_ids[:10])
                try:
                    rows = conn.execute(
                        f"SELECT id, title, signal_strength FROM wf_stories "
                        f"WHERE id IN ({placeholders}) ORDER BY signal_strength DESC LIMIT 5",
                        tuple(signal_ids[:10]),
                    ).fetchall()
                    top_signals = [
                        {
                            "story_id": r["id"],
                            "title": r["title"],
                            "signal_strength": r["signal_strength"],
                        }
                        for r in rows
                    ]
                except Exception:
                    pass
        except (json.JSONDecodeError, TypeError):
            pass

        quality_score = edition.get("quality_score")

        return {
            "edition_number": edition.get("edition_number"),
            "source_count": source_count,
            "item_count": item_count,
            "total_tokens": total_tokens,
            "duration_seconds": duration_seconds,
            "avg_tokens_per_source": avg_tokens_per_source,
            "signal_count": signal_count,
            "top_signals": top_signals,
            "quality_score": quality_score,
        }

    def compute_trend(self) -> dict:
        """Compute trend statistics across all editions.

        Returns::

            {
                "total_editions": N,
                "avg_sources_per_edition": N.N,
                "avg_items_per_edition": N.N,
                "total_tokens_across_all": N,
                "avg_duration": N.N,
                "day_frequency": N.N,
            }
        """
        conn = self._conn or get_connection()

        # Aggregate stats
        row = conn.execute(
            f"""SELECT
                COUNT(*) as total_editions,
                COALESCE(AVG(source_count), 0) as avg_sources,
                COALESCE(AVG(item_count), 0) as avg_items,
                COALESCE(SUM(total_tokens), 0) as total_tokens,
                COALESCE(AVG(duration_seconds), 0) as avg_duration
            FROM {WFE_TABLE}"""
        ).fetchone()

        total_editions = row["total_editions"] or 0

        # Day frequency — compute avg days between editions
        day_frequency = 0.0
        if total_editions > 1:
            rows = conn.execute(
                f"SELECT date FROM {WFE_TABLE} ORDER BY edition_number ASC"
            ).fetchall()
            dates = [r["date"] for r in rows if r["date"]]
            if len(dates) > 1:
                try:
                    from datetime import datetime

                    parsed = [
                        datetime.fromisoformat(d) for d in dates
                    ]
                    gaps = [
                        (parsed[i + 1] - parsed[i]).days
                        for i in range(len(parsed) - 1)
                    ]
                    if gaps:
                        day_frequency = round(
                            sum(gaps) / len(gaps), 2
                        )
                except (ValueError, TypeError):
                    pass

        return {
            "total_editions": total_editions,
            "avg_sources_per_edition": round(float(row["avg_sources"]), 2),
            "avg_items_per_edition": round(float(row["avg_items"]), 2),
            "total_tokens_across_all": row["total_tokens"] or 0,
            "avg_duration": round(float(row["avg_duration"]), 2),
            "day_frequency": day_frequency,
        }
