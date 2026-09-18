"""Tests for reporting a return with and without the exchange rate.

A return is a percentage, so a rupee holding can be compared to a dollar index
without converting anything -- and the benchmark comparison is done that way,
in the holding's own currency, which is what isolates the business from the
exchange rate. Where the two currencies differ, though, the investor's own
outcome is a different number, and showing only one of them hides which is
which. These pin down the arithmetic and the cases where it is suppressed.
"""

from __future__ import annotations

from datetime import date

import pytest

from benchmark_pulse.adjustments import currency_split
from benchmark_pulse.cashflows import AssetClass, CashflowStream, FlowType


class FakeMarket:
    """Fixed rates, so the split is testable without the network."""

    def __init__(self, start=1.0, end=1.0, pegged=()):
        self.start, self.end, self._pegged = start, end, set(pegged)
        self.calls = 0

    def fx_rate(self, base, quote, on=None):
        self.calls += 1
        first = date(2022, 1, 1)
        return self.start if on == first else self.end

    def is_pegged(self, currency):
        return currency.upper() in self._pegged


def holding(currency="INR", bought=date(2022, 1, 1), sold=date(2026, 1, 1)):
    s = CashflowStream(asset_id="X", name="X",
                       asset_class=AssetClass.PUBLIC_EQUITY,
                       currency=currency, country="IN")
    s.add(bought, -1000.0, FlowType.PURCHASE)
    s.set_terminal_value(sold, 1500.0)
    return s


# -- the arithmetic ---------------------------------------------------------

def test_a_weakening_currency_lowers_the_translated_return():
    """A rupee holding that made 12% locally made less to a dollar investor if
    the rupee fell. Both numbers are true; they answer different questions."""
    md = FakeMarket(start=0.014, end=0.012)          # INR/USD falls
    split = currency_split(holding(), 0.12, md, "USD")
    assert split is not None
    assert split.local == pytest.approx(0.12)
    assert split.fx < 0
    assert split.with_fx < split.local


def test_a_strengthening_currency_raises_the_translated_return():
    md = FakeMarket(start=0.012, end=0.014)
    split = currency_split(holding(), 0.12, md, "USD")
    assert split.fx > 0
    assert split.with_fx > split.local


def test_the_two_returns_compound_rather_than_add():
    """(1+local)(1+fx)-1, not local+fx. Over four years the difference between
    the two is large enough to change a verdict."""
    md = FakeMarket(start=1.0, end=1.0)
    md.fx_rate = lambda base, quote, on=None: (
        1.0 if on == date(2022, 1, 1) else 1.2)
    split = currency_split(holding(), 0.10, md, "USD")
    years = (date(2026, 1, 1) - date(2022, 1, 1)).days / 365.0
    fx = 1.2 ** (1 / years) - 1
    assert split.fx == pytest.approx(fx)
    assert split.with_fx == pytest.approx((1.10 * (1 + fx)) - 1)


# -- when the split is suppressed -------------------------------------------

def test_no_split_when_the_currencies_already_match():
    """Two identical figures would imply a distinction that does not exist."""
    md = FakeMarket(start=1.0, end=1.3)
    assert currency_split(holding("USD"), 0.12, md, "USD") is None
    assert md.calls == 0, "no rate should be fetched when there is nothing to split"


def test_a_pegged_currency_reports_the_same_number_twice_and_says_so():
    """The riyal has been fixed at 3.75 to the dollar since 1986. Computing a
    rate anyway would report a few basis points of noise as a currency view."""
    md = FakeMarket(start=0.2665, end=0.2670, pegged={"SAR"})
    split = currency_split(holding("SAR"), 0.08, md, "USD")
    assert split.pegged
    assert split.fx == 0.0
    assert split.with_fx == split.local
    assert not split.material


def test_no_split_without_a_return_to_split():
    md = FakeMarket()
    assert currency_split(holding(), None, md, "USD") is None


def test_no_split_over_too_short_a_holding_period():
    """An annualised currency move over a few weeks is noise."""
    md = FakeMarket(start=1.0, end=1.1)
    short = holding(bought=date(2026, 1, 1), sold=date(2026, 2, 1))
    assert currency_split(short, 0.05, md, "USD") is None


def test_a_missing_rate_does_not_break_the_report():
    class Broken(FakeMarket):
        def fx_rate(self, base, quote, on=None):
            raise RuntimeError("no rate")

    assert currency_split(holding(), 0.12, Broken(), "USD") is None


# -- materiality ------------------------------------------------------------

@pytest.mark.parametrize("start,end,material", [
    (1.0, 1.0001, False),   # a rounding-level move is not worth a reader's eye
    (1.0, 1.35, True),      # several points a year is
])
def test_only_a_meaningful_currency_move_is_flagged_material(start, end, material):
    split = currency_split(holding(), 0.10, FakeMarket(start, end), "USD")
    assert split.material is material
