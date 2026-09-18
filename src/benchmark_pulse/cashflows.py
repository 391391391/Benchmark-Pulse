"""Unified cashflow model.

The central idea of Benchmark Pulse: a listed share, an office tower and a buyout
fund look nothing alike on a statement, but all three reduce to the same shape --

    money out  ->  money in  ->  value today

Once every asset is expressed that way, the same return maths applies to all of
them, and they become directly comparable. Everything downstream (metrics.py,
directalpha.py) consumes only the ``CashflowStream`` produced here.

Sign convention, applied everywhere without exception:
    negative = money leaving the investor (purchase, capital call, capex)
    positive = money returning to the investor (dividend, distribution, rent, sale)

The terminal value (today's price x quantity, current NAV, latest appraisal) is
appended as a positive flow, because the standard IRR treatment is to assume the
position is liquidated at its carrying value on the valuation date.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class AssetClass(str, Enum):
    """Kept deliberately coarse -- it drives benchmark choice, not reporting."""

    PUBLIC_EQUITY = "public_equity"
    FIXED_INCOME = "fixed_income"
    PRIVATE_FUND = "private_fund"      # PE / VC / infra / private credit
    DIRECT_REAL_ESTATE = "direct_real_estate"
    CASH = "cash"


class FlowType(str, Enum):
    PURCHASE = "purchase"
    CAPITAL_CALL = "capital_call"
    CAPEX = "capex"
    DIVIDEND = "dividend"
    DISTRIBUTION = "distribution"
    RENTAL_INCOME = "rental_income"
    SALE = "sale"
    TERMINAL_VALUE = "terminal_value"


#: Flows that represent the investor putting money in. Used by the metrics layer
#: to split contributions from distributions (TVPI/DPI need that distinction, and
#: getting it from the sign alone would silently misclassify a negative dividend).
CONTRIBUTION_TYPES = frozenset(
    {FlowType.PURCHASE, FlowType.CAPITAL_CALL, FlowType.CAPEX}
)


@dataclass(frozen=True)
class Cashflow:
    when: date
    amount: float          # signed, in the asset's own currency
    kind: FlowType

    def __post_init__(self) -> None:
        if self.kind in CONTRIBUTION_TYPES and self.amount > 0:
            raise ValueError(
                f"{self.kind.value} must be negative (money out), got {self.amount}"
            )
        if self.kind is FlowType.TERMINAL_VALUE and self.amount < 0:
            raise ValueError(
                f"terminal_value must be >= 0, got {self.amount}. A negative "
                "carrying value is not meaningful; use 0 for a write-off."
            )


@dataclass
class CashflowStream:
    """Every asset in the portfolio, in one shape.

    ``currency`` is the asset's local currency. Conversion to the reporting
    currency happens in the FX layer, not here -- keeping flows in local terms is
    what lets us decompose return into asset performance vs. currency movement.
    """

    asset_id: str
    name: str
    asset_class: AssetClass
    currency: str
    country: str                        # ISO-2: US, IN, SA
    flows: list[Cashflow] = field(default_factory=list)
    valuation_date: date | None = None   # when the terminal value was struck

    # Optional context used by the fairness adjustments (adjustments.py).
    leverage_ratio: float | None = None  # debt / gross asset value, for RE
    debt_rate: float | None = None       # interest rate on that debt
    vintage_year: int | None = None      # for private funds, drives J-curve logic
    sector: str | None = None

    # Position detail, kept for reporting rather than for the return maths.
    # The cashflows alone are enough to compute a return, but an analyst reading
    # a holdings list wants to see the shares held and what they cost.
    quantity: float | None = None
    cost_per_unit: float | None = None
    last_price: float | None = None

    # Gross (whole-asset) figures for real estate, retained because the flows
    # above are the investor's levered equity position and the income/capital
    # split has to be computed on the unlevered asset.
    gross_cost: float | None = None
    gross_valuation: float | None = None
    annual_noi: float | None = None

    @property
    def total_return(self) -> float | None:
        """Simple return over the whole holding period, not annualised.

        Reported alongside IRR because the two answer different questions: this
        is what the position actually made, the IRR is the rate it made it at.
        """
        paid_in = self.contributions
        if paid_in <= 0:
            return None
        return (self.distributions + self.terminal_value) / paid_in - 1.0

    def add(self, when: date, amount: float, kind: FlowType) -> None:
        self.flows.append(Cashflow(when=when, amount=amount, kind=kind))

    def set_terminal_value(self, when: date, value: float) -> None:
        """Set today's carrying value. Replaces any previous terminal value."""
        self.flows = [f for f in self.flows if f.kind is not FlowType.TERMINAL_VALUE]
        self.flows.append(
            Cashflow(when=when, amount=value, kind=FlowType.TERMINAL_VALUE)
        )
        self.valuation_date = when

    def ordered(self) -> list[Cashflow]:
        return sorted(self.flows, key=lambda f: f.when)

    @property
    def contributions(self) -> float:
        """Total paid in, as a positive number."""
        return -sum(f.amount for f in self.flows if f.kind in CONTRIBUTION_TYPES)

    @property
    def distributions(self) -> float:
        """Total returned, excluding the unrealised terminal value."""
        return sum(
            f.amount
            for f in self.flows
            if f.amount > 0 and f.kind is not FlowType.TERMINAL_VALUE
        )

    @property
    def terminal_value(self) -> float:
        for f in self.flows:
            if f.kind is FlowType.TERMINAL_VALUE:
                return f.amount
        return 0.0

    @property
    def is_stale(self) -> bool:
        """True when the carrying value is old enough to distort a comparison.

        A listed share is priced continuously; a building is appraised once or
        twice a year. Comparing a 6-month-old appraisal against a live index is
        not wrong, but the reader must be told -- see adjustments.staleness_flag.
        """
        if self.valuation_date is None:
            return True
        return (date.today() - self.valuation_date).days > 90

    def validate(self) -> list[str]:
        """Return human-readable problems. Empty list means the stream is sound.

        Deliberately returns warnings rather than raising: a portfolio with one
        bad row should still produce a report for the other twenty-nine.
        """
        problems: list[str] = []
        if not self.flows:
            problems.append(f"{self.name}: no cashflows")
            return problems
        if self.contributions <= 0:
            problems.append(f"{self.name}: no money was ever invested")
        if self.terminal_value == 0 and self.distributions == 0:
            problems.append(f"{self.name}: no value returned and nothing held")
        if self.valuation_date is None:
            problems.append(f"{self.name}: no valuation date on the terminal value")
        return problems
