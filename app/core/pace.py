"""Required pace to a stated launch date: how many units a day must sell for the stock to be gone by then. Pure functions.

It is arithmetic on a date the news source states and on the quantity on the shelf. It is NOT a sales forecast: it says nothing about
what will sell, only what would have to. `today` is passed in (the app's injectable clock); nothing here reads the system date.
"""

from dataclasses import dataclass
from datetime import date

UPCOMING, TODAY, PAST, NOTHING_TO_SELL = "upcoming", "today", "past", "nothing_to_sell"


@dataclass(frozen=True)
class RequiredPace:
    status: str                  # upcoming | today | past | nothing_to_sell
    units: int                   # the quantity the pace is for
    days_until: int              # launch date minus today; 0 on the day, negative after it
    per_day: int | None          # ceil(units / days_until) when the launch is still ahead; otherwise None (a pace needs a day to sell in)


def required_pace(units: int, launch: date, today: date) -> RequiredPace:
    """ceil(units / days_until) when the launch is at least a day away. On the day itself, and after it, no pace is defined:
    those cases are reported as such and never divided by zero or by a negative number."""
    days = (launch - today).days
    if units <= 0:
        return RequiredPace(NOTHING_TO_SELL, max(units, 0), days, None)
    if days > 0:
        return RequiredPace(UPCOMING, units, days, -(-units // days))            # integer ceiling, no float rounding
    return RequiredPace(TODAY if days == 0 else PAST, units, days, None)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def pace_text(p: RequiredPace, launch_text: str, synthetic: bool = False) -> str:
    """The sentence the app shows. Every figure in it comes from `p`; `launch_text` is the date as the article wrote it."""
    tail = " It is a required pace, not a sales forecast." + (" The launch date comes from the synthetic demo article." if synthetic else "")
    if p.status == UPCOMING:
        return (f"To sell all {_plural(p.units, 'unit')} before the launch on {launch_text} ({_plural(p.days_until, 'day')} from today), "
                f"you would need to sell at least {_plural(p.per_day, 'unit')} a day.{tail}")
    if p.status == TODAY:
        return f"The launch is today ({launch_text}), so there is no day left to sell {_plural(p.units, 'unit')} before it.{tail}"
    if p.status == PAST:
        return f"The launch date, {launch_text}, was {_plural(-p.days_until, 'day')} ago, so no required pace applies.{tail}"
    return f"There is nothing on the shelf to sell before the launch on {launch_text}.{tail}"
