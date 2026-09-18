"""Tests for identifying a holding's sector from its ticker.

The sector picks the benchmark. An Indian bank with a sector is measured
against the Nifty Bank index; the same bank without one falls back to the broad
market, and the whole second level of the analysis quietly stops working for it.
So the rules that matter here are about precedence and honesty rather than about
coverage: a sector the client stated must win, the feed must be tried before the
model, and whatever answered must be recorded as having answered.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from benchmark_pulse import sectors
from benchmark_pulse.benchmarks import canonical_sector
from benchmark_pulse.holdings_edit import (
    COLUMNS, blank_row, fill_markets, fill_sectors, missing_sectors,
    set_markets, set_sectors, tidy,
)
from benchmark_pulse.sectors import SectorGuess, identify, market_from_suffix


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Never read or write the real cache from a test."""
    monkeypatch.setattr(sectors, "SECTOR_CACHE", tmp_path / "sectors.json")


class FakeYahoo:
    """Stands in for yfinance, recording what it was asked."""

    def __init__(self, answers: dict[str, dict]):
        self.answers = answers
        self.asked: list[str] = []

    def __call__(self, ticker: str) -> SectorGuess:
        self.asked.append(ticker)
        info = self.answers.get(ticker)
        if not info:
            return SectorGuess(ticker=ticker, note="no sector held")
        raw = info["sector"]
        return SectorGuess(ticker=ticker, sector=canonical_sector(raw), raw=raw,
                           industry=info.get("industry", ""), source="yahoo")


class FakeModel:
    """A live LLM client that answers from a lookup table."""

    is_live = True

    def __init__(self, answers: dict[str, str]):
        self.answers = answers
        self.asked: list[str] = []

    def structured(self, prompt, schema, system="", **_kw):
        ticker = next((t for t in self.answers if t in prompt), None)
        self.asked.append(prompt)
        return {"sector": self.answers.get(ticker, ""), "reason": "because"}


def row(ticker="AAPL", name="Apple Inc.", sector="Technology", country="US"):
    return {"Security Name": name, "Ticker": ticker, "Ccy": "USD",
            "Quantity": 100.0, "Avg Cost": 10.0,
            "Purchase Date": pd.Timestamp("2024-01-02").date(),
            "Country": country, "Sector": sector}


# -- translating what a source said ------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Financial Services", "Financials"),      # Yahoo's name for banks
    ("Healthcare", "Health Care"),
    ("Basic Materials", "Materials"),
    ("Consumer Cyclical", "Consumer Discretionary"),
    ("Consumer Defensive", "Consumer Staples"),
    ("Communication Services", "Communication Services"),
    ("Technology", "Technology"),
    ("Energy", "Energy"),
])
def test_yahoos_own_vocabulary_maps_onto_the_benchmark_universe(raw, expected):
    """Yahoo says Consumer Cyclical where the index family says Consumer
    Discretionary. Left untranslated, every carmaker fell through to the broad
    market benchmark."""
    assert canonical_sector(raw) == expected


def test_a_label_no_index_family_recognises_is_not_forced():
    assert canonical_sector("Blank Cheque") == ""
    assert canonical_sector("") == ""


# -- precedence --------------------------------------------------------------

def test_the_feed_is_asked_before_the_model(monkeypatch):
    yahoo = FakeYahoo({"AAPL": {"sector": "Technology"}})
    model = FakeModel({"AAPL": "Energy"})
    monkeypatch.setattr(sectors, "from_yahoo", yahoo)

    guess = identify("AAPL", "Apple Inc.", "US", client=model)

    assert guess.sector == "Technology"
    assert guess.source == "yahoo"
    assert model.asked == [], "the model must not be asked when the feed answered"


def test_the_model_answers_only_where_the_feed_does_not(monkeypatch):
    yahoo = FakeYahoo({})                       # holds nothing
    model = FakeModel({"NEWCO.NS": "Industrials"})
    monkeypatch.setattr(sectors, "from_yahoo", yahoo)

    guess = identify("NEWCO.NS", "Newco Ltd", "IN", client=model)

    assert guess.sector == "Industrials"
    assert guess.source == "model"
    assert model.asked, "the model should have been asked"


def test_the_model_can_be_switched_off(monkeypatch):
    model = FakeModel({"NEWCO.NS": "Industrials"})
    monkeypatch.setattr(sectors, "from_yahoo", FakeYahoo({}))

    guess = identify("NEWCO.NS", client=model, use_model=False)

    assert not guess.found
    assert model.asked == []


def test_an_unidentifiable_company_returns_empty_rather_than_a_guess(monkeypatch):
    """Inventing a sector would silently pick a benchmark. Returning nothing
    leaves the holding on its market index, which is defensible."""
    monkeypatch.setattr(sectors, "from_yahoo", FakeYahoo({}))
    guess = identify("ZZZZ", client=FakeModel({}))
    assert guess.sector == ""
    assert not guess.found
    assert guess.note


# -- saying who answered -----------------------------------------------------

def test_the_source_is_recorded_and_readable():
    guess = SectorGuess(ticker="ICICIBANK.NS", sector="Financials",
                        raw="Financial Services", source="yahoo")
    assert "Yahoo Finance" in guess.attribution
    assert "Financial Services" in guess.attribution, (
        "the source's own wording belongs in the attribution, because it is "
        "what someone would find if they went and checked")


def test_a_model_answer_says_it_is_a_model_answer():
    guess = SectorGuess(ticker="NEWCO", sector="Industrials",
                        raw="Industrials", source="model")
    assert "model" in guess.attribution.lower()


def test_an_unidentified_holding_says_so():
    assert SectorGuess(ticker="ZZZZ").attribution == "not identified"


# -- the cache ---------------------------------------------------------------

def test_an_answer_is_remembered(monkeypatch):
    yahoo = FakeYahoo({"AAPL": {"sector": "Technology"}})
    monkeypatch.setattr(sectors, "from_yahoo", yahoo)

    identify("AAPL")
    identify("AAPL")

    assert yahoo.asked == ["AAPL"], "a sector does not change between two calls"


def test_a_failure_is_not_remembered(monkeypatch):
    """Caching 'unknown' would make a transient outage permanent."""
    yahoo = FakeYahoo({})
    monkeypatch.setattr(sectors, "from_yahoo", yahoo)

    identify("AAPL", use_model=False)
    identify("AAPL", use_model=False)

    assert len(yahoo.asked) == 2


def test_refresh_asks_again(monkeypatch):
    yahoo = FakeYahoo({"AAPL": {"sector": "Technology"}})
    monkeypatch.setattr(sectors, "from_yahoo", yahoo)

    identify("AAPL")
    identify("AAPL", refresh=True)

    assert len(yahoo.asked) == 2


def test_a_corrupt_cache_does_not_break_the_lookup(monkeypatch):
    sectors.SECTOR_CACHE.parent.mkdir(parents=True, exist_ok=True)
    sectors.SECTOR_CACHE.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(sectors, "from_yahoo",
                        FakeYahoo({"AAPL": {"sector": "Technology"}}))

    assert identify("AAPL").sector == "Technology"


def test_the_cache_can_be_read_back_for_the_sources_screen(monkeypatch):
    monkeypatch.setattr(sectors, "from_yahoo",
                        FakeYahoo({"AAPL": {"sector": "Technology"}}))
    identify("AAPL")

    everything = sectors.cached()
    assert everything["AAPL"].sector == "Technology"
    assert everything["AAPL"].source == "yahoo"
    assert json.loads(sectors.SECTOR_CACHE.read_text(encoding="utf-8"))


# -- filling the grid --------------------------------------------------------

def frame(rows):
    return pd.DataFrame(rows, columns=COLUMNS)


def test_only_blank_sectors_are_looked_up():
    grid = frame([row(), row("XOM", "Exxon", sector="")])
    assert [t for t, _, _ in missing_sectors(grid)] == ["XOM"]


def test_a_stated_sector_is_never_overwritten():
    """The client's own file, or an analyst's own typing, outranks any feed --
    they may know something about the business a classification table does
    not."""
    grid = frame([row(sector="Semiconductors")])
    filled_frame, filled, unresolved = fill_sectors(
        grid, lambda t, n, c: SectorGuess(t, "Financials", source="yahoo"))

    assert filled == [] and unresolved == []
    assert filled_frame.loc[0, "Sector"] == "Semiconductors"


def test_a_blank_sector_is_filled_and_reported():
    grid = frame([row("RELIANCE.NS", "Reliance", sector="", country="IN")])
    filled_frame, filled, unresolved = fill_sectors(
        grid, lambda t, n, c: SectorGuess(t, "Energy", raw="Energy",
                                          source="yahoo"))

    assert filled_frame.loc[0, "Sector"] == "Energy"
    assert filled == [("RELIANCE.NS", "Energy", "Yahoo Finance")]
    assert unresolved == []


def test_a_source_that_raises_leaves_the_cell_blank_and_says_why():
    """One unreachable lookup must not stop the other nine being filled, and
    the failure has to be reported rather than swallowed."""
    def flaky(ticker, name, country):
        if ticker == "XOM":
            raise RuntimeError("network down")
        return SectorGuess(ticker, "Technology", source="yahoo")

    grid = frame([row("AAPL", sector=""), row("XOM", "Exxon", sector="")])
    filled_frame, filled, unresolved = fill_sectors(grid, flaky)

    assert filled_frame.loc[0, "Sector"] == "Technology"
    assert str(filled_frame.loc[1, "Sector"]) == ""
    assert [t for t, _, _ in filled] == ["AAPL"]
    assert [t for t, _, _ in unresolved] == ["XOM"]
    assert "network down" in unresolved[0][2]


def test_a_company_no_source_can_place_is_reported_with_its_reason():
    """'We asked and nothing could place it' has to be distinguishable from
    'nobody has asked yet' -- otherwise the only offer the screen can make is
    to run the same failing lookup again."""
    grid = frame([row("NEWCO.NS", "Newco Ltd", sector="", country="IN")])
    filled_frame, filled, unresolved = fill_sectors(
        grid, lambda t, n, c: SectorGuess(t, note="the model did not "
                                                  "recognise this company"))

    assert filled == []
    assert unresolved == [("NEWCO.NS", "Newco Ltd",
                           "the model did not recognise this company")]
    pd.testing.assert_frame_equal(tidy(grid), filled_frame)


def test_an_unresolved_holding_without_a_reason_still_gets_one():
    grid = frame([row(sector="")])
    _frame, _filled, unresolved = fill_sectors(grid,
                                               lambda t, n, c: SectorGuess(t))
    assert unresolved[0][2], "a blank reason tells the reader nothing"


# -- setting one by hand -----------------------------------------------------

def test_a_sector_chosen_by_hand_is_written_as_stated():
    grid = frame([row("NEWCO.NS", "Newco Ltd", sector="", country="IN")])
    out = set_sectors(grid, {"NEWCO.NS": "Industrials"})
    assert out.loc[0, "Sector"] == "Industrials"


def test_choosing_by_hand_overrules_what_a_source_would_have_said():
    """The analyst is the authority here, not the fallback. A sector they
    state is never looked up again and never overwritten."""
    grid = frame([row("AAPL", sector="")])
    stated = set_sectors(grid, {"AAPL": "Consumer Discretionary"})

    _frame, filled, unresolved = fill_sectors(
        stated, lambda t, n, c: SectorGuess(t, "Technology", source="yahoo"))

    assert filled == [] and unresolved == []
    assert stated.loc[0, "Sector"] == "Consumer Discretionary"


def test_a_partly_completed_form_sets_only_what_was_answered():
    grid = frame([row("AAPL", sector=""), row("XOM", "Exxon", sector="")])
    out = set_sectors(grid, {"AAPL": "Technology", "XOM": ""})

    assert out.loc[0, "Sector"] == "Technology"
    assert str(out.loc[1, "Sector"]) == ""


def test_setting_a_ticker_that_is_not_held_changes_nothing():
    grid = frame([row("AAPL", sector="")])
    out = set_sectors(grid, {"TSLA": "Consumer Discretionary"})
    assert str(out.loc[0, "Sector"]) == ""
    assert len(out) == 1


def test_the_manual_choices_are_the_sectors_the_benchmarks_are_built_on():
    """The dropdown offers these, so every one of them has to map to a real
    benchmark rather than land back at 'unclassified'."""
    for name in sectors.CANONICAL:
        assert canonical_sector(name) == name


# -- where a security is listed ----------------------------------------------

@pytest.mark.parametrize("ticker,expected", [
    ("2222.SR", "SA"),
    ("ICICIBANK.NS", "IN"),
    ("ASML.AS", "NL"),
    ("NOVO-B.CO", "DK"),
    ("7203.T", "JP"),
    ("HSBA.L", "GB"),
    ("PETR4.SA", "BR"),      # Sao Paulo, one keystroke from Saudi's .SR
    ("AAPL", ""),            # bare: the suffix says nothing, so neither do we
    ("", ""),
])
def test_the_exchange_suffix_names_the_listing_country(ticker, expected):
    assert market_from_suffix(ticker) == expected


def test_an_unrecognised_symbol_is_not_quietly_american():
    """The older helper answers 'US' for anything it does not know, which is
    right often enough to be dangerous: a Tadawul code typed without .SR would
    be filed as a US listing and measured against the S&P."""
    assert market_from_suffix("2222") == ""
    assert market_from_suffix("SOMETHING.XYZ") == ""


@pytest.mark.parametrize("market,expected", [
    ("us_market", "US"), ("in_market", "IN"), ("nl_market", "NL"),
    ("dk_market", "DK"), ("gb_market", "GB"), ("jp_market", "JP"),
    ("sr_market", "SA"),     # Yahoo uses the ticker suffix here, not ISO-2
    ("", ""), ("something_else", ""),
])
def test_the_feeds_market_field_maps_onto_iso_codes(market, expected):
    assert sectors._market_from_info({"market": market})[0] == expected


class FakeYf:
    """A stand-in yfinance module, so a payload can be tested end to end."""

    def __init__(self, payload):
        self.payload = payload

    def Ticker(self, code):  # noqa: N802 - matching yfinance's own spelling
        outer = self

        class _T:
            info = outer.payload
        return _T()


def yahoo_says(monkeypatch, payload):
    monkeypatch.setitem(__import__("sys").modules, "yfinance", FakeYf(payload))


def test_the_listing_country_is_the_venue_not_the_head_office(monkeypatch):
    """The Infosys ADR trades in dollars on the NYSE while Yahoo's `country`
    says India. Reading `country` would benchmark a US listing against Indian
    indices it does not trade in."""
    yahoo_says(monkeypatch, {"sector": "Technology", "market": "us_market",
                             "country": "India", "currency": "USD",
                             "fullExchangeName": "NYSE"})

    profile = sectors.from_yahoo("INFY")

    assert profile.market == "US"
    assert profile.market_raw == "NYSE"
    assert profile.currency == "USD"


def test_the_suffix_wins_over_the_feed(monkeypatch):
    """A fact carried by the symbol itself does not need confirming, and it
    still stands on a day the feed is wrong or unreachable."""
    yahoo_says(monkeypatch, {"sector": "Energy", "market": "us_market",
                             "currency": "USD"})

    profile = sectors.from_yahoo("2222.SR")

    assert profile.market == "SA"
    assert profile.market_source == "suffix"


def test_the_suffix_survives_the_feed_being_down(monkeypatch):
    class Broken(FakeYf):
        def Ticker(self, code):  # noqa: N802
            raise RuntimeError("no network")

    monkeypatch.setitem(__import__("sys").modules, "yfinance", Broken({}))
    profile = sectors.from_yahoo("ICICIBANK.NS")

    assert profile.market == "IN"
    assert not profile.found, "a sector still needs a source"


def test_pence_is_refused_rather_than_written_as_pounds(monkeypatch):
    """London quotes in pence and Yahoo marks it GBp. Writing GBP against a
    price series denominated in pence overstates the position a hundredfold."""
    yahoo_says(monkeypatch, {"sector": "Financials", "market": "gb_market",
                             "currency": "GBp"})

    profile = sectors.from_yahoo("HSBA.L")

    assert profile.currency == ""
    assert "fractional unit" in profile.currency_note
    assert "GBP" not in profile.currency_note, (
        "naming the parent currency guesses: GBp is a hundredth of GBP but "
        "ZAc is a hundredth of ZAR")
    assert profile.market == "GB", "the listing is still known"


def test_the_model_is_never_asked_where_something_is_listed(monkeypatch):
    """A listing venue is a fact the ticker or the feed states outright. Only
    the judgement call goes to the model, and its answer must not discard what
    the feed already established."""
    monkeypatch.setattr(sectors, "from_yahoo", lambda t: SectorGuess(
        ticker=t, market="IN", market_source="suffix", currency="INR",
        note="no sector held"))
    model = FakeModel({"NEWCO.NS": "Industrials"})

    profile = identify("NEWCO.NS", "Newco Ltd", "IN", client=model)

    assert profile.sector == "Industrials" and profile.source == "model"
    assert profile.market == "IN", "the feed's listing must survive"
    assert profile.currency == "INR"


def test_a_listing_is_cached_even_without_a_sector(monkeypatch):
    monkeypatch.setattr(sectors, "from_yahoo", lambda t: SectorGuess(
        ticker=t, market="IN", market_source="suffix"))

    identify("NEWCO.NS", use_model=False)
    assert sectors.cached()["NEWCO.NS"].market == "IN"


def test_the_market_attribution_names_the_venue():
    profile = SectorGuess(ticker="2222.SR", market="SA", market_raw="Saudi",
                          market_source="suffix")
    assert "suffix" in profile.market_attribution
    assert "Saudi" in profile.market_attribution
    assert SectorGuess(ticker="X").market_attribution == "not identified"


# -- filling and setting the market ------------------------------------------

def test_a_blank_market_is_filled_and_reported():
    grid = frame([row("NEWCO.NS", "Newco Ltd", country="")])
    filled_frame, filled, unresolved = fill_markets(
        grid, lambda t, n, c: SectorGuess(t, market="IN",
                                          market_source="suffix"))

    assert filled_frame.loc[0, "Country"] == "IN"
    assert filled == [("NEWCO.NS", "IN", "the exchange suffix on the ticker")]
    assert unresolved == []


def test_a_stated_market_is_never_overwritten():
    grid = frame([row("BABA", "Alibaba", country="US")])
    _f, filled, unresolved = fill_markets(
        grid, lambda t, n, c: SectorGuess(t, market="CN"))
    assert filled == [] and unresolved == []


def test_a_market_nothing_can_place_is_reported_with_its_reason():
    grid = frame([row("ODD", "Odd Ltd", country="")])
    _f, filled, unresolved = fill_markets(
        grid, lambda t, n, c: SectorGuess(t, note="no venue stated"))
    assert filled == []
    assert unresolved == [("ODD", "Odd Ltd", "no venue stated")]


def test_a_market_chosen_by_hand_is_written_as_stated():
    grid = frame([row("ODD", "Odd Ltd", country="")])
    assert set_markets(grid, {"ODD": "SA"}).loc[0, "Country"] == "SA"


def test_a_new_row_leaves_an_unplaceable_market_blank():
    """Defaulting to a US listing in dollars is how a Tadawul code ends up
    measured against the S&P."""
    blank = blank_row("SOMETHING")
    assert blank["Country"] == ""
    assert blank["Ccy"] == ""

    known = blank_row("2222.SR")
    assert known["Country"] == "SA" and known["Ccy"] == "SAR"
