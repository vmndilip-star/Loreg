"""Turn fetched HTML into plain, bounded text, and report anything suspicious on the way.

Fetched text is untrusted data. Sanitizing removes markup, hidden text, comments, control and zero-width
characters and truncates to MAX_CHARS. It does not make text safe to obey: nothing in an article is ever
treated as an instruction. The returned flags tell the owner which sources to check.
"""

import re
import unicodedata
from html import unescape
from html.parser import HTMLParser

from app.core.untrusted import detect_injection

MAX_CHARS = 8000
INVISIBLE_FLAG = "invisible or control characters were removed"  # a soft flag: shown, but does not block an article
MAX_TITLE = 300

_INVISIBLE = {ord(c): None for c in "​‌‍‎‏⁠⁡⁢⁣⁤﻿­᠎"
              "‪‫‬‭‮⁦⁧⁨⁩"}
# Characters that have no place in a news article. A soft hyphen or a left-to-right mark is ordinary typography, so it is
# removed quietly; these are removed and flagged.
_SUSPICIOUS = set("\u200b\u200c\u200d\u2060\u2061\u2062\u2063\u2064\ufeff\u180e\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0|opacity\s*:\s*0(\.0+)?\s*(;|$)|"
                           r"text-indent\s*:\s*-\d{3,}|left\s*:\s*-\d{3,}px|color\s*:\s*(#f{3,6}|white)\b", re.IGNORECASE)
_BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "section", "article", "blockquote", "figure"}
_SKIP = {"script", "style", "noscript", "template", "iframe", "object", "embed", "svg", "head"}
_VOID = {"br", "img", "hr", "input", "meta", "link", "source", "wbr"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[tuple[str, bool]] = []   # (tag, hides its content)
        self.hidden_removed = False
        self.comments: list[str] = []

    def _hiding(self) -> bool:
        return any(h for _, h in self.stack)

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        hides = (tag in _SKIP or "hidden" in a or a.get("aria-hidden", "").lower() == "true"
                 or bool(_HIDDEN_STYLE.search(a.get("style", ""))))
        if tag in _BLOCK:
            self.parts.append("\n")
        if tag not in _VOID:
            self.stack.append((tag, hides))
        if hides and tag not in _SKIP:
            self.hidden_removed = True

    def handle_startendtag(self, tag, attrs):
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._hiding():
            if data.strip() and not any(t in _SKIP for t, _ in self.stack):
                self.hidden_removed = True
            return
        self.parts.append(data)

    def handle_comment(self, data):
        self.comments.append(data)


def _clean(text: str) -> tuple[str, bool]:
    """Normalize, drop control and zero-width characters, collapse spaces. Returns (text, removed_something_invisible)."""
    text = unicodedata.normalize("NFKC", unescape(text))
    stripped = text.translate(_INVISIBLE)
    out = []
    for ch in stripped:
        if unicodedata.category(ch) == "Cc" and ch not in "\n\t":
            out.append(" ")
        else:
            out.append(ch)
    cleaned = "".join(out)
    cleaned = re.sub(r"[ \t\r\f\v]+", " ", cleaned)
    cleaned = re.sub(r" ?\n ?", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    suspicious = any(c in _SUSPICIOUS or (unicodedata.category(c) == "Cc" and c not in "\n\t\r") for c in text)
    return cleaned, suspicious


def truncate(text: str, limit: int = MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    return (cut[:cut.rfind(" ")] if " " in cut[-60:] else cut).rstrip()


def sanitize_html(raw: str, limit: int = MAX_CHARS) -> tuple[str, list[str]]:
    """Plain text of an HTML fragment, at most `limit` characters, and the flags found while cleaning it."""
    parser = _Text()
    try:
        parser.feed(raw or "")
        parser.close()
    except Exception:  # malformed markup must never break a scan; fall back to a blunt tag strip
        parser = _Text()
        parser.parts = [re.sub(r"<[^>]*>", " ", raw or "")]
    text, invisible = _clean("".join(parser.parts))
    flags = []
    if parser.hidden_removed:
        flags.append("hidden text was removed")
    if invisible:
        flags.append(INVISIBLE_FLAG)
    comment_hits = [f for c in parser.comments for f in detect_injection(c)]
    if comment_hits:
        flags.append("instructions were hidden in a comment")
    flags += [f for f in detect_injection(raw or "") + detect_injection(text) if f not in flags]
    return truncate(text, limit), list(dict.fromkeys(flags))


def sanitize_title(raw: str) -> str:
    text, _ = sanitize_html(raw, MAX_TITLE)
    return " ".join(text.split())
