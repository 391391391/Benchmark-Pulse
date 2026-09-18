"""Saying when a comparison is not fair.

Direct Alpha puts every asset on one scale, but a number on a shared scale can
still mislead. Four distortions matter enough to correct for, and each one has
flipped a real verdict in this portfolio:

  Leverage    A 55%-levered property compared against an unlevered index is not
              being judged on the same risk. Property returns look excellent
              until the debt is stripped out.

  J-curve     A two-year-old venture fund shows a negative IRR because fees are
              charged before value is realised, not because it is bad. Ranking
              it last in a league table is simply wrong.

  Currency    A holding can beat its local benchmark handsomely and still lose
              the investor money once translated. The two facts are different
              and both belong in the report.

  Staleness   An appraisal struck months ago compared against an index priced
              this morning is a comparison across different points in time.

The house style throughout is to report the headline number *and* the adjusted
number side by side, never to silently replace one with the other. The analyst
decides which to lead with; the tool makes sure they can see both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .cashflows import AssetClass, CashflowStream
from .marketdata import MarketData, MarketDataError

#: Private funds younger than this are still in the drawdown phase, where a
#: negative IRR is a structural feature rather than a performance signal.
J_CURVE_YEARS = 4

#: Beyond this, a carrying value and a live index are describing different days.
STALE_MARK_DAYS = 90


@dataclass
class Adjustment:
    """One caveat about one holding, in a form the narrative layer can cite."""

    kind: str               # leverage | j_curve | currency | staleness
    headline: str           # one line, safe to put straight into a memo
    detail: str
    severity: str           # info | warning | material
    values: dict = field(default_factory=dict)


def unlever_return(levered_irr: float, ltv: float, debt_rate: float) -> float:
    """Return the asset would have produced with no debt.

    Weighted-average relationship between asset, equity and debt returns:

        R_asset = R_equity x (1 - LTV) + R_debt x LTV

    Reverses the amplifying effect of borrowing so a property can be compared
    against an unlevered index on like terms. Note the direction: whenever the
    asset out-earns its debt, unlevering *reduces* the reported return, which is
    the opposite of the flattering direction and the reason it is worth doing.
    """
    if not 0.0 <= ltv < 1.0:
        raise ValueError(f"LTV must be in [0, 1), got {ltv}")
    return levered_irr * (1.0 - ltv) + debt_rate * ltv


def leverage_adjustment(stream: CashflowStream,
                        levered_irr: float | None) -> Adjustment | None:
    """Show the unlevered equivalent whenever debt is doing part of the work."""
    ltv = stream.leverage_ratio or 0.0
    if ltv <= 0.0 or levered_irr is None:
        return None

    rate = stream.debt_rate or 0.0
    unlevered = unlever_return(levered_irr, ltv, rate)
    gap = levered_irr - unlevered

    return Adjustment(
        kind="leverage",
        headline=(
            f"{stream.name} returned {levered_irr * 100:.1f}% on equity, but it is "
            f"{ltv:.0%} levered at {rate:.2%}. Unlevered, the asset itself "
            f"returned {unlevered * 100:.1f}%."
        ),
        detail=(
            f"{gap * 100:.1f} percentage points of the headline return come from "
            "borrowing rather than from the property. The benchmark it is "
            "measured against is unlevered, so the unlevered figure is the "
            "like-for-like comparison."
        ),
        severity="material" if abs(gap) >= 0.03 else "warning",
        values={"levered_irr": levered_irr, "unlevered_irr": unlevered,
                "ltv": ltv, "debt_rate": rate, "leverage_contribution": gap},
    )


def j_curve_adjustment(stream: CashflowStream, irr: float | None,
                       dpi: float | None, as_of: date | None = None) -> Adjustment | None:
    """Flag funds too young for their IRR to mean what it appears to mean."""
    if stream.asset_class is not AssetClass.PRIVATE_FUND:
        return None
    if stream.vintage_year is None:
        return None

    as_of = as_of or date.today()
    age = as_of.year - stream.vintage_year
    if age > J_CURVE_YEARS:
        return None

    return Adjustment(
        kind="j_curve",
        headline=(
            f"{stream.name} is a {stream.vintage_year} vintage, {age} year"
            f"{'s' if age != 1 else ''} old. Its "
            f"{'negative ' if irr is not None and irr < 0 else ''}IRR reflects the "
            "J-curve, not performance."
        ),
        detail=(
            "Management fees and early costs are borne before value is realised, "
            f"so funds of this age typically show depressed or negative returns. "
            f"Realised distributions to date are "
            f"{'nil' if not dpi else f'{dpi:.2f}x paid-in capital'}. Ranking this "
            "fund against mature holdings is not a like-for-like comparison, and "
            "its Direct Alpha should be treated as indicative only."
        ),
        severity="material",
        values={"vintage_year": stream.vintage_year, "age_years": age,
                "irr": irr, "dpi": dpi},
    )


@dataclass
class CurrencySplit:
    """One holding's return with and without the exchange rate in it.

    A return is a percentage, so a rupee holding can be compared to a dollar
    index without converting anything -- which is why the benchmark comparison
    is done in the holding's own currency. But where the two currencies differ
    the investor's own outcome is a different number from the company's
    performance, and only showing one of them hides which is which:

        local     what the business did, in the currency it trades in
        with FX   what the holder actually got, once translated
        fx        the difference, annualised

    Both are reported wherever they differ. Neither is "the" return.
    """

    currency: str
    target: str
    local: float
    with_fx: float
    fx: float
    pegged: bool = False

    @property
    def material(self) -> bool:
        """Worth a reader's attention: half a point a year or more."""
        return not self.pegged and abs(self.fx) >= 0.005


def currency_split(stream: CashflowStream, local_irr: float | None,
                   md: MarketData, target_ccy: str) -> CurrencySplit | None:
    """The same return measured two ways, or None when they cannot differ.

    Returns None when the holding already trades in the target currency, since
    there is then nothing to split and showing two identical figures would
    imply a distinction that does not exist.
    """
    if local_irr is None or not target_ccy:
        return None
    currency = (stream.currency or "").upper()
    target = target_ccy.upper()
    if not currency or currency == target:
        return None

    flows = stream.ordered()
    if len(flows) < 2:
        return None
    start, end = flows[0].when, flows[-1].when
    years = (end - start).days / 365.0
    if years <= 0.25:
        return None

    # A pegged currency cannot move, so the two returns are the same number.
    # Computing a rate anyway would report a few basis points of noise as if it
    # were a currency view.
    if md.is_pegged(currency) and target == "USD":
        return CurrencySplit(currency=currency, target=target, local=local_irr,
                             with_fx=local_irr, fx=0.0, pegged=True)

    try:
        rate_start = md.fx_rate(currency, target, start)
        rate_end = md.fx_rate(currency, target, end)
    except Exception:  # noqa: BLE001
        return None
    if not rate_start or not rate_end or rate_start <= 0:
        return None

    fx = (rate_end / rate_start) ** (1.0 / years) - 1.0
    return CurrencySplit(
        currency=currency, target=target, local=local_irr,
        with_fx=(1.0 + local_irr) * (1.0 + fx) - 1.0, fx=fx,
    )


def currency_adjustment(stream: CashflowStream, local_irr: float | None,
                        md: MarketData, reporting_ccy: str = "USD") -> Adjustment | None:
    """Split a local-currency return from the currency movement on top of it.

    Every return in this system is computed in the asset's own currency, which
    isolates investment performance from exchange rates. The client reports in
    one currency though, so the translation effect has to be stated explicitly
    -- and where it is a pegged currency, stated as immaterial rather than
    reported as a spurious few basis points.
    """
    if local_irr is None or stream.currency.upper() == reporting_ccy.upper():
        return None

    flows = stream.ordered()
    if len(flows) < 2:
        return None
    start, end = flows[0].when, flows[-1].when
    years = (end - start).days / 365.0
    if years <= 0.25:
        return None

    if md.is_pegged(stream.currency):
        return Adjustment(
            kind="currency",
            headline=(
                f"{stream.name} is denominated in {stream.currency}, which is "
                f"pegged to the US dollar. Currency effect is immaterial."
            ),
            detail=(
                f"The {stream.currency} has been held at a fixed rate against the "
                "dollar by policy, so the local return and the USD return are "
                "effectively the same. No currency attribution is reported, "
                "because reporting one would imply a signal that the peg precludes."
            ),
            severity="info",
            values={"currency": stream.currency, "pegged": True},
        )

    try:
        rate_start = md.fx_rate(stream.currency, reporting_ccy, start)
        rate_end = md.fx_rate(stream.currency, reporting_ccy, end)
    except MarketDataError:
        return None
    if rate_start <= 0:
        return None

    fx_return = (rate_end / rate_start) ** (1.0 / years) - 1.0
    total = (1.0 + local_irr) * (1.0 + fx_return) - 1.0

    direction = "cost" if fx_return < 0 else "added"
    return Adjustment(
        kind="currency",
        headline=(
            f"{stream.name} returned {local_irr * 100:.1f}% in {stream.currency}, "
            f"but {total * 100:.1f}% in {reporting_ccy}. The currency "
            f"{direction} {abs(fx_return) * 100:.1f}% a year."
        ),
        detail=(
            f"{stream.currency}/{reporting_ccy} moved from {rate_start:.4f} to "
            f"{rate_end:.4f} over the {years:.1f}-year holding period. Benchmark "
            f"comparison is performed in {stream.currency} so that investment "
            "performance is judged on its own terms; this line states separately "
            "what the translation did to the investor."
        ),
        severity="material" if abs(fx_return) >= 0.02 else "info",
        values={"local_irr": local_irr, "fx_return": fx_return,
                "reporting_irr": total, "rate_start": rate_start,
                "rate_end": rate_end, "currency": stream.currency},
    )


def staleness_adjustment(stream: CashflowStream,
                         as_of: date | None = None) -> Adjustment | None:
    """Flag a carrying value old enough to distort the comparison."""
    if stream.valuation_date is None:
        return None
    as_of = as_of or date.today()
    age_days = (as_of - stream.valuation_date).days
    if age_days <= STALE_MARK_DAYS:
        return None

    return Adjustment(
        kind="staleness",
        headline=(
            f"{stream.name} was last valued on {stream.valuation_date}, "
            f"{age_days} days ago."
        ),
        detail=(
            "The benchmark is priced to the most recent trading day, so this "
            "comparison spans two different points in time. Appraisal-based "
            "valuations also lag and smooth market movements, which understates "
            "volatility and delays the recognition of both gains and losses."
        ),
        severity="material" if age_days > 270 else "warning",
        values={"valuation_date": stream.valuation_date.isoformat(),
                "age_days": age_days},
    )


def income_capital_split(stream: CashflowStream) -> Adjustment | None:
    """Separate rental yield from appraisal movement, as NCREIF reports it.

    Two properties returning 10% are different investments if one earns it from
    rent and the other from a valuer's opinion of what it might now sell for.
    Computed on the unlevered asset, because leverage changes the split.
    """
    if stream.asset_class is not AssetClass.DIRECT_REAL_ESTATE:
        return None
    if not stream.gross_cost or not stream.gross_valuation:
        return None

    flows = stream.ordered()
    years = (flows[-1].when - flows[0].when).days / 365.0
    if years <= 0:
        return None

    income_yield = (stream.annual_noi or 0.0) / stream.gross_cost
    capital_return = (stream.gross_valuation / stream.gross_cost) ** (1.0 / years) - 1.0

    return Adjustment(
        kind="income_capital",
        headline=(
            f"{stream.name}: {income_yield * 100:.1f}% a year from net rent, "
            f"{capital_return * 100:.1f}% a year from valuation movement."
        ),
        detail=(
            "Income is cash actually received; capital return is an appraiser's "
            "estimate that is only realised on sale. A return dominated by "
            "capital movement carries more uncertainty than the headline suggests."
        ),
        severity="info",
        values={"income_yield": income_yield, "capital_return": capital_return,
                "gross_cost": stream.gross_cost,
                "gross_valuation": stream.gross_valuation},
    )


def assess(stream: CashflowStream, metrics: dict, md: MarketData,
           reporting_ccy: str = "USD") -> list[Adjustment]:
    """Every applicable caveat for one holding, most serious first.

    ``currency_adjustment`` is deliberately absent. Every holding is now priced
    in the currency of the market it trades in and measured against a benchmark
    quoted in that same currency, so there is no translation inside the
    comparison for a caveat to warn about: a euro stock is judged against a
    euro index, a rupee stock against a rupee one. The function is kept, and
    still tested, because a report that rolls the book up into one reporting
    currency does move the total -- but that is a portfolio-level effect and
    putting it on every holding row implied a distortion that is not there.
    """
    irr = metrics.get("irr")
    found = [
        leverage_adjustment(stream, irr),
        j_curve_adjustment(stream, irr, metrics.get("dpi")),
        staleness_adjustment(stream),
        income_capital_split(stream),
    ]
    order = {"material": 0, "warning": 1, "info": 2}
    return sorted((a for a in found if a is not None),
                  key=lambda a: order.get(a.severity, 3))
