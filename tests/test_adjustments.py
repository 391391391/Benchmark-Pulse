"""Tests for the fairness layer.

The unlevering test is the one that matters: it fixes the direction of the
correction. Getting the sign backwards would make levered property look *better*
after adjustment, which is exactly the error the module exists to prevent.
"""

from __future__ import annotations

from datetime import date

import pytest

from benchmark_pulse.adjustments import (
    J_CURVE_YEARS, j_curve_adjustment, leverage_adjustment, unlever_return,
)
from benchmark_pulse.cashflows import AssetClass, CashflowStream, FlowType


def property_stream(ltv: float, debt_rate: float) -> CashflowStream:
    s = CashflowStream(
        asset_id="RE:Test", name="Test Tower",
        asset_class=AssetClass.DIRECT_REAL_ESTATE,
        currency="USD", country="US",
        leverage_ratio=ltv, debt_rate=debt_rate,
    )
    s.add(date(2020, 1, 1), -1_000_000, FlowType.PURCHASE)
    s.set_terminal_value(date(2026, 1, 1), 1_800_000)
    return s


def fund_stream(vintage: int) -> CashflowStream:
    s = CashflowStream(
        asset_id="FUND:Test", name="Test Fund",
        asset_class=AssetClass.PRIVATE_FUND,
        currency="USD", country="US", vintage_year=vintage,
    )
    s.add(date(vintage, 6, 1), -1_000_000, FlowType.CAPITAL_CALL)
    s.set_terminal_value(date(2026, 6, 30), 900_000)
    return s


# -- unlevering -------------------------------------------------------------

def test_unlever_reduces_return_when_debt_is_cheaper_than_the_asset():
    """The whole point: borrowing amplifies returns, so stripping it lowers them.

    14% equity return, 55% LTV at 8.5% debt:
        0.14 x 0.45 + 0.085 x 0.55 = 0.063 + 0.04675 = 0.10975
    """
    result = unlever_return(0.14, ltv=0.55, debt_rate=0.085)
    assert result == pytest.approx(0.10975)
    assert result < 0.14, "unlevering must reduce an accretively levered return"


def test_unlever_is_identity_with_no_debt():
    assert unlever_return(0.12, ltv=0.0, debt_rate=0.05) == pytest.approx(0.12)


def test_unlever_raises_return_when_debt_costs_more_than_the_asset():
    """Leverage cuts both ways, and the maths must reflect that honestly."""
    result = unlever_return(0.02, ltv=0.50, debt_rate=0.09)
    assert result == pytest.approx(0.055)
    assert result > 0.02


def test_unlever_rejects_impossible_ltv():
    with pytest.raises(ValueError, match="LTV must be in"):
        unlever_return(0.10, ltv=1.0, debt_rate=0.05)


def test_leverage_adjustment_reports_both_numbers_and_the_gap():
    adj = leverage_adjustment(property_stream(0.55, 0.085), levered_irr=0.14)
    assert adj is not None
    assert adj.kind == "leverage"
    assert adj.values["unlevered_irr"] == pytest.approx(0.10975)
    assert adj.values["leverage_contribution"] == pytest.approx(0.03025)
    assert adj.severity == "material"
    # Both figures must survive into the prose the narrative layer will cite.
    assert "14.0%" in adj.headline and "11.0%" in adj.headline


def test_no_leverage_adjustment_for_an_unlevered_asset():
    assert leverage_adjustment(property_stream(0.0, 0.0), levered_irr=0.14) is None


# -- J-curve ----------------------------------------------------------------

def test_young_fund_with_negative_irr_is_flagged_as_j_curve():
    adj = j_curve_adjustment(fund_stream(2024), irr=-0.053, dpi=None,
                             as_of=date(2026, 9, 13))
    assert adj is not None
    assert adj.kind == "j_curve"
    assert adj.severity == "material"
    assert adj.values["age_years"] == 2
    assert "J-curve" in adj.headline


def test_mature_fund_is_not_excused_by_the_j_curve():
    """A 2016 fund has had a decade. A bad number is a bad number."""
    adj = j_curve_adjustment(fund_stream(2016), irr=-0.02, dpi=1.35,
                             as_of=date(2026, 9, 13))
    assert adj is None


def test_j_curve_boundary_is_inclusive_of_the_cutoff_year():
    on_edge = j_curve_adjustment(
        fund_stream(2026 - J_CURVE_YEARS), irr=-0.01, dpi=None,
        as_of=date(2026, 9, 13),
    )
    just_past = j_curve_adjustment(
        fund_stream(2026 - J_CURVE_YEARS - 1), irr=-0.01, dpi=None,
        as_of=date(2026, 9, 13),
    )
    assert on_edge is not None
    assert just_past is None


def test_j_curve_does_not_apply_to_listed_equity():
    s = CashflowStream(
        asset_id="AAPL", name="Apple", asset_class=AssetClass.PUBLIC_EQUITY,
        currency="USD", country="US", vintage_year=2024,
    )
    assert j_curve_adjustment(s, irr=-0.10, dpi=None) is None
