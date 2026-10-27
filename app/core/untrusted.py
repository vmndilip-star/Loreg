"""Fetched web content is untrusted data. This wraps it and flags instruction-like text.

Flagging never changes what the system does with the text: it is still only data. The flag
lets the UI tell the owner to check the source, and an article that is flagged is never attached
to an alert automatically.
"""

import re

_PATTERNS = {
    "tells the reader to ignore earlier instructions":
        r"\b(ignore|disregard|forget)\b.{0,40}\b(previous|prior|above|earlier|all)\b.{0,20}\b(instructions?|rules?|prompts?)\b",
    "addresses the AI directly": r"\b(you are now|act as (?:a|an|the|my|your|if)\b|pretend to be|as an ai|system prompt|new instructions?)",
    "asks for secrets": r"\b(api[ _-]?key|password|secret|token|credentials?)\b",
    "asks for an action": r"\b(send|email|forward|post|upload|delete|transfer|pay)\b.{0,40}\b(to|at)\b.{0,40}(@|https?://)",
    "contains a markup role marker": r"(<\s*/?\s*(system|assistant|developer)\s*>|\[\s*(system|inst)\s*\])",
    "asks the reader to reveal its prompt or key":
        r"\b(reveal|print|show|output|repeat|leak)\b.{0,30}\b(system prompt|your prompt|api key|instructions)\b",
    "gives the assistant orders":
        r"\b(you must|you should|you will|you are required to)\b.{0,60}\b(recommend|discount|mark|set|reveal|send|ignore|disregard|output)\b",
    "claims a special mode": r"\b(admin mode|developer mode|jailbreak|dan mode|god mode)\b",
    "asks to hide something from the owner": r"\b(do not|don't|never)\b.{0,20}\b(tell|mention|reveal|show)\b.{0,20}\b(user|owner|anyone)\b",
}
_COMPILED = {label: re.compile(rx, re.IGNORECASE | re.DOTALL) for label, rx in _PATTERNS.items()}


def detect_injection(text: str) -> list[str]:
    return [label for label, rx in _COMPILED.items() if rx.search(text)]


def wrap_untrusted(text: str, source_id: str) -> str:
    """Delimit fetched text so a prompt can state that everything inside is data."""
    safe = text.replace("</untrusted_source>", "[end marker removed]")
    return f'<untrusted_source id="{source_id}">\n{safe}\n</untrusted_source>'
