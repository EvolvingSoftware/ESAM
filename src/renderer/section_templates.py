"""Section templates for the Evolving Software email renderer.

Each known section name maps to a rendering function that wraps content in
brand-consistent HTML markup.
"""

from __future__ import annotations

import re
from typing import Any


def _markdown_to_html(text: str) -> str:
    """Convert basic markdown inline elements to HTML."""
    # Bold
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # Italic
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<em>\1</em>', text)
    # Inline code
    text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
    # Links
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    return text


def _render_converging_signals(content: str) -> str:
    """Render ## Converging Signals with styled list items."""
    html_parts = ['<h2>Converging Signals</h2>']
    # Split by list items (lines starting with - or *)
    lines = content.strip().split('\n')
    in_list = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_list:
                html_parts.append('</ul>')
                in_list = False
            continue
        if stripped.startswith('- ') or stripped.startswith('* '):
            if not in_list:
                html_parts.append('<ul>')
                in_list = True
            item_text = stripped[2:]
            html_parts.append(f'<li>{_markdown_to_html(item_text)}</li>')
        else:
            if in_list:
                html_parts.append('</ul>')
                in_list = False
            html_parts.append(f'<p>{_markdown_to_html(stripped)}</p>')
    if in_list:
        html_parts.append('</ul>')
    return '\n'.join(html_parts)


def _render_frontier_lab_48h(content: str) -> str:
    """Render ## Frontier Lab 48h Watch with styled cards."""
    html_parts = ['<h2>Frontier Lab 48h Watch</h2>']
    lines = content.strip().split('\n')
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # If it looks like a card title (followed by colon or bold)
        if stripped.startswith('**') and '**' in stripped[2:]:
            # Bold title card
            html_parts.append(f'<p>{_markdown_to_html(stripped)}</p>')
        elif stripped.startswith('- ') or stripped.startswith('* '):
            item_text = stripped[2:]
            html_parts.append(f'<p><strong>•</strong> {_markdown_to_html(item_text)}</p>')
        else:
            html_parts.append(f'<p>{_markdown_to_html(stripped)}</p>')
    return '\n'.join(html_parts)


def _render_ai_interaction_psychology(content: str) -> str:
    """Render section with blockquotes."""
    html_parts = ['<h2>AI Interaction Psychology</h2>']
    lines = content.strip().split('\n')
    in_blockquote = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_blockquote:
                html_parts.append('</blockquote>')
                in_blockquote = False
            continue
        if stripped.startswith('> '):
            if not in_blockquote:
                html_parts.append('<blockquote>')
                in_blockquote = True
            html_parts.append(f'<p>{_markdown_to_html(stripped[2:])}</p>')
        else:
            if in_blockquote:
                html_parts.append('</blockquote>')
                in_blockquote = False
            html_parts.append(f'<p>{_markdown_to_html(stripped)}</p>')
    if in_blockquote:
        html_parts.append('</blockquote>')
    return '\n'.join(html_parts)


def _render_business_architecture(content: str) -> str:
    """Render section with styled paragraphs."""
    html_parts = ['<h2>Business Architecture</h2>']
    lines = content.strip().split('\n')
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('- ') or stripped.startswith('* '):
            html_parts.append(f'<p><strong>•</strong> {_markdown_to_html(stripped[2:])}</p>')
        else:
            html_parts.append(f'<p>{_markdown_to_html(stripped)}</p>')
    return '\n'.join(html_parts)


def _render_what_seems_taking_off(content: str) -> str:
    """Render What Seems Taking Off section."""
    html_parts = ['<h2>What Seems Taking Off</h2>']
    lines = content.strip().split('\n')
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('- ') or stripped.startswith('* '):
            html_parts.append(f'<li>{_markdown_to_html(stripped[2:])}</li>')
        else:
            html_parts.append(f'<p>{_markdown_to_html(stripped)}</p>')
    return '\n'.join(html_parts)


def _render_article_ideas(content: str) -> str:
    """Render ## Article Ideas with numbered list."""
    html_parts = ['<h2>Article Ideas</h2>', '<ol>']
    lines = content.strip().split('\n')
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('- ') or stripped.startswith('* '):
            html_parts.append(f'<li>{_markdown_to_html(stripped[2:])}</li>')
        elif stripped.startswith(('1.', '2.', '3.', '4.', '5.', '6.', '7.', '8.', '9.')):
            # Already numbered, strip the number
            item_text = re.sub(r'^\d+\.\s*', '', stripped)
            html_parts.append(f'<li>{_markdown_to_html(item_text)}</li>')
        else:
            html_parts.append(f'<li>{_markdown_to_html(stripped)}</li>')
    html_parts.append('</ol>')
    return '\n'.join(html_parts)


def _render_source_appendix(content: str) -> str:
    """Render citation appendix as styled footnotes."""
    html_parts = ['<h2>Sources &amp; Citations</h2>', '<ol class="citations">']
    # Split content by S001, S002 etc patterns or by lines
    lines = content.strip().split('\n')
    citation_counter = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Handle citation links: [S001: title](url)
        citation_match = re.match(r'\[?S(\d{3}):\s*(.+?)\]?\((.+?)\)', stripped)
        if citation_match:
            citation_counter += 1
            cid = citation_match.group(1)
            title = citation_match.group(2)
            url = citation_match.group(3)
            html_parts.append(
                f'<li id="citation-{cid}">'
                f'<strong>S{cid}:</strong> '
                f'<a href="{url}" target="_blank">{_markdown_to_html(title)}</a>'
                f'</li>'
            )
        elif stripped.startswith('- ') or stripped.startswith('* '):
            citation_counter += 1
            html_parts.append(
                f'<li>{_markdown_to_html(stripped[2:])}</li>'
            )
        else:
            citation_counter += 1
            html_parts.append(
                f'<li>{_markdown_to_html(stripped)}</li>'
            )
    html_parts.append('</ol>')
    return '\n'.join(html_parts)


# ── Default / fallback section renderer ────────────────────────────────


def _render_default_section(heading: str, content: str) -> str:
    """Render a generic section with heading and content."""
    html_parts = [f'<h2>{heading}</h2>']
    lines = content.strip().split('\n')
    in_list = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_list:
                html_parts.append('</ul>')
                in_list = False
            continue
        if stripped.startswith('- ') or stripped.startswith('* '):
            if not in_list:
                html_parts.append('<ul>')
                in_list = True
            html_parts.append(f'<li>{_markdown_to_html(stripped[2:])}</li>')
        else:
            if in_list:
                html_parts.append('</ul>')
                in_list = False
            html_parts.append(f'<p>{_markdown_to_html(stripped)}</p>')
    if in_list:
        html_parts.append('</ul>')
    return '\n'.join(html_parts)


# ── Section registry ──────────────────────────────────────────────────

# Maps section names (as they appear in ## headings, lowercase, no punctuation)
# to their rendering functions.
SECTION_RENDERERS: dict[str, callable] = {
    'converging signals': _render_converging_signals,
    'frontier lab 48h watch': _render_frontier_lab_48h,
    'ai interaction psychology': _render_ai_interaction_psychology,
    'business architecture': _render_business_architecture,
    'what seems taking off': _render_what_seems_taking_off,
    'article ideas': _render_article_ideas,
    'source appendix': _render_source_appendix,
    'sources & citations': _render_source_appendix,
}

SECTION_TEMPLATES: dict[str, str] = {
    'converging-signals': 'converging signals',
    'frontier-lab-48h': 'frontier lab 48h watch',
    'ai-interaction-psychology': 'ai interaction psychology',
    'business-architecture': 'business architecture',
    'what-seems-taking-off': 'what seems taking off',
    'article-ideas': 'article ideas',
    'source-appendix': 'source appendix',
}


class SectionTemplate:
    """Renders markdown sections into brand-styled HTML."""

    def __init__(self):
        self.templates = SECTION_TEMPLATES

    def render_section(self, name: str, content: str) -> str:
        """Render a section by name with its content.

        Args:
            name: Section key (e.g. 'converging-signals', 'article-ideas').
            content: The raw markdown content for this section.

        Returns:
            HTML string for the section.
        """
        # Map the template name to a renderer key
        key = self.templates.get(name, name)
        renderer = SECTION_RENDERERS.get(key)

        if renderer:
            return renderer(content)

        # Fallback: render as generic section
        heading = name.replace('-', ' ').title()
        return _render_default_section(heading, content)
