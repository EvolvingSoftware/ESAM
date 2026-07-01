"""CitationMap — Build, merge, and format citation maps for LLM prompts.

Provides utilities for constructing citation maps from items, merging
multiple maps, and formatting them as context for LLM prompts.
"""

from typing import Any


class CitationMap:
    """Utilities for building, merging, and formatting citation maps."""

    @staticmethod
    def build_map(
        items: list[dict[str, Any]]
    ) -> dict[str, dict[str, str]]:
        """Build a ``{citation_id: {url, title}}`` dict from items.

        Each item must have at least ``citation_id`` and ``url``.
        If ``title`` is present it is included; otherwise an empty string
        is used.

        Args:
            items: List of dicts, each with ``citation_id`` and ``url``
                (and optionally ``title``).

        Returns:
            Dict keyed by citation_id.
        """
        result: dict[str, dict[str, str]] = {}
        for item in items:
            cid = item.get("citation_id", "")
            if not cid:
                continue
            result[cid] = {
                "url": str(item.get("url", "")),
                "title": str(item.get("title", "")),
            }
        return result

    @staticmethod
    def merge_maps(
        *maps: dict[str, dict[str, str]]
    ) -> dict[str, dict[str, str]]:
        """Merge multiple citation maps into one.

        Later maps override earlier maps when citation_id keys collide.
        This allows layering e.g. a base map with a run-specific map.

        Args:
            *maps: One or more citation map dicts.

        Returns:
            Single merged dict.
        """
        result: dict[str, dict[str, str]] = {}
        for m in maps:
            result.update(m)
        return result

    @staticmethod
    def format_for_prompt(
        citation_map: dict[str, dict[str, str]]
    ) -> str:
        """Format a citation map as LLM-friendly context text.

        Output format::

            S001: https://example.com/article "Title of Article"
            S002: https://other.com/page "Another Title"

        Args:
            citation_map: Dict of ``{citation_id: {url, title}}``.

        Returns:
            Newline-separated string suitable for prompt injection.
        """
        lines: list[str] = []
        for cid in sorted(citation_map.keys()):
            entry = citation_map[cid]
            url = entry.get("url", "")
            title = entry.get("title", "")
            if title:
                lines.append(f'{cid}: {url} "{title}"')
            else:
                lines.append(f"{cid}: {url}")
        return "\n".join(lines)
