"""A trade recorded in the editor has to reach every screen.

This exists because it did not. A position bought the same day has no holding
period, so its whole-life IRR has no solution and its Direct Alpha is None --
correct arithmetic. But several views filtered on "has a Direct Alpha", so the
holding appeared on one screen and silently vanished from others. Twelve
positions in the header, ten in the table, and nothing on screen explaining the
gap. It read as a failed save, which is how it was reported.

The rule these pin down: a collection is filtered by a measure only when that
measure is what the screen is about. Winners and laggards is point-to-point on
the security and must therefore include a holding bought this morning, because
the stock has three years of history whichever day the investor turned up.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from benchmark_pulse.cashflows import AssetClass, CashflowStream, FlowType
from benchmark_pulse.holdings_edit import COLUMNS, apply_trade, read_holdings
from benchmark_pulse.periods import PeriodReturn, trend_score

TODAY = date(2026, 9, 18)


def row(ticker="AAPL", name="Apple Inc.", qty=100.0, cost=10.0, when=None,
        country="US", ccy="USD", sector="Technology") -> dict:
    return {"Security Name": name, "Ticker": ticker, "Ccy": ccy,
            "Quantity": qty, "Avg Cost": cost,
            "Purchase Date": when or date(2022, 1, 10),
            "Country": country, "Sector": sector}


def frame(rows):
    return pd.DataFrame(rows, columns=COLUMNS)


def stream(ticker="NEW.NS", bought=TODAY, qty=100.0, price=50.0):
    s = CashflowStream(asset_id=ticker, name=ticker,
                       asset_class=AssetClass.PUBLIC_EQUITY,
                       currency="INR", country="IN")
    s.add(bought, -(qty * price), FlowType.PURCHASE)
    s.set_terminal_value(TODAY, qty * price)
    s.quantity = qty
    return s


# -- the arithmetic that started it ------------------------------------------

@pytest.mark.parametrize("value", [5000.0, 5500.0, 4500.0])
def test_a_position_bought_today_has_no_whole_life_return(value):
    """Not a bug, and it has to be a refusal rather than a number.

    Every flow discounts by (1+r)^0 = 1 when no time has passed, so the NPV is
    flat and the solver returns whichever end of its bracket it started from.
    A position bought this morning at today's price came back as -99.99%, which
    would have ranked it the worst holding in the book. The parametrisation
    covers flat, up and down, because only the flat case hit the bad path.
    """
    from benchmark_pulse.metrics import MetricError, xirr

    s = stream(bought=TODAY, qty=100.0, price=50.0)
    s.set_terminal_value(TODAY, value)

    with pytest.raises(MetricError, match="no time has elapsed"):
        xirr(s)


def test_a_recent_position_still_scores_once_time_has_passed():
    """The new guard is about zero elapsed time, not about being recent.

    Note what it does not change: a position held a single day at +2% would
    annualise to roughly 137,000%, which the existing bracket already refuses
    at its 1000% ceiling. Very short holdings were always unscored, and still
    are -- the guard only closes the case where no time passed at all and the
    solver returned a number anyway.
    """
    from benchmark_pulse.metrics import xirr

    s = stream(bought=TODAY - timedelta(days=90), qty=100.0, price=50.0)
    s.set_terminal_value(TODAY, 5100.0)
    assert 0.05 < xirr(s) < 0.12


def test_a_position_bought_today_still_has_a_trend():
    """The windows are point-to-point on the security. The stock has three
    years of history whichever day the investor turned up, so this is the
    measure that must not exclude a new holding."""
    windows = [PeriodReturn("6M", "6 months", 0.20, 0.10),
               PeriodReturn("1Y", "1 year", 0.30, 0.15),
               PeriodReturn("3Y", "3 years", 0.25, 0.12)]
    score = trend_score(windows)

    assert score.scored
    assert score.weighted == pytest.approx(0.20 * 0.10 + 0.30 * 0.15
                                           + 0.50 * 0.13)


# -- the trade reaches the book ----------------------------------------------

def test_a_buy_adds_a_row_that_carries_everything_a_view_needs():
    out, said = apply_trade(frame([row()]), "BAJFINANCE.NS", "Buy", 1000, 1125,
                            TODAY, name="Bajaj Finance", country="IN",
                            sector="Financials", currency="INR")
    added = out[out["Ticker"] == "BAJFINANCE.NS"].iloc[0]

    assert added["Quantity"] == pytest.approx(1000)
    assert added["Avg Cost"] == pytest.approx(1125)
    assert added["Purchase Date"] == TODAY
    # Every field a downstream view groups or filters on must be populated,
    # or the holding lands in an "Unclassified" bucket instead of its own.
    assert added["Country"] == "IN"
    assert added["Ccy"] == "INR"
    assert added["Sector"] == "Financials"
    assert "Opened" in said


def test_a_full_sale_removes_the_row_everywhere_at_once():
    out, _ = apply_trade(
        frame([row(), row("XOM", "Exxon", qty=500.0)]), "XOM", "Sell", 500, 0,
        TODAY)
    assert "XOM" not in set(out["Ticker"])
    assert set(out["Ticker"]) == {"AAPL"}


def test_a_partial_sale_leaves_the_row_with_the_rest(tmp_path):
    out, _ = apply_trade(frame([row(qty=500.0)]), "AAPL", "Sell", 200, 0, TODAY)
    assert out.loc[0, "Quantity"] == pytest.approx(300)


def test_an_edited_book_round_trips_through_the_workbook(tmp_path):
    """What the grid saves is what the loader reads back. A trade that only
    lives in memory reaches nothing."""
    from benchmark_pulse.holdings_edit import write_holdings

    grid, _ = apply_trade(frame([row()]), "BAJFINANCE.NS", "Buy", 1000, 1125,
                          TODAY, name="Bajaj Finance", country="IN",
                          sector="Financials", currency="INR")
    path = write_holdings(grid, tmp_path / "book.xlsx")
    back = read_holdings(path)

    assert set(back["Ticker"]) == {"AAPL", "BAJFINANCE.NS"}
    fresh = back[back["Ticker"] == "BAJFINANCE.NS"].iloc[0]
    assert fresh["Quantity"] == pytest.approx(1000)
    assert fresh["Sector"] == "Financials"
    assert fresh["Purchase Date"] == TODAY


# -- the rule that was broken ------------------------------------------------

def test_the_trend_collection_is_not_filtered_by_the_whole_life_measure():
    """The regression itself, stated as a rule.

    Winners and laggards selects on `weighted_of(h) is not None`, over every
    holding. It used to select over `analysis.scored` first, which is holdings
    with a Direct Alpha -- so a position bought today was dropped before its
    trend was ever looked at.
    """
    import ast
    import pathlib

    app = pathlib.Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"
    source = app.read_text(encoding="utf-8")

    assert "graded = [h for h in analysis.holdings" in source, (
        "Winners and laggards must grade every holding, not only the scored "
        "ones -- nothing on that page depends on when the position was opened")
    assert "graded = [h for h in scored" not in source

    # And the trend scores themselves have to be computed for every holding,
    # or `graded` has nothing to find.
    tree = ast.parse(source)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "trend_scores")
    body = ast.get_source_segment(source, fn) or ""
    assert ".holdings:" in body, (
        "trend_scores must iterate every holding; iterating .scored silently "
        "excluded anything bought today")
    assert ".scored:" not in body


def test_a_count_over_scored_holdings_states_its_denominator():
    """Where a ratio is taken over the scored holdings, the screen has to say
    so when that is fewer than the positions in the book.

    "7 of 10" beside a header reading 12 positions is the gap that made a saved
    trade look lost. The three places that still divide by `scored` -- the win
    rate tile, the sidebar stat and the mandate card -- each qualify it.
    """
    import pathlib

    app = pathlib.Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"
    source = app.read_text(encoding="utf-8")

    assert "too new" in source, "the win-rate tile must qualify its denominator"
    assert "are too recent to " in source, (
        "the mandate card must explain why its count is below the header")
    assert source.count("len(scored) != len(analysis.holdings)") >= 3, (
        "every scored-based count shown beside a total has to be qualified")
