"""Return metrics computed from a CashflowStream.

Everything here is deterministic arithmetic with no network access and no model
involvement. That is a design rule, not an accident: the language model in this
system is handed the output of these functions and asked to write prose about
them. It never computes a number itself, so it cannot invent one.

Two families of metric, and the distinction matters when reading the output:

  Money-weighted (XIRR, TVPI, DPI, MOIC)
      Reflect when money went in and out. The right lens for private assets,
      where the manager controls the timing and should be judged on it.

  Time-weighted (TWR)
      Strips out the effect of contribution timing. The convention for public
      markets, because the investor -- not the manager -- chose the timing.

The league table ranks on Direct Alpha (see directalpha.py), which is
money-weighted. TWR is reported alongside for public holdings so the familiar
number is still visible.
"""

from __future__ import annotations

import math
from datetime import date

from scipy.optimize import brentq

from .cashflows import CashflowStream, FlowType

DAYS_PER_YEAR = 365.0

#: XIRR search bounds. -99.99% is near-total loss; +1000% covers any plausible
#: venture outcome. A root outside this range means the cashflows are pathological
#: and a single rate of return is not a meaningful summary of them.
_RATE_FLOOR = -0.9999
_RATE_CEILING = 10.0


class MetricError(ValueError):
    """Raised when a metric is genuinely undefined for the given cashflows.

    Callers should surface this to the user rather than substituting zero -- a
    missing return and a zero return mean very different things to a reader.
    """


def _year_fractions(flows, origin: date) -> list[float]:
    return [(f.when - origin).days / DAYS_PER_YEAR for f in flows]


def npv(rate: float, flows, origin: date) -> float:
    """Net present value of signed cashflows at a continuous annual ``rate``."""
    if rate <= -1.0:
        return math.inf
    return sum(
        f.amount / (1.0 + rate) ** t
        for f, t in zip(flows, _year_fractions(flows, origin))
    )


def xirr(stream: CashflowStream) -> float:
    """Money-weighted annualised return, solving NPV(r) = 0.

    Works identically on a share purchase, a property acquisition and a fund
    commitment -- which is the whole point of normalising to cashflows first.
    """
    flows = stream.ordered()
    if len(flows) < 2:
        raise MetricError(f"{stream.name}: need at least two cashflows for an IRR")

    amounts = [f.amount for f in flows]
    if not (any(a < 0 for a in amounts) and any(a > 0 for a in amounts)):
        raise MetricError(
            f"{stream.name}: IRR needs both an outflow and an inflow; "
            f"got only {'outflows' if all(a <= 0 for a in amounts) else 'inflows'}"
        )

    origin = flows[0].when

    # No elapsed time, no annual rate. Every flow discounts by (1+r)^0 = 1, so
    # NPV is constant and the solver returns whichever bracket end it started
    # from -- a position bought this morning at today's price came back as
    # -99.99%, which is not a cautious answer but a wrong one. It would then
    # rank as the worst holding in the book.
    #
    # Refusing is correct: a same-day position has a value, and it does not yet
    # have a return. The caller reports it as unscored, which every screen now
    # handles.
    if (flows[-1].when - origin).days <= 0:
        raise MetricError(
            f"{stream.name}: every cashflow falls on {origin}, so no time has "
            "elapsed and there is no annualised return to compute. The "
            "position is held but not yet scored."
        )

    def f(r: float) -> float:
        return npv(r, flows, origin)

    lo, hi = _RATE_FLOOR, _RATE_CEILING
    f_lo, f_hi = f(lo), f(hi)

    if math.isnan(f_lo) or math.isnan(f_hi):
        raise MetricError(f"{stream.name}: cashflows produced a non-finite NPV")

    # A sign change across the bracket guarantees brentq converges. Without one,
    # scan for a sub-bracket that does change sign -- this happens with genuinely
    # irregular private-fund flows (call, distribution, call again).
    if f_lo * f_hi > 0:
        step = 0.05
        prev_r, prev_v = lo, f_lo
        found = False
        r = lo + step
        while r <= hi:
            v = f(r)
            if prev_v * v < 0:
                lo, hi, found = prev_r, r, True
                break
            prev_r, prev_v = r, v
            r += step
        if not found:
            raise MetricError(
                f"{stream.name}: no IRR exists between "
                f"{_RATE_FLOOR:.0%} and {_RATE_CEILING:.0%}. The cashflow pattern "
                "has no single rate of return; report TVPI instead."
            )

    return float(brentq(f, lo, hi, xtol=1e-10, maxiter=200))


def tvpi(stream: CashflowStream) -> float:
    """Total value to paid-in: (distributions + current value) / contributions.

    The headline private-markets multiple. Says nothing about how long the money
    was tied up, which is exactly why it must never be read without an IRR or
    Direct Alpha beside it -- a 2.0x over four years and a 2.0x over twelve are
    wildly different investments that this number reports identically.
    """
    paid_in = stream.contributions
    if paid_in <= 0:
        raise MetricError(f"{stream.name}: TVPI undefined with no contributions")
    return (stream.distributions + stream.terminal_value) / paid_in


def dpi(stream: CashflowStream) -> float:
    """Distributions to paid-in -- realised cash only, ignoring carrying value.

    The honest half of TVPI. A fund with TVPI 2.1x and DPI 0.3x has returned very
    little actual cash and is asking to be believed about the rest.
    """
    paid_in = stream.contributions
    if paid_in <= 0:
        raise MetricError(f"{stream.name}: DPI undefined with no contributions")
    return stream.distributions / paid_in


def rvpi(stream: CashflowStream) -> float:
    """Residual value to paid-in -- the unrealised portion."""
    paid_in = stream.contributions
    if paid_in <= 0:
        raise MetricError(f"{stream.name}: RVPI undefined with no contributions")
    return stream.terminal_value / paid_in


def moic(stream: CashflowStream) -> float:
    """Multiple on invested capital. Identical to TVPI under this cashflow model.

    Kept as a separate name because private-markets reporting uses both terms and
    a reader looking for 'MOIC' should find it rather than assume it is missing.
    """
    return tvpi(stream)


def holding_period_years(stream: CashflowStream) -> float:
    flows = stream.ordered()
    if len(flows) < 2:
        return 0.0
    return (flows[-1].when - flows[0].when).days / DAYS_PER_YEAR


def twr_from_prices(prices, dividends=None) -> float:
    """Annualised time-weighted return from a price series.

    Only defined for assets that have a continuous observable price, so this is
    a public-markets metric. Private assets get XIRR and Direct Alpha instead.

    ``prices`` is a pandas Series indexed by date; ``dividends`` an optional
    Series of per-share payments which are treated as reinvested at the payment
    date, matching total-return index convention.
    """
    prices = prices.dropna()
    if len(prices) < 2:
        raise MetricError("TWR needs at least two price observations")

    if dividends is not None and len(dividends) > 0:
        growth = 1.0
        divs = dividends[dividends > 0]
        for i in range(1, len(prices)):
            p0, p1 = prices.iloc[i - 1], prices.iloc[i]
            d = float(
                divs[(divs.index > prices.index[i - 1]) & (divs.index <= prices.index[i])].sum()
            )
            growth *= (p1 + d) / p0
        total_return = growth
    else:
        total_return = float(prices.iloc[-1] / prices.iloc[0])

    years = (prices.index[-1] - prices.index[0]).days / DAYS_PER_YEAR
    if years <= 0:
        raise MetricError("TWR needs a positive time span")
    return total_return ** (1.0 / years) - 1.0


def summarise(stream: CashflowStream) -> dict:
    """All metrics for one asset, with failures reported rather than hidden.

    Returns a plain dict destined for the facts layer, so every key here is a
    number the narrative model is permitted to cite -- and nothing else is.
    """
    out: dict = {
        "asset_id": stream.asset_id,
        "name": stream.name,
        "asset_class": stream.asset_class.value,
        "currency": stream.currency,
        "country": stream.country,
        "contributions": round(stream.contributions, 2),
        "distributions": round(stream.distributions, 2),
        "terminal_value": round(stream.terminal_value, 2),
        "valuation_date": stream.valuation_date.isoformat() if stream.valuation_date else None,
        "holding_period_years": round(holding_period_years(stream), 2),
        "errors": [],
    }
    for label, fn in (
        ("irr", xirr), ("tvpi", tvpi), ("dpi", dpi), ("rvpi", rvpi),
    ):
        try:
            out[label] = round(fn(stream), 6)
        except MetricError as exc:
            out[label] = None
            out["errors"].append(str(exc))
    return out
