"""A short, verbatim excerpt of an article. Pure code: no model, no network.

The excerpt is what Loreg keeps of an article once it has been read: a stretch of the article's own words, at most `limit` characters,
chosen around the first launch cue. It is always an exact substring of the text it came from; nothing is paraphrased or added.
"""

import re

LAUNCH_CUE = re.compile(r"\b(launch(?:es|ed|ing)?|unveil(?:s|ed|ing)?|introduc(?:e|es|ed|ing)|announce(?:s|d)?|available|release(?:s|d)?|"
                        r"pre-?orders?|on sale|ships?|shipping)\b", re.IGNORECASE)
_SENTENCE_END = (". ", "! ", "? ")


def make_excerpt(text: str, limit: int = 280) -> str:
    """At most `limit` characters of `text`, starting at the sentence that holds the first launch cue (or the start of the text)."""
    text = text or ""
    start = 0
    cue = LAUNCH_CUE.search(text)
    if cue:
        before = max(text.rfind(end, 0, cue.start()) for end in _SENTENCE_END)
        start = before + 2 if before >= 0 else 0
        start = max(start, cue.start() - limit // 2)          # a very long sentence: begin shortly before the cue
    piece = text[start:start + limit]
    if start + limit < len(text) and " " in piece:              # cut at a word boundary rather than in the middle of a word
        piece = piece[:piece.rfind(" ")]
    piece = piece.strip()
    assert piece in text and len(piece) <= limit
    return piece
