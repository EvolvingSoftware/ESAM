"""CitationResolver — Text rewriting and citation verification.

Replaces ``[SXXX]`` markers in generated text with hyperlinked HTML
and verifies that all referenced citations exist in the citation map.
"""

import re
from typing import Any

__all__ = ["CitationResolver"]

# Matches [S001], [S042], [S999] etc.
CITATION_PATTERN = re.compile(r"\[(S\d{3,5})\]")


class CitationResolver:
    """Resolve citation markers in text and verify citation integrity."""

    @staticmethod
    def resolve_text(
        text: str, citation_map: dict[str, dict[str, str]]
    ) -> str:
        """Replace ``[SXXX]`` markers with hyperlinked HTML.

        Each marker ``[S001]`` is replaced with::

            <a href="url">[S001]</a>

        If the citation ID is not found in the map, the marker is left
        unchanged (no broken links).

        Args:
            text: Text possibly containing ``[SXXX]`` markers.
            citation_map: Dict of ``{citation_id: {url: ..., title: ...}}``.

        Returns:
            Text with hyperlinked citation markers.
        """

        def _replacer(match: re.Match) -> str:
            cid = match.group(1)
            entry = citation_map.get(cid)
            if entry and entry.get("url"):
                url = entry["url"]
                return f'<a href="{url}">[{cid}]</a>'
            return match.group(0)  # leave unchanged

        return CITATION_PATTERN.sub(_replacer, text)

    @staticmethod
    def verify_citations(
        text: str, citation_map: dict[str, dict[str, str]]
    ) -> dict[str, Any]:
        """Check that all ``[SXXX]`` references in text exist in the map.

        Args:
            text: Text with ``[SXXX]`` markers.
            citation_map: Dict of ``{citation_id: {url: ..., title: ...}}``.

        Returns:
            Dict with keys:
            - ``valid`` (bool): True if all references exist.
            - ``missing_ids`` (list[str]): IDs referenced but not in map.
            - ``extra_ids`` (list[str]): IDs in map but not referenced.
            - ``found_ids`` (list[str]): IDs both referenced and in map.
        """
        referenced = set(CITATION_PATTERN.findall(text))
        available = set(citation_map.keys())

        missing_ids = sorted(referenced - available)
        found_ids = sorted(referenced & available)
        extra_ids = sorted(available - referenced)

        return {
            "valid": len(missing_ids) == 0,
            "missing_ids": missing_ids,
            "extra_ids": extra_ids,
            "found_ids": found_ids,
            "referenced_count": len(referenced),
            "available_count": len(available),
        }
