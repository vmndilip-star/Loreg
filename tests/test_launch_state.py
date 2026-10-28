"""Launch tracker states and the availability phrases. Pure functions, no clock, no database, no web."""

from datetime import date, timedelta

import pytest

from app.core.launch_state import (ANNOUNCED, DATE_APPROXIMATE, DATE_CHANGED, DUE, LAUNCH_CONFIRMED, TEMPLATES, UNVERIFIED, StatedDate,
                                   confirms_launch, launch_state)
from app.core.thresholds import load_thresholds

TH = load_thresholds()
GRACE = TH.launch_grace_days
DAY = date(2026, 11, 20)
EXACT = StatedDate("November 20, 2026", "2026-11-20")


def at(offset: int, current=EXACT, earlier=(), confirmed_on=None):
    return launch_state(current, earlier, confirmed_on, DAY + timedelta(days=offset), GRACE)


def test_the_grace_period_comes_from_the_config_and_is_seven_days():
    assert GRACE == 7


def test_a_future_exact_date_is_announced_with_the_days_until():
    s = at(-23)
    assert (s.state, s.days_until, s.date, s.date_iso) == (ANNOUNCED, 23, "November 20, 2026", "2026-11-20")
    assert s.text == "Launch date as written: November 20, 2026, 23 days from today."


def test_due_on_the_day_and_through_the_grace_period_and_due_never_says_launched():
    for offset in range(0, GRACE + 1):
        s = at(offset)
        assert s.state == DUE and s.days_until == -offset and s.confirmed_on is None
        assert "does not mean it did" in s.text and "launched;" in s.text
    assert "is today" in at(0).text and "was 1 day ago" in at(1).text


def test_after_the_grace_period_with_no_news_it_is_unverified_and_never_delayed():
    for offset in (GRACE + 1, 30, 400):
        s = at(offset)
        assert s.state == UNVERIFIED and "This does not mean it was delayed." in s.text
        assert s.text.count("delayed") == 1


@pytest.mark.parametrize("offset", [-400, -1, 0, 1, 8, 400, 4000])
def test_a_date_with_no_year_is_never_due_however_much_time_passes(offset):
    s = at(offset, current=StatedDate("November 20", None))
    assert (s.state, s.days_until, s.date_iso) == (DATE_APPROXIMATE, None, None)


def test_no_date_at_all_is_announced_with_no_date():
    s = launch_state(None, ["March 12, 2027"], None, DAY, GRACE)
    assert (s.state, s.date, s.text) == (ANNOUNCED, None, TEMPLATES["announced_no_date"])


def test_a_changed_date_still_ahead_is_date_changed_and_names_the_earlier_date():
    s = at(-10, earlier=["November 1, 2026", "November 20, 2026"])
    assert s.state == DATE_CHANGED and s.text.endswith("Before: November 1, 2026.")
    assert at(-10, earlier=["November 20, 2026"]).state == ANNOUNCED        # the same date again is not a change


def test_a_changed_date_that_has_come_is_due_not_changed():
    assert at(0, earlier=["November 1, 2026"]).state == DUE
    assert at(GRACE + 1, earlier=["November 1, 2026"]).state == UNVERIFIED


def test_confirmed_only_by_a_source_dated_on_or_after_the_stated_day():
    s = at(2, confirmed_on=DAY + timedelta(days=1))
    assert s.state == LAUNCH_CONFIRMED and s.text == "Launch confirmed by a source dated 2026-11-21." and s.confirmed_on == "2026-11-21"
    assert at(0, confirmed_on=DAY).state == LAUNCH_CONFIRMED
    assert at(2, confirmed_on=DAY - timedelta(days=1)).state == DUE                  # an older source never confirms
    assert at(-5, confirmed_on=DAY - timedelta(days=6)).state == ANNOUNCED
    assert at(40, confirmed_on=DAY + timedelta(days=35)).state == LAUNCH_CONFIRMED  # confirmation wins over unverified


def test_a_date_with_no_year_can_never_be_confirmed():
    assert at(5, current=StatedDate("November 20", None), confirmed_on=DAY + timedelta(days=1)).state == DATE_APPROXIMATE


def test_every_state_text_comes_from_a_template():
    seen = {at(-3).text, at(0).text, at(30).text, at(-3, earlier=["May 1, 2026"]).text, at(1, confirmed_on=DAY).text,
            at(0, current=StatedDate("November 20", None)).text}
    shapes = [t.split("{")[0] for t in TEMPLATES.values()]
    assert all(any(text.startswith(s) for s in shapes) for text in seen)


# ------------------------------------------------------------------------------ availability phrases
def confirms(text):
    return confirms_launch(text, TH.launch_confirm_phrases, TH.launch_block_phrases)


@pytest.mark.parametrize("text", ["The QN90D Neo QLED is now available in the US.", "QN90D: NOW AVAILABLE",
                                  "The new QN90D is on sale now at retailers.", "On sale now: the QN90D Neo QLED."])
def test_now_available_and_on_sale_now_confirm(text):
    assert confirms(text)


@pytest.mark.parametrize("text", [
    "The QN90D Neo QLED will be available on November 20, 2026.",
    "The QN90D will be available soon; pre-orders are now available.",            # a future clause anywhere blocks it
    "The QN90D launch is delayed. Older models are now available.",
    "Samsung delays the QN90D. It was to be on sale now.",
    "The QN90D launch has been postponed; the old model is now available.",
    "The QN90D was pushed back to 2027; now available: the old one.",
    "Samsung will push back the QN90D, which is not on sale now.",
    "The QN90D goes on sale on November 20, 2026.",                               # no confirming phrase at all
    "Available now in Korea.",                                                    # word order matters: only the listed phrases count
    "",
])
def test_future_delay_and_other_wording_never_confirm(text):
    assert not confirms(text)


def test_phrases_are_whole_words():
    assert not confirms("It is not snow available here.") and not confirms("on sale nowhere")
    assert confirms("Is it now\navailable? Yes, now   available.")
    assert confirms_launch("The relay is now available.", ("now available",), ("delay",))                # "relay" is not "delay"
