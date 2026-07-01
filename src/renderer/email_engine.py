"""Email Renderer Engine — converts markdown to brand-styled HTML email.

Uses the Evolving Software brand templates and inline CSS for email client
compatibility.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import markdown as md_lib

from .brand_styles import BrandStyle
from .section_templates import SectionTemplate

logger = logging.getLogger(__name__)

# ── Citation pattern: S001, S002, etc. ─────────────────────────────────
CITATION_PATTERN = re.compile(r'S(\d{3})')

# ── Default brand template ─────────────────────────────────────────────

DAILY_SIGNAL_HTML_TEMPLATE = """<!doctype html>
<html lang='en'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1, viewport-fit=cover'>
<title>{subject}</title>
<style>
{css}
</style>
</head>
<body>
{body}
<div class="footer">
<p><strong>Evolving Software</strong> — Daily Signal Intelligence Brief</p>
<p style="font-size:.72rem; color:var(--es-grey); margin-top:6px;">
Generated automatically. This is a research intelligence brief prepared by
automated analysis of publicly available sources.
</p>
</div>
</body>
</html>"""

DAILY_SIGNAL_TEXT_TEMPLATE = """{subject}
{underline}

{preamble}

{sections}

---
Evolving Software — Daily Signal Intelligence Brief
"""


def _extract_subject(markdown_text: str) -> str:
    """Extract first # heading as the subject line."""
    for line in markdown_text.split('\n'):
        stripped = line.strip()
        if stripped.startswith('# ') and not stripped.startswith('## '):
            return stripped[2:].strip()
    return "Evolving Software Daily Signal"


def _split_sections(markdown_text: str) -> list[dict[str, str]]:
    """Split markdown into sections by ## headings.

    Returns list of {heading, content} dicts in order.
    Handles preamble (text before first ## heading).
    """
    lines = markdown_text.split('\n')
    sections: list[dict[str, str]] = []
    current_heading = ""
    current_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith('## '):
            # Save previous section
            if current_lines:
                sections.append({
                    "heading": current_heading,
                    "content": '\n'.join(current_lines).strip(),
                })
            current_heading = stripped[3:].strip()
            current_lines = []
        elif stripped.startswith('# ') and not stripped.startswith('## '):
            # Title — skip, handled separately
            continue
        else:
            current_lines.append(line)

    # Save last section
    if current_lines:
        sections.append({
            "heading": current_heading,
            "content": '\n'.join(current_lines).strip(),
        })

    return sections


def _render_citations(html: str) -> str:
    """Convert S001 citations into hyperlinked footnotes."""
    def _citation_replacer(match: re.Match) -> str:
        cid = match.group(1)
        return f'<sup><a href="#citation-{cid}" class="citation-ref">S{cid}</a></sup>'

    return CITATION_PATTERN.sub(_citation_replacer, html)


def _markdown_to_html(markdown_text: str) -> str:
    """Convert markdown text to HTML using the markdown library."""
    extensions = [
        'markdown.extensions.extra',
        'markdown.extensions.codehilite',
        'markdown.extensions.smarty',
    ]
    return md_lib.markdown(markdown_text, extensions=extensions)


def _convert_sections_to_text(sections: list[dict[str, str]]) -> str:
    """Convert rendered sections to plain text."""
    text_parts = []
    for section in sections:
        heading = section.get("heading", "")
        content = section.get("content", "")
        if heading:
            text_parts.append(f"\n{heading.upper()}")
            text_parts.append("-" * len(heading))
        # Strip basic markdown
        plain = content
        plain = re.sub(r'\*\*(.+?)\*\*', r'\1', plain)
        plain = re.sub(r'\*(.+?)\*', r'\1', plain)
        plain = re.sub(r'`(.+?)`', r'\1', plain)
        plain = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1 (\2)', plain)
        plain = re.sub(r'<[^>]+>', '', plain)
        text_parts.append(plain.strip())
    return '\n\n'.join(text_parts)


# ── Preamble extraction ────────────────────────────────────────────────


def _extract_preamble(markdown_text: str) -> str:
    """Extract text between the # title and the first ## section heading."""
    lines = markdown_text.split('\n')
    preamble_lines: list[str] = []
    in_preamble = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('# ') and not stripped.startswith('## '):
            in_preamble = True  # Skip the title line itself
            continue
        if in_preamble:
            if stripped.startswith('## '):
                break
            preamble_lines.append(line)
    return '\n'.join(preamble_lines).strip()


# ── EmailRenderer ──────────────────────────────────────────────────────


class EmailRenderer:
    """Converts markdown content to brand-styled HTML email."""

    def __init__(self):
        self.section_template = SectionTemplate()
        self.brand_style = BrandStyle()

    def render(
        self,
        body_markdown: str,
        template_name: str = 'daily-signal',
        dark_mode: bool = False,
    ) -> dict[str, Any]:
        """Render markdown to a branded HTML email.

        Args:
            body_markdown: Raw markdown content to render.
            template_name: Template name ('daily-signal').
            dark_mode: Force dark mode styling when True.

        Returns:
            dict with keys: html, text, subject, sections_rendered, errors
        """
        errors: list[str] = []
        sections_rendered: list[dict[str, str]] = []

        try:
            subject = _extract_subject(body_markdown)
        except Exception as e:
            subject = "Evolving Software Daily Signal"
            errors.append(f"Subject extraction failed: {e}")

        try:
            raw_sections = _split_sections(body_markdown)
        except Exception as e:
            raw_sections = []
            errors.append(f"Section splitting failed: {e}")

        # Render each section
        section_bodies: list[str] = []
        for section in raw_sections:
            heading = section.get("heading", "")
            content = section.get("content", "")
            if not heading and not content:
                continue

            try:
                # Map heading to template key
                template_key = heading.lower().replace(' ', '-').replace('&', 'and')
                rendered = self.section_template.render_section(template_key, content)
                section_bodies.append(rendered)
                sections_rendered.append({
                    "heading": heading,
                    "status": "rendered",
                })
            except Exception as e:
                errors.append(f"Section '{heading}' render failed: {e}")
                section_bodies.append(f"<h2>{heading}</h2><p><!-- render error --></p>")
                sections_rendered.append({
                    "heading": heading,
                    "status": "error",
                    "error": str(e),
                })

        # Also render preamble as introductory content
        preamble = _extract_preamble(body_markdown)
        preamble_html = ""
        if preamble:
            try:
                preamble_html = _markdown_to_html(preamble)
            except Exception as e:
                errors.append(f"Preamble render failed: {e}")

        try:
            body_html = '\n'.join(section_bodies)
            # If we have preamble, insert before sections
            if preamble_html:
                body_html = preamble_html + '\n' + body_html

            # Render citations
            body_html = _render_citations(body_html)

            # Get CSS
            css = self.brand_style.get_css(dark_mode=dark_mode)

            # Wrap in template
            html = DAILY_SIGNAL_HTML_TEMPLATE.format(
                subject=subject,
                css=css,
                body=body_html,
            )
        except Exception as e:
            html = ""
            errors.append(f"HTML wrapping failed: {e}")

        # Generate plain text version
        try:
            text_parts = [subject]
            text_parts.append("=" * len(subject))
            if preamble:
                text_parts.append("")
                text_parts.append(preamble)
            for section in raw_sections:
                heading = section.get("heading", "")
                content = section.get("content", "")
                if heading:
                    text_parts.append("")
                    text_parts.append(heading.upper())
                    text_parts.append("-" * len(heading))
                if content:
                    text_parts.append(content)
            text = '\n'.join(text_parts)
            # Strip basic markdown
            text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
            text = re.sub(r'\*(.+?)\*', r'\1', text)
            text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1 (\2)', text)
        except Exception as e:
            text = subject
            errors.append(f"Text generation failed: {e}")

        return {
            "html": html,
            "text": text,
            "subject": subject,
            "sections_rendered": sections_rendered,
            "errors": errors,
        }
