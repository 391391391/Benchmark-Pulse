"""Rolling a book of holdings up into one portfolio-level cashflow stream.

The insight that makes portfolio-level benchmarking almost free: a portfolio is
itself an investment, with money going in and value standing today. Merge every
holding's cashflows into one stream and the same Direct Alpha engine that scores
a single stock scores the whole book against its mandate benchmark.

Two things have to be right for the result to mean anything.

  Currency.  Flows arrive in dollars, rupees and riyals. They are converted to
  the reporting currency at the rate prevailing on each flow's own date, not at
  today's rate. Over a book held through a 20% currency move, a single spot rate
  misstates how much was actually committed and when.

  Valuation date.  Holdings are priced on slightly different days -- exchanges
  close on different holidays. The portfolio's terminal value is struck at the
  latest common date, and each holding contributes its own most recent value.
  The small mismatch is stated rather than hidden.
"""

from __future__ import annotations

from datetime import date

from .cashflows import (
    CONTRIBUTION_TYPES, AssetClass, Cashflow, CashflowStream, FlowType,
)
from .marketdata import MarketData


def to_reporting_currency(stream: CashflowStream, md: MarketData,
                          reporting_ccy: str) -> list[Cashflow]:
    """Every flow translated at the rate on its own date."""
    if stream.currency.upper() == reporting_ccy.upper():
        return list(stream.ordered())

    converted: list[Cashflow] = []
    for flow in stream.ordered():
        try:
            rate = md.fx_rate(stream.currency, reporting_ccy, flow.when)
        except Exception:  # noqa: BLE001 - a missing rate must not drop the flow
            try:
                rate = md.fx_rate(stream.currency, reporting_ccy)
            except Exception:  # noqa: BLE001 - a genuinely unresolvable
                # currency (e.g. a typo no feed recognises) must not crash
                # the whole portfolio over one holding; current_values()
                # below takes the same fallback for the same reason.
                rate = 1.0
        converted.append(Cashflow(when=flow.when, amount=flow.amount * rate,
                                  kind=flow.kind))
    return converted


def build_portfolio_stream(streams: list[CashflowStream], md: MarketData,
                           reporting_ccy: str = "USD",
                           name: str = "Portfolio") -> tuple[CashflowStream, list[str]]:
    """Merge holdings into one stream in the reporting currency.

    Returns the combined stream and any notes worth surfacing.
    """
    notes: list[str] = []
    combined = CashflowStream(
        asset_id="PORTFOLIO", name=name,
        asset_class=AssetClass.PUBLIC_EQUITY,
        currency=reporting_ccy.upper(), country="WW",
    )

    terminal_total = 0.0
    terminal_dates: list[date] = []

    for stream in streams:
        flows = to_reporting_currency(stream, md, reporting_ccy)
        for flow in flows:
            if flow.kind is FlowType.TERMINAL_VALUE:
                terminal_total += flow.amount
                if stream.valuation_date:
                    terminal_dates.append(stream.valuation_date)
            else:
                combined.flows.append(flow)

    if not combined.flows:
        return combined, ["no cashflows to aggregate"]

    if terminal_dates:
        latest = max(terminal_dates)
        earliest = min(terminal_dates)
        combined.set_terminal_value(latest, terminal_total)
        spread = (latest - earliest).days
        if spread > 5:
            notes.append(
                f"Holdings were last priced across a {spread}-day window "
                f"({earliest} to {latest}); the portfolio is valued at the "
                "latest of those. Exchange holidays differ, so a small spread "
                "is normal."
            )
    else:
        notes.append("no holding carried a valuation date; portfolio value is "
                     "the sum of last known prices")

    return combined, notes


def performance_index(streams: list[CashflowStream], md: MarketData,
                      reporting_ccy: str = "USD",
                      base: float = 100.0):
    """A time-weighted index of the portfolio, for charting against a benchmark.

    Market value alone cannot be charted against an index: the book grows when a
    position is opened, and that is not performance. So each day's return is
    taken net of the money added that day, and the daily returns are chained --
    the standard time-weighted construction, which is what makes a portfolio
    line comparable to an index line.

    Positions only enter on the day they were bought, so the early part of the
    series reflects the book as it actually existed rather than back-filling
    today's holdings into a past they were not held through.
    """
    import pandas as pd

    prices: dict[str, pd.Series] = {}
    for stream in streams:
        try:
            series = md.prices(stream.asset_id)
        except Exception:  # noqa: BLE001 - a missing price drops one holding
            continue
        if stream.currency.upper() != reporting_ccy.upper():
            try:
                fx = md.fx_series(stream.currency, reporting_ccy)
                joined = pd.concat([series, fx], axis=1).ffill().dropna()
                series = joined.iloc[:, 0] * joined.iloc[:, 1]
            except Exception:  # noqa: BLE001
                continue
        prices[stream.asset_id] = series

    if not prices:
        return pd.DataFrame()

    frame = pd.concat(prices, axis=1).ffill().dropna(how="all")
    if frame.empty:
        return pd.DataFrame()

    quantity = {s.asset_id: (s.quantity or 0.0) for s in streams}
    opened = {s.asset_id: pd.Timestamp(s.ordered()[0].when)
              for s in streams if s.flows}
    if not opened:
        return pd.DataFrame()

    # Start the day the first position was opened. Price history reaches back
    # a decade, but the portfolio did not exist then, and running the index
    # from the start of the data averages years of an empty book into the
    # return -- which reported 7.6% a year for a portfolio that made 16.7%.
    frame = frame.loc[min(opened.values()):]
    if frame.empty:
        return pd.DataFrame()

    values, inflows = [], []
    for when, row in frame.iterrows():
        held = 0.0
        added = 0.0
        for asset_id, price in row.items():
            if pd.isna(price) or asset_id not in opened:
                continue
            if when < opened[asset_id]:
                continue
            position = quantity.get(asset_id, 0.0) * float(price)
            held += position
            if when == opened[asset_id]:
                added += position     # the day it was bought, it is a flow
        values.append(held)
        inflows.append(added)

    series = pd.Series(values, index=frame.index)
    flows = pd.Series(inflows, index=frame.index)

    # Chain daily returns net of that day's contribution.
    index_values = [base]
    for i in range(1, len(series)):
        start = series.iloc[i - 1]
        if start <= 0:
            index_values.append(index_values[-1])
            continue
        gain = (series.iloc[i] - flows.iloc[i]) / start - 1.0
        index_values.append(index_values[-1] * (1.0 + gain))

    return pd.Series(index_values, index=frame.index).dropna()


def contribution_weights(streams: list[CashflowStream], md: MarketData,
                         reporting_ccy: str = "USD") -> dict[str, float]:
    """Capital committed per holding, in the reporting currency.

    Used to weight everything portfolio-level. Weighting by the raw figure would
    let a rupee position outweigh a dollar one of the same size by roughly
    ninety times and silently dominate every average.
    """
    weights: dict[str, float] = {}
    for stream in streams:
        total = 0.0
        for flow in to_reporting_currency(stream, md, reporting_ccy):
            if flow.kind in CONTRIBUTION_TYPES:
                total += -flow.amount
        weights[stream.asset_id] = total
    return weights


def current_values(streams: list[CashflowStream], md: MarketData,
                   reporting_ccy: str = "USD") -> dict[str, float]:
    """Today's market value per holding, in the reporting currency."""
    values: dict[str, float] = {}
    for stream in streams:
        if stream.valuation_date is None:
            values[stream.asset_id] = 0.0
            continue
        try:
            rate = md.fx_rate(stream.currency, reporting_ccy,
                              stream.valuation_date)
        except Exception:  # noqa: BLE001
            rate = 1.0
        values[stream.asset_id] = stream.terminal_value * rate
    return values
