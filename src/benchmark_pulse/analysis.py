"""Orchestration: a workbook in, a two-level benchmarked portfolio out.

Level 1, the portfolio, is measured against the benchmark implied by its
mandate. Level 2, each holding, is measured against its own sector. The two
answer different questions and routinely disagree, which is the point: a book
can beat its mandate while most of its holdings lose to their sectors, because
the allocation did the work rather than the stock picking.

One code path serves the CLI, the app and the memo writer, so what an analyst
approves on screen is provably the computation that reaches the client. A second
implementation for the UI is how a dashboard and a document end up disagreeing
about the same portfolio.

Failures are collected per holding rather than raised. One security with a bad
symbol must not cost the client the other twenty-three.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from .adjustments import Adjustment, CurrencySplit, assess, currency_split
from .aggregate import (
    build_portfolio_stream, contribution_weights, current_values,
)
from .benchmarks import (
    UNIVERSE, BenchmarkDecision, assign_holding_benchmark,
    assign_mandate_benchmark, classify_exposure, load_decisions, save_decisions,
)
from .cashflows import CashflowStream
from .directalpha import PMEResult, compute_pme, league_table
from .llm import LLMClient, get_client
from .marketdata import MarketData
from .metrics import summarise
from .portfolio import LoadReport, load_workbook


@dataclass
class Holding:
    """Everything known about one holding after the full pipeline."""

    stream: CashflowStream
    metrics: dict
    decision: BenchmarkDecision
    pme: PMEResult | None = None
    adjustments: list[Adjustment] = field(default_factory=list)
    error: str | None = None

    #: Set only where the holding and its benchmark are quoted in different
    #: currencies. Carries the same return measured with and without the
    #: exchange rate, so neither has to stand in for the other.
    fx: CurrencySplit | None = None

    #: Capital committed and current value, both in the reporting currency.
    contributions_base: float = 0.0
    value_base: float = 0.0

    @property
    def asset_id(self) -> str:
        return self.stream.asset_id

    @property
    def name(self) -> str:
        return self.stream.name

    @property
    def sector(self) -> str:
        return self.stream.sector or "Unclassified"

    @property
    def direct_alpha(self) -> float | None:
        return self.pme.direct_alpha if self.pme else None

    @property
    def material_caveats(self) -> list[Adjustment]:
        return [a for a in self.adjustments if a.severity == "material"]

    @property
    def country(self) -> str:
        return self.stream.country or "Unknown"

    @property
    def total_return(self) -> float | None:
        return self.stream.total_return

    @property
    def beats_benchmark(self) -> bool | None:
        alpha = self.direct_alpha
        return None if alpha is None else alpha > 0


@dataclass
class PortfolioLevel:
    """The book measured as a single investment against its mandate."""

    stream: CashflowStream
    metrics: dict
    decision: BenchmarkDecision
    pme: PMEResult | None
    exposure: dict
    notes: list[str] = field(default_factory=list)

    @property
    def direct_alpha(self) -> float | None:
        return self.pme.direct_alpha if self.pme else None

    @property
    def irr(self) -> float | None:
        return self.metrics.get("irr")

    @property
    def benchmark_irr(self) -> float | None:
        return self.pme.benchmark_irr if self.pme else None

    @property
    def beat_mandate(self) -> bool | None:
        alpha = self.direct_alpha
        return None if alpha is None else alpha > 0


@dataclass
class PortfolioAnalysis:
    holdings: list[Holding]
    portfolio: PortfolioLevel | None
    load_report: LoadReport
    reporting_currency: str
    as_of: date
    provider: str
    benchmark_note: str = ""

    #: Where every series came from.
    provenance: dict = field(default_factory=dict)
    source_notes: list[str] = field(default_factory=list)

    @property
    def table(self) -> pd.DataFrame:
        return league_table([h.pme for h in self.holdings if h.pme])

    @property
    def scored(self) -> list[Holding]:
        return [h for h in self.holdings if h.direct_alpha is not None]

    @property
    def outperformers(self) -> list[Holding]:
        return [h for h in self.scored if h.direct_alpha > 0]

    @property
    def underperformers(self) -> list[Holding]:
        return sorted((h for h in self.scored if h.direct_alpha <= 0),
                      key=lambda h: h.direct_alpha)

    @property
    def total_capital(self) -> float:
        return sum(h.contributions_base for h in self.holdings)

    @property
    def total_value(self) -> float:
        return sum(h.value_base for h in self.holdings)

    def by_id(self, asset_id: str) -> Holding | None:
        return next((h for h in self.holdings if h.asset_id == asset_id), None)

    def weight_of(self, holding: Holding) -> float:
        """Share of the book by current market value, in reporting currency."""
        total = self.total_value
        return holding.value_base / total if total else 0.0

    @property
    def sectors(self) -> list[str]:
        return sorted({h.sector for h in self.holdings})

    @property
    def countries(self) -> list[str]:
        return sorted({h.country for h in self.holdings})

    def filtered(self, sectors=None, countries=None,
                 search: str = "") -> list[Holding]:
        """Holdings narrowed by sector, market and a free-text match.

        An empty selection means "no filter" rather than "nothing", which is
        what a reader expects from an untouched filter control.
        """
        out = self.holdings
        if sectors:
            out = [h for h in out if h.sector in sectors]
        if countries:
            out = [h for h in out if h.country in countries]
        if search:
            needle = search.strip().lower()
            out = [h for h in out
                   if needle in h.name.lower() or needle in h.asset_id.lower()]
        return out

    def _grouped(self, key) -> pd.DataFrame:
        """Capital-weighted alpha by an arbitrary grouping.

        Weighted by capital committed, because an equal-weighted average would
        let a token position outvote the largest holding in the book.
        """
        rows = [{
            "group": key(h),
            "alpha": h.direct_alpha,
            "weight": h.contributions_base,
            "value": h.value_base,
            "beat": h.direct_alpha > 0,
        } for h in self.scored]
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        out = df.groupby("group").apply(
            lambda g: pd.Series({
                "holdings": len(g),
                "beat_benchmark": int(g["beat"].sum()),
                "capital": g["weight"].sum(),
                "value": g["value"].sum(),
                "weighted_alpha": (
                    (g["alpha"] * g["weight"]).sum() / g["weight"].sum()
                    if g["weight"].sum() > 0 else g["alpha"].mean()
                ),
            }),
            include_groups=False,
        ).reset_index()
        return out.sort_values("weighted_alpha", ascending=False)

    def sector_summary(self) -> pd.DataFrame:
        return self._grouped(lambda h: h.sector)

    def country_summary(self) -> pd.DataFrame:
        return self._grouped(lambda h: h.stream.country or "Unknown")


def _tickers_in(workbook: Path | str) -> list[str]:
    """Read just the ticker column, so the batch fetch can run before loading.

    Deliberately tolerant: this is an optimisation, and a workbook it cannot
    skim should still be analysed by the normal per-ticker path.
    """
    try:
        frame = pd.read_excel(workbook, sheet_name=0, header=None)
    except Exception:  # noqa: BLE001
        return []

    from .portfolio import _norm, normalise_ticker

    found: list[str] = []
    for column in frame.columns:
        header_hits = frame[column].astype(str).map(_norm)
        if not header_hits.isin({"ticker", "symbol", "code"}).any():
            continue
        start = header_hits[header_hits.isin({"ticker", "symbol", "code"})].index[0]
        for value in frame[column].iloc[start + 1:]:
            if pd.isna(value):
                continue
            ticker = normalise_ticker(value)
            if ticker:
                found.append(ticker)
        break
    return found


def analyse(
    workbook: Path | str,
    *,
    md: MarketData | None = None,
    client: LLMClient | None = None,
    reporting_currency: str = "USD",
    portfolio_name: str = "Portfolio",
    reuse_decisions: bool = True,
) -> PortfolioAnalysis:
    """Run both levels of benchmarking on one client workbook."""
    md = md or MarketData()
    client = client or get_client()

    # Pull every series the report could need in one batched request before
    # anything is computed. The benchmark universe is small and fixed, so
    # fetching all of it costs no more than fetching the few that end up used,
    # and it turns a live refresh from a minute of sequential calls into one
    # request of about five seconds.
    try:
        md.prefetch(_tickers_in(workbook) + list(UNIVERSE))
    except Exception:  # noqa: BLE001 - the per-ticker path still works
        pass

    report = load_workbook(workbook, md)
    saved = load_decisions() if reuse_decisions else {}

    weights = contribution_weights(report.streams, md, reporting_currency)
    values = current_values(report.streams, md, reporting_currency)

    # -- level 2: each holding against its sector --------------------------
    holdings: list[Holding] = []
    for stream in report.streams:
        # Reuse any decision on file. An approved one is authoritative; an
        # unapproved model decision is reused too, because re-deriving it would
        # spend a free-tier API call per holding on every run, and a benchmark
        # whose wording changed between the rehearsal and the demo is worse
        # than one that is merely provisional. Rules fallbacks carry no
        # reasoning, so those are worth retrying.
        decision = saved.get(stream.asset_id)
        if decision is None or (decision.source == "rules" and client.is_live):
            decision = assign_holding_benchmark(stream, client)
            saved[stream.asset_id] = decision

        holding = Holding(
            stream=stream, metrics=summarise(stream), decision=decision,
            contributions_base=weights.get(stream.asset_id, 0.0),
            value_base=values.get(stream.asset_id, 0.0),
        )

        try:
            prices = md.prices(decision.benchmark_ticker)
            holding.pme = compute_pme(stream, prices, decision.benchmark_ticker)
        except Exception as exc:  # noqa: BLE001 - record and carry on
            holding.error = f"could not benchmark: {exc}"

        try:
            holding.adjustments = assess(stream, holding.metrics, md,
                                         reporting_currency)
        except Exception as exc:  # noqa: BLE001
            report.warnings.append(f"{stream.name}: adjustments failed: {exc}")

        # Split against the BENCHMARK's currency, not the report's. The
        # comparison on screen is holding against benchmark, so that is the
        # translation a reader needs explained; the reporting currency only
        # matters when the book is added up.
        try:
            bmk = UNIVERSE.get(decision.benchmark_ticker)
            holding.fx = currency_split(stream, holding.metrics.get("irr"), md,
                                        bmk.currency if bmk else "")
        except Exception:  # noqa: BLE001 - never let a rate break a report
            holding.fx = None

        holdings.append(holding)

    # -- level 1: the whole book against its mandate -----------------------
    portfolio: PortfolioLevel | None = None
    if report.streams:
        combined, notes = build_portfolio_stream(
            report.streams, md, reporting_currency, portfolio_name)
        exposure = classify_exposure(report.streams, weights)

        mandate = saved.get("PORTFOLIO")
        if mandate is None or (mandate.source == "rules" and client.is_live):
            mandate = assign_mandate_benchmark(
                report.streams, exposure, portfolio_name, client)
            saved["PORTFOLIO"] = mandate

        pme = None
        try:
            prices = md.prices(mandate.benchmark_ticker)
            pme = compute_pme(combined, prices, mandate.benchmark_ticker)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"could not benchmark the portfolio: {exc}")

        portfolio = PortfolioLevel(
            stream=combined, metrics=summarise(combined), decision=mandate,
            pme=pme, exposure=exposure, notes=notes,
        )

    if reuse_decisions:
        save_decisions(saved)

    used = {h.decision.benchmark_ticker for h in holdings}
    if portfolio:
        used.add(portfolio.decision.benchmark_ticker)
    caveats = [UNIVERSE[t].caveat for t in used
               if t in UNIVERSE and UNIVERSE[t].caveat]

    # Cross-source comparison is available through MarketData.check_fx_sources
    # if a figure is ever questioned, but it is not run here. Every analysis
    # paid for it, and a report should state where its data came from rather
    # than carry an apparatus for defending it.
    return PortfolioAnalysis(
        holdings=holdings,
        portfolio=portfolio,
        load_report=report,
        reporting_currency=reporting_currency,
        as_of=date.today(),
        provider=f"{client.provider}/{client.model}",
        benchmark_note=" ".join(dict.fromkeys(caveats)),
        provenance=dict(md.provenance),
        source_notes=list(md.notes),
    )
