"""Tests for the Content Extractor engine components."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

# Ensure src is on the path
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from extractor.engine import ContentExtractor
from extractor.batch import BatchExtractor
from extractor.metadata import MetadataExtractor


SIMPLE_HTML = """<html>
<head>
  <title>Test Article</title>
  <meta name="author" content="John Doe">
  <meta name="description" content="A test article">
</head>
<body>
  <article>
    <h1>Test Article Title</h1>
    <p>Hello world. This is a test article with some content.</p>
    <p>Second paragraph with more words for reading time calculation.</p>
  </article>
</body>
</html>"""

NOISY_HTML = """<html>
<head><title>Noisy Page</title></head>
<body>
  <script>alert('bad');</script>
  <style>.hidden{display:none}</style>
  <nav>Navigation links</nav>
  <article>
    <h1>Real Content</h1>
    <p>This is the real article content.</p>
  </article>
  <footer>Footer stuff</footer>
  <!-- comment -->
</body>
</html>"""

OG_HTML = """<html>
<head>
  <title>OG Test</title>
  <meta property="og:title" content="OG Title">
  <meta property="og:description" content="OG Description">
  <meta property="og:image" content="https://example.com/image.jpg">
  <meta property="og:type" content="article">
  <meta property="og:url" content="https://example.com/article">
  <meta property="og:site_name" content="Example Site">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:site" content="@example">
  <meta name="description" content="Meta Description">
  <meta name="keywords" content="test, article, demo">
  <meta name="author" content="Jane Smith">
</head>
<body><p>Body text</p></body>
</html>"""

JSONLD_HTML = """<html>
<head>
  <title>JSON-LD Test</title>
  <script type="application/ld+json">
  {"@context": "https://schema.org", "@type": "Article", "headline": "JSON-LD Article", "author": {"name": "Bob"}}
  </script>
</head>
<body><p>Content</p></body>
</html>"""

NO_META_HTML = """<html>
<head><title>No Meta</title></head>
<body><p>Just some text without any meta tags or OG data.</p></body>
</html>"""


class TestContentExtractor:
    """Test the ContentExtractor engine."""

    @pytest.fixture
    def extractor(self) -> ContentExtractor:
        return ContentExtractor()

    def test_extract_basic(self, extractor: ContentExtractor) -> None:
        """Test basic extraction from simple HTML."""
        url = "http://example.com/article"
        result = extractor.extract(SIMPLE_HTML, url)

        assert result["url"] == url
        assert result["title"] == "Test Article"
        assert "Hello world" in result["content_text"]
        assert result["author"] == "John Doe"
        assert result["word_count"] > 0
        assert result["reading_time"] > 0
        assert result["domain"] == "example.com"

    def test_extract_strips_noise(self, extractor: ContentExtractor) -> None:
        """Test that scripts, styles, nav, footer are stripped."""
        url = "http://example.com/noisy"
        result = extractor.extract(NOISY_HTML, url)

        assert "Navigation links" not in result["content_text"]
        assert "Footer stuff" not in result["content_text"]
        assert "alert" not in result["content_text"]
        assert "hidden" not in result["content_text"]
        assert "Real Content" in result["content_text"]
        assert result["title"] == "Noisy Page"

    def test_extract_fallback(self, extractor: ContentExtractor) -> None:
        """Test fallback extraction for malformed HTML."""
        result = extractor.extract("<div><p>Hello</p>", "http://example.com")
        assert "Hello" in result["content_text"]
        assert result["word_count"] > 0

    def test_extract_empty(self, extractor: ContentExtractor) -> None:
        """Test empty HTML returns empty result."""
        result = extractor.extract("", "http://example.com")
        assert result["word_count"] == 0
        assert result["content_text"] == ""


class TestMetadataExtractor:
    """Test the MetadataExtractor."""

    @pytest.fixture
    def extractor(self) -> MetadataExtractor:
        return MetadataExtractor()

    def test_extract_metadata_og(self, extractor: MetadataExtractor) -> None:
        """Test Open Graph metadata extraction."""
        result = extractor.extract_metadata(OG_HTML, "http://example.com")

        assert result["title"] == "OG Title"
        assert result["description"] == "OG Description"
        assert result["image"] == "https://example.com/image.jpg"
        assert result["type"] == "article"
        assert result["url"] == "https://example.com/article"
        assert result["site_name"] == "Example Site"
        assert result["domain"] == "example.com"

    def test_extract_metadata_twitter(self, extractor: MetadataExtractor) -> None:
        """Test Twitter Card metadata extraction."""
        result = extractor.extract_metadata(OG_HTML, "http://example.com")

        assert result["twitter_card"] == "summary_large_image"
        assert result["twitter_site"] == "@example"

    def test_extract_meta_tags(self, extractor: MetadataExtractor) -> None:
        """Test standard meta tag extraction."""
        result = extractor.extract_metadata(OG_HTML, "http://example.com")

        assert result["meta_description"] == "Meta Description"
        assert result["meta_keywords"] == "test, article, demo"
        assert result["meta_author"] == "Jane Smith"

    def test_metadata_json_ld(self, extractor: MetadataExtractor) -> None:
        """Test JSON-LD structured data extraction."""
        result = extractor.extract_metadata(JSONLD_HTML, "http://example.com")

        assert len(result["json_ld"]) == 1
        assert result["json_ld"][0]["@type"] == "Article"
        assert result["json_ld"][0]["headline"] == "JSON-LD Article"

    def test_metadata_fallback(self, extractor: MetadataExtractor) -> None:
        """Test metadata fallback when no OG tags are present."""
        result = extractor.extract_metadata(NO_META_HTML, "http://example.com")

        assert result["title"] == "No Meta"
        assert result["description"] == ""
        assert result["image"] == ""
        assert result["twitter_card"] == ""
        assert result["json_ld"] == []
        assert result["domain"] == "example.com"


class TestBatchExtractor:
    """Test the BatchExtractor."""

    @pytest.fixture
    def extractor(self) -> BatchExtractor:
        return BatchExtractor()

    def test_batch_extract(self, extractor: BatchExtractor) -> None:
        """Test batch extraction of multiple items."""
        items = [
            {"url": "http://example.com/a", "body_html": SIMPLE_HTML},
            {"url": "http://example.com/b", "body_html": NOISY_HTML},
            {"url": "http://example.com/c", "body_html": OG_HTML},
        ]
        results = extractor.extract_batch(items, max_workers=3)

        assert len(results) == 3
        assert results[0]["title"] == "Test Article"
        assert results[0]["url"] == "http://example.com/a"
        assert results[1]["title"] == "Noisy Page"
        assert "Real Content" in results[1]["content_text"]
        assert results[2]["word_count"] > 0
        # OG HTML has no article tag, so title comes from <title>
        assert "OG Test" in results[2]["title"]

    def test_batch_extract_with_errors(self, extractor: BatchExtractor) -> None:
        """Test that batch extraction handles per-item errors gracefully."""
        items = [
            {"url": "http://example.com/a", "body_html": SIMPLE_HTML},
            {"url": "http://example.com/b", "body_html": ""},
        ]
        results = extractor.extract_batch(items, max_workers=2)

        assert len(results) == 2
        assert "_error" not in results[0]
        assert results[0]["word_count"] > 0


class TestExecutorStepTypeRouting:
    """Test that the executor correctly routes extractor step types."""

    def test_extract_step_type(self) -> None:
        """Test that extract_article step type is registered (smoke test)."""
        from workflow_executor import WorkflowExecutor

        # Verify the method exists on the class
        assert hasattr(WorkflowExecutor, "_execute_extract_article_step")
        assert hasattr(WorkflowExecutor, "_execute_batch_extract_step")
        assert hasattr(WorkflowExecutor, "_execute_extract_metadata_step")
        # Verify step type strings are defined
        from workflow_executor import WorkflowExecutor
        import inspect

        source = inspect.getsource(WorkflowExecutor._execute_extract_article_step)
        assert "extract_article" in source or "ContentExtractor" in source
