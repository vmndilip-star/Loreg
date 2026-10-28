"""A short, verbatim excerpt of an article. Pure code: no model, no network.

The excerpt is what Loreg keeps of an article once it has been read: a stretch of the article's own words, at most `limit` characters,
chosen around the first launch cue. It is always an exact substring of the text it came from; nothing is paraphrased or added.
"""

import re

LAUNCH_CUE = re.compile(r"\b(launch(?:es|ed|ing)?|unveil(?:s|ed|ing)?|introduc(?:e|es|ed|ing)|announce(?:s|d)?|available|release(?:s|d)?|"
                        r"pre-?orders?|on sale|ships?|shipping)\b", re.IGNORECASE)
_SENTENCE_END = (". ", "! ", "? ")


def _next_word_start(text: str, i: int, ceiling: int) -> int:
    """The first position at or after `i` that begins a word (never beyond `ceiling`), so an excerpt never starts in the middle of a word."""
    if i <= 0 or text[i - 1].isspace():
        return i
    j = i
    while j < len(text) and not text[j].isspace():
        j += 1
    while j < len(text) and text[j].isspace():
        j += 1
    return min(j, ceiling)


def make_excerpt(text: str, limit: int = 280) -> str:
    """At most `limit` characters of `text`, starting at the sentence that holds the first launch cue (or the start of the text).
    It begins at the start of a sentence or of a word, and ends at the end of a word."""
    text = text or ""
    start = 0
    cue = LAUNCH_CUE.search(text)
    if cue:
        before = max(text.rfind(end, 0, cue.start()) for end in _SENTENCE_END)
        start = before + 2 if before >= 0 else 0
        if cue.start() - start > limit // 2:                  # a very long sentence: begin shortly before the cue, at the start of a word
            start = _next_word_start(text, cue.start() - limit // 2, cue.start())
    piece = text[start:start + limit]
    if start + limit < len(text) and " " in piece:              # cut at a word boundary rather than in the middle of a word
        piece = piece[:piece.rfind(" ")]
    piece = piece.strip()
    assert piece in text and len(piece) <= limit
    return piece
