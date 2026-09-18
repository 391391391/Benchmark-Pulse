"""Public Market Equivalent and Direct Alpha -- the common currency.

The question every one of these methods asks is the same:

    "If this exact money, on these exact dates, had gone into a public index
     instead, would the investor be better off today?"

That framing is what makes a Riyadh office tower comparable to Apple. Neither
needs a peer universe, a vintage database or a subscription -- only the asset's
own cashflows and a free index price series.

Two methods, deliberately both reported:

  KS-PME (Kaplan-Schoar)
      A ratio. Every flow is discounted by the index's growth to the valuation
      date. Above 1.0 means the asset beat the index. Intuitive, but it is a
      multiple, so it says nothing about the time taken to achieve it.

  Direct Alpha (Gredil, Griffiths & Stucke)
      A rate. The same index-adjusted flows are run through an IRR, giving an
      annualised excess return in percentage points. Because it is a rate in the
      same unit for every asset class, this is what the league table ranks on.

Direct Alpha is money-weighted, so it credits and penalises timing. For public
equities the convention is time-weighted; that number is reported separately by
metrics.twr_from_prices. The difference is stated openly in the memo rather than
quietly resolved in favour of whichever looks better.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from .cashflows import CashflowStream, FlowType
from .metrics import MetricError, xirr
from .cashflows import Cashflow


@dataclass
class PMEResult:
    asset_id: str
    name: str
    benchmark_ticker: str
    ks_pme: float | None          # >1 beat the index
    direct_alpha: float | None    # annualised excess return, e.g. 0.041 = +4.1%
    asset_irr: float | None
    benchmark_irr: float | None   # IRR of the same flows invested in the index
    valuation_date: date
    notes: list[str]

    @property
    def outperformed(self) -> bool | None:
        if self.direct_alpha is None:
            return None
        return self.direct_alpha > 0


#: A benchmark whose feed has stopped updating stops earning return, while the
#: holding measured against it keeps moving. Beyond this many days the resulting
#: alpha is an artefact of the data feed, not a fact about the investment.
_BENCHMARK_STALE_DAYS = 14


class _NormalisedIndex:
    """A benchmark series prepared once for repeated as-of lookups.

    Normalising inside the lookup was costing a full re-parse and re-sort per
    cashflow. With thirty assets and years of flows apiece that is the difference
    between a report that renders while the analyst waits and one that does not.
    """

    __slots__ = ("_dates", "_values", "earliest", "latest")

    def __init__(self, index_prices: pd.Series) -> None:
        s = index_prices.dropna()
        if s.empty:
            raise MetricError("benchmark price series is empty")

        idx = pd.to_datetime(s.index)
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_localize(None)
        s = pd.Series(s.to_numpy(), index=idx).sort_index()

        self._dates = s.index.to_numpy(dtype="datetime64[D]")
        self._values = s.to_numpy(dtype=float)
        self.earliest = s.index.min().date()
        self.latest = s.index.max().date()

    def level_on(self, when: date) -> float:
        """Level on ``when``, or the most recent prior close.

        Markets close at weekends and on local holidays that differ across the
        US, India and Saudi Arabia, so an exact date match fails routinely.
        Carrying the last observed level forward is the standard treatment.
        """
        import numpy as np

        pos = int(np.searchsorted(self._dates, np.datetime64(when, "D"), side="right"))
        if pos == 0:
            # Flow predates the series. Using the earliest available level
            # understates index growth and so flatters the asset -- compute_pme
            # flags this case rather than letting it pass unremarked.
            return float(self._values[0])
        return float(self._values[pos - 1])


def compute_pme(
    stream: CashflowStream,
    index_prices: pd.Series,
    benchmark_ticker: str,
) -> PMEResult:
    """Benchmark one asset against one index using only its own cashflows."""
    notes: list[str] = []
    flows = stream.ordered()
    valuation_date = stream.valuation_date or flows[-1].when

    if not flows:
        raise MetricError(f"{stream.name}: no cashflows to benchmark")

    try:
        series = _NormalisedIndex(index_prices)
    except MetricError:
        raise MetricError(f"{stream.name}: benchmark {benchmark_ticker} has no data")

    if flows[0].when < series.earliest:
        notes.append(
            f"first cashflow ({flows[0].when}) predates available {benchmark_ticker} "
            f"history ({series.earliest}); earliest level carried back, which "
            "understates index growth and flatters the asset"
        )

    # A benchmark that stopped updating before the holding was valued freezes
    # the comparator while the holding keeps moving, which manufactures alpha
    # out of nothing. Say so rather than reporting the number unqualified.
    benchmark_lag = (valuation_date - series.latest).days
    if benchmark_lag > _BENCHMARK_STALE_DAYS:
        notes.append(
            f"{benchmark_ticker} last priced {series.latest}, {benchmark_lag} days "
            f"before this holding's valuation date ({valuation_date}); the "
            "benchmark is frozen over that gap and the resulting alpha is "
            "overstated"
        )

    index_at_valuation = series.level_on(valuation_date)

    # --- KS-PME -------------------------------------------------------------
    # Grow every flow forward to the valuation date at the index's return, then
    # compare what came back against what went in.
    fv_contributions = 0.0
    fv_distributions = 0.0
    for f in flows:
        growth = index_at_valuation / series.level_on(f.when)
        if f.amount < 0:
            fv_contributions += -f.amount * growth
        elif f.kind is not FlowType.TERMINAL_VALUE:
            fv_distributions += f.amount * growth

    # The terminal value is already expressed at the valuation date, so it is
    # added undiscounted -- growing it would double-count the index's return.
    numerator = fv_distributions + stream.terminal_value
    ks = numerator / fv_contributions if fv_contributions > 0 else None

    # --- Direct Alpha -------------------------------------------------------
    # Discount each flow by the index's growth from that date to the valuation
    # date, then take the IRR of the adjusted stream. The resulting rate is the
    # annualised return *in excess of* the index.
    adjusted: list[Cashflow] = []
    for f in flows:
        growth = index_at_valuation / series.level_on(f.when)
        if f.kind is FlowType.TERMINAL_VALUE:
            adjusted.append(f)
        else:
            adjusted.append(
                Cashflow(when=f.when, amount=f.amount * growth, kind=f.kind)
            )

    adjusted_stream = CashflowStream(
        asset_id=stream.asset_id,
        name=stream.name,
        asset_class=stream.asset_class,
        currency=stream.currency,
        country=stream.country,
        flows=adjusted,
        valuation_date=valuation_date,
    )

    try:
        direct_alpha = xirr(adjusted_stream)
    except MetricError as exc:
        direct_alpha = None
        notes.append(f"Direct Alpha unavailable: {exc}")

    try:
        asset_irr = xirr(stream)
    except MetricError:
        asset_irr = None

    # The index's own IRR over the same flow pattern -- the "what if" return.
    benchmark_irr = None
    if asset_irr is not None and direct_alpha is not None:
        # Direct Alpha compounds with the benchmark: (1+asset) = (1+bmk)(1+alpha)
        benchmark_irr = (1.0 + asset_irr) / (1.0 + direct_alpha) - 1.0

    if stream.is_stale:
        notes.append(
            f"carrying value dated {valuation_date} is more than 90 days old, "
            "while the benchmark is priced to today -- comparison is indicative"
        )

    # Full precision is retained here on purpose. Rounding is a presentation
    # concern, and rounding mid-computation breaks the identity
    # (1 + asset IRR) = (1 + benchmark IRR)(1 + Direct Alpha) that the facts
    # layer relies on to reconcile.
    return PMEResult(
        asset_id=stream.asset_id,
        name=stream.name,
        benchmark_ticker=benchmark_ticker,
        ks_pme=ks,
        direct_alpha=direct_alpha,
        asset_irr=asset_irr,
        benchmark_irr=benchmark_irr,
        valuation_date=valuation_date,
        notes=notes,
    )


def league_table(results: list[PMEResult]) -> pd.DataFrame:
    """Rank every asset by Direct Alpha -- the screen the whole product exists for.

    Public, private and real assets in one ordering, on one measure, honestly
    comparable. Assets whose alpha could not be computed sort last rather than
    being dropped, so nothing disappears silently from the client's portfolio.
    """
    rows = [
        {
            "asset_id": r.asset_id,
            "name": r.name,
            "benchmark": r.benchmark_ticker,
            "irr": r.asset_irr,
            "benchmark_irr": r.benchmark_irr,
            "direct_alpha": r.direct_alpha,
            "ks_pme": r.ks_pme,
            "outperformed": r.outperformed,
            "flags": "; ".join(r.notes) if r.notes else "",
        }
        for r in results
    ]
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(
        "direct_alpha", ascending=False, na_position="last"
    ).reset_index(drop=True)
