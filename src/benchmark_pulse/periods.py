"""Point-to-point returns over standard windows, holding against benchmark.

This answers a different question from Direct Alpha, and the difference matters
enough to state on screen:

  Direct Alpha    what YOUR money made, from the day you bought, weighted by
                  when you put it in. The right measure of the investment.

  Period return   what the SECURITY did over the last day, month or year,
                  regardless of when you bought it. The right measure of the
                  market, and what anyone means by "how is it doing?"

A position opened two months ago still has a one-year number here; it is the
stock's year, not the investor's. The UI labels it that way rather than letting
the two be confused.

Two calendars rarely line up -- Saudi trades Sunday to Thursday, the US Monday
to Friday, and Indian holidays match neither. Every comparison is therefore made
on dates both series actually traded, so a return is never measured against a
day the benchmark was shut.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

#: Windows offered, in order. Capped at five years: beyond that a single
#: point-to-point number says more about the starting date than the security.
PERIODS: list[tuple[str, str]] = [
    ("1D", "1 day"),
    ("1W", "1 week"),
    ("1M", "1 month"),
    ("3M", "3 months"),
    ("6M", "6 months"),
    ("YTD", "year to date"),
    ("1Y", "1 year"),
    ("3Y", "3 years"),
    ("5Y", "5 years"),
]

_OFFSETS: dict[str, pd.DateOffset | timedelta] = {
    "1W": timedelta(days=7),
    "1M": pd.DateOffset(months=1),
    "3M": pd.DateOffset(months=3),
    "6M": pd.DateOffset(months=6),
    "1Y": pd.DateOffset(years=1),
    "3Y": pd.DateOffset(years=3),
    "5Y": pd.DateOffset(years=5),
}


@dataclass
class PeriodReturn:
    """One window, for one security and its benchmark."""

    code: str
    label: str
    security: float | None
    benchmark: float | None
    start: date | None = None
    end: date | None = None
    annualised: bool = False

    @property
    def relative(self) -> float | None:
        """How far the security ran ahead of, or behind, its benchmark.

        A difference of the two returns, not a ratio: over a fixed window both
        are measured from the same start and end, so subtraction is what a
        reader expects and what they can check by eye.
        """
        if self.security is None or self.benchmark is None:
            return None
        return self.security - self.benchmark

    @property
    def complete(self) -> bool:
        return self.security is not None and self.benchmark is not None


def _clean(series: pd.Series) -> pd.Series:
    out = series.dropna()
    index = pd.to_datetime(out.index)
    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)
    return pd.Series(out.to_numpy(), index=index).sort_index()


def _value_on_or_before(series: pd.Series, when: pd.Timestamp) -> tuple[float, date] | None:
    """The last observation at or before ``when``.

    Markets close for weekends and local holidays, so an exact date match fails
    routinely; carrying the previous close forward is the standard treatment.
    """
    prior = series.loc[:when]
    if prior.empty:
        return None
    return float(prior.iloc[-1]), prior.index[-1].date()


def _window_start(code: str, end: pd.Timestamp, series: pd.Series) -> pd.Timestamp | None:
    if code == "1D":
        earlier = series.loc[:end]
        if len(earlier) < 2:
            return None
        return earlier.index[-2]
    if code == "YTD":
        return pd.Timestamp(year=end.year, month=1, day=1)
    offset = _OFFSETS.get(code)
    return end - offset if offset is not None else None


def compare(security: pd.Series, benchmark: pd.Series) -> list[PeriodReturn]:
    """Returns over every window, for a security and its benchmark.

    Both are measured across the same dates. Where one series does not reach
    far enough back the window is reported incomplete rather than computed
    against whatever data happens to exist, which would silently compare
    different lengths of time.
    """
    sec, bmk = _clean(security), _clean(benchmark)
    results: list[PeriodReturn] = []

    if sec.empty or bmk.empty:
        return [PeriodReturn(code, label, None, None)
                for code, label in PERIODS]

    # Compare only on days both markets traded.
    common = sec.index.intersection(bmk.index)
    if len(common) < 2:
        return [PeriodReturn(code, label, None, None)
                for code, label in PERIODS]
    sec, bmk = sec.loc[common], bmk.loc[common]
    end = common[-1]

    for code, label in PERIODS:
        start = _window_start(code, end, sec)
        if start is None or start < common[0]:
            results.append(PeriodReturn(code, label, None, None))
            continue

        sec_start = _value_on_or_before(sec, start)
        bmk_start = _value_on_or_before(bmk, start)
        if sec_start is None or bmk_start is None:
            results.append(PeriodReturn(code, label, None, None))
            continue

        years = (end - pd.Timestamp(sec_start[1])).days / 365.25
        annualise = years > 1.25

        def rate(first: float, last: float) -> float:
            total = last / first - 1.0
            if annualise and first > 0:
                return (last / first) ** (1.0 / years) - 1.0
            return total

        results.append(PeriodReturn(
            code=code, label=label,
            security=rate(sec_start[0], float(sec.iloc[-1])),
            benchmark=rate(bmk_start[0], float(bmk.iloc[-1])),
            start=sec_start[1], end=end.date(),
            annualised=annualise,
        ))

    return results


# -- trend scorecard --------------------------------------------------------
#
# A single window can say anything. A holding can be ahead over three months and
# behind over twelve, and reporting whichever one flatters the answer is how a
# scorecard stops being useful. So all three are shown, and then combined into
# one number with the long window carrying the most weight -- a twelve-month
# trend says more about a company than a three-month move, which is as often
# sentiment as it is substance.
#
# This is a different measure from Direct Alpha and the two will disagree:
#
#   Direct Alpha     what YOUR money made against the benchmark, from the day
#                    you bought, weighted by when you put it in.
#
#   Weighted alpha   what the SECURITY has done against its benchmark lately,
#                    regardless of when you bought. A trend, not a result.
#
# A holding bought well years ago can show strong Direct Alpha and a negative
# weighted alpha: the investment is good and the recent trend is not. That
# disagreement is a finding, which is why both are on screen.

#: Window weights. They sum to 1, and the ordering is the point: the longest
#: window decides half the score on its own.
#:
#: Six months, one year and three years -- not three, six and twelve. A quarter
#: is mostly noise, and three years is long enough to cover a full cycle in the
#: security without ever depending on when the investor bought. The shape of the
#: original specification is kept: three windows, 20/30/50, longest heaviest.
WEIGHTS: dict[str, float] = {"6M": 0.20, "1Y": 0.30, "3Y": 0.50}

#: Lower bound of each band, in decimal (0.10 == ten percentage points), with
#: the status, the action it implies, and a tone for colour.
_BANDS: list[tuple[float, str, str, str]] = [
    (0.10, "Strong outperformer", "Hold", "up"),
    (0.03, "Outperformer", "Hold", "up"),
    (-0.03, "Neutral", "Hold", "flat"),
    (-0.10, "Underperformer", "Watchlist", "down"),
    (float("-inf"), "Severe underperformer", "Exit review", "down"),
]


@dataclass
class TrendScore:
    """One holding's record against its own benchmark, free of its cashflows."""

    alphas: dict[str, float | None]       # by window code
    weighted: float | None
    status: str
    action: str
    tone: str                              # "up" | "down" | "flat"
    coverage: float = 1.0                  # share of weight actually priced
    #: The scored windows in full, so a caller can show what the security and
    #: the benchmark each did rather than only the gap between them. A +5%
    #: alpha built from +30 against +25 is a different fact from one built from
    #: -10 against -15, and only the pair distinguishes them.
    windows: dict[str, PeriodReturn] = field(default_factory=dict)

    @property
    def partial(self) -> bool:
        """True when a window was missing and the weights were renormalised."""
        return self.weighted is not None and self.coverage < 0.999

    @property
    def scored(self) -> bool:
        return self.weighted is not None


def band(weighted: float) -> tuple[str, str, str]:
    """The status, action and tone for a weighted alpha."""
    for floor, status, action, tone in _BANDS:
        if weighted >= floor:
            return status, action, tone
    return _BANDS[-1][1], _BANDS[-1][2], _BANDS[-1][3]


def trend_score(returns: list[PeriodReturn]) -> TrendScore:
    """Combine 3M, 6M and 12M alpha into one weighted number and a status.

    Where a window is missing -- a recent listing, a benchmark that does not
    reach back -- its weight is redistributed across the windows that do exist
    rather than counted as zero. Counting it as zero would quietly drag every
    young holding towards Neutral and make a real signal look like an average
    one. The share of weight actually used is reported, so a score built on one
    window can be told from a score built on three.
    """
    by_code = {r.code: r for r in returns}
    alphas: dict[str, float | None] = {}
    windows = {code: by_code[code] for code in WEIGHTS if code in by_code}
    total_weight = 0.0
    running = 0.0

    for code, weight in WEIGHTS.items():
        value = by_code[code].relative if code in by_code else None
        alphas[code] = value
        if value is None:
            continue
        running += value * weight
        total_weight += weight

    if total_weight <= 0:
        return TrendScore(alphas=alphas, weighted=None, status="Not scored",
                          action="", tone="flat", coverage=0.0, windows=windows)

    weighted = running / total_weight
    status, action, tone = band(weighted)
    return TrendScore(alphas=alphas, weighted=weighted, status=status,
                      action=action, tone=tone, coverage=total_weight,
                      windows=windows)


def rebased(security: pd.Series, benchmark: pd.Series, code: str = "1Y",
            base: float = 100.0) -> pd.DataFrame:
    """Both series rebased to ``base`` at the window's start, for charting.

    Rebasing is what makes two series on wildly different scales -- a $330 share
    against a 7,600-point index -- readable on one axis.
    """
    sec, bmk = _clean(security), _clean(benchmark)
    if sec.empty or bmk.empty:
        return pd.DataFrame()

    common = sec.index.intersection(bmk.index)
    if len(common) < 2:
        return pd.DataFrame()
    sec, bmk = sec.loc[common], bmk.loc[common]

    start = _window_start(code, common[-1], sec)
    if start is None:
        start = common[0]
    start = max(pd.Timestamp(start), common[0])

    sec, bmk = sec.loc[start:], bmk.loc[start:]
    if sec.empty or bmk.empty or sec.iloc[0] == 0 or bmk.iloc[0] == 0:
        return pd.DataFrame()

    return pd.DataFrame({
        "security": sec / sec.iloc[0] * base,
        "benchmark": bmk / bmk.iloc[0] * base,
    })
