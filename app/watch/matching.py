"""Match a launch event to catalog lines. Deterministic code, no model, no fuzzy guessing.

A line matches when the event is about the same brand and the text names the line: its full line name
("QN90D Neo QLED") or its model code ("QN90D"), as whole words, case-insensitively. The text searched is the
article title, the event's product family and the quote that carries the date, not the whole article body.
"""

import re

from app.catalog import norm

_TOO_GENERIC = {"plus", "pro", "max", "ultra", "edge", "play", "power", "series", "galaxy", "smart", "fire", "tv", "oled", "qled"}


def line_keys(line: str) -> list[str]:
    """What counts as naming a line: its full name, and a model code such as QN90D if it has one."""
    name = norm(line)
    keys = [name] if name not in _TOO_GENERIC and len(name) >= 4 else []
    for tok in name.split():
        if len(tok) >= 4 and re.search(r"[a-z]", tok) and re.search(r"\d", tok):
            keys.append(tok)
    return list(dict.fromkeys(keys))


def _mentions(haystack: str, key: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])", haystack) is not None


def catalog_lines(variants: list[dict]) -> list[dict]:
    seen, out = set(), []
    for v in variants:
        if v["line_id"] not in seen:
            seen.add(v["line_id"])
            out.append({"line_id": v["line_id"], "brand": v["brand"], "line": v["line"], "category": v["category"], "year": v["year"]})
    return out


def match_lines(brand: str, text: str, lines: list[dict]) -> list[str]:
    """line_ids whose brand equals `brand` and whose name or model code appears in `text`."""
    b, hay = norm(brand), norm(text)
    if not b or not hay:
        return []
    return [ln["line_id"] for ln in lines if norm(ln["brand"]) == b and any(_mentions(hay, k) for k in line_keys(ln["line"]))]
