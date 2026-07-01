"""Evolving Software Brand CSS — matching the sample newsletter HTML exactly.

Font stack: 'Source Serif 4' body, 'Barlow Condensed' headers, 'IBM Plex Mono' labels.
Colors: #000000 bg (light), #ffffff bg (dark), #FF1F3C red accent (dark) / #C8102E red accent (light).
Container: max-width 44rem, centered.
Dark mode: @media (prefers-color-scheme: dark) with inverted bg.
Inline CSS for email client compatibility.
"""

BRAND_CSS = """
:root { color-scheme: light dark; --es-black:#000000; --es-white:#ffffff; --es-red:#C8102E; --es-ink:#101010; --es-grey:#5f5f5f; --es-rule:#d8d8d8; --es-paper:#ffffff; --es-soft:#f3f3f3; }
body {
  font-family: 'Source Serif 4', Georgia, 'Times New Roman', serif;
  max-width: 44rem;
  margin: 0 auto;
  padding: 22px 18px 34px;
  color: var(--es-ink);
  background: var(--es-paper);
  font-size: 19px;
  line-height: 1.68;
  -webkit-text-size-adjust: 100%;
  text-size-adjust: 100%;
  overflow-wrap: anywhere;
}
body::before { content:'EVOLVING SOFTWARE'; display:block; font-family:'IBM Plex Mono','Courier New',monospace; letter-spacing:.16em; font-size:.72rem; color:var(--es-grey); border-top:5px solid var(--es-black); padding-top:10px; margin-bottom:14px; }
h1 { font-family: 'Barlow Condensed', 'Arial Narrow', Arial, sans-serif; text-transform: uppercase; letter-spacing:-.015em; font-size:2.0rem; line-height:1.02; margin:0 0 18px; border-bottom:4px solid var(--es-red); padding-bottom:12px; color:var(--es-black); }
h2 { font-family:'Barlow Condensed','Arial Narrow',Arial,sans-serif; text-transform:uppercase; letter-spacing:.01em; font-size:1.42rem; line-height:1.08; margin:32px 0 12px; color:var(--es-black); border-top:1px solid var(--es-rule); padding-top:14px; }
h2::before { content:'//'; color:var(--es-red); font-family:'IBM Plex Mono','Courier New',monospace; margin-right:.45em; }
p { margin:0 0 16px; color:#222222; }
strong { color:var(--es-black); }
ul, ol { padding-left:1.2rem; margin:0 0 18px; }
li { margin:0 0 13px; color:#222222; }
li::marker { color:var(--es-red); }
blockquote { margin:2px 0 20px; padding:12px 15px; border-left:5px solid var(--es-red); background:var(--es-soft); color:var(--es-black); font-style:italic; }
a { color:var(--es-red); text-decoration-thickness:1px; text-underline-offset:3px; word-break:break-word; }
code { font-family:'IBM Plex Mono','Courier New',monospace; font-size:.9em; background:var(--es-soft); padding:.08em .22em; }
hr { border:0; border-top:1px solid var(--es-rule); margin:26px 0; }
.footer { color:var(--es-grey); font-family:'IBM Plex Mono','Courier New',monospace; font-size:.82rem; margin-top:28px; }
@media (prefers-color-scheme: dark) {
  :root { --es-paper:#000000; --es-ink:#f4f4f4; --es-grey:#a8a8a8; --es-rule:#343434; --es-soft:#111111; --es-red:#FF1F3C; }
  body { color:var(--es-ink); background:var(--es-paper); }
  body::before { border-top-color:#ffffff; }
  h1, h2, strong { color:#ffffff; }
  p, li { color:#ededed; }
  blockquote { background:#111111; color:#ffffff; }
}
"""

DARK_MODE_ONLY_CSS = """
:root { --es-paper:#000000; --es-ink:#f4f4f4; --es-grey:#a8a8a8; --es-rule:#343434; --es-soft:#111111; --es-red:#FF1F3C; }
body { color:var(--es-ink); background:var(--es-paper); }
body::before { border-top-color:#ffffff; }
h1, h2, strong { color:#ffffff; }
p, li { color:#ededed; }
blockquote { background:#111111; color:#ffffff; }
"""


class BrandStyle:
    """Provides brand CSS as a Python string."""

    @classmethod
    def get_css(cls, dark_mode: bool = False) -> str:
        """Return inline CSS string. If dark_mode=True, returns forced dark CSS."""
        if dark_mode:
            return BRAND_CSS.replace(
                "color-scheme: light dark;",
                "color-scheme: dark;",
            ).replace(
                "@media (prefers-color-scheme: dark) {",
                "/* dark mode forced */",
            )
        return BRAND_CSS
