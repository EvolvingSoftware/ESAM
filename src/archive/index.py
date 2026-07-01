"""Archive Index — rebuilds the archive index.html listing all editions.

Uses the same brand styling as the renderer for visual consistency.
"""

from __future__ import annotations

import html as html_mod
from pathlib import Path
from typing import Optional

from database import get_connection

__all__ = ["ArchiveIndex"]

# Brand CSS copied from the renderer for visual consistency
_ARCHIVE_CSS = """
:root { color-scheme: light dark; --es-black:#000000; --es-white:#ffffff; --es-red:#C8102E; --es-ink:#101010; --es-grey:#5f5f5f; --es-rule:#d8d8d8; --es-paper:#ffffff; --es-soft:#f3f3f3; }
* { box-sizing:border-box; }
body {
  font-family: 'Source Serif 4', Georgia, 'Times New Roman', serif;
  max-width: 52rem;
  margin: 0 auto;
  padding: 22px 18px 34px;
  color: var(--es-ink);
  background: var(--es-paper);
  font-size: 17px;
  line-height: 1.6;
  -webkit-text-size-adjust: 100%;
  text-size-adjust: 100%;
}
body::before { content:'NEWSLETTER ARCHIVE'; display:block; font-family:'IBM Plex Mono','Courier New',monospace; letter-spacing:.16em; font-size:.72rem; color:var(--es-grey); border-top:5px solid var(--es-black); padding-top:10px; margin-bottom:14px; }
h1 { font-family: 'Barlow Condensed', 'Arial Narrow', Arial, sans-serif; text-transform: uppercase; letter-spacing:-.015em; font-size:1.8rem; line-height:1.02; margin:0 0 18px; border-bottom:4px solid var(--es-red); padding-bottom:12px; color:var(--es-black); }
h2 { font-family:'Barlow Condensed','Arial Narrow',Arial,sans-serif; text-transform:uppercase; letter-spacing:.01em; font-size:1.2rem; line-height:1.08; margin:24px 0 8px; color:var(--es-black); }
h2 a { color:var(--es-black); text-decoration:none; }
h2 a:hover { text-decoration:underline; }
.edition { margin:0 0 20px; padding:14px 16px; border-left:4px solid var(--es-red); background:var(--es-soft); border-radius:0 6px 6px 0; }
.edition-date { font-family:'IBM Plex Mono','Courier New',monospace; font-size:.78rem; color:var(--es-grey); margin-bottom:4px; }
.edition-subject { margin:0 0 6px; }
.edition-excerpt { font-size:.92rem; color:#444; margin:0; }
.edition-permalink { font-family:'IBM Plex Mono','Courier New',monospace; font-size:.78rem; }
a { color:var(--es-red); text-decoration-thickness:1px; text-underline-offset:3px; }
.footer { color:var(--es-grey); font-family:'IBM Plex Mono','Courier New',monospace; font-size:.78rem; margin-top:32px; border-top:1px solid var(--es-rule); padding-top:14px; }
.count { font-family:'IBM Plex Mono','Courier New',monospace; font-size:.8rem; color:var(--es-grey); margin-bottom:14px; }
@media (prefers-color-scheme: dark) {
  :root { --es-paper:#000000; --es-ink:#f4f4f4; --es-grey:#a8a8a8; --es-rule:#343434; --es-soft:#111111; --es-red:#FF1F3C; }
  body { color:var(--es-ink); background:var(--es-paper); }
  body::before { border-top-color:#ffffff; }
  h1, h2, strong { color:#ffffff; }
  h2 a { color:#ffffff; }
  .edition-excerpt { color:#ccc; }
}
"""


class ArchiveIndex:
    """Rebuilds the archive root ``index.html`` listing all editions.

    Called automatically after each ``ArchiveEngine.store()`` call.
    """

    def __init__(self, archive_dir: str | None = None) -> None:
        self.archive_dir = Path(archive_dir or "").expanduser()

    def rebuild(self) -> str:
        """Generate (or regenerate) the master index.html.

        Queries ``wf_archived_editions`` for all editions ordered newest-first,
        builds a single HTML page, and writes it to ``{archive_dir}/index.html``.

        Returns:
            The absolute path to the generated ``index.html``.
        """
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, subject, body_markdown, permalink, created_at "
            "FROM wf_archived_editions ORDER BY created_at DESC"
        ).fetchall()

        editions_html = ""
        for row in rows:
            edition_id = row["id"]
            subject = row["subject"] or "(no subject)"
            body_md = row["body_markdown"] or ""
            permalink = row["permalink"] or f"/archives/{edition_id}"
            created = (row["created_at"] or "")[:10]  # YYYY-MM-DD

            # Build excerpt from first 200 chars of markdown
            excerpt = body_md.replace("**", "").replace("__", "")[:200].strip()
            if len(body_md) > 200:
                excerpt += "…"

            editions_html += (
                '<div class="edition">\n'
                f'  <div class="edition-date">{html_mod.escape(created)}</div>\n'
                f'  <h2 class="edition-subject"><a href="{html_mod.escape(permalink)}">'
                f"{html_mod.escape(subject)}</a></h2>\n"
                f'  <p class="edition-excerpt">{html_mod.escape(excerpt)}</p>\n'
                f'  <div class="edition-permalink"><a href="{html_mod.escape(permalink)}">'
                f"Permalink</a></div>\n"
                "</div>\n"
            )

        if not editions_html:
            editions_html = (
                '<p style="color:var(--es-grey);font-style:italic;">'
                "No editions have been archived yet.</p>\n"
            )

        total = len(rows)
        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Newsletter Archive</title>
<style>
{_ARCHIVE_CSS}
</style>
</head>
<body>
<h1>Newsletter Archive</h1>
<div class="count">{total} edition{'s' if total != 1 else ''}</div>
{editions_html}
<div class="footer">
  <a href="{html_mod.escape(str(self.archive_dir / '..' / '..' / 'index.html'))}">Home</a>
  &middot; <a href="/api/archive/rss">RSS Feed</a>
</div>
</body>
</html>
"""

        index_path = self.archive_dir / "index.html"
        index_path.write_text(html_content, encoding="utf-8")
        return str(index_path.resolve())
