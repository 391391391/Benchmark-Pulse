"""Tests for the portfolio's return over a fixed window.

The formula under test is the one a committee recognises:

    gain = closing value - opening value - new cash injected + anything taken out

Two things have to hold for it to be worth reporting. Money paid into the book
must never read as performance, and the percentage must divide that gain by the
capital that was actually at work rather than by whatever happened to be there
on day one. Both are pinned down here, along with the cases where the window
cannot honestly be computed at all.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from benchmark_pulse.cashflows import AssetClass, CashflowStream, FlowType
from benchmark_pulse.window_return import (
    holding_value_at, quantity_at, window_return, windows_against,
)

END = date(2026, 9, 17)
START_1Y = date(2025, 9, 17)


def line(first: float, last: float, start: date = date(2018, 1, 1),
         end: date = END) -> pd.Series:
    """A price series rising smoothly from ``first`` to ``last``."""
    index = pd.date_range(start, end, freq="D")
    step = (last - first) / (len(index) - 1)
    return pd.Series([first + step * i for i in range(len(index))], index=index)


def flat(level: float = 100.0, **kw) -> pd.Series:
    return line(level, level, **kw)


class FakeMarket:
    """Prices and rates supplied directly, so no test touches the network."""

    def __init__(self, series: dict[str, pd.Series],
                 rates: dict[tuple[str, str], float] | None = None):
        self._series = series
        self._rates = rates or {}

    def prices(self, ticker: str, **_kw) -> pd.Series:
        if ticker not in self._series:
            raise RuntimeError(f"{ticker}: no data")
        return self._series[ticker]

    def fx_rate(self, base: str, quote: str, on: date | None = None) -> float:
        if base.upper() == quote.upper():
            return 1.0
        return self._rates[(base.upper(), quote.upper())]


def stock(ticker: str = "AAA", qty: float = 100.0, cost: float = 5.0,
          bought: date = date(2020, 1, 1), last: float = 12.0,
          currency: str = "USD") -> CashflowStream:
    """A listed holding: one purchase, a constant quantity, valued today."""
    s = CashflowStream(asset_id=ticker, name=ticker,
                       asset_class=AssetClass.PUBLIC_EQUITY,
                       currency=currency, country="US")
    s.add(bought, -(qty * cost), FlowType.PURCHASE)
    s.set_terminal_value(END, qty * last)
    s.quantity = qty
    s.cost_per_unit = cost
    s.last_price = last
    return s


# -- the formula ------------------------------------------------------------

def test_gain_is_closing_less_opening_less_injections_plus_exits():
    """The identity itself, on a position held right through the window."""
    md = FakeMarket({"AAA": line(1.0, 12.0), "BMK": flat()})
    w = window_return([stock()], md, "BMK", "USD", years=1)

    assert w.ok
    assert w.start == START_1Y and w.end == END
    assert w.gain == pytest.approx(w.closing - w.opening - w.injected + w.withdrawn)
    assert w.injected == 0.0 and w.withdrawn == 0.0
    assert w.closing == pytest.approx(1200.0)          # 100 shares at 12
    assert w.portfolio == pytest.approx(w.gain / w.opening)


def test_new_cash_does_not_read_as_performance():
    """A position opened inside the window: the purchase is an injection, and
    the book must not show a gain merely because money arrived."""
    prices = flat(10.0)
    md = FakeMarket({"AAA": prices, "BMK": flat()})
    fresh = stock(bought=date(2026, 3, 17), cost=10.0, last=10.0)

    w = window_return([fresh], md, "BMK", "USD", years=1)

    assert w.opening == 0.0
    assert w.injected == pytest.approx(1000.0)
    assert w.closing == pytest.approx(1000.0)
    assert w.gain == pytest.approx(0.0)
    assert w.portfolio == pytest.approx(0.0)
    assert w.opened_during == 1


def test_an_exit_is_added_back():
    """Money taken out is not a loss. Without the ``+ exits`` term a book that
    sold half its position would report the sale as a fall in value."""
    md = FakeMarket({"AAA": flat(10.0), "BMK": flat()})
    s = stock(cost=10.0, last=10.0)
    s.add(date(2026, 3, 17), 400.0, FlowType.SALE)

    w = window_return([s], md, "BMK", "USD", years=1)

    assert w.withdrawn == pytest.approx(400.0)
    # Opening is the whole position, closing is what the statement still shows,
    # and the sale proceeds close the difference.
    assert w.gain == pytest.approx(w.closing - w.opening + 400.0)


# -- the denominator --------------------------------------------------------

def test_capital_is_weighted_by_time_invested():
    """Money that arrived halfway through the year counts as half a year of
    capital. Dividing by the opening value alone, or by the full injection,
    both give a return nobody could reconstruct."""
    md = FakeMarket({"AAA": flat(10.0), "BBB": flat(10.0), "BMK": flat()})
    held = stock("AAA", qty=100, cost=10.0, bought=date(2020, 1, 1), last=10.0)
    added = stock("BBB", qty=100, cost=10.0,
                  bought=START_1Y + timedelta(days=183), last=10.0)

    w = window_return([held, added], md, "BMK", "USD", years=1)

    assert w.opening == pytest.approx(1000.0)
    assert w.injected == pytest.approx(1000.0)
    # Roughly half the injection counts, so the denominator sits near 1500 --
    # well clear of both 1000 (opening only) and 2000 (injection at full weight).
    assert w.average_capital == pytest.approx(1500.0, abs=15.0)


def test_a_return_needs_capital_to_have_been_at_work():
    """A window that starts before the portfolio existed and receives nothing
    is reported as not computed, with a reason, rather than as zero."""
    md = FakeMarket({"AAA": flat(10.0), "BMK": flat()})
    future = stock(bought=date(2026, 9, 1))
    w = window_return([future], md, "BMK", "USD", years=3)

    # Bought inside the 1Y window but not the 3Y one? No -- it is inside both.
    # Force the empty case by asking for a window that ends before the book.
    empty = window_return([future], md, "BMK", "USD", years=1,
                          end=date(2026, 8, 1))
    assert w.ok
    assert not empty.ok
    assert empty.reason


# -- annualising ------------------------------------------------------------

def test_one_year_is_not_annualised_and_three_years_is():
    md = FakeMarket({"AAA": line(1.0, 12.0), "BMK": line(50.0, 100.0)})
    one, three = windows_against([stock()], md, "BMK", "USD")

    assert one.code == "1Y" and not one.annualised
    assert one.portfolio_annual == pytest.approx(one.portfolio)

    assert three.code == "3Y" and three.annualised
    assert three.portfolio_annual < three.portfolio
    assert (1 + three.portfolio_annual) ** three.elapsed == pytest.approx(
        1 + three.portfolio, rel=1e-6)


def test_a_total_loss_does_not_annualise_into_a_complex_number():
    """(1 + r) ** (1/n) is undefined at r <= -1. It must return None, not raise."""
    md = FakeMarket({"AAA": line(100.0, 0.0001), "BMK": flat()})
    dead = stock(qty=100, cost=1.0, bought=date(2020, 1, 1), last=0.0)
    w = window_return([dead], md, "BMK", "USD", years=3)
    assert w.portfolio < -0.99
    assert w.portfolio_annual is None or w.portfolio_annual > -1.0


# -- against the benchmark --------------------------------------------------

def test_alpha_is_the_difference_over_the_same_dates():
    md = FakeMarket({"AAA": line(1.0, 12.0), "BMK": line(90.0, 100.0)})
    w = window_return([stock()], md, "BMK", "USD", years=1)

    assert w.benchmark is not None
    assert w.alpha == pytest.approx(w.portfolio - w.benchmark)
    assert w.beat is (w.alpha > 0)


def test_a_benchmark_that_does_not_reach_back_is_refused():
    """Comparing a three-year portfolio against eighteen months of index would
    be a shorter race reported as the same one."""
    md = FakeMarket({"AAA": line(1.0, 12.0),
                     "BMK": flat(100.0, start=date(2025, 1, 1))})
    w = window_return([stock()], md, "BMK", "USD", years=3)

    assert w.portfolio is not None          # the book's own number still stands
    assert w.benchmark is None
    assert "does not price back" in w.reason
    assert w.alpha is None


def test_a_missing_benchmark_does_not_break_the_window():
    md = FakeMarket({"AAA": line(1.0, 12.0)})
    w = window_return([stock()], md, "MISSING", "USD", years=1)
    assert w.portfolio is not None
    assert w.benchmark is None
    assert "unavailable" in w.reason


# -- what cannot be valued --------------------------------------------------

def test_an_asset_with_no_quantity_is_named_rather_than_counted_as_zero():
    """A fund NAV is struck twice a year. Interpolating one to an arbitrary
    date would be invention; counting a zero opening against a real closing
    value would be worse."""
    md = FakeMarket({"AAA": line(1.0, 12.0), "BMK": flat()})
    fund = CashflowStream(asset_id="FUND:X", name="Cedar Growth III",
                          asset_class=AssetClass.PRIVATE_FUND,
                          currency="USD", country="US")
    fund.add(date(2021, 6, 1), -1_000_000.0, FlowType.CAPITAL_CALL)
    fund.set_terminal_value(END, 1_800_000.0)

    w = window_return([stock(), fund], md, "BMK", "USD", years=1)

    assert w.unavailable == ["Cedar Growth III"]
    assert w.priced == 1 and w.holdings == 2
    assert w.partial
    # Excluded from both ends: the fund's 1.8m must not appear in the closing
    # value while its opening value is missing.
    assert w.closing == pytest.approx(1200.0)


def test_a_holding_with_no_price_history_is_excluded_from_both_ends():
    md = FakeMarket({"AAA": line(1.0, 12.0), "BMK": flat()})
    w = window_return([stock(), stock("ZZZ")], md, "BMK", "USD", years=1)
    assert w.unavailable == ["ZZZ"]
    assert w.closing == pytest.approx(1200.0)


# -- currency ---------------------------------------------------------------

def test_a_past_valuation_uses_the_rate_of_its_own_date():
    """An opening value translated at today's rate would put this year's
    currency move into last year's balance."""
    md = FakeMarket({"AAA": flat(100.0), "BMK": flat()},
                    rates={("INR", "USD"): 0.012})
    rupees = stock(qty=1000, cost=80.0, last=100.0, currency="INR")

    w = window_return([rupees], md, "BMK", "USD", years=1)
    assert w.opening == pytest.approx(1000 * 100.0 * 0.012)


# -- quantity reconstruction ------------------------------------------------

def test_a_single_purchase_returns_the_stated_quantity_untouched():
    prices = line(1.0, 12.0)
    s = stock()
    assert quantity_at(s, prices, END) == pytest.approx(100.0)
    assert quantity_at(s, prices, date(2019, 1, 1)) == 0.0


def test_a_position_built_in_lots_is_reconstructed_in_proportion():
    """Two equal-cash purchases at different prices are not two equal lots. The
    lot sizes come from the prices, the level comes from the statement."""
    prices = line(10.0, 20.0, start=date(2024, 1, 1))
    s = CashflowStream(asset_id="AAA", name="AAA",
                       asset_class=AssetClass.PUBLIC_EQUITY,
                       currency="USD", country="US")
    s.add(date(2024, 1, 1), -1000.0, FlowType.PURCHASE)     # cheap: more units
    s.add(date(2026, 9, 16), -1000.0, FlowType.PURCHASE)    # dear: fewer units
    s.set_terminal_value(END, 3000.0)
    s.quantity = 150.0

    first_lot = quantity_at(s, prices, date(2024, 6, 1))
    assert first_lot > 75.0, "the cheaper lot must be the larger one"
    assert quantity_at(s, prices, END) == pytest.approx(150.0)


def test_holding_value_before_the_position_existed_is_zero_not_missing():
    md = FakeMarket({"AAA": line(1.0, 12.0)})
    assert holding_value_at(stock(bought=date(2024, 1, 1)), md,
                            date(2023, 1, 1), "USD") == 0.0


# -- the shape of the result ------------------------------------------------

def test_windows_against_returns_one_year_then_three():
    md = FakeMarket({"AAA": line(1.0, 12.0), "BMK": line(50.0, 100.0)})
    out = windows_against([stock()], md, "BMK", "USD")
    assert [w.code for w in out] == ["1Y", "3Y"]
    assert [w.years for w in out] == [1, 3]


def test_the_window_ends_on_the_valuation_date_not_today():
    """Both sides must be measured to the same close. Running the portfolio to
    its valuation date and the index to today compares different races."""
    md = FakeMarket({"AAA": line(1.0, 12.0), "BMK": flat()})
    s = stock()
    s.set_terminal_value(date(2026, 6, 30), 1200.0)
    w = window_return([s], md, "BMK", "USD", years=1)
    assert w.end == date(2026, 6, 30)
    assert w.start == date(2025, 6, 30)


def test_a_window_cut_short_values_the_book_at_its_own_end():
    """Asking for the year to March must not close it at September's value.
    The statement figure is only the right closing value when the window ends
    where the statement does."""
    md = FakeMarket({"AAA": line(1.0, 12.0), "BMK": flat()})
    s = stock()                                    # valued 17 Sep at 1200
    cut = window_return([s], md, "BMK", "USD", years=1, end=date(2026, 3, 17))

    assert cut.end == date(2026, 3, 17)
    assert cut.closing < 1200.0
    assert cut.priced == 1


def test_an_empty_book_is_reported_rather_than_divided_by_zero():
    md = FakeMarket({"BMK": flat()})
    w = window_return([], md, "BMK", "USD", years=1)
    assert not w.ok
    assert w.reason == "no holdings"
