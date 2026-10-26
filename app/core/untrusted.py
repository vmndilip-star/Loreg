"""Fetched web content is untrusted data. This wraps it and flags instruction-like text.

Flagging never changes what the system does with the text: it is still only data. The flag
lets the UI tell the owner to check the source.
"""

import re

_PATTERNS = {
    "tells the reader to ignore earlier instructions":
        r"\b(ignore|disregard|forget)\b.{0,40}\b(previous|prior|above|earlier|all)\b.{0,20}\b(instructions?|rules?|prompts?)\b",
    "addresses the AI directly": r"\b(you are now|act as|pretend to be|as an ai|system prompt|new instructions?)\b",
    "asks for secrets": r"\b(api[ _-]?key|password|secret|token|credentials?)\b",
    "asks for an action": r"\b(send|email|forward|post|upload|delete|transfer|pay)\b.{0,40}\b(to|at)\b.{0,40}(@|https?://)",
    "contains a markup role marker": r"(<\s*/?\s*(system|assistant|developer)\s*>|\[\s*(system|inst)\s*\])",
}
_COMPILED = {label: re.compile(rx, re.IGNORECASE | re.DOTALL) for label, rx in _PATTERNS.items()}


def detect_injection(text: str) -> list[str]:
    return [label for label, rx in _COMPILED.items() if rx.search(text)]


def wrap_untrusted(text: str, source_id: str) -> str:
    """Delimit fetched text so a prompt can state that everything inside is data."""
    safe = text.replace("</untrusted_source>", "[end marker removed]")
    return f'<untrusted_source id="{source_id}">\n{safe}\n</untrusted_source>'
