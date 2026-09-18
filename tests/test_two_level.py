"""Tests for two-level benchmarking.

The portfolio-level result is an aggregate, which makes its failures quiet: a
currency conversion applied at the wrong date or a weighting taken in local
currency produces a plausible-looking number that is simply wrong. These pin
down the parts that cannot be eyeballed.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from benchmark_pulse.aggregate import build_portfolio_stream, contribution_weights
from benchmark_pulse.benchmarks import (
    COUNTRY_MARKET, DEVELOPED, EMERGING, MANDATES, MARKETS, SECTORS, UNIVERSE,
    Level, _rules_holding_benchmark, _rules_mandate, assign_holding_benchmark,
    canonical_sector, classify_exposure, holding_candidates, sectors_in,
)
from benchmark_pulse.cashflows import AssetClass, CashflowStream, FlowType
from benchmark_pulse.metrics import xirr


class FakeMarketData:
    """Fixed rates, so conversion behaviour is testable without the network."""

    RATES = {("INR", "USD"): 0.012, ("SAR", "USD"): 0.2667, ("USD", "USD"): 1.0}

    def fx_rate(self, base, quote, on=None):
        if base.upper() == quote.upper():
            return 1.0
        return self.RATES[(base.upper(), quote.upper())]

    @staticmethod
    def is_pegged(currency):
        return currency.upper() == "SAR"


def equity(asset_id, country, currency, cost, value, sector="Technology",
           bought=date(2021, 1, 1), valued=date(2026, 1, 1)) -> CashflowStream:
    s = CashflowStream(asset_id=asset_id, name=asset_id,
                       asset_class=AssetClass.PUBLIC_EQUITY,
                       currency=currency, country=country, sector=sector)
    s.add(bought, -cost, FlowType.PURCHASE)
    s.set_terminal_value(valued, value)
    return s


# -- exposure classification -----------------------------------------------

def test_exposure_is_weighted_by_capital_not_by_count():
    """One large US position must not be outvoted by three tiny Indian ones."""
    streams = [equity("US1", "US", "USD", 1000, 1500),
               equity("IN1", "IN", "INR", 10, 15),
               equity("IN2", "IN", "INR", 10, 15),
               equity("IN3", "IN", "INR", 10, 15)]
    weights = {"US1": 900_000.0, "IN1": 10_000.0, "IN2": 10_000.0,
               "IN3": 10_000.0}

    exposure = classify_exposure(streams, weights)
    assert exposure["developed_pct"] == pytest.approx(0.9677, abs=1e-3)
    assert exposure["emerging_pct"] == pytest.approx(0.0323, abs=1e-3)


def test_exposure_without_weights_falls_back_to_equal_weighting():
    streams = [equity("US1", "US", "USD", 1, 1), equity("IN1", "IN", "INR", 1, 1)]
    exposure = classify_exposure(streams)
    assert exposure["developed_pct"] == pytest.approx(0.5)
    assert exposure["emerging_pct"] == pytest.approx(0.5)


def test_developed_and_emerging_sets_do_not_overlap():
    """A country in both buckets would be counted twice and silently inflate
    whichever branch happened to be tested first."""
    assert not (DEVELOPED & EMERGING)


# -- mandate selection ------------------------------------------------------

def test_mixed_book_gets_an_all_country_benchmark():
    """The case the mentor's design exists for: measuring a genuinely mixed
    book against a developed index would report an asset-class bet as skill."""
    ticker, label, _ = _rules_mandate(
        {"developed_pct": 0.41, "emerging_pct": 0.59, "by_country":
         {"US": 0.33, "SA": 0.32, "IN": 0.27, "NL": 0.08}})
    assert ticker == "ACWI"
    assert "Global" in label


def test_a_spread_book_stays_global_even_when_developed_heavy():
    """Leaning developed is not a mandate. Only a dominant single market or a
    dominant sector changes the portfolio's type."""
    ticker, label, _ = _rules_mandate(
        {"developed_pct": 0.92, "emerging_pct": 0.08,
         "by_country": {"US": 0.5, "JP": 0.42, "IN": 0.08},
         "by_sector": {"Technology": 0.4, "Financials": 0.35, "Energy": 0.25}})
    assert ticker == "ACWI"
    assert label == "Global Equity"


def test_single_country_concentration_sets_the_type():
    """A book 90% in India is an India mandate."""
    ticker, label, _ = _rules_mandate(
        {"developed_pct": 0.05, "emerging_pct": 0.95,
         "by_country": {"IN": 0.90, "SA": 0.05, "US": 0.05},
         "by_sector": {"Technology": 0.5, "Financials": 0.5}})
    assert ticker == "^CRSLDX"
    assert label == "India Equity"


def test_us_concentration_gets_the_sp500():
    ticker, label, _ = _rules_mandate(
        {"by_country": {"US": 0.85, "NL": 0.15},
         "by_sector": {"Technology": 0.4, "Financials": 0.35, "Energy": 0.25}})
    assert ticker == "^SP500TR"
    assert label == "US Focused"


def test_the_gcc_is_recognised_as_a_bloc_not_a_country():
    """No single Gulf state dominates, but the region does. Tested on the bloc
    because a GCC mandate is regional by construction."""
    ticker, label, rationale = _rules_mandate(
        {"gcc_pct": 0.82,
         "by_country": {"SA": 0.35, "AE": 0.28, "QA": 0.19, "US": 0.18},
         "by_sector": {"Financials": 0.45, "Energy": 0.55}})
    assert ticker == "KSA"
    assert label == "GCC Equity"
    assert "GCC" in rationale


@pytest.mark.parametrize("sector,ticker,label", [
    ("Real Estate", "REET", "REIT Portfolio"),
    ("Financials", "IXG", "Banking Portfolio"),
    ("Technology", "QQQ", "Technology Portfolio"),
])
def test_a_dominant_sector_sets_the_type(sector, ticker, label):
    got, got_label, _ = _rules_mandate(
        {"by_country": {"US": 0.4, "IN": 0.3, "NL": 0.3},
         "by_sector": {sector: 0.78, "Energy": 0.22}})
    assert got == ticker
    assert got_label == label


def test_sector_concentration_beats_country_concentration():
    """A book that is mostly banks is a banking mandate whichever countries
    those banks are in -- the sector claim is the more specific one."""
    ticker, label, _ = _rules_mandate(
        {"by_country": {"US": 0.80, "NL": 0.20},
         "by_sector": {"Financials": 0.75, "Technology": 0.25}})
    assert ticker == "IXG"
    assert label == "Banking Portfolio"


def test_an_overweight_sector_is_not_a_sector_mandate():
    """45% in one sector is a tilt. Measuring it against a single industry
    index would report an allocation choice as stock selection."""
    ticker, label, _ = _rules_mandate(
        {"by_country": {"US": 0.35, "IN": 0.35, "NL": 0.30},
         "by_sector": {"Financials": 0.45, "Technology": 0.35, "Energy": 0.20}})
    assert ticker == "ACWI"
    assert label == "Global Equity"


def test_every_mandate_type_on_the_list_is_reachable():
    """The seven portfolio types each need a benchmark behind them."""
    types = {b.scope for b in MANDATES.values()}
    assert types == {"Global Equity", "US Focused", "GCC Equity",
                     "India Equity", "REIT Portfolio", "Banking Portfolio",
                     "Technology Portfolio"}


def test_a_price_index_mandate_declares_itself():
    """Only one mandate is price-only -- NSE publishes the Nifty 500 that way
    and no ETF tracking it carries usable history. A price index understates
    the benchmark by its dividend yield and flatters the book, so where one is
    used it has to say so rather than pass as total return."""
    for b in MANDATES.values():
        if b.total_return:
            continue
        assert b.caveat, f"{b.ticker} is a price index with no caveat"
        assert "dividend" in b.caveat.lower()


def test_an_equity_only_workbook_loads_without_warnings(tmp_path):
    """A book with no private-fund or property sheet is the normal case for a
    public-equity tool, not a fault. Those absences used to surface as "could
    not read private funds", which reads as a failure on a page of caveats and
    invites exactly the wrong question in a review."""
    import pandas as pd

    from benchmark_pulse.portfolio import load_workbook

    book = tmp_path / "equities_only.xlsx"
    pd.DataFrame([{
        "Security Name": "Apple Inc.", "Ticker": "AAPL", "Ccy": "USD",
        "Quantity": 100, "Avg Cost": 150.0,
        "Purchase Date": date(2023, 1, 3),
        "Country": "US", "Sector": "Technology",
    }]).to_excel(book, sheet_name="Holdings", index=False)

    class _NoNetwork:
        @staticmethod
        def prices(ticker):
            raise RuntimeError("offline")

    report = load_workbook(book, _NoNetwork())
    missing_sheet = [w for w in report.warnings
                     if "private funds" in w or "direct property" in w]
    assert not missing_sheet, f"absent optional sheets warned: {missing_sheet}"


def test_a_holding_is_benchmarked_in_its_own_currency():
    """A euro stock measured against a dollar index folds the exchange rate
    into the alpha. Every market's benchmark is quoted in that market's own
    currency, with one stated exception."""
    expected = {"US": "USD", "IN": "INR", "NL": "EUR", "JP": "USD",
                "DK": "USD", "SA": "USD"}
    for country, ticker in COUNTRY_MARKET.items():
        if country not in expected:
            continue
        got = UNIVERSE[ticker].currency
        assert got == expected[country], (
            f"{country} is benchmarked in {got}, not {expected[country]}")


def test_the_one_currency_exception_explains_itself():
    """Saudi holdings are in riyals and their benchmark is in dollars. That is
    allowed only because the peg makes the two identical -- and only if the
    benchmark says so, rather than leaving a reader to spot the mismatch."""
    saudi = UNIVERSE[COUNTRY_MARKET["SA"]]
    assert saudi.currency == "USD"
    assert "peg" in saudi.caveat.lower()


def test_currency_impact_is_not_reported_per_holding():
    """Every holding is priced and benchmarked in one currency, so there is no
    translation inside the comparison for a caveat to warn about."""
    from benchmark_pulse.adjustments import assess

    class _FakeMarket:
        @staticmethod
        def is_pegged(currency):
            return currency.upper() == "SAR"

        @staticmethod
        def fx_rate(base, quote, on=None):
            return 0.012

    holding = equity("INFY.NS", "IN", "INR", 100, 150)
    kinds = {a.kind for a in assess(holding, {"irr": 0.12}, _FakeMarket(), "USD")}
    assert "currency" not in kinds


def test_every_substituted_mandate_names_the_substitution():
    """Three of the seven have no exact free tracker. A substitution stated is
    acceptable; one hidden is not."""
    for ticker in ("KSA", "IXG", "QQQ"):
        assert MANDATES[ticker].caveat, f"{ticker} substitutes without saying so"


def test_mandate_and_sector_universes_are_registered_at_their_level():
    for ticker, b in MANDATES.items():
        assert b.level is Level.MANDATE
        assert ticker in UNIVERSE
    for ticker, b in SECTORS.items():
        assert b.level is Level.SECTOR
        assert ticker in UNIVERSE


# -- holding selection: a holding is judged in its own market ---------------
#
# The rule these pin down: an Indian company is measured against an Indian
# benchmark, a US company against a US one. Only the portfolio is measured
# globally, because only the portfolio chose which countries to be in. Charging
# a stock for its market's performance measures the allocator twice and the
# stock not at all.

def test_an_indian_bank_is_never_offered_a_global_financials_index():
    """The mistake this exists to make impossible: ICICI Bank against the S&P
    Global 1200 Financials, which scores it on the India-versus-world trade."""
    offered = {b.ticker for b in holding_candidates("IN")}
    assert "IXG" not in offered
    assert "XLF" not in offered
    assert "BANKBEES.NS" in offered


def test_an_indian_bank_gets_the_nifty_bank_index():
    holding = equity("ICICIBANK.NS", "IN", "INR", 100, 150, sector="Financials")
    ticker, rationale = _rules_holding_benchmark(holding)
    assert ticker == "BANKBEES.NS"
    assert "Nifty Bank" in rationale


def test_a_us_bank_gets_the_us_financials_index():
    """The mirror image, so the rule is symmetric rather than India-specific."""
    holding = equity("JPM", "US", "USD", 100, 150, sector="Financials")
    ticker, _ = _rules_holding_benchmark(holding)
    assert ticker == "XLF"


@pytest.mark.parametrize("country", sorted(set(COUNTRY_MARKET)))
def test_every_candidate_offered_belongs_to_the_holdings_own_market(country):
    """Enforced by the candidate list, not by asking the model nicely."""
    home = COUNTRY_MARKET[country]
    for b in holding_candidates(country):
        assert b.country == country or b.ticker == home, (
            f"{b.ticker} was offered to a {country} holding")


def test_a_market_without_sector_indices_falls_back_to_its_own_broad_index():
    """Saudi Arabia publishes no free total-return sector index. The answer is
    the Saudi market, not a global sector index -- a fair comparison with a
    stated limitation beats a precise-looking comparison against foreign peers.
    """
    holding = equity("1120.SR", "SA", "SAR", 100, 150, sector="Financials")
    ticker, rationale = _rules_holding_benchmark(holding)
    assert ticker == "KSA"
    assert "no total-return Financials index" in rationale


def test_an_indian_sector_without_a_total_return_etf_stays_in_india():
    """Reliance is Energy, and no Indian energy ETF carries usable history. It
    falls back to the Nifty 50 rather than to a US or global energy index."""
    holding = equity("RELIANCE.NS", "IN", "INR", 100, 150, sector="Energy")
    ticker, _ = _rules_holding_benchmark(holding)
    assert ticker == "NIFTYBEES.NS"
    assert UNIVERSE[ticker].country == "IN"


def test_an_uncovered_market_still_gets_a_benchmark():
    holding = equity("XYZ", "BR", "BRL", 100, 150, sector="Technology")
    ticker, _ = _rules_holding_benchmark(holding)
    assert ticker in UNIVERSE


def test_every_sector_benchmark_is_total_return():
    """A price index would understate the benchmark by its dividend yield and
    hand every holding measured against it free alpha."""
    for b in SECTORS.values():
        assert b.total_return, f"{b.ticker} is a price index"


def test_every_home_market_benchmark_exists_and_is_total_return():
    for country, ticker in COUNTRY_MARKET.items():
        assert ticker in UNIVERSE, f"{country} points at an unknown {ticker}"
        assert UNIVERSE[ticker].total_return


def test_sector_names_do_not_collide_within_one_market():
    """Two benchmarks claiming the same sector in the same market would make
    the rules path depend on dict ordering."""
    for country in {b.country for b in SECTORS.values() if b.country}:
        scopes = [b.scope for b in sectors_in(country)]
        assert len(scopes) == len(set(scopes)), f"{country} has a duplicate"


def test_a_single_candidate_market_needs_no_model_and_no_review():
    """Saudi Arabia publishes one usable benchmark. Asking a model to choose
    from a list of one spends a call to be told the only answer, and filing the
    result as a rules fallback misreports a determined outcome as a degraded
    one."""
    holding = equity("1120.SR", "SA", "SAR", 100, 150, sector="Financials")
    decision = assign_holding_benchmark(holding, client=_NeverCalled())
    assert decision.benchmark_ticker == "KSA"
    assert decision.source == "universe"
    assert decision.confidence == 1.0
    assert not decision.needs_review


class _NeverCalled:
    """A model client that fails the test if it is consulted at all."""

    is_live = True

    def structured(self, *args, **kwargs):
        raise AssertionError("the model was called for a determined choice")


@pytest.mark.parametrize("label,expected", [
    ("Banking", "Financials"),
    ("IT Services", "Technology"),
    ("Oil & Gas", "Energy"),
    ("Pharmaceuticals", "Health Care"),
    ("FMCG", "Consumer Staples"),
    ("Consumer Staples", "Consumer Staples"),
    ("", ""),
    ("Something Unmappable", ""),
])
def test_client_sector_labels_are_mapped_onto_the_universes_naming(label, expected):
    """Client workbooks say "Banking" and "Oil & Gas"; the universe says
    "Financials" and "Energy"."""
    assert canonical_sector(label) == expected


# -- aggregation ------------------------------------------------------------

def test_portfolio_stream_converts_every_flow_to_the_reporting_currency():
    """A rupee flow left untranslated would outweigh a dollar one ~80x."""
    md = FakeMarketData()
    streams = [equity("US1", "US", "USD", 1000, 2000),
               equity("IN1", "IN", "INR", 100_000, 200_000)]

    combined, _ = build_portfolio_stream(streams, md, "USD")
    assert combined.currency == "USD"
    # 1000 USD + (100,000 INR x 0.012) = 1000 + 1200 = 2200
    assert combined.contributions == pytest.approx(2200.0)
    # 2000 USD + (200,000 INR x 0.012) = 2000 + 2400 = 4400
    assert combined.terminal_value == pytest.approx(4400.0)


def test_aggregate_irr_sits_between_the_holdings_it_is_made_of():
    """A portfolio cannot return more than its best holding or less than its
    worst; a result outside that range means the merge is wrong."""
    md = FakeMarketData()
    good = equity("G", "US", "USD", 1000, 3000)
    bad = equity("B", "US", "USD", 1000, 1200)

    combined, _ = build_portfolio_stream([good, bad], md, "USD")
    assert xirr(bad) < xirr(combined) < xirr(good)


def test_terminal_date_spread_is_reported_not_hidden():
    md = FakeMarketData()
    streams = [
        equity("A", "US", "USD", 1000, 1500, valued=date(2026, 1, 5)),
        equity("B", "SA", "SAR", 1000, 1500, valued=date(2025, 10, 1)),
    ]
    _, notes = build_portfolio_stream(streams, md, "USD")
    assert any("window" in n for n in notes)


def test_contribution_weights_are_translated():
    md = FakeMarketData()
    streams = [equity("US1", "US", "USD", 1000, 1500),
               equity("SA1", "SA", "SAR", 1000, 1500)]
    weights = contribution_weights(streams, md, "USD")
    assert weights["US1"] == pytest.approx(1000.0)
    assert weights["SA1"] == pytest.approx(266.7, abs=0.5)


def test_empty_book_aggregates_without_raising():
    combined, notes = build_portfolio_stream([], FakeMarketData(), "USD")
    assert combined.flows == []
    assert notes
