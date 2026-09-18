"""Tests for the weighted-alpha trend scorecard.

The arithmetic is simple enough to check by hand, which is exactly why it needs
pinning: a weight typed in the wrong row would still produce a plausible number
and a plausible verdict, and nobody would notice.
"""

from __future__ import annotations

import pytest

from benchmark_pulse.periods import (
    WEIGHTS, PeriodReturn, band, trend_score,
)


def alphas(short, mid, long):
    """PeriodReturns carrying the given relative performance.

    The windows are six months, one year and three years. They were three, six
    and twelve months when the scorecard was first specified, and were widened
    on instruction: a quarter is mostly noise, and three years covers a cycle in
    the company without ever depending on when the investor bought. The
    arithmetic is untouched -- three windows at 20/30/50, longest heaviest -- so
    the worked examples below still reconcile exactly.
    """
    out = []
    for code, value in (("6M", short), ("1Y", mid), ("3Y", long)):
        if value is None:
            out.append(PeriodReturn(code, code, None, None))
        else:
            # relative is security - benchmark, so a zero benchmark makes the
            # security's own return the alpha.
            out.append(PeriodReturn(code, code, value, 0.0))
    return out


# -- the worked example -----------------------------------------------------

def test_the_specified_worked_example():
    """Infosys: +4% over the short window, -5% over the middle, +7% over the
    long one.

        (4 x 0.20) + (-5 x 0.30) + (7 x 0.50)
      =  0.8       -  1.5        +  3.5        = +2.8%

    Positive despite a weak middle window, which is the case the weighting
    exists to handle.
    """
    score = trend_score(alphas(0.04, -0.05, 0.07))
    assert score.weighted == pytest.approx(0.028)
    assert score.status == "Neutral"
    assert score.action == "Hold"


def test_the_specified_watchlist_example():
    """HDFC Bank: -6%, -8%, -12% -> -9.6%."""
    score = trend_score(alphas(-0.06, -0.08, -0.12))
    assert score.weighted == pytest.approx(-0.096)
    assert score.status == "Underperformer"
    assert score.action == "Watchlist"


def test_the_specified_exit_review_example():
    """XYZ Ltd: -8%, -12%, -18%.

        (-8 x 0.20) + (-12 x 0.30) + (-18 x 0.50)
      =  -1.6       -   3.6        -   9.0        = -14.2%

    The specification wrote -13.6% for this row. The formula it gives produces
    -14.2%, and the other two worked examples reconcile exactly, so the row is
    an arithmetic slip rather than a different rule. Left at -14.2 deliberately:
    the verdict is Severe underperformer either way, but pinning the stated
    figure would encode the slip.
    """
    score = trend_score(alphas(-0.08, -0.12, -0.18))
    assert score.weighted == pytest.approx(-0.142)
    assert score.status == "Severe underperformer"
    assert score.action == "Exit review"


# -- weights ----------------------------------------------------------------

def test_the_windows_are_six_months_one_year_and_three_years():
    """Widened from 3M/6M/12M on instruction, because every figure on the
    holding pages had to be independent of when a position was opened and a
    three-month window is mostly noise. The 20/30/50 shape is unchanged."""
    assert WEIGHTS == {"6M": 0.20, "1Y": 0.30, "3Y": 0.50}
    assert list(WEIGHTS) == ["6M", "1Y", "3Y"], "order drives the column order"
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


def test_the_longest_window_decides_half_the_score():
    """Three years says more about a company than six months does. Equal
    weighting would let two quarters of noise overturn a cycle of evidence."""
    short_lead = trend_score(alphas(0.30, 0.0, -0.10))
    long_lead = trend_score(alphas(-0.10, 0.0, 0.30))
    assert short_lead.weighted == pytest.approx(0.01)
    assert long_lead.weighted == pytest.approx(0.13)
    assert long_lead.weighted > short_lead.weighted


# -- bands ------------------------------------------------------------------

@pytest.mark.parametrize("value,status", [
    (0.25, "Strong outperformer"),
    (0.101, "Strong outperformer"),
    (0.10, "Strong outperformer"),      # boundary is inclusive upward
    (0.099, "Outperformer"),
    (0.03, "Outperformer"),
    (0.029, "Neutral"),
    (0.0, "Neutral"),
    (-0.03, "Neutral"),
    (-0.031, "Underperformer"),
    (-0.10, "Underperformer"),
    (-0.101, "Severe underperformer"),
    (-0.40, "Severe underperformer"),
])
def test_bands_match_the_specification(value, status):
    assert band(value)[0] == status


@pytest.mark.parametrize("value,tone", [
    (0.20, "up"), (0.05, "up"), (0.0, "flat"), (-0.05, "down"), (-0.20, "down"),
])
def test_tone_follows_the_band(value, tone):
    assert band(value)[2] == tone


# -- the colour and the verdict must agree ----------------------------------

@pytest.mark.parametrize("value,expected", [
    (0.25, "ps-b-strong-up"),
    (0.10, "ps-b-strong-up"),
    (0.05, "ps-b-up"),
    (0.03, "ps-b-up"),
    (0.0, "ps-b-flat"),
    (-0.03, "ps-b-flat"),
    (-0.05, "ps-b-down"),
    (-0.10, "ps-b-down"),
    (-0.15, "ps-b-strong-down"),
    (None, "ps-b-none"),
])
def test_cell_tint_matches_the_band(value, expected):
    from benchmark_pulse import brand
    assert brand.band_class(value) == expected


@pytest.mark.parametrize("value", [0.3, 0.12, 0.1, 0.05, 0.03, 0.0, -0.03,
                                   -0.05, -0.1, -0.11, -0.4])
def test_a_cell_is_never_tinted_against_its_own_status(value):
    """The tint and the status read off the same thresholds. Two ladders would
    eventually drift and put an amber cell on a green row, which is worse than
    no colour at all: it makes a reader distrust the table."""
    from benchmark_pulse import brand
    status = band(value)[0]
    tint = brand.band_class(value)
    pairs = {
        "Strong outperformer": "ps-b-strong-up",
        "Outperformer": "ps-b-up",
        "Neutral": "ps-b-flat",
        "Underperformer": "ps-b-down",
        "Severe underperformer": "ps-b-strong-down",
    }
    assert tint == pairs[status], f"{value:+.0%} is {status} but tinted {tint}"


def test_a_banded_cell_still_reads_as_a_number():
    """Colour is never the only carrier: the figure and its sign stay in the
    text, so the table survives being printed, screenshotted or read by
    somebody who cannot distinguish the tints."""
    from benchmark_pulse import brand
    assert brand.band_cell(0.028) == "+2.8%"
    assert brand.band_cell(-0.096) == "-9.6%"
    assert brand.band_cell(None) == "&mdash;"


# -- missing windows --------------------------------------------------------

def test_a_missing_window_redistributes_its_weight():
    """Counting a missing window as zero would drag every young holding towards
    Neutral and make a real signal look like an average one."""
    score = trend_score(alphas(0.10, None, 0.20))
    # 0.20 and 0.50 of weight survive, renormalised over 0.70:
    # (0.10*0.20 + 0.20*0.50) / 0.70 = 0.12 / 0.70
    assert score.weighted == pytest.approx(0.12 / 0.70)
    assert score.coverage == pytest.approx(0.70)
    assert score.partial


def test_a_full_score_is_not_flagged_partial():
    score = trend_score(alphas(0.01, 0.01, 0.01))
    assert score.coverage == pytest.approx(1.0)
    assert not score.partial


def test_a_holding_with_no_windows_is_not_scored():
    """Better to say nothing than to publish a verdict from no evidence."""
    score = trend_score(alphas(None, None, None))
    assert score.weighted is None
    assert not score.scored
    assert score.status == "Not scored"


def test_one_window_still_scores_but_says_so():
    score = trend_score(alphas(None, None, 0.12))
    assert score.weighted == pytest.approx(0.12)
    assert score.status == "Strong outperformer"
    assert score.coverage == pytest.approx(0.50)
    assert score.partial


def test_every_window_is_reported_even_when_missing():
    """The table shows all three columns whatever is available, so a gap has to
    be a visible blank rather than an absent row."""
    score = trend_score(alphas(0.01, None, None))
    assert set(score.alphas) == {"6M", "1Y", "3Y"}
    assert score.alphas["1Y"] is None


# -- both sides of the gap ---------------------------------------------------

def test_the_scored_windows_are_kept_in_full():
    """An alpha alone hides where it came from. +5% built from +30 against +25
    is a different fact from +5% built from -10 against -15, and only the pair
    tells them apart -- so the windows themselves are carried, not just the
    difference."""
    windows = [PeriodReturn("6M", "6 months", 0.30, 0.25),
               PeriodReturn("1Y", "1 year", -0.10, -0.15),
               PeriodReturn("3Y", "3 years", None, None)]
    score = trend_score(windows)

    assert score.windows["6M"].security == pytest.approx(0.30)
    assert score.windows["6M"].benchmark == pytest.approx(0.25)
    assert score.windows["1Y"].security == pytest.approx(-0.10)
    assert score.alphas["6M"] == pytest.approx(score.alphas["1Y"]), (
        "both are +5 alpha, from opposite absolute outcomes")


def test_a_window_outside_the_weighting_is_not_carried():
    """1D and 1W are computed for other screens. Keeping them here would put
    columns on the scorecard that the weighted number does not use."""
    windows = [PeriodReturn("1D", "1 day", 0.01, 0.0),
               PeriodReturn("6M", "6 months", 0.05, 0.0)]
    score = trend_score(windows)
    assert set(score.windows) == {"6M"}


def test_an_unscored_holding_still_reports_the_windows_it_has():
    score = trend_score([PeriodReturn("6M", "6 months", None, None)])
    assert not score.scored
    assert "6M" in score.windows
