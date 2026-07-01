"""Body differ — sentence-level comparison of story body texts."""

from __future__ import annotations

import re
from typing import Any


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences."""
    # Simple sentence splitting on sentence-ending punctuation
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _jaccard_similarity(words_a: set[str], words_b: set[str]) -> float:
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


class BodyDiffer:
    """Compare story body texts at the sentence level.

    Produces a structured diff dict with change type, individual changes,
    and an overall similarity score.
    """

    def diff(self, body_a: str, body_b: str) -> dict[str, Any]:
        """Compare two body texts and produce a structured diff.

        Returns::

            {
                "diff_type": "unchanged" | "minor_changes" | "significant_changes" | "new",
                "changes": [
                    {"type": "added" | "removed" | "modified", "text": str, "position": int},
                    ...
                ],
                "similarity_score": float,
            }
        """
        a_clean = (body_a or "").strip()
        b_clean = (body_b or "").strip()

        # If both empty — no content
        if not a_clean and not b_clean:
            return {
                "diff_type": "unchanged",
                "changes": [],
                "similarity_score": 1.0,
            }

        # One is empty — treat as new
        if not a_clean and b_clean:
            return {
                "diff_type": "new",
                "changes": [{"type": "added", "text": b_clean[:200], "position": 0}],
                "similarity_score": 0.0,
            }
        if a_clean and not b_clean:
            return {
                "diff_type": "significant_changes",
                "changes": [{"type": "removed", "text": a_clean[:200], "position": 0}],
                "similarity_score": 0.0,
            }

        # Full comparison
        sents_a = _split_sentences(a_clean)
        sents_b = _split_sentences(b_clean)

        # Token-level Jaccard for the whole body
        words_a = set(re.findall(r"[a-z0-9]+", a_clean.lower()))
        words_b = set(re.findall(r"[a-z0-9]+", b_clean.lower()))
        similarity = _jaccard_similarity(words_a, words_b)

        # Sentence-level diff
        changes: list[dict[str, Any]] = []
        set_a = set(s.lower() for s in sents_a)
        set_b = set(s.lower() for s in sents_b)

        # Removed sentences
        for i, sent in enumerate(sents_a):
            if sent.lower() not in set_b:
                changes.append({
                    "type": "removed",
                    "text": sent[:200],
                    "position": i,
                })

        # Added sentences
        for i, sent in enumerate(sents_b):
            if sent.lower() not in set_a:
                changes.append({
                    "type": "added",
                    "text": sent[:200],
                    "position": i,
                })

        # Determine diff type
        if similarity >= 0.95:
            diff_type = "unchanged"
        elif similarity >= 0.5:
            diff_type = "minor_changes"
        elif similarity > 0.0:
            diff_type = "significant_changes"
        else:
            diff_type = "new"

        return {
            "diff_type": diff_type,
            "changes": changes,
            "similarity_score": round(similarity, 4),
        }

    def summarize_diffs(self, body_diffs: list[dict], max_points: int = 3) -> list[str]:
        """Generate human-readable bullet points from a list of body diffs.

        Each entry in *body_diffs* should be a dict returned by ``diff()``.
        Returns at most *max_points* bullet-point strings.
        """
        points: list[str] = []
        for diff_entry in body_diffs:
            diff_type = diff_entry.get("diff_type", "unknown")
            score = diff_entry.get("similarity_score", 0.0)
            changes = diff_entry.get("changes", [])

            # Summary line per entry
            n_added = sum(1 for c in changes if c.get("type") == "added")
            n_removed = sum(1 for c in changes if c.get("type") == "removed")

            summary_parts = []
            if n_added:
                summary_parts.append(f"{n_added} sentence(s) added")
            if n_removed:
                summary_parts.append(f"{n_removed} sentence(s) removed")

            if not changes:
                if diff_type == "unchanged":
                    summary_parts.append("No changes")
                elif diff_type == "new":
                    summary_parts.append("Entirely new content")
                else:
                    summary_parts.append(f"Similarity: {score:.0%}")
            else:
                summary_parts.append(f"Similarity: {score:.0%}")

            base = f"{diff_type.replace('_', ' ').title()}: {'; '.join(summary_parts)}"
            points.append(base)

            # Add first few concrete change descriptions
            for change in changes[:max_points]:
                text = change.get("text", "")[:80]
                ctype = change.get("type", "changed")
                points.append(f"  • {ctype}: \"{text}...\"")
                if len(points) >= max_points + 1:  # +1 for summary
                    break

            if len(points) >= max_points + 1:
                break

        return points[:max_points]
