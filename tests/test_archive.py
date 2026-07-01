"""Tests for the Archive System.

Tests are isolated from the real DB by wrapping ``get_connection`` with an
in-memory SQLite connection.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

# Ensure src is on the path
SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

import pytest

# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _in_memory_db(monkeypatch):
    """Replace ``get_connection`` with an in-memory SQLite connection.

    This keeps archive tests isolated from the real DB state.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS wf_archived_editions (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            edition_number INTEGER,
            date TEXT,
            subject TEXT,
            body_html TEXT,
            body_markdown TEXT,
            archive_path TEXT,
            permalink TEXT,
            citation_count INTEGER,
            source_count INTEGER,
            item_count INTEGER,
            created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_wf_archived_editions_run
            ON wf_archived_editions(run_id);
        CREATE INDEX IF NOT EXISTS idx_wf_archived_editions_created
            ON wf_archived_editions(created_at);
    """)
    monkeypatch.setattr("database.get_connection", lambda: conn)
    monkeypatch.setattr("archive.engine.get_connection", lambda: conn)
    monkeypatch.setattr("archive.index.get_connection", lambda: conn)
    return conn


@pytest.fixture
def tmp_archive_dir():
    with tempfile.TemporaryDirectory() as td:
        yield td


@pytest.fixture
def engine(tmp_archive_dir):
    from archive.engine import ArchiveEngine
    return ArchiveEngine(archive_dir=tmp_archive_dir)


# ── Tests ──────────────────────────────────────────────────────────────


def test_store_and_get(engine):
    """Store an edition, then retrieve it by id."""
    result = engine.store(
        edition_id="test-ed-001",
        subject="Test Edition One",
        body_html="<h1>Hello</h1>",
        body_markdown="# Hello",
        run_id="run-abc",
        metadata={"citation_count": 3, "source_count": 2},
    )
    assert result["id"] == "test-ed-001"
    assert "permalink" in result
    assert "path" in result

    edition = engine.get("test-ed-001")
    assert edition["subject"] == "Test Edition One"
    assert edition["body_html"] == "<h1>Hello</h1>"
    assert edition["body_markdown"] == "# Hello"
    assert edition["citation_count"] == 3
    assert edition["source_count"] == 2


def test_list_editions(engine):
    """Store multiple editions, verify list returns them all."""
    engine.store("ed-1", "Subject 1", "<p>1</p>", "**1**", "r1", {})
    engine.store("ed-2", "Subject 2", "<p>2</p>", "**2**", "r2", {})
    engine.store("ed-3", "Subject 3", "<p>3</p>", "**3**", "r3", {})

    all_editions = engine.list(limit=10, offset=0)
    assert len(all_editions) == 3
    # Newest first
    ids = [e["id"] for e in all_editions]
    assert ids == ["ed-3", "ed-2", "ed-1"]

    # Pagination
    page = engine.list(limit=2, offset=0)
    assert len(page) == 2


def test_delete(engine):
    """Delete removes the edition from the DB."""
    engine.store("del-ed", "Delete Me", "<p>x</p>", "**x**", "r-del", {})
    assert engine.get("del-ed")  # exists

    deleted = engine.delete("del-ed")
    assert deleted is True

    assert not engine.get("del-ed")  # gone

    # Deleting a non-existent id returns False
    assert engine.delete("nonexistent") is False


def test_get_latest(engine):
    """Latest edition returned correctly."""
    # No editions yet
    assert engine.get_latest() is None

    engine.store("ed-a", "Earlier", "<p>A</p>", "**A**", "r1", {})
    latest = engine.get_latest()
    assert latest["id"] == "ed-a"

    engine.store("ed-b", "Later", "<p>B</p>", "**B**", "r2", {})
    latest = engine.get_latest()
    assert latest["id"] == "ed-b"


def test_get_by_run(engine):
    """Get edition by run ID."""
    engine.store("run-ed", "By Run", "<p>x</p>", "**x**", "run-special", {})
    found = engine.get_by_run("run-special")
    assert found is not None
    assert found["id"] == "run-ed"

    assert engine.get_by_run("nonexistent-run") is None


def test_rss_generate():
    """RSS output is valid XML."""
    from archive.rss import RSSFeed

    editions = [
        {
            "id": "ed-001",
            "subject": "First Edition",
            "body_html": "<p>Hello</p>",
            "body_markdown": "Hello",
            "permalink": "https://hermes.local/archives/ed-001",
            "created_at": "2025-03-01T12:00:00+00:00",
        },
        {
            "id": "ed-002",
            "subject": "Second Edition",
            "body_html": "<p>World</p>",
            "body_markdown": "World",
            "permalink": "https://hermes.local/archives/ed-002",
            "created_at": "2025-03-02T12:00:00+00:00",
        },
    ]

    rss = RSSFeed().generate(editions)
    assert '<?xml version="1.0" encoding="UTF-8"?>' in rss
    assert "<rss " in rss
    assert 'version="2.0"' in rss
    assert "<title>First Edition</title>" in rss
    assert "<title>Second Edition</title>" in rss
    assert "<link>https://hermes.local/archives/ed-001</link>" in rss


def test_rebuild_index(engine, tmp_archive_dir, _in_memory_db, monkeypatch):
    """index.html is generated correctly."""
    from archive.index import ArchiveIndex

    # Store one edition first
    engine.store("idx-ed", "Index Test", "<p>Hello</p>", "# Hello", "r-idx", {})

    index = ArchiveIndex(archive_dir=tmp_archive_dir)
    path = index.rebuild()

    assert Path(path).exists()
    content = Path(path).read_text("utf-8")
    assert "Newsletter Archive" in content  # title
    assert "Index Test" in content  # subject
    assert "# Hello" in content  # excerpt
    assert "1 edition" in content

    # Empty archive (separate DB to avoid cross-contamination)
    empty_conn = sqlite3.connect(":memory:")
    empty_conn.row_factory = sqlite3.Row
    empty_conn.executescript("""
        CREATE TABLE IF NOT EXISTS wf_archived_editions (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            edition_number INTEGER,
            date TEXT,
            subject TEXT,
            body_html TEXT,
            body_markdown TEXT,
            archive_path TEXT,
            permalink TEXT,
            citation_count INTEGER,
            source_count INTEGER,
            item_count INTEGER,
            created_at TEXT
        );
    """)
    with monkeypatch.context() as m:
        m.setattr("archive.index.get_connection", lambda: empty_conn)
        empty_dir = Path(tmp_archive_dir) / "empty"
        empty_dir.mkdir()
        empty_index = ArchiveIndex(archive_dir=str(empty_dir))
        path2 = empty_index.rebuild()
        content2 = Path(path2).read_text("utf-8")
        assert "No editions have been archived yet" in content2


def test_archive_engine_files_on_disk(engine, tmp_archive_dir):
    """Verify files are written to disk correctly."""
    result = engine.store(
        "disk-ed",
        "Disk Edition",
        "<h1>Disk</h1>",
        "# Disk",
        "r-disk",
        {"edition_number": 1},
    )

    edition_dir = Path(tmp_archive_dir) / "disk-ed"
    assert edition_dir.exists()
    assert (edition_dir / "index.html").read_text("utf-8") == "<h1>Disk</h1>"
    assert (edition_dir / "body.md").read_text("utf-8") == "# Disk"
    meta = json.loads((edition_dir / "metadata.json").read_text("utf-8"))
    assert meta["edition_number"] == 1


def test_archive_step_type_routing():
    """Verify archive_edition and rebuild_archive_index are recognised step types."""
    from workflow_executor import WorkflowExecutor

    executor = WorkflowExecutor()

    # These step types should exist as string literals in the executor
    assert hasattr(executor, "_execute_archive_edition_step")
    assert hasattr(executor, "_execute_rebuild_archive_index_step")

    # The step type constants are used in the routing if/elif chain
    # We verify by checking the step type names are present in the source
    import inspect
    source = inspect.getsource(type(executor))
    assert "\"archive_edition\"" in source
    assert "\"rebuild_archive_index\"" in source
