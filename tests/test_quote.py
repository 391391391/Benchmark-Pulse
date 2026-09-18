"""Tests for the live price shown on a holding's page.

The number itself is the easy part. What has to hold is the claim made about
it: a price fetched from the market and a price carried over from last night's
close are both useful and are not the same statement, and a book spread across
four timezones will always have some exchanges trading and some shut. So the
rules pinned here are about honesty and about never failing -- a holding page
that cannot reach the market should show the last close and say so, not an
exception.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from benchmark_pulse.marketdata import MarketData, Quote


class FakeYf:
    """A stand-in yfinance module returning one prepared payload."""

    def __init__(self, payload, fail: bool = False):
        self.payload = payload
        self.fail = fail
        self.calls = 0

    def Ticker(self, code):  # noqa: N802 - matching yfinance's own spelling
        outer = self
        outer.calls += 1
        if outer.fail:
            raise RuntimeError("429 Too Many Requests")

        class _T:
            info = outer.payload
        return _T()


def market(monkeypatch, payload=None, *, fail=False, offline=False,
           closes=(100.0, 110.0)):
    """A MarketData whose price series is stubbed and whose feed is fake."""
    md = MarketData(offline=offline)
    index = pd.date_range(date(2026, 9, 16), periods=len(closes), freq="D")
    monkeypatch.setattr(
        md, "prices",
        lambda ticker, **_kw: pd.Series(list(closes), index=index))
    fake = FakeYf(payload or {}, fail=fail)
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    return md, fake


LIVE = {
    "regularMarketPrice": 337.0,
    "regularMarketPreviousClose": 332.41,
    "currency": "USD",
    "marketState": "REGULAR",
    "regularMarketTime": 1789675201,
    "exchangeTimezoneName": "America/New_York",
    "exchangeTimezoneShortName": "EDT",
}


# -- the live path ----------------------------------------------------------

def test_a_live_quote_carries_the_price_and_the_move(monkeypatch):
    md, _ = market(monkeypatch, LIVE)
    q = md.quote("AAPL")

    assert q.live
    assert q.price == pytest.approx(337.0)
    assert q.currency == "USD"
    assert q.change == pytest.approx(337.0 - 332.41)
    assert q.change_pct == pytest.approx(337.0 / 332.41 - 1)


def test_the_market_state_is_translated_out_of_yahoos_vocabulary(monkeypatch):
    """A book across four timezones always has some exchanges shut. Calling
    every price 'live' would be wrong about most of them."""
    for state, expected in (("REGULAR", "market open"), ("PRE", "pre-market"),
                            ("POST", "after hours"),
                            ("CLOSED", "market closed")):
        md, _ = market(monkeypatch, {**LIVE, "marketState": state})
        assert md.quote(f"X{state}").state_label == expected


def test_the_quote_time_is_shown_in_the_exchanges_own_timezone(monkeypatch):
    """An analyst reading a Tadawul quote wants Riyadh time, not their own."""
    md, _ = market(monkeypatch, LIVE)
    q = md.quote("AAPL")

    assert q.when is not None
    assert q.when.utcoffset() != timezone.utc.utcoffset(None), (
        "the timestamp should have been moved into the exchange's zone")
    assert "EDT" in q.stamp


def test_an_unreadable_timezone_still_produces_a_quote(monkeypatch):
    md, _ = market(monkeypatch, {**LIVE, "exchangeTimezoneName": "Mars/Olympus"})
    q = md.quote("AAPL")
    assert q.live and q.when is not None


# -- the fallback -----------------------------------------------------------

def test_a_refused_feed_falls_back_to_the_last_close_and_says_so(monkeypatch):
    md, _ = market(monkeypatch, fail=True, closes=(100.0, 110.0))
    q = md.quote("AAPL")

    assert not q.live
    assert q.price == pytest.approx(110.0)
    assert q.previous_close == pytest.approx(100.0)
    assert "did not answer" in q.note
    assert "close of" in q.stamp


def test_a_feed_with_no_price_falls_back_rather_than_reporting_none(monkeypatch):
    md, _ = market(monkeypatch, {"currency": "USD", "marketState": "CLOSED"})
    q = md.quote("AAPL")
    assert not q.live
    assert q.price == pytest.approx(110.0)
    assert "no current price" in q.note


def test_offline_mode_never_reaches_for_the_network(monkeypatch):
    """Offline is the setting that makes a conference room safe. It has to mean
    no call at all, not a call that fails quietly."""
    md, fake = market(monkeypatch, LIVE, offline=True)
    q = md.quote("AAPL")

    assert fake.calls == 0
    assert not q.live
    assert "offline" in q.note


def test_a_single_close_still_quotes_without_a_change(monkeypatch):
    md, _ = market(monkeypatch, fail=True, closes=(110.0,))
    q = md.quote("AAPL")
    assert q.price == pytest.approx(110.0)
    assert q.previous_close is None
    assert q.change is None and q.change_pct is None


# -- the hold ---------------------------------------------------------------

def test_a_quote_is_held_briefly_so_a_rerun_does_not_refetch(monkeypatch):
    """The endpoint behind this is the one Yahoo rate-limits hardest, and a
    Streamlit page re-runs on every widget touch."""
    md, fake = market(monkeypatch, LIVE)
    md.quote("AAPL")
    md.quote("AAPL")
    md.quote("AAPL")
    assert fake.calls == 1


def test_the_hold_can_be_bypassed(monkeypatch):
    md, fake = market(monkeypatch, LIVE)
    md.quote("AAPL")
    md.quote("AAPL", max_age_seconds=0)
    assert fake.calls == 2


def test_a_failed_quote_is_not_held(monkeypatch):
    """Caching an outage would make a transient refusal last the session."""
    md, fake = market(monkeypatch, fail=True)
    md.quote("AAPL")
    md.quote("AAPL")
    assert fake.calls == 2


# -- the shape of the answer ------------------------------------------------

def test_a_zero_previous_close_does_not_divide_by_zero():
    q = Quote(ticker="X", price=10.0, previous_close=0.0)
    assert q.change is None and q.change_pct is None


def test_a_quote_with_nothing_to_say_about_time_says_that():
    assert Quote(ticker="X", price=1.0).stamp == "time unknown"


def test_a_close_names_its_date_rather_than_implying_it_is_now():
    q = Quote(ticker="X", price=1.0, as_of=date(2026, 9, 17))
    assert q.stamp == "close of 17 Sep 2026"


def test_an_unknown_state_is_left_blank_rather_than_guessed():
    assert Quote(ticker="X", price=1.0, state="WEIRD").state_label == ""
    assert Quote(ticker="X", price=1.0).state_label == ""


# -- only an open exchange counts as live -----------------------------------

def test_only_an_open_exchange_counts_as_trading():
    """Outside regular hours the feed's 'current price' is the last completed
    session's. Labelling that live would be the overclaim this tool exists to
    avoid."""
    assert Quote(ticker="X", price=1.0, live=True, state="REGULAR").trading
    for shut in ("PRE", "POST", "CLOSED", ""):
        assert not Quote(ticker="X", price=1.0, live=True, state=shut).trading


def test_a_fallback_close_is_never_trading():
    assert not Quote(ticker="X", price=1.0, live=False, state="REGULAR").trading


def test_the_live_path_marks_an_open_market_as_trading(monkeypatch):
    md, _ = market(monkeypatch, LIVE)
    assert md.quote("AAPL").trading

    md2, _ = market(monkeypatch, {**LIVE, "marketState": "PRE"})
    q = md2.quote("AAPL")
    assert q.live and not q.trading, (
        "a pre-market quote is real data and is not a trade happening now")
