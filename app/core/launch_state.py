"""Launch tracker states. Pure functions: `today` is passed in (the app's injectable clock); nothing here reads the clock, a file or the web.

A tracked line has at most one current launch date, taken word for word from stored news. The state is worked out from that date, the dates
it replaced and a confirming source, each time it is read; it is never stored. Two rules hold in every state:

- A date that has come does not mean the product launched. Only a confirming source does (`launch_confirmed`).
- No news does not mean a delay. A date long past with no confirmation is `unverified`, never "delayed".

Only an exact day (a day, a month and a year) can become `due`. A date written without a year is `date_approximate` and never becomes due.
Dates are compared as calendar days; the time of day plays no part (see config/thresholds.yaml for the time-zone assumption).
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

ANNOUNCED, DATE_APPROXIMATE, DUE, LAUNCH_CONFIRMED, DATE_CHANGED, UNVERIFIED = (
    "announced", "date_approximate", "due", "launch_confirmed", "date_changed", "unverified")
STATES = (ANNOUNCED, DATE_APPROXIMATE, DUE, LAUNCH_CONFIRMED, DATE_CHANGED, UNVERIFIED)

# Every sentence the tracker shows comes from one of these fixed templates; only dates and day counts are filled in.
TEMPLATES = {
    "announced_no_date": "Announced. The stored news gives no exact launch day.",
    ANNOUNCED: "Launch date as written: {date}, {days} from today.",
    DATE_APPROXIMATE: "Launch date as written: {date}. It has no year, so it is not an exact day and is never treated as due.",
    DATE_CHANGED: "The launch date changed to {date}, {days} from today. Before: {earlier}.",
    DUE: "The stated launch day, {date}, {when}. No stored news says it launched; the date alone does not mean it did.",
    UNVERIFIED: "The stated launch day, {date}, was {ago} ago. No stored news confirms the launch. This does not mean it was delayed.",
    LAUNCH_CONFIRMED: "Launch confirmed by a source dated {confirmed}.",
}


@dataclass(frozen=True)
class StatedDate:
    text: str                 # exactly as written in the article
    iso: str | None           # YYYY-MM-DD, only when the written date has a day, a month and a year

    @property
    def exact(self) -> bool:
        return self.iso is not None


@dataclass(frozen=True)
class LaunchState:
    state: str
    date: str | None          # the current date as written, or None
    date_iso: str | None
    days_until: int | None    # stated day minus today, for an exact day only; negative once it has passed
    confirmed_on: str | None  # the confirming source's date, YYYY-MM-DD
    text: str


def _days(n: int) -> str:
    return f"{n} day" if n == 1 else f"{n} days"


def launch_state(current: StatedDate | None, earlier: Iterable[str], confirmed_on: date | None, today: date, grace_days: int) -> LaunchState:
    """The state of one tracked line on `today`.

    `current`: the latest stated date, or None. `earlier`: dates (as written) that the line had before; the current one is ignored if listed.
    `confirmed_on`: the date of a source that confirms the launch. It counts only for an exact day and only if it is on or after that day."""
    if current is None:
        return LaunchState(ANNOUNCED, None, None, None, None, TEMPLATES["announced_no_date"])
    before = list(dict.fromkeys(d for d in earlier if d and d != current.text))
    if not current.exact:
        return LaunchState(DATE_APPROXIMATE, current.text, None, None, None, TEMPLATES[DATE_APPROXIMATE].format(date=current.text))
    day = date.fromisoformat(current.iso)
    days = (day - today).days
    if confirmed_on is not None and confirmed_on >= day:
        return LaunchState(LAUNCH_CONFIRMED, current.text, current.iso, days, confirmed_on.isoformat(),
                           TEMPLATES[LAUNCH_CONFIRMED].format(confirmed=confirmed_on.isoformat()))
    if days <= 0 and -days <= grace_days:
        when = "is today" if days == 0 else f"was {_days(-days)} ago"
        return LaunchState(DUE, current.text, current.iso, days, None, TEMPLATES[DUE].format(date=current.text, when=when))
    if days < 0:
        return LaunchState(UNVERIFIED, current.text, current.iso, days, None, TEMPLATES[UNVERIFIED].format(date=current.text, ago=_days(-days)))
    if before:
        return LaunchState(DATE_CHANGED, current.text, current.iso, days, None,
                           TEMPLATES[DATE_CHANGED].format(date=current.text, days=_days(days), earlier=", ".join(before)))
    return LaunchState(ANNOUNCED, current.text, current.iso, days, None, TEMPLATES[ANNOUNCED].format(date=current.text, days=_days(days)))


def _plain(text: str) -> str:
    return " ".join((text or "").lower().replace("’", "'").split())


def confirms_launch(text: str, confirm_phrases: Iterable[str], block_phrases: Iterable[str]) -> bool:
    """True if the text says the product is on sale: one of `confirm_phrases` as whole words, and none of `block_phrases`.
    A blocking phrase also matches longer forms of its last word ("delay" blocks "delayed"); a future or a delay never confirms."""
    t = _plain(text)
    if any(re.search(r"(?<![a-z0-9])" + re.escape(_plain(p)), t) for p in block_phrases if _plain(p)):
        return False
    return any(re.search(r"(?<![a-z0-9])" + re.escape(_plain(p)) + r"(?![a-z0-9])", t) for p in confirm_phrases if _plain(p))
