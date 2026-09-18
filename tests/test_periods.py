"""Tests for point-to-point period returns.

The subtle failures here are silent ones: comparing a security against a day its
benchmark did not trade, or reporting a window the data does not actually cover.
Both produce a plausible number that is simply wrong.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from benchmark_pulse.periods import PERIODS, compare, rebased


def series(start: str, days: int, first: float, last: float,
           freq: str = "D") -> pd.Series:
    """A straight line from ``first`` to ``last`` over ``days`` sessions."""
    index = pd.date_range(start, periods=days, freq=freq)
    step = (last - first) / (days - 1)
    return pd.Series([first + step * i for i in range(days)], index=index)


def by_code(results, code):
    return next(r for r in results if r.code == code)


# -- basic behaviour --------------------------------------------------------

def test_every_period_is_returned_even_when_uncomputable():
    """A short series must still report each window, as incomplete."""
    short = series("2026-09-01", 5, 100, 105)
    results = compare(short, short)
    assert [r.code for r in results] == [c for c, _ in PERIODS]
    assert by_code(results, "5Y").security is None


def test_one_day_return_uses_the_previous_session():
    s = pd.Series([100.0, 110.0], index=pd.to_datetime(["2026-09-14", "2026-09-15"]))
    b = pd.Series([200.0, 202.0], index=pd.to_datetime(["2026-09-14", "2026-09-15"]))
    day = by_code(compare(s, b), "1D")
    assert day.security == pytest.approx(0.10)
    assert day.benchmark == pytest.approx(0.01)
    assert day.relative == pytest.approx(0.09)


def test_short_windows_are_not_annualised():
    s = series("2026-06-01", 120, 100, 110)
    month = by_code(compare(s, s), "1M")
    assert month.annualised is False


def test_long_windows_are_annualised():
    """A 5-year figure quoted as a total return is not comparable to a 1-year
    one; past roughly a year these are stated per annum.

    The series runs just over five years so the 5Y window starts near the
    beginning: a longer run would start the window part-way up the line and the
    expected rate would no longer be a doubling.
    """
    s = series("2021-01-01", 1836, 100, 200)      # ~5.03 years, 100 -> 200
    five = by_code(compare(s, s), "5Y")
    assert five.annualised is True
    # A doubling over five years is about 14.9% a year, not 100%.
    assert five.security == pytest.approx(0.149, abs=0.01)
    assert five.security < 0.20, "a total return was reported as annualised"


# -- the guard against silent mismatches ------------------------------------

def test_comparison_uses_only_days_both_markets_traded():
    """Saudi trades Sunday to Thursday, the US Monday to Friday. Measuring a
    Saudi holding against a day the US index was shut would be meaningless."""
    both = pd.date_range("2026-01-01", periods=40, freq="D")
    sec = pd.Series(range(100, 140), index=both, dtype=float)
    # Benchmark missing every third day.
    bmk = pd.Series(range(200, 240), index=both, dtype=float).iloc[::3]

    results = compare(sec, bmk)
    week = by_code(results, "1W")
    assert week.complete
    assert week.end in {d.date() for d in bmk.index}


def test_window_longer_than_the_data_is_reported_incomplete():
    """Rather than computing against whatever history happens to exist."""
    one_year = series("2025-09-15", 360, 100, 130)
    results = compare(one_year, one_year)
    assert by_code(results, "6M").complete
    assert by_code(results, "3Y").security is None
    assert by_code(results, "5Y").security is None


def test_no_overlap_between_the_two_series_yields_nothing():
    a = series("2020-01-01", 300, 100, 150)
    b = series("2025-01-01", 300, 100, 150)
    assert all(not r.complete for r in compare(a, b))


def test_empty_input_does_not_raise():
    empty = pd.Series(dtype=float)
    assert all(not r.complete for r in compare(empty, empty))


# -- relative performance ---------------------------------------------------

def test_relative_is_the_difference_of_the_two_returns():
    s = series("2026-01-01", 200, 100, 130)   # +30%
    b = series("2026-01-01", 200, 100, 110)   # +10%
    result = by_code(compare(s, b), "3M")
    assert result.relative == pytest.approx(
        result.security - result.benchmark)
    assert result.relative > 0


def test_relative_is_none_when_either_side_is_missing():
    short = series("2026-09-01", 5, 100, 101)
    assert by_code(compare(short, short), "5Y").relative is None


# -- rebasing ---------------------------------------------------------------

def test_rebased_starts_both_series_at_the_same_value():
    s = series("2025-09-15", 360, 50, 75)
    b = series("2025-09-15", 360, 7000, 7700)
    frame = rebased(s, b, "6M", base=100.0)
    assert not frame.empty
    assert frame["security"].iloc[0] == pytest.approx(100.0)
    assert frame["benchmark"].iloc[0] == pytest.approx(100.0)


def test_rebased_preserves_relative_shape():
    """A security that outperforms must end above its benchmark."""
    s = series("2025-09-15", 360, 100, 150)
    b = series("2025-09-15", 360, 100, 110)
    frame = rebased(s, b, "6M")
    assert frame["security"].iloc[-1] > frame["benchmark"].iloc[-1]


def test_rebased_handles_no_overlap():
    a = series("2020-01-01", 100, 100, 150)
    b = series("2025-01-01", 100, 100, 150)
    assert rebased(a, b).empty
