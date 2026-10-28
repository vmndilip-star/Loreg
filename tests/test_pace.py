"""Required pace to a stated launch date: ceil(units / days_until), with the launch day and a past launch handled on purpose.

It is a required pace, not a sales forecast, and the synthetic demo article's made-up date is relative to the stock list's date and labelled.
The clock is injected; nothing reads the system date. Offline."""

import json
import re
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.agents.market import written_date_to_iso
from app.api.main import create_app
from app.core.pace import NOTHING_TO_SELL, PAST, TODAY, UPCOMING, pace_text, required_pace
from app.watch.demo_news import SYNTHETIC_LAUNCH_DAYS, synthetic_excerpt, synthetic_launch
from csv_truth import Truth
from test_news_provenance import Clock, build, reply_to
from test_watch import FakeWeb, article, rss
from auth import AuthedClient

LAUNCH = date(2026, 11, 20)
WRITTEN = "November 20, 2026"


# ------------------------------------------------------------------------------ the function
@pytest.mark.parametrize("units, days, per_day", [(46, 23, 2), (47, 23, 3), (1, 23, 1), (23, 23, 1), (24, 23, 2), (100, 1, 100), (7, 30, 1)])
def test_the_pace_is_units_over_days_rounded_up(units, days, per_day):
    p = required_pace(units, LAUNCH, LAUNCH - timedelta(days=days))
    assert (p.status, p.units, p.days_until, p.per_day) == (UPCOMING, units, days, per_day)
    assert p.per_day * p.days_until >= units > (p.per_day - 1) * p.days_until          # the smallest whole pace that gets there


def test_on_the_day_of_the_launch_there_is_no_pace_and_nothing_is_divided_by_zero():
    p = required_pace(40, LAUNCH, LAUNCH)
    assert (p.status, p.days_until, p.per_day) == (TODAY, 0, None)
    assert f"The launch is today ({WRITTEN}), so there is no day left to sell 40 units before it." in pace_text(p, WRITTEN)


@pytest.mark.parametrize("ago", [1, 2, 400])
def test_after_the_launch_no_pace_applies(ago):
    p = required_pace(40, LAUNCH, LAUNCH + timedelta(days=ago))
    assert (p.status, p.days_until, p.per_day) == (PAST, -ago, None)
    assert f"was {ago} day{'' if ago == 1 else 's'} ago, so no required pace applies." in pace_text(p, WRITTEN)


def test_nothing_on_the_shelf_needs_no_pace():
    for units in (0, -3):
        p = required_pace(units, LAUNCH, LAUNCH - timedelta(days=5))
        assert (p.status, p.per_day) == (NOTHING_TO_SELL, None)


def test_every_wording_says_it_is_not_a_forecast_and_the_synthetic_one_says_where_the_date_is_from():
    for today in (LAUNCH - timedelta(days=9), LAUNCH, LAUNCH + timedelta(days=9)):
        for units in (0, 12):
            p = required_pace(units, LAUNCH, today)
            assert "not a sales forecast" in pace_text(p, WRITTEN) and "synthetic" not in pace_text(p, WRITTEN)
            assert "synthetic demo article" in pace_text(p, WRITTEN, synthetic=True)
    one_day = pace_text(required_pace(5, LAUNCH, LAUNCH - timedelta(days=1)), WRITTEN)
    assert "1 day from today" in one_day and "5 units a day" in one_day


# ------------------------------------------------------------------------------ the synthetic date
@pytest.mark.parametrize("as_of", [date(2026, 10, 26), date(2026, 12, 20), date(2027, 2, 10)])
def test_the_synthetic_launch_date_is_relative_to_the_stock_list_date_and_reads_back_as_the_same_day(as_of):
    text, iso = synthetic_launch(as_of)
    assert iso == (as_of + timedelta(days=SYNTHETIC_LAUNCH_DAYS)).isoformat() and written_date_to_iso(text) == iso
    assert text in synthetic_excerpt(as_of) and synthetic_excerpt(as_of).startswith("Demo article (synthetic).") and len(synthetic_excerpt(as_of)) <= 280


# ------------------------------------------------------------------------------ in the app
def exposed_units(catalog_dir):
    return sum(i["qty"] for i in Truth(catalog_dir, master="upload_demo_200.csv").launch_exposed())


def group(c):
    return c.get("/api/launch/exposed").json()["groups"][0]


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.setenv("LOREG_DEMO", "1")


def demo_app(tmp_path, clock=None, as_of="2026-10-26"):
    return AuthedClient(create_app(tmp_path / "p.sqlite", as_of=as_of, http_factory=FakeWeb(b"").factory(), clock=clock or Clock()))


def test_the_demo_alert_has_a_labelled_synthetic_launch_date_and_the_pace_to_it(tmp_path, catalog_dir, demo):
    c = demo_app(tmp_path)
    c.post("/api/demo/load")
    g, units = group(c), exposed_units(catalog_dir)
    assert g["source"]["launch_date"] == WRITTEN and g["source"]["origin"] == "demo_synthetic" and units > 0
    p = g["pace"]
    assert (p["status"], p["units"], p["days_until"], p["per_day"]) == (UPCOMING, units, 25, -(-units // 25))
    assert f"{p['per_day']} unit" in p["text"] and "not a sales forecast" in p["text"] and "synthetic demo article" in p["text"]


@pytest.mark.parametrize("today, status", [(datetime(2026, 11, 19, 12, tzinfo=timezone.utc), UPCOMING), (datetime(2026, 11, 20, 12, tzinfo=timezone.utc), TODAY),
                                           (datetime(2026, 11, 21, 12, tzinfo=timezone.utc), PAST)])
def test_the_clock_decides_whether_the_launch_is_ahead_today_or_past(tmp_path, demo, today, status):
    clock = Clock()
    c = demo_app(tmp_path, clock)
    c.post("/api/demo/load")
    clock.t = today
    p = group(c)["pace"]
    assert p["status"] == status and (p["per_day"] is None) == (status != UPCOMING)


def test_the_chat_reply_and_the_alert_card_carry_the_pace_and_the_date_question_still_says_synthetic(tmp_path, demo):
    c = demo_app(tmp_path)
    c.post("/api/demo/load")
    want = group(c)["pace"]["text"]
    body = c.post("/api/ask", json={"question": "What should I clear before the launch?"}).text
    reply = "".join(json.loads(f.split("data: ", 1)[1])["text"] for f in body.split("\n\n") if f.startswith("event: token"))
    card = next(json.loads(f.split("data: ", 1)[1])["cards"][0] for f in body.split("\n\n") if f.startswith("event: cards"))
    assert want in reply and card["pace"]["text"] == want
    answer = reply_to(c, "When will Samsung launch the new QN90D?")
    assert answer.startswith("From your stored news only: This source is a synthetic demo article, not real news.")
    assert WRITTEN in answer and want in answer
    assert set(re.findall(r"\b(?:19|20)\d\d\b", answer)) == {"2026"}                       # no year but the stored ones


def test_the_synthetic_date_follows_the_stock_list_date_when_the_demo_is_loaded_again(tmp_path, demo):
    demo_app(tmp_path).post("/api/demo/load")
    c2 = demo_app(tmp_path, as_of="2026-11-02")
    c2.post("/api/demo/load")
    s = group(c2)["source"]
    assert (s["launch_date"], s["launch_iso"]) == ("November 27, 2026", "2026-11-27") and s["excerpt"] == synthetic_excerpt(date(2026, 11, 2))


def test_a_real_article_with_a_stated_date_gets_the_same_pace_and_no_synthetic_wording(tmp_path, catalog_dir):
    c = build(tmp_path, catalog_dir, FakeWeb(rss([article()])))                           # the article says "March 12, 2027"
    c.post("/api/watch/scan")
    g = group(c)
    assert g["source"]["origin"] == "feed" and g["source"]["launch_iso"] == "2027-03-12"
    p = g["pace"]
    assert p["status"] == UPCOMING and p["days_until"] == (date(2027, 3, 12) - date(2026, 10, 26)).days
    assert "synthetic" not in p["text"] and "not a sales forecast" in p["text"]


def test_an_article_with_no_launch_date_has_no_pace(tmp_path, catalog_dir):
    c = build(tmp_path, catalog_dir, FakeWeb(rss([article(html="SYNTHETIC. Samsung announced the QN90D Neo QLED refresh will launch this spring.")])))
    c.post("/api/watch/scan")
    g = group(c)
    assert g["source"]["attached"] and g["source"]["launch_date"] is None and g["pace"] is None
