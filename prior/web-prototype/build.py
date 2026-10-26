#!/usr/bin/env python3
"""Inline Bootstrap 5.3.3 CSS, styles.css and app.js into one self-contained HTML file (for hosts that cannot load a CDN stylesheet)."""
import re, sys
from pathlib import Path
here = Path(__file__).parent
bs = Path(sys.argv[1]).read_text()
bs = re.sub(r'^@charset "UTF-8";', '', bs)
bs = re.sub(r'/\*# sourceMappingURL=.*?\*/', '', bs)
html = (here / 'index.html').read_text()
link_bs = '<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">'
assert link_bs in html and '<link href="styles.css" rel="stylesheet">' in html and '<script src="app.js"></script>' in html
html = html.replace(link_bs, '<style>' + bs + '</style>')
html = html.replace('<link href="styles.css" rel="stylesheet">', '<style>' + (here / 'styles.css').read_text() + '</style>')
snap = (here / 'snapshot.json').read_text().replace('</', '<\\/')
html = html.replace('<script type="application/json" id="snapshot"></script>', '<script type="application/json" id="snapshot">' + snap + '</script>')
html = html.replace('<script src="app.js"></script>', '<script>' + (here / 'app.js').read_text() + '</script>')
out = Path(sys.argv[2]); out.write_text(html)
print(out, round(out.stat().st_size / 1024), 'KB')
