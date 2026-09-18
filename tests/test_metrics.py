"""Golden-number tests for the return engine.

If one of these is wrong, every number in every client memo is wrong, so these
are checked against hand arithmetic rather than against the implementation.

The two invariant tests at the bottom are the important ones: they pin down the
Direct Alpha maths by construction, and would catch a flipped sign or a
misapplied discount factor that eyeballing the output never would.
"""

from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest

from benchmark_pulse.cashflows import AssetClass, CashflowStream, FlowType
from benchmark_pulse.directalpha import compute_pme
from benchmark_pulse.metrics import MetricError, dpi, tvpi, xirr


def stream(flows, *, name="Test Asset", asset_class=AssetClass.PRIVATE_FUND,
           valuation_date=None) -> CashflowStream:
    s = CashflowStream(
        asset_id="T1", name=name, asset_class=asset_class,
        currency="USD", country="US",
    )
    for when, amount, kind in flows:
        s.add(when, amount, kind)
    if valuation_date:
        s.valuation_date = valuation_date
    return s


def flat_index(start: date, end: date, level: float = 100.0) -> pd.Series:
    idx = pd.date_range(start, end, freq="D")
    return pd.Series([level] * len(idx), index=idx)


def linear_index(start: date, end: date, first: float, last: float) -> pd.Series:
    idx = pd.date_range(start, end, freq="D")
    step = (last - first) / (len(idx) - 1)
    return pd.Series([first + step * i for i in range(len(idx))], index=idx)


# --------------------------------------------------------------------------
# XIRR
# --------------------------------------------------------------------------

def test_xirr_doubling_over_five_years():
    """-1000 in, 2000 back 1827 days later. IRR = 2^(365/1827) - 1."""
    s = stream([
        (date(2020, 1, 1), -1000.0, FlowType.PURCHASE),
        (date(2025, 1, 1), 2000.0, FlowType.TERMINAL_VALUE),
    ])
    expected = 2.0 ** (365.0 / 1827.0) - 1.0        # 0.148524...
    assert xirr(s) == pytest.approx(expected, abs=1e-8)


def test_xirr_zero_when_money_comes_back_unchanged():
    s = stream([
        (date(2022, 3, 15), -500.0, FlowType.CAPITAL_CALL),
        (date(2026, 3, 15), 500.0, FlowType.TERMINAL_VALUE),
    ])
    assert xirr(s) == pytest.approx(0.0, abs=1e-9)


def test_xirr_handles_irregular_private_fund_pattern():
    """Call, distribution, call again, exit -- the shape that breaks naive solvers."""
    s = stream([
        (date(2021, 4, 12), -1_200_000.0, FlowType.CAPITAL_CALL),
        (date(2022, 1, 30), -900_000.0, FlowType.CAPITAL_CALL),
        (date(2023, 9, 15), 1_850_000.0, FlowType.DISTRIBUTION),
        (date(2026, 6, 30), 2_400_000.0, FlowType.TERMINAL_VALUE),
    ])
    r = xirr(s)
    assert 0.15 < r < 0.30, f"IRR {r:.4f} outside a plausible range"
    # Verify it really is a root: NPV at that rate must be ~0.
    from benchmark_pulse.metrics import npv
    flows = s.ordered()
    assert npv(r, flows, flows[0].when) == pytest.approx(0.0, abs=1e-4)


def test_xirr_refuses_when_no_money_ever_came_back():
    s = stream([
        (date(2020, 1, 1), -1000.0, FlowType.PURCHASE),
        (date(2021, 1, 1), -500.0, FlowType.CAPITAL_CALL),
    ])
    with pytest.raises(MetricError, match="outflow and an inflow"):
        xirr(s)


# --------------------------------------------------------------------------
# Multiples
# --------------------------------------------------------------------------

def test_tvpi_and_dpi_split_realised_from_unrealised():
    s = stream([
        (date(2021, 1, 1), -1000.0, FlowType.CAPITAL_CALL),
        (date(2024, 1, 1), 500.0, FlowType.DISTRIBUTION),
        (date(2026, 6, 30), 800.0, FlowType.TERMINAL_VALUE),
    ])
    assert tvpi(s) == pytest.approx(1.30)   # (500 + 800) / 1000
    assert dpi(s) == pytest.approx(0.50)    # 500 / 1000 -- cash actually returned


def test_contribution_sign_is_enforced():
    """A positive capital call is a data error, not a windfall. Fail loudly."""
    s = CashflowStream(
        asset_id="T1", name="Bad", asset_class=AssetClass.PRIVATE_FUND,
        currency="USD", country="US",
    )
    with pytest.raises(ValueError, match="must be negative"):
        s.add(date(2021, 1, 1), 1000.0, FlowType.CAPITAL_CALL)


# --------------------------------------------------------------------------
# Direct Alpha invariants -- the load-bearing tests
# --------------------------------------------------------------------------

def test_asset_tracking_index_exactly_has_zero_alpha():
    """The defining property: match the index, score zero excess return.

    Invest 1000 when the index is at 100; hold until it reaches 200, ending with
    2000. The investor did exactly as well as the index, so Direct Alpha must be
    0.0 and KS-PME exactly 1.0. Any sign flip or misapplied discount breaks this.
    """
    start, end = date(2021, 1, 1), date(2026, 1, 1)
    s = stream(
        [(start, -1000.0, FlowType.PURCHASE),
         (end, 2000.0, FlowType.TERMINAL_VALUE)],
        valuation_date=end,
    )
    result = compute_pme(s, linear_index(start, end, 100.0, 200.0), "^TEST")

    assert result.ks_pme == pytest.approx(1.0, abs=1e-9)
    assert result.direct_alpha == pytest.approx(0.0, abs=1e-9)


def test_flat_index_makes_alpha_equal_the_assets_own_irr():
    """Against an index that goes nowhere, all of the return is excess return."""
    start, end = date(2021, 1, 1), date(2026, 1, 1)
    s = stream(
        [(start, -1000.0, FlowType.PURCHASE),
         (end, 2000.0, FlowType.TERMINAL_VALUE)],
        valuation_date=end,
    )
    result = compute_pme(s, flat_index(start, end), "^FLAT")

    assert result.ks_pme == pytest.approx(2.0, abs=1e-9)
    assert result.direct_alpha == pytest.approx(xirr(s), abs=1e-9)


def test_beating_the_index_gives_positive_alpha_and_pme_above_one():
    start, end = date(2021, 1, 1), date(2026, 1, 1)
    s = stream(
        [(start, -1000.0, FlowType.PURCHASE),
         (end, 3000.0, FlowType.TERMINAL_VALUE)],   # 3x while index does 2x
        valuation_date=end,
    )
    result = compute_pme(s, linear_index(start, end, 100.0, 200.0), "^TEST")

    assert result.ks_pme == pytest.approx(1.5, abs=1e-9)   # 3000 / 2000
    assert result.direct_alpha > 0


def test_losing_to_the_index_gives_negative_alpha():
    """The flattering-multiple case: 1.5x looks fine until the index did 2x."""
    start, end = date(2021, 1, 1), date(2026, 1, 1)
    s = stream(
        [(start, -1000.0, FlowType.PURCHASE),
         (end, 1500.0, FlowType.TERMINAL_VALUE)],
        valuation_date=end,
    )
    result = compute_pme(s, linear_index(start, end, 100.0, 200.0), "^TEST")

    assert result.ks_pme == pytest.approx(0.75, abs=1e-9)
    assert result.direct_alpha < 0
    assert tvpi(s) == pytest.approx(1.5)   # the number that would have misled


def test_benchmark_irr_reconciles_with_asset_irr_and_alpha():
    """(1 + asset IRR) must equal (1 + benchmark IRR)(1 + Direct Alpha)."""
    start, end = date(2021, 1, 1), date(2026, 1, 1)
    s = stream(
        [(start, -1000.0, FlowType.CAPITAL_CALL),
         (date(2023, 6, 1), 400.0, FlowType.DISTRIBUTION),
         (end, 1900.0, FlowType.TERMINAL_VALUE)],
        valuation_date=end,
    )
    r = compute_pme(s, linear_index(start, end, 100.0, 180.0), "^TEST")

    assert (1 + r.asset_irr) == pytest.approx(
        (1 + r.benchmark_irr) * (1 + r.direct_alpha), rel=1e-9
    )


def test_stale_valuation_is_flagged_not_silently_compared():
    """A two-year-old appraisal against a live index must warn the reader."""
    start, old = date(2019, 1, 1), date(2024, 1, 1)
    s = stream(
        [(start, -1000.0, FlowType.PURCHASE),
         (old, 1800.0, FlowType.TERMINAL_VALUE)],
        asset_class=AssetClass.DIRECT_REAL_ESTATE,
        valuation_date=old,
    )
    result = compute_pme(s, linear_index(start, old, 100.0, 150.0), "^TEST")
    assert any("more than 90 days old" in n for n in result.notes)
