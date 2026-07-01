#!/usr/bin/env python3
"""Source Registry — curated source list with RSS feeds and domain authority tiers.

Provides CRUD operations for curated sources (wf_sources table) and RSS
fetching that stores raw articles in wf_source_articles.

Also provides ContentSourceManager for YAML-driven content source definitions
(wf_content_sources table) with a full fetch-and-parse pipeline.

Authority tier meanings:
    A = Primary sources / organizations (gov, edu, research)
    B = Industry publications (trade journals, major tech media)
    C = Blogs, social media, aggregators, general media

Usage:
    from source_registry import SourceRegistry
    r = SourceRegistry()
    r.create(name="Ars Technica", feed_url="https://feeds.arstechnica.com/arstechnica/index",
             domain="arstechnica.com", authority_tier="B")
    r.fetch_all()  # Fetch RSS for all enabled sources
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from database import get_connection

logger = logging.getLogger(__name__)

__all__ = ["SourceRegistry", "ContentSourceManager", "detect_authority_tier"]

# ── Schema SQL for source registry tables ─────────────────────────

SOURCE_REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS wf_sources (
    id                  TEXT PRIMARY KEY,
    workflow_id         TEXT,
    name                TEXT NOT NULL,
    feed_url            TEXT NOT NULL DEFAULT '',
    domain              TEXT NOT NULL DEFAULT '',
    authority_tier      TEXT NOT NULL DEFAULT 'B' CHECK(authority_tier IN ('A','B','C')),
    fetch_interval_mins INTEGER NOT NULL DEFAULT 1440,
    last_fetched        TEXT,
    enabled             INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wf_sources_workflow ON wf_sources(workflow_id);
CREATE INDEX IF NOT EXISTS idx_wf_sources_enabled ON wf_sources(enabled);
CREATE INDEX IF NOT EXISTS idx_wf_sources_authority ON wf_sources(authority_tier);

CREATE TABLE IF NOT EXISTS wf_source_articles (
    id              TEXT PRIMARY KEY,
    source_id       TEXT NOT NULL REFERENCES wf_sources(id) ON DELETE CASCADE,
    source_name     TEXT NOT NULL DEFAULT '',
    url             TEXT NOT NULL UNIQUE,
    title           TEXT NOT NULL DEFAULT '',
    content_snippet TEXT DEFAULT '',
    published_date  TEXT,
    fetched_at      TEXT NOT NULL,
    score           REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_wf_articles_source ON wf_source_articles(source_id);
CREATE INDEX IF NOT EXISTS idx_wf_articles_url ON wf_source_articles(url);
CREATE INDEX IF NOT EXISTS idx_wf_articles_score ON wf_source_articles(score);
"""

# ── Schema SQL for content source tables ────────────────────────

CONTENT_SOURCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS wf_content_sources (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    type                TEXT NOT NULL CHECK(type IN ('rss','http_api','http_html','http_xml')),
    source_config_json  TEXT NOT NULL DEFAULT '{}',
    authority_tier      TEXT NOT NULL DEFAULT 'B' CHECK(authority_tier IN ('A','B','C')),
    interval_minutes    INTEGER NOT NULL DEFAULT 60,
    last_fetched        TEXT,
    enabled             INTEGER NOT NULL DEFAULT 1,
    rate_limit_per_minute INTEGER DEFAULT 0,
    rate_limit_burst      INTEGER DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wf_content_sources_enabled ON wf_content_sources(enabled);
CREATE INDEX IF NOT EXISTS idx_wf_content_sources_authority ON wf_content_sources(authority_tier);
CREATE INDEX IF NOT EXISTS idx_wf_content_sources_type ON wf_content_sources(type);

CREATE TABLE IF NOT EXISTS wf_content_source_items (
    id                TEXT PRIMARY KEY,
    source_id         TEXT NOT NULL REFERENCES wf_content_sources(id) ON DELETE CASCADE,
    fetch_run_id      TEXT NOT NULL DEFAULT '',
    url               TEXT NOT NULL UNIQUE,
    title             TEXT NOT NULL DEFAULT '',
    author            TEXT DEFAULT '',
    body_raw          TEXT DEFAULT '',
    body_extracted    TEXT DEFAULT '',
    entities_json     TEXT DEFAULT '[]',
    keywords_json     TEXT DEFAULT '[]',
    published_date    TEXT,
    fetched_at        TEXT NOT NULL,
    source_hash       TEXT DEFAULT '',
    citation_id       TEXT DEFAULT '',
    score             REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_wf_content_items_source ON wf_content_source_items(source_id);
CREATE INDEX IF NOT EXISTS idx_wf_content_items_url ON wf_content_source_items(url);
CREATE INDEX IF NOT EXISTS idx_wf_content_items_score ON wf_content_source_items(score);
CREATE INDEX IF NOT EXISTS idx_wf_content_items_fetch_run ON wf_content_source_items(fetch_run_id);
"""


# ── Domain authority heuristics ───────────────────────────────────

def detect_authority_tier(domain: str) -> str:
    """Detect authority tier for a domain based on heuristics.

    Args:
        domain: The domain name (e.g. 'reuters.com', 'blog.example.com')

    Returns:
        'A' for primary sources (gov, edu, research, major newswires)
        'B' for industry publications
        'C' for blogs, social media, aggregators
    """
    domain = domain.lower().strip()

    # Tier A: government, education, research, major newswires
    a_domains = {
        ".gov", ".gov.au", ".gov.uk", ".gov.ca", ".gov.in",
        ".edu", ".edu.au", ".edu.uk",
        ".mil", ".mil.uk",
        ".int",          # international organizations
        ".ac.uk",        # UK academic
        ".ac.in",        # Indian academic
        ".ac.kr",        # Korean academic
    }
    a_keywords = {
        "who.int", "un.org", "imf.org", "worldbank.org", "oecd.org",
        "reuters.com", "ap.org", "apnews.com", "afp.com",
        "bbc.com", "bbc.co.uk", "npr.org", "pbs.org",
        "bloomberg.com", "wsj.com", "ft.com", "economist.com",
        "nature.com", "science.org", "sciencedaily.com",
        "nih.gov", "cdc.gov", "nasa.gov", "noaa.gov",
        "europa.eu", "ec.europa.eu",
        "research.google", "research.microsoft.com",
        "arxiv.org", "pubmed.ncbi.nlm.nih.gov",
        "whitehouse.gov", "congress.gov", "state.gov",
    }

    # Tier B: industry publications, major tech media
    b_domains = {
        "techcrunch.com", "theverge.com", "wired.com", "arstechnica.com",
        "zdnet.com", "cnet.com", "theregister.com",
        "infoworld.com", "computerworld.com",
        "hbr.org", "forbes.com", "inc.com",
        "venturebeat.com", "axios.com", "politico.com",
        "theguardian.com", "nytimes.com", "washingtonpost.com",
        "latimes.com", "chicagotribune.com",
        "ieee.org", "acm.org",
        "gartner.com", "idc.com", "forrester.com",
        "stackoverflow.blog", "github.blog", "netflixtechblog.com",
        "aws.amazon.com/blog", "cloud.google.com/blog",
        "openai.com/blog", "anthropic.com/blog",
        "meta.com/blog", "news.ycombinator.com",
        "dev.to",
    }

    # Check for A-tier TLDs
    for tld in a_domains:
        if domain.endswith(tld):
            return "A"

    # Check for A-tier keywords (exact or subdomain)
    for kw in a_keywords:
        if domain == kw or domain.endswith("." + kw):
            return "A"

    # Check for B-tier domains
    for bd in b_domains:
        if domain == bd or domain.endswith("." + bd):
            return "B"

    # Check for blog/C-tier indicators
    c_indicators = [
        ".blogspot.", ".wordpress.", ".tumblr.", ".substack.",
        "medium.com", "dev.to", "hashnode.dev",
        "newsletter", ".blog.", "/blog/",
    ]
    for ind in c_indicators:
        if ind in domain:
            return "C"

    # Default to B for unknown but legitimate-looking domains
    return "B"


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict:
    return dict(row) if row else {}


def _rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]


# ── Source Registry ───────────────────────────────────────────────

class SourceRegistry:
    """Registry for curated sources with RSS feed management.

    Provides CRUD for sources and RSS fetch capabilities.
    Uses the shared database connection from database.py.
    """

    def __init__(self) -> None:
        self._ensure_schema()

    @staticmethod
    def _ensure_schema() -> None:
        """Ensure source registry tables exist."""
        conn = get_connection()
        conn.executescript(SOURCE_REGISTRY_SCHEMA)
        conn.commit()

    # ── Source CRUD ─────────────────────────────────────────────

    def list(self, workflow_id: str | None = None,
             authority_tier: str | None = None,
             enabled_only: bool = False) -> list[dict[str, Any]]:
        """List sources, optionally filtered.

        Args:
            workflow_id: Filter by workflow (None = all workflows)
            authority_tier: Filter by tier ('A', 'B', 'C')
            enabled_only: Only return enabled sources

        Returns:
            List of source dicts
        """
        conn = get_connection()
        query = "SELECT * FROM wf_sources WHERE 1=1"
        params: list[Any] = []

        if workflow_id is not None:
            query += " AND (workflow_id = ? OR workflow_id IS NULL)"
            params.append(workflow_id)
        if authority_tier is not None:
            query += " AND authority_tier = ?"
            params.append(authority_tier)
        if enabled_only:
            query += " AND enabled = 1"

        query += " ORDER BY authority_tier ASC, name ASC"
        rows = conn.execute(query, params).fetchall()
        return _rows_to_dicts(rows)

    def get(self, source_id: str) -> dict[str, Any] | None:
        """Get a single source by ID."""
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM wf_sources WHERE id = ?", (source_id,)
        ).fetchone()
        return _row_to_dict(row)

    def create(
        self,
        name: str,
        feed_url: str = "",
        domain: str = "",
        authority_tier: str | None = None,
        workflow_id: str | None = None,
        fetch_interval_mins: int = 1440,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Create a new source.

        Args:
            name: Human-readable name for the source
            feed_url: RSS feed URL
            domain: Domain name (auto-detected from feed_url if empty)
            authority_tier: 'A', 'B', or 'C' (auto-detected if None)
            workflow_id: Optional workflow association
            fetch_interval_mins: Minutes between RSS fetches (default 1440 = 1 day)
            enabled: Whether the source is active

        Returns:
            Created source dict

        Raises:
            ValueError: If name is empty
        """
        if not name or not name.strip():
            raise ValueError("Source name is required")

        # Auto-detect domain from feed_url
        if not domain and feed_url:
            domain = self._extract_domain(feed_url)

        # Auto-detect authority tier
        if authority_tier is None:
            authority_tier = detect_authority_tier(domain or name)

        if authority_tier not in ("A", "B", "C"):
            raise ValueError(f"Invalid authority_tier '{authority_tier}'. Must be 'A', 'B', or 'C'")

        source_id = _new_id()
        now = _now()

        conn = get_connection()
        conn.execute(
            """INSERT INTO wf_sources
               (id, workflow_id, name, feed_url, domain, authority_tier,
                fetch_interval_mins, enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (source_id, workflow_id, name.strip(), feed_url, domain,
             authority_tier, fetch_interval_mins, 1 if enabled else 0,
             now, now),
        )
        conn.commit()

        result = self.get(source_id)
        assert result is not None
        logger.info("Created source: %s (tier=%s, domain=%s)", name, authority_tier, domain)
        return result

    def update(self, source_id: str, **kwargs: Any) -> dict[str, Any]:
        """Update a source. Only known fields are accepted.

        Args:
            source_id: ID of the source to update
            **kwargs: Fields to update (name, feed_url, domain, authority_tier,
                     fetch_interval_mins, enabled, workflow_id)

        Returns:
            Updated source dict

        Raises:
            ValueError: If authority_tier is invalid
        """
        allowed = {
            "name", "feed_url", "domain", "authority_tier",
            "fetch_interval_mins", "enabled", "workflow_id",
        }
        updates: dict[str, Any] = {}
        for k, v in kwargs.items():
            if k in allowed:
                if k == "authority_tier" and v is not None and v not in ("A", "B", "C"):
                    raise ValueError(f"Invalid authority_tier '{v}'")
                if k == "enabled" and isinstance(v, bool):
                    v = 1 if v else 0
                updates[k] = v

        if not updates:
            return self.get(source_id) or {}

        updates["updated_at"] = _now()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [source_id]

        conn = get_connection()
        conn.execute(f"UPDATE wf_sources SET {set_clause} WHERE id = ?", vals)
        conn.commit()

        result = self.get(source_id)
        assert result is not None
        return result

    def delete(self, source_id: str) -> bool:
        """Delete a source and its articles.

        Args:
            source_id: ID of the source to delete

        Returns:
            True if deleted, False if not found
        """
        conn = get_connection()
        # Articles are cascade-deleted via FK
        cur = conn.execute("DELETE FROM wf_sources WHERE id = ?", (source_id,))
        conn.commit()
        deleted = cur.rowcount > 0
        if deleted:
            logger.info("Deleted source: %s", source_id)
        return deleted

    # ── RSS Fetching ────────────────────────────────────────────

    def fetch_all(self, force: bool = False) -> list[dict[str, Any]]:
        """Fetch RSS feeds for all enabled sources.

        Args:
            force: If True, bypass fetch_interval check

        Returns:
            List of fetch result dicts, one per source
        """
        sources = self.list(enabled_only=True)
        results: list[dict[str, Any]] = []
        now = _now()

        for source in sources:
            source_id = source["id"]

            # Check fetch interval if not forced
            if not force and source.get("last_fetched"):
                try:
                    last = datetime.fromisoformat(source["last_fetched"])
                    elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 60
                    if elapsed < source.get("fetch_interval_mins", 1440):
                        continue
                except (ValueError, TypeError):
                    pass  # Proceed if we can't parse the timestamp

            result = self.fetch_url(source["feed_url"], source_id, source["name"])
            results.append(result)

            # Update last_fetched timestamp
            conn = get_connection()
            conn.execute(
                "UPDATE wf_sources SET last_fetched = ?, updated_at = ? WHERE id = ?",
                (now, now, source_id),
            )
            conn.commit()

        logger.info("Fetched RSS for %d sources", len(results))
        return results

    def fetch_url(self, feed_url: str, source_id: str = "",
                  source_name: str = "") -> dict[str, Any]:
        """Fetch a single RSS/Atom feed URL and store articles.

        Args:
            feed_url: URL of the RSS feed
            source_id: Associated source ID (optional for ad-hoc fetches)
            source_name: Source name for display

        Returns:
            Dict with status and article count
        """
        if not feed_url:
            return {"error": "no_feed_url", "articles_fetched": 0}

        try:
            import feedparser  # type: ignore[import-untyped]

            parsed = feedparser.parse(feed_url)
        except Exception as exc:
            logger.warning("Failed to parse feed %s: %s", feed_url, exc)
            return {"error": str(exc), "articles_fetched": 0, "feed_url": feed_url}

        if parsed.bozo and not parsed.entries:
            error_detail = str(parsed.bozo_exception) if parsed.bozo_exception else "unknown"
            logger.warning("Bozo feed %s: %s", feed_url, error_detail)
            return {"error": f"bozo_feed: {error_detail}", "articles_fetched": 0, "feed_url": feed_url}

        now = _now()
        conn = get_connection()
        articles_stored = 0

        for entry in parsed.entries:
            article_url = entry.get("link", "")
            if not article_url:
                continue

            title = entry.get("title", "")
            content_snippet = self._extract_snippet(entry)
            published = self._parse_published(entry)

            try:
                article_id = _new_id()
                conn.execute(
                    """INSERT OR IGNORE INTO wf_source_articles
                       (id, source_id, source_name, url, title, content_snippet,
                        published_date, fetched_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (article_id, source_id, source_name, article_url,
                     title, content_snippet, published, now),
                )
                if conn.total_changes:
                    # Check if actually inserted (INSERT OR IGNORE may skip dups)
                    pass
                articles_stored += 1
            except Exception as exc:
                logger.debug("Failed to store article '%s': %s", title, exc)

        conn.commit()

        return {
            "status": "ok",
            "feed_url": feed_url,
            "source_id": source_id or "",
            "source_name": source_name or "",
            "articles_fetched": len(parsed.entries),
            "articles_stored": articles_stored,
            "feed_title": parsed.feed.get("title", ""),
        }

    def get_articles(self, source_id: str | None = None,
                     limit: int = 100,
                     min_score: float | None = None) -> list[dict[str, Any]]:
        """Get stored articles, optionally filtered by source.

        Args:
            source_id: Optional source filter
            limit: Max articles to return (default 100)
            min_score: Minimum score filter

        Returns:
            List of article dicts
        """
        conn = get_connection()
        query = "SELECT * FROM wf_source_articles WHERE 1=1"
        params: list[Any] = []

        if source_id is not None:
            query += " AND source_id = ?"
            params.append(source_id)
        if min_score is not None:
            query += " AND score >= ?"
            params.append(min_score)

        query += " ORDER BY published_date DESC, fetched_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        return _rows_to_dicts(rows)

    def delete_articles(self, source_id: str | None = None) -> int:
        """Delete articles, optionally for a specific source.

        Args:
            source_id: If provided, only delete articles for this source

        Returns:
            Number of deleted articles
        """
        conn = get_connection()
        if source_id:
            cur = conn.execute(
                "DELETE FROM wf_source_articles WHERE source_id = ?", (source_id,)
            )
        else:
            cur = conn.execute("DELETE FROM wf_source_articles")
        conn.commit()
        return cur.rowcount

    # ── Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _extract_domain(url: str) -> str:
        """Extract domain from a URL."""
        match = re.match(r"(?:https?://)?(?:www\.)?([^/]+)", url)
        if match:
            return match.group(1).lower()
        return ""

    @staticmethod
    def _extract_snippet(entry) -> str:
        """Extract content snippet from a feed entry."""
        # Try summary first
        snippet = entry.get("summary", "")
        if snippet:
            return _strip_html(snippet)[:500]

        # Try content (list of FeedParserDict objects)
        content_raw = getattr(entry, "content", None) or entry.get("content", [])
        if content_raw and isinstance(content_raw, list) and len(content_raw) > 0:
            first = content_raw[0]
            if isinstance(first, dict):
                value = first.get("value", "")
                if value:
                    return _strip_html(value)[:500]

        # Try description
        desc = entry.get("description", "")
        if desc:
            return _strip_html(desc)[:500]

        return ""

    @staticmethod
    def _parse_published(entry) -> str:
        """Parse published date from a feed entry."""
        # Try standard feedparser parsed_parsed
        pp = entry.get("published_parsed")
        if pp:
            try:
                import time
                return datetime.fromtimestamp(time.mktime(pp), tz=timezone.utc).isoformat()
            except Exception:
                pass

        # Try raw string
        raw = entry.get("published", "")
        if raw:
            return raw

        # Try updated
        raw = entry.get("updated", "")
        if raw:
            return raw

        return ""


def _strip_html(text: str) -> str:
    """Strip HTML tags from text."""
    cleaned = re.sub(r"<[^>]+>", "", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


# ══════════════════════════════════════════════════════════════════════
# ContentSourceManager — YAML-driven source definitions
# ══════════════════════════════════════════════════════════════════════

class ContentSourceManager:
    """Manages YAML-driven content source definitions for the web content acquisition pipeline.

    Each ContentSource defines *how* to fetch and *how* to parse a content surface
    (RSS feed, JSON API, HTML page, or XML endpoint).  Sources are stored in the
    ``wf_content_sources`` table alongside ``wf_sources`` (the older RSS-only registry).

    Usage::

        m = ContentSourceManager()
        source = m.create_from_yaml({
            "id": "hackernews-top",
            "name": "Hacker News (Top)",
            "type": "http_api",
            "url": "https://hacker-news.firebaseio.com/v0/topstories.json",
            "response_type": "json_array",
            "fields": {"url": "url", "title": "title", "author": "by"},
            "authority_tier": "A",
            "interval_minutes": 30,
        })
        items = m.fetch_and_parse(source["id"], fetcher_engine, parser_engine)
    """

    def __init__(self) -> None:
        self._ensure_schema()

    @staticmethod
    def _ensure_schema() -> None:
        """Ensure content source tables exist."""
        conn = get_connection()
        conn.executescript(CONTENT_SOURCE_SCHEMA)
        conn.commit()

    # ── CRUD Operations ─────────────────────────────────────────────

    def create_from_yaml(self, yaml_def: dict) -> dict:
        """Create a content source from a YAML definition dict.

        Args:
            yaml_def: Source definition dict with keys:
                - ``name`` (required): Human-readable name
                - ``type`` (required): One of ``rss``, ``http_api``, ``http_html``, ``http_xml``
                - ``url`` / ``url_template``: URL with optional {variable} placeholders
                - ``url_params``: Dict of arrays to iterate over (e.g. ``{"subreddit": [...]}``)
                - ``response_type``: ``json_array``, ``json_path``, ``xpath``, ``rss``, ``html_selector``, ``id_list``
                - ``response_path``: Path expression for extraction
                - ``fields``: Field mapping dict ``{standard_name: source_field}``
                - ``authority_tier``: ``A``, ``B``, or ``C``
                - ``interval_minutes``: Fetch interval in minutes
                - ``rate_limit_per_minute``, ``rate_limit_burst``
                - Any other keys go into ``source_config_json``

        Returns:
            Created source dict

        Raises:
            ValueError: If required fields are missing
        """
        name = yaml_def.get("name", "").strip()
        if not name:
            raise ValueError("Content source 'name' is required")

        stype = yaml_def.get("type", "")
        if stype not in ("rss", "http_api", "http_html", "http_xml"):
            raise ValueError(
                f"Invalid type '{stype}'. Must be one of: rss, http_api, http_html, http_xml"
            )

        authority_tier = yaml_def.get("authority_tier", "B")
        if authority_tier not in ("A", "B", "C"):
            raise ValueError(f"Invalid authority_tier '{authority_tier}'. Must be A, B, or C")

        source_id = _new_id()
        now = _now()

        # Build the full source config JSON from the YAML definition
        # Keep all keys except the ones stored as top-level columns
        top_level_keys = {
            "name", "type", "authority_tier", "interval_minutes",
            "rate_limit_per_minute", "rate_limit_burst",
        }
        source_config = {k: v for k, v in yaml_def.items() if k not in top_level_keys}
        source_config_json = json.dumps(source_config)

        interval = int(yaml_def.get("interval_minutes", 60))
        rate_limit = int(yaml_def.get("rate_limit_per_minute", 0))
        burst = int(yaml_def.get("rate_limit_burst", 0))

        conn = get_connection()
        conn.execute(
            """INSERT INTO wf_content_sources
               (id, name, type, source_config_json, authority_tier,
                interval_minutes, enabled, rate_limit_per_minute,
                rate_limit_burst, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)""",
            (source_id, name, stype, source_config_json, authority_tier,
             interval, rate_limit, burst, now, now),
        )
        conn.commit()

        result = self.get(source_id)
        assert result is not None
        logger.info("Created content source: %s (type=%s, tier=%s)", name, stype, authority_tier)
        return result

    def list(self) -> list[dict]:
        """List all content sources.

        Returns:
            List of content source dicts
        """
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM wf_content_sources ORDER BY authority_tier ASC, name ASC"
        ).fetchall()
        return _rows_to_dicts(rows)

    def get(self, source_id: str) -> dict | None:
        """Get a single content source by ID.

        Args:
            source_id: The content source ID

        Returns:
            Source dict or None if not found
        """
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM wf_content_sources WHERE id = ?", (source_id,)
        ).fetchone()
        if not row:
            return None
        return dict(row)

    def update(self, source_id: str, yaml_def: dict) -> dict:
        """Update a content source from a YAML definition dict.

        Only known top-level columns are updated directly; everything else
        goes into ``source_config_json``.

        Args:
            source_id: ID of the source to update
            yaml_def: Updated source definition dict

        Returns:
            Updated source dict

        Raises:
            ValueError: If the source doesn't exist
        """
        existing = self.get(source_id)
        if not existing:
            raise ValueError(f"Content source not found: {source_id}")

        top_level = {
            "name", "type", "authority_tier", "interval_minutes",
            "rate_limit_per_minute", "rate_limit_burst",
        }

        updates: dict[str, Any] = {}
        if "name" in yaml_def:
            updates["name"] = yaml_def["name"].strip()
        if "type" in yaml_def:
            stype = yaml_def["type"]
            if stype not in ("rss", "http_api", "http_html", "http_xml"):
                raise ValueError(f"Invalid type '{stype}'")
            updates["type"] = stype
        if "authority_tier" in yaml_def:
            tier = yaml_def["authority_tier"]
            if tier not in ("A", "B", "C"):
                raise ValueError(f"Invalid authority_tier '{tier}'")
            updates["authority_tier"] = tier
        if "interval_minutes" in yaml_def:
            updates["interval_minutes"] = int(yaml_def["interval_minutes"])
        if "rate_limit_per_minute" in yaml_def:
            updates["rate_limit_per_minute"] = int(yaml_def["rate_limit_per_minute"])
        if "rate_limit_burst" in yaml_def:
            updates["rate_limit_burst"] = int(yaml_def["rate_limit_burst"])

        # Rebuild source_config_json from the full yaml_def
        source_config = {k: v for k, v in yaml_def.items() if k not in top_level}
        updates["source_config_json"] = json.dumps(source_config)

        updates["updated_at"] = _now()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [source_id]

        conn = get_connection()
        conn.execute(f"UPDATE wf_content_sources SET {set_clause} WHERE id = ?", vals)
        conn.commit()

        result = self.get(source_id)
        assert result is not None
        logger.info("Updated content source: %s", result["name"])
        return result

    def delete(self, source_id: str) -> bool:
        """Delete a content source and its items.

        Args:
            source_id: ID of the source to delete

        Returns:
            True if deleted, False if not found
        """
        conn = get_connection()
        # Items are cascade-deleted via FK
        cur = conn.execute(
            "DELETE FROM wf_content_sources WHERE id = ?", (source_id,)
        )
        conn.commit()
        deleted = cur.rowcount > 0
        if deleted:
            logger.info("Deleted content source: %s", source_id)
        return deleted

    # ── Fetch and Parse Pipeline ─────────────────────────────────────

    def fetch_and_parse(
        self,
        source_id: str,
        fetcher_engine: Any = None,
        parser_engine: Any = None,
    ) -> list[dict]:
        """Fetch a content source URL and parse the response into items.

        This is the main pipeline method:
        1. Load source definition from DB
        2. Expand URL params (iterate over arrays)
        3. Fetch each URL via FetcherEngine
        4. Parse each response via ParserEngine
        5. Store items in wf_content_source_items
        6. Return items

        Args:
            source_id: ID of the content source to fetch
            fetcher_engine: A :class:`~fetcher.engine.FetcherEngine` instance.
                Created fresh if not provided.
            parser_engine: A :class:`~parser.engine.ParserEngine` instance.
                Created fresh if not provided.

        Returns:
            List of parsed item dicts with standard fields
                (url, title, content, author, published_date, source_fields)
        """
        source = self.get(source_id)
        if not source:
            raise ValueError(f"Content source not found: {source_id}")

        if not source.get("enabled"):
            logger.info("Content source %s is disabled, skipping", source_id)
            return []

        if fetcher_engine is None:
            from fetcher.engine import FetcherEngine
            config = json.loads(source.get("source_config_json", "{}"))
            proxy = config.get("proxy")
            if proxy == "camoufox":
                fetcher_engine = FetcherEngine(proxy_url="http://130.61.44.207:3211")
            else:
                fetcher_engine = FetcherEngine()
        else:
            config = json.loads(source.get("source_config_json", "{}"))

        if parser_engine is None:
            from parser.engine import ParserEngine
            parser_engine = ParserEngine()

        # Determine URLs to fetch
        urls = self._build_urls(config, source)
        if not urls:
            logger.warning("No URLs to fetch for content source %s", source.get("name"))
            return []

        fetch_run_id = _new_id()
        now = _now()
        all_items: list[dict] = []
        conn = get_connection()

        for url in urls:
            # Fetch
            fetch_result = fetcher_engine.fetch(
                url=url,
                method=config.get("method", "GET"),
                headers=config.get("headers"),
                timeout=config.get("timeout", 30),
            )

            if fetch_result.get("error"):
                logger.warning("Fetch failed for %s: %s", url, fetch_result["error"])
                continue

            response_body = fetch_result.get("body_text", "")

            # Build parser config
            parser_config = self._build_parser_config(config, source["type"])
            parser_result = parser_engine.parse(response_body, parser_config)

            items = parser_result.get("items", [])
            errors = parser_result.get("errors", [])

            if errors:
                for err in errors:
                    logger.warning("Parse error for %s: %s", url, err)

            # Normalize and store items
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_url = item.get("url", "")
                if not item_url:
                    continue

                title = str(item.get("title", ""))[:500]
                author = str(item.get("author", ""))[:200]
                content = str(item.get("content", ""))
                published = item.get("published_date", "")

                body_raw = content
                source_hash = hashlib.sha256(body_raw.encode("utf-8")).hexdigest()[:16]

                item_id = _new_id()
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO wf_content_source_items
                           (id, source_id, fetch_run_id, url, title, author,
                            body_raw, published_date, fetched_at, source_hash)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (item_id, source_id, fetch_run_id, item_url,
                         title, author, body_raw, published, now, source_hash),
                    )
                    conn.commit()
                except Exception as exc:
                    logger.debug("Failed to store item '%s': %s", title, exc)

                all_items.append(item)

        # Update last_fetched
        conn.execute(
            "UPDATE wf_content_sources SET last_fetched = ?, updated_at = ? WHERE id = ?",
            (now, now, source_id),
        )
        conn.commit()

        logger.info(
            "Content source %s: fetched %d URLs, stored %d items",
            source.get("name"), len(urls), len(all_items),
        )
        return all_items

    def _build_urls(self, config: dict, source: dict) -> list[str]:
        """Build the list of URLs to fetch from a source definition.

        Handles ``{variable}`` substitution in ``url`` / ``url_template``
        and ``url_params`` iteration.

        Returns:
            List of resolved URL strings
        """
        # Determine URL field
        url = config.get("url") or config.get("url_template", "")
        if not url:
            return []

        url_params = config.get("url_params", {})
        if not url_params:
            # Single URL, no param iteration
            return [url]

        # Find the first param array to iterate over
        # e.g. {"subreddit": ["OpenAI", "ClaudeAI", ...]}
        param_values: list[str] = []
        param_key = ""
        for k, vals in url_params.items():
            if isinstance(vals, list) and vals:
                param_key = k
                param_values = [str(v) for v in vals]
                break

        if not param_values:
            return [url]

        # Generate URLs by substituting each param value
        urls = []
        for val in param_values:
            resolved = url.replace("{" + param_key + "}", val)
            urls.append(resolved)

        return urls

    @staticmethod
    def _build_parser_config(config: dict, source_type: str) -> dict:
        """Build a ParserEngine-compatible parser config from a source definition.

        Maps the source's ``response_type`` to the parser type and config.

        Args:
            config: The source configuraion dict (from ``source_config_json``)
            source_type: The source type (``rss``, ``http_api``, etc.)

        Returns:
            Parser config dict compatible with :meth:`ParserEngine.parse`
        """
        response_type = config.get("response_type", "")
        response_path = config.get("response_path", "")
        fields = config.get("fields", {})

        # Determine parser type
        if response_type == "rss" or source_type == "rss":
            ptype = "rss"
            pconfig = {"field_map": fields}
        elif response_type == "json_array":
            ptype = "jsonpath"
            pconfig = {"path": "$", "field_map": fields}
        elif response_type == "json_path":
            ptype = "jsonpath"
            pconfig = {"path": response_path or "$", "field_map": fields}
        elif response_type == "xpath":
            ptype = "xpath"
            pconfig = {
                "path": response_path or "//item",
                "field_map": fields,
            }
        elif response_type == "html_selector":
            ptype = "html"
            pconfig = {
                "selector": response_path or "body",
                "field_map": fields,
            }
        elif response_type == "id_list":
            ptype = "id_list"
            pconfig = {
                "ids": config.get("ids", []),
                "url_template": config.get("items_url", ""),
            }
        else:
            # Default to RSS for rss type, jsonpath for everything else
            ptype = "rss" if source_type == "rss" else "jsonpath"
            pconfig = {"field_map": fields}

        return {"type": ptype, "config": pconfig}

    def get_items(
        self,
        source_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Get stored content source items, optionally filtered by source.

        Args:
            source_id: Optional source filter
            limit: Max items to return (default 100)

        Returns:
            List of item dicts
        """
        conn = get_connection()
        if source_id:
            rows = conn.execute(
                """SELECT * FROM wf_content_source_items
                   WHERE source_id = ?
                   ORDER BY published_date DESC, fetched_at DESC
                   LIMIT ?""",
                (source_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM wf_content_source_items ORDER BY fetched_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return _rows_to_dicts(rows)

    def delete_items(self, source_id: str | None = None) -> int:
        """Delete items, optionally for a specific source.

        Args:
            source_id: If provided, only delete items for this source

        Returns:
            Number of deleted items
        """
        conn = get_connection()
        if source_id:
            cur = conn.execute(
                "DELETE FROM wf_content_source_items WHERE source_id = ?",
                (source_id,),
            )
        else:
            cur = conn.execute("DELETE FROM wf_content_source_items")
        conn.commit()
        return cur.rowcount

    def bulk_import(self, sources_defs: list[dict]) -> list[dict]:
        """Bulk import content sources from a list of YAML definitions.

        Each definition is processed through :meth:`create_from_yaml`.
        If a source with the same name already exists, it's updated instead.

        Args:
            sources_defs: List of source definition dicts

        Returns:
            List of result dicts with ``action`` (``created``/``updated``/``skipped``)
            and ``name`` keys
        """
        results: list[dict] = []
        existing = self.list()
        name_map = {s["name"]: s for s in existing}

        for yaml_def in sources_defs:
            name = yaml_def.get("name", "").strip()
            if not name:
                results.append({"action": "skipped", "name": "", "reason": "no_name"})
                continue

            if name in name_map:
                self.update(name_map[name]["id"], yaml_def)
                results.append({"action": "updated", "name": name})
            else:
                self.create_from_yaml(yaml_def)
                results.append({"action": "created", "name": name})

        return results

    # ── YAML Export ──────────────────────────────────────────────────

    def to_yaml_dict(self, source_id: str) -> dict:
        """Export a content source to a YAML-friendly dict.

        Args:
            source_id: ID of the source to export

        Returns:
            Dict that can be serialized to YAML
        """
        source = self.get(source_id)
        if not source:
            raise ValueError(f"Content source not found: {source_id}")

        config = json.loads(source.get("source_config_json", "{}"))
        yaml_def = {
            "name": source["name"],
            "type": source["type"],
            "authority_tier": source["authority_tier"],
            "interval_minutes": source["interval_minutes"],
        }
        if source.get("rate_limit_per_minute"):
            yaml_def["rate_limit_per_minute"] = source["rate_limit_per_minute"]
        if source.get("rate_limit_burst"):
            yaml_def["rate_limit_burst"] = source["rate_limit_burst"]

        # Merge in config keys
        for k, v in config.items():
            yaml_def[k] = v

        return yaml_def
