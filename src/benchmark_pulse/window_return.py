"""Portfolio return over a fixed window, measured against the mandate benchmark.

Direct Alpha answers "what did this money earn, from the day it went in". It is
the right measure of an investment, it is money-weighted and whole-life, and it
is what the rest of the tool runs on. It is not, however, what a committee means
when it asks how the portfolio did last year. That question is about a window --
the twelve months everyone in the room lived through -- and it has its own
arithmetic:

    gain = closing value - opening value - new cash injected + anything taken out

Subtracting injections and adding back exits is the whole point: without it a
portfolio that simply received money would read as having made it. That identity
is exactly the numerator of the Modified Dietz return, the standard period
measure where daily valuations are not available, and the denominator follows
from it -- capital is weighted by the share of the window it was actually
invested for, so a purchase made in the final month does not count as a full
year of capital. Dividing by the opening value alone would be wrong the moment a
client adds to the book mid-year, and every real book does.

Two deliberate boundaries.

  Portfolio level only.  Each holding already has a like-for-like window
  comparison on Winners and laggards (3M / 6M / 12M against its own sector
  index). Running this construction per holding as well would answer one
  question twice in two different ways, and the two would disagree at the edges.

  Total return on both sides.  Prices arrive dividend-adjusted, so the opening
  value here is the dividend-adjusted equivalent rather than the screen price on
  that date, and the gain therefore includes income. The benchmark is measured
  the same way. Mixing a price index against a total-return portfolio quietly
  hands the portfolio roughly two points a year.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from .aggregate import current_values, to_reporting_currency
from .cashflows import CONTRIBUTION_TYPES, CashflowStream, FlowType
from .marketdata import MarketData

#: Windows offered, as (code, label, years). Two, because the question this
#: answers is a committee question and a committee asks about one year and three.
WINDOWS: list[tuple[str, str, int]] = [
    ("1Y", "1 year", 1),
    ("3Y", "3 years", 3),
]


@dataclass
class WindowReturn:
    """The portfolio and its benchmark over one window.

    Every input to the formula is kept, not just the answer, because a return
    nobody can reconstruct is a return nobody will defend in a meeting.
    """

    code: str
    label: str
    years: int
    start: date | None = None
    end: date | None = None
    opening: float = 0.0            # market value at the start of the window
    closing: float = 0.0            # market value today
    injected: float = 0.0           # new cash put in during the window
    withdrawn: float = 0.0          # sales and income taken out during it
    average_capital: float = 0.0    # time-weighted denominator
    benchmark: float | None = None  # index return over the same dates
    holdings: int = 0
    priced: int = 0
    opened_during: int = 0
    unavailable: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.start is not None and self.average_capital > 0

    @property
    def gain(self) -> float:
        """Closing - opening - injected + withdrawn. The formula, unmodified."""
        return self.closing - self.opening - self.injected + self.withdrawn

    @property
    def elapsed(self) -> float:
        if self.start is None or self.end is None:
            return 0.0
        return (self.end - self.start).days / 365.25

    @property
    def portfolio(self) -> float | None:
        """Cumulative return over the window."""
        if not self.ok:
            return None
        return self.gain / self.average_capital

    def _annual(self, total: float | None) -> float | None:
        """A cumulative return expressed as a rate per year.

        Left alone for a window of a year or less: annualising three months of
        performance multiplies a quarter's noise by four and presents it as a
        forecast.
        """
        if total is None:
            return None
        years = self.elapsed
        if years <= 1.0:
            return total
        if 1.0 + total <= 0:
            return None
        return (1.0 + total) ** (1.0 / years) - 1.0

    @property
    def portfolio_annual(self) -> float | None:
        return self._annual(self.portfolio)

    @property
    def benchmark_annual(self) -> float | None:
        return self._annual(self.benchmark)

    @property
    def alpha(self) -> float | None:
        """Difference, not ratio: both sides span the same dates, so a reader
        can check it by eye."""
        if self.portfolio is None or self.benchmark is None:
            return None
        return self.portfolio - self.benchmark

    @property
    def alpha_annual(self) -> float | None:
        if self.portfolio_annual is None or self.benchmark_annual is None:
            return None
        return self.portfolio_annual - self.benchmark_annual

    @property
    def beat(self) -> bool | None:
        return None if self.alpha is None else self.alpha > 0

    @property
    def annualised(self) -> bool:
        return self.elapsed > 1.0

    @property
    def partial(self) -> bool:
        """True when the window covers less of the book than it appears to."""
        return bool(self.unavailable) or self.opened_during > 0


def _series(raw: pd.Series) -> pd.Series:
    """Tz-naive, sorted, NaN-free. Index series arrive tz-aware from Yahoo and
    a tz-aware index cannot be sliced with a plain date."""
    out = raw.dropna()
    index = pd.to_datetime(out.index)
    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)
    return pd.Series(out.to_numpy(), index=index).sort_index()


def quantity_at(stream: CashflowStream, prices: pd.Series,
                when: date) -> float | None:
    """Shares held on a past date.

    A holding read from a statement has one purchase and a constant quantity, so
    the usual answer is the stated quantity, or zero before it was bought. The
    general case -- a position built in lots, or partly sold -- is reconstructed
    by dividing each flow's cash by the price on its own date, then rescaling so
    the lots sum to the quantity actually on the statement. The rescaling is
    what keeps the dividend adjustment in the price series from inflating the
    older lots: only the relative sizes are taken from the division, the level
    comes from the statement.
    """
    if stream.quantity is None:
        return None

    lots: list[tuple[date, float]] = []
    for flow in stream.ordered():
        if flow.kind not in CONTRIBUTION_TYPES and flow.kind is not FlowType.SALE:
            continue
        prior = prices.loc[: pd.Timestamp(flow.when)]
        if prior.empty or float(prior.iloc[-1]) == 0:
            return None
        # Negative amount (a purchase) buys units; a sale sells them.
        lots.append((flow.when, -flow.amount / float(prior.iloc[-1])))

    total = sum(units for _, units in lots)
    if total <= 0:
        return None
    scale = stream.quantity / total
    held = sum(units for on, units in lots if on <= when)
    return max(held * scale, 0.0)


def holding_value_at(stream: CashflowStream, md: MarketData, when: date,
                     reporting_ccy: str = "USD") -> float | None:
    """One holding's market value on a past date, in the reporting currency.

    Returns 0.0 when the position had not been opened yet, because that is a
    value and not a gap -- a book that owned nothing owned nothing. Returns None
    when the position existed but cannot be priced on that date, which is a gap,
    and the caller must then leave the holding out of both sides of the sum
    rather than counting a zero opening against a real closing value.
    """
    flows = stream.ordered()
    if not flows or flows[0].when > when:
        return 0.0
    # Quantity is what makes a past valuation possible at all. A fund NAV or a
    # property appraisal is struck twice a year; there is no honest way to
    # interpolate one to an arbitrary date, so it is reported as unavailable.
    if stream.quantity is None:
        return None
    try:
        prices = _series(md.prices(stream.asset_id))
    except Exception:  # noqa: BLE001 - a missing series is a gap, not a crash
        return None

    qty = quantity_at(stream, prices, when)
    if qty is None:
        return None
    if qty == 0.0:
        return 0.0

    prior = prices.loc[: pd.Timestamp(when)]
    if prior.empty:
        return None
    try:
        rate = md.fx_rate(stream.currency, reporting_ccy, when)
    except Exception:  # noqa: BLE001
        return None
    return float(prior.iloc[-1]) * qty * rate


def window_return(streams: list[CashflowStream], md: MarketData,
                  benchmark_ticker: str, reporting_ccy: str = "USD",
                  *, years: int = 1, code: str = "1Y", label: str = "1 year",
                  end: date | None = None) -> WindowReturn:
    """The portfolio's return over the last ``years``, and the benchmark's.

    The window ends on the portfolio's valuation date rather than today, so the
    two sides are measured to the same close.
    """
    out = WindowReturn(code=code, label=label, years=years)
    if not streams:
        out.reason = "no holdings"
        return out

    dated = [s.valuation_date for s in streams if s.valuation_date]
    end_date = end or (max(dated) if dated else date.today())
    start_date = (pd.Timestamp(end_date) - pd.DateOffset(years=years)).date()
    out.start, out.end = start_date, end_date
    span = (end_date - start_date).days or 1

    closing = current_values(streams, md, reporting_ccy)
    weighted_flows = 0.0

    for stream in streams:
        out.holdings += 1
        opening = holding_value_at(stream, md, start_date, reporting_ccy)
        if opening is None:
            out.unavailable.append(stream.name)
            continue

        # The statement's own value is used wherever the window ends on the
        # valuation date, which is the normal case: it makes the closing figure
        # here tie exactly to the market value on every other screen. Only a
        # window deliberately cut short falls back to pricing the position.
        if stream.valuation_date and end_date < stream.valuation_date:
            settled = holding_value_at(stream, md, end_date, reporting_ccy)
            if settled is None:
                out.unavailable.append(stream.name)
                continue
        else:
            settled = closing.get(stream.asset_id, 0.0)

        out.priced += 1
        out.opening += opening
        out.closing += settled
        # Opened inside the window, rather than merely worth nothing at its
        # start. A position sold out before the window also opens at zero, and
        # calling that one "newly opened" would read as the opposite of what
        # happened.
        first = stream.ordered()[0].when if stream.flows else None
        if first is not None and first > start_date:
            out.opened_during += 1

        for flow in to_reporting_currency(stream, md, reporting_ccy):
            if flow.kind is FlowType.TERMINAL_VALUE:
                continue
            if not (start_date < flow.when <= end_date):
                continue
            # Modified Dietz weight: the share of the window the money was
            # actually in the book for.
            share = (end_date - flow.when).days / span
            if flow.kind in CONTRIBUTION_TYPES:
                out.injected += -flow.amount
                weighted_flows += -flow.amount * share
            elif flow.amount > 0:
                out.withdrawn += flow.amount
                weighted_flows -= flow.amount * share

    out.average_capital = out.opening + weighted_flows

    if not out.ok:
        out.reason = out.reason or (
            f"the book held nothing over the {label} to {end_date}"
        )
        return out

    try:
        index = _series(md.prices(benchmark_ticker))
    except Exception as exc:  # noqa: BLE001
        out.reason = f"{benchmark_ticker} unavailable: {exc}"
        return out

    first = index.loc[: pd.Timestamp(start_date)]
    last = index.loc[: pd.Timestamp(end_date)]
    if first.empty or last.empty or float(first.iloc[-1]) == 0:
        out.reason = f"{benchmark_ticker} does not price back to {start_date}"
        return out

    out.benchmark = float(last.iloc[-1]) / float(first.iloc[-1]) - 1.0
    return out


def windows_against(streams: list[CashflowStream], md: MarketData,
                    benchmark_ticker: str, reporting_ccy: str = "USD",
                    windows: list[tuple[str, str, int]] | None = None,
                    end: date | None = None) -> list[WindowReturn]:
    """Every standard window, for one portfolio against one benchmark."""
    return [
        window_return(streams, md, benchmark_ticker, reporting_ccy,
                      years=years, code=code, label=label, end=end)
        for code, label, years in (windows or WINDOWS)
    ]
