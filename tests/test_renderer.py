"""Tests for the HTML Email Renderer."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure src is on the path
SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

import pytest

from renderer.email_engine import EmailRenderer
from renderer.brand_styles import BrandStyle
from renderer.section_templates import SectionTemplate


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def renderer() -> EmailRenderer:
    return EmailRenderer()


SAMPLE_MARKDOWN = """# Daily Signal: AI Frontier + Psychology

Key observations from the latest research cycle.

## Converging Signals

- **Signal one:** First convergence point.
- **Signal two:** Second convergence point.

## Article Ideas

- Idea number one about AI.
- Idea number two about agents.

## Source Appendix

[S001: Example Research](https://example.com/research)
[S002: Another Source](https://example.com/source2)
"""

SAMPLE_MARKDOWN_WITH_CITATIONS = """# Test Citations

Some text with a reference S001 to a source.

## Converging Signals

- **Important:** This cites S001 and S002.
"""


# ── Test basic render ─────────────────────────────────────────────────


def test_basic_render(renderer: EmailRenderer):
    """Renders markdown and checks HTML structure."""
    result = renderer.render(SAMPLE_MARKDOWN, template_name='daily-signal')
    assert 'html' in result
    assert 'text' in result
    assert 'subject' in result
    assert 'sections_rendered' in result
    assert 'errors' in result

    html = result['html']
    assert html.startswith('<!doctype html>')
    assert 'Evolving Software' in html
    assert 'Daily Signal' in html
    assert 'Converging Signals' in html
    assert 'Article Ideas' in html
    assert 'Source' in html
    assert 'Source Serif 4' in html
    assert 'Barlow Condensed' in html
    assert 'IBM Plex Mono' in html
    assert result['subject'] == 'Daily Signal: AI Frontier + Psychology'
    assert isinstance(result['errors'], list)
    assert len(result['errors']) == 0


def test_basic_render_empty_errors(renderer: EmailRenderer):
    """Basic render produces no errors."""
    result = renderer.render(SAMPLE_MARKDOWN)
    assert len(result['errors']) == 0, f"Unexpected errors: {result['errors']}"
    assert len(result['sections_rendered']) > 0


# ── Test brand styles ─────────────────────────────────────────────────


def test_brand_styles():
    """Checks CSS contains brand colors and fonts."""
    css = BrandStyle.get_css()
    assert '#FF1F3C' in css or '#C8102E' in css
    assert 'Source Serif 4' in css
    assert 'Barlow Condensed' in css
    assert 'IBM Plex Mono' in css
    assert '44rem' in css
    assert '@media (prefers-color-scheme: dark)' in css


def test_brand_styles_dark_mode():
    """Checks dark mode CSS override."""
    css = BrandStyle.get_css(dark_mode=True)
    assert '--es-paper:#000000' in css or 'color-scheme: dark' in css
    assert '#FF1F3C' in css


# ── Test section rendering ────────────────────────────────────────────


def test_section_rendering(renderer: EmailRenderer):
    """Checks section boundaries are detected."""
    result = renderer.render(SAMPLE_MARKDOWN)
    sections = result['sections_rendered']
    assert len(sections) >= 3  # Converging Signals, Article Ideas, Source Appendix

    headings = [s['heading'] for s in sections]
    assert 'Converging Signals' in headings
    assert 'Article Ideas' in headings
    assert 'Source Appendix' in headings

    html = result['html']
    assert 'Converging Signals' in html
    assert 'Article Ideas' in html


# ── Test dark mode ────────────────────────────────────────────────────


def test_dark_mode(renderer: EmailRenderer):
    """Checks dark mode CSS present in HTML output."""
    result = renderer.render(SAMPLE_MARKDOWN, dark_mode=True)
    html = result['html']

    # Should have forced dark mode
    assert '#FF1F3C' in html or '--es-red:#FF1F3C' in html


def test_dark_mode_flag_toggles_output(renderer: EmailRenderer):
    """Light and dark mode produce different CSS."""
    light = renderer.render(SAMPLE_MARKDOWN, dark_mode=False)
    dark = renderer.render(SAMPLE_MARKDOWN, dark_mode=True)

    # Dark mode has the dark-specific override (forced dark, no media query wrapping it)
    assert '@media (prefers-color-scheme: dark)' in light['html']
    assert '@media (prefers-color-scheme: dark)' not in dark['html']


# ── Test citation formatting ──────────────────────────────────────────


def test_citation_formatting(renderer: EmailRenderer):
    """Checks S001 rendered as hyperlinked footnote reference."""
    result = renderer.render(SAMPLE_MARKDOWN_WITH_CITATIONS)
    html = result['html']
    # S001 should appear as a citation reference link
    assert 'S001' in html
    assert 'citation-ref' in html or 'href="#citation' in html


# ── Test empty content ────────────────────────────────────────────────


def test_empty_content(renderer: EmailRenderer):
    """Empty markdown produces a basic template."""
    result = renderer.render('', template_name='daily-signal')
    assert result['html'] is not None
    assert 'Evolving Software' in result['html']
    assert result['subject'] == 'Evolving Software Daily Signal'


# ── Test SectionTemplate ──────────────────────────────────────────────


def test_section_template_render():
    """SectionTemplate renders known sections."""
    st = SectionTemplate()
    html = st.render_section('converging-signals', '- **Test item** content')
    assert '<h2>Converging Signals</h2>' in html
    assert 'Test item' in html


def test_section_template_unknown():
    """Unknown section falls back to generic render."""
    st = SectionTemplate()
    html = st.render_section('unknown-section', 'Some content')
    assert html is not None
    assert 'Some content' in html or 'Unknown' in html
