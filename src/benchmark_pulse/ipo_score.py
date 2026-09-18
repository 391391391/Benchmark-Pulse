"""Scoring an IPO the way a research associate would.

The division of labour is the same one used everywhere else in Benchmark Pulse,
and it is what makes the output defensible:

  the model READS       a prospectus is prose, and pulling offer terms, revenue
                        history and the nature of the proceeds out of it is a
                        reading task. Extracted values carry the sentence they
                        came from, so every figure can be checked against source.

  the engine COMPUTES   peer multiples, the implied valuation, every dimension
                        score and the composite are arithmetic over those
                        extracted facts. The model never scores anything, so a
                        scorecard cannot be talked into a better number.

Two things here are deliberately unlike a generic IPO screener:

  Use of proceeds is scored.  An offer that is wholly secondary returns nothing
  to the business -- existing holders are selling out and the company receives
  no capital. Screeners rarely surface it; an experienced analyst looks for it
  immediately.

  Portfolio fit is scored.  A high-quality IPO that doubles an existing
  concentration is not a good addition to *this* book. That judgement is only
  possible because the portfolio engine already knows what is held and how each
  sleeve has performed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ipo_sources import FilingRef, IPOListing, ProspectusSections
from .llm import LLMClient, get_client
from .marketdata import MarketData

#: Weights sum to 100. Valuation and use of proceeds carry the most because they
#: are the two questions that most often decide whether to participate.
DIMENSION_WEIGHTS = {
    "valuation": 20,
    "use_of_proceeds": 16,
    "growth": 14,
    "profitability": 14,
    "balance_sheet": 12,
    "market_position": 10,
    "governance": 8,
    "risk_density": 6,
}

VERDICT_BANDS = [
    (75, "Participate"),
    (60, "Participate at the lower end of the range"),
    (45, "Watch"),
    (0, "Pass"),
]


@dataclass
class Dimension:
    key: str
    label: str
    score: float           # 0-10
    weight: int
    comment: str
    evidence: str = ""     # quoted from the filing, or the computed inputs

    @property
    def contribution(self) -> float:
        return self.score / 10.0 * self.weight


@dataclass
class Comparable:
    ticker: str
    name: str
    market_cap: float | None
    ev_to_sales: float | None
    ev_to_ebitda: float | None
    price_to_book: float | None
    trailing_pe: float | None


@dataclass
class IPOScorecard:
    listing: IPOListing
    filing: FilingRef | None
    facts: dict
    dimensions: list[Dimension] = field(default_factory=list)
    comparables: list[Comparable] = field(default_factory=list)
    portfolio_note: str | None = None
    blocked_reason: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def composite(self) -> float | None:
        if self.blocked_reason or not self.dimensions:
            return None
        return round(sum(d.contribution for d in self.dimensions), 1)

    @property
    def verdict(self) -> str:
        if self.blocked_reason:
            return "Not scored"
        total = self.composite or 0
        return next(label for floor, label in VERDICT_BANDS if total >= floor)

    @property
    def confidence(self) -> str:
        """How much of the scorecard rests on facts actually found in the filing."""
        if self.blocked_reason:
            return "n/a"
        known = sum(1 for d in self.dimensions if d.evidence)
        ratio = known / len(self.dimensions) if self.dimensions else 0
        return "high" if ratio >= 0.75 else "medium" if ratio >= 0.5 else "low"


EXTRACTION_SYSTEM = """You are an equity research associate reading an IPO \
prospectus. Extract only what the text states. Never estimate, infer or fill a \
gap from general knowledge -- a missing value must come back as null, because a \
downstream engine scores these figures and a guessed number becomes a wrong \
recommendation.

For every figure you return, also return the phrase from the document that \
supports it, so a reviewer can verify it against source."""

EXTRACTION_SCHEMA = {
    "properties": {
        "shares_offered": {"type": "number"},
        "price_low": {"type": "number"},
        "price_high": {"type": "number"},
        "net_proceeds_usd": {"type": "number"},
        "proceeds_to_company_pct": {"type": "number"},
        "secondary_selling_shareholders": {"type": "boolean"},
        "use_of_proceeds_summary": {"type": "string"},
        "use_of_proceeds_evidence": {"type": "string"},
        "revenue_latest": {"type": "number"},
        "revenue_latest_period": {"type": "string"},
        "revenue_prior": {"type": "number"},
        "revenue_prior_period": {"type": "string"},
        "revenue_evidence": {"type": "string"},
        "net_income_latest": {"type": "number"},
        "is_profitable": {"type": "boolean"},
        "total_debt": {"type": "number"},
        "total_equity": {"type": "number"},
        "financials_evidence": {"type": "string"},
        "business_summary": {"type": "string"},
        "market_position": {"type": "string"},
        "lockup_days": {"type": "number"},
        "dual_class_shares": {"type": "boolean"},
        "governance_evidence": {"type": "string"},
        "top_risks": {"type": "array"},
        "comparable_tickers": {"type": "array"},
        "currency": {"type": "string"},
    }
}


def extract_facts(sections: ProspectusSections, client: LLMClient | None = None) -> dict:
    """Read the prospectus sections into structured facts."""
    client = client or get_client()
    if not client.is_live:
        return {"_stub": True}

    body = "\n\n".join(
        f"### {key.replace('_', ' ').upper()}\n{text}"
        for key, text in sections.sections.items()
    )
    prompt = (
        f"Company: {sections.company_name}\n\n"
        "Extract the facts below from these prospectus extracts. Amounts in the "
        "document's own currency; state which in `currency`.\n\n"
        "For revenue, state the exact period each figure covers in "
        "`revenue_latest_period` and `revenue_prior_period` (for example "
        "'FY2025' or 'six months ended 30 June 2026'). Prospectuses print "
        "interim and annual columns side by side, and a six-month figure read "
        "against a full year manufactures a fake collapse in revenue. If two "
        "comparable full-year columns are not available, return the two you "
        "used and label them honestly rather than forcing a pair.\n\n"
        "For `comparable_tickers`, list 5 to 8 LISTED companies that are "
        "genuine trading comparables, as exchange tickers.\n\n"
        f"{body[:24000]}"
    )
    try:
        return client.structured(prompt, EXTRACTION_SCHEMA,
                                 system=EXTRACTION_SYSTEM, max_tokens=3000)
    except Exception as exc:  # noqa: BLE001
        return {"_error": str(exc)}


def fetch_comparables(tickers: list[str], md: MarketData,
                      limit: int = 8) -> tuple[list[Comparable], list[str]]:
    """Resolve proposed peers to real multiples, discarding any that fail.

    The model proposes the peer set because knowing who competes with whom is
    knowledge; the multiples are pulled from market data because those are
    facts. A ticker that returns nothing is dropped and reported rather than
    carried through as a silent gap in the median.
    """
    import yfinance as yf

    found: list[Comparable] = []
    rejected: list[str] = []

    for raw in tickers[:limit * 2]:
        ticker = str(raw).strip().upper()
        if not ticker or len(found) >= limit:
            continue
        try:
            info = yf.Ticker(ticker).info or {}
            if not info.get("shortName") and not info.get("marketCap"):
                rejected.append(f"{ticker} (no data)")
                continue
            found.append(Comparable(
                ticker=ticker,
                name=info.get("shortName") or ticker,
                market_cap=info.get("marketCap"),
                ev_to_sales=info.get("enterpriseToRevenue"),
                ev_to_ebitda=info.get("enterpriseToEbitda"),
                price_to_book=info.get("priceToBook"),
                trailing_pe=info.get("trailingPE"),
            ))
        except Exception:  # noqa: BLE001
            rejected.append(f"{ticker} (lookup failed)")
    return found, rejected


#: Phrases that mark a partial-year reporting period. A prospectus prints
#: interim and annual columns beside each other, so reading one of each is an
#: easy mistake to make and an invisible one to spot in the output: it looks
#: exactly like a business whose revenue halved.
_INTERIM_MARKERS = (
    "six month", "6 month", "6m", "three month", "3 month", "3m",
    "nine month", "9 month", "9m", "quarter", "interim", "half year",
    "h1", "q1", "q2", "q3",
)


def _periods_comparable(latest: str, prior: str) -> tuple[bool, str]:
    """Are two revenue figures measured over the same length of time?

    Returns (comparable, explanation). Unlabelled periods are treated as
    comparable, because refusing to score every filing that omits a label would
    make the dimension useless -- but a clear interim/annual mismatch is caught.
    """
    a, b = latest.lower(), prior.lower()
    if not a or not b:
        return True, ""

    a_interim = any(m in a for m in _INTERIM_MARKERS)
    b_interim = any(m in b for m in _INTERIM_MARKERS)

    if a_interim != b_interim:
        which = "latest" if a_interim else "prior"
        return False, (f"{which} figure covers only part of a year while the "
                       "other covers a full one.")
    return True, ""


def _median(values: list[float | None]) -> float | None:
    clean = sorted(v for v in values if v is not None and v > 0)
    if not clean:
        return None
    mid = len(clean) // 2
    return clean[mid] if len(clean) % 2 else (clean[mid - 1] + clean[mid]) / 2


def _band(value: float, thresholds: list[tuple[float, float]]) -> float:
    """Map a value onto a 0-10 score using descending thresholds."""
    for floor, score in thresholds:
        if value >= floor:
            return score
    return 0.0


def score_ipo(
    listing: IPOListing,
    filing: FilingRef | None,
    sections: ProspectusSections | None,
    facts: dict,
    md: MarketData,
    portfolio=None,
) -> IPOScorecard:
    """Build the scorecard. Every dimension is computed, never asked for."""
    card = IPOScorecard(listing=listing, filing=filing, facts=facts)

    # -- the blank-cheque guard ---------------------------------------------
    # A SPAC has no revenue, no margins and no operating history. Scoring it on
    # those dimensions would produce a confident number about nothing, so it is
    # refused outright and the reason given. SIC 6770 is the SEC's own
    # classification, which is far more reliable than searching the text.
    if filing and filing.is_blank_check:
        card.blocked_reason = (
            "Blank-cheque company (SEC SIC 6770). There is no operating "
            "business to analyse: no revenue, no margins and no history. A "
            "quality scorecard would be meaningless here. Evaluate the sponsor's "
            "track record, the trust structure and the redemption terms instead."
        )
        return card

    if facts.get("_stub"):
        card.warnings.append(
            "No language model configured, so no facts were extracted from the "
            "prospectus. Offer terms below come from the exchange calendar only."
        )
        return card
    if facts.get("_error"):
        card.warnings.append(f"Extraction failed: {facts['_error']}")
        return card

    if sections and sections.missing:
        card.warnings.append(
            "Prospectus sections not located: "
            + ", ".join(s.replace("_", " ") for s in sections.missing)
        )

    # -- comparables and implied valuation ----------------------------------
    comps, rejected = fetch_comparables(
        [str(t) for t in (facts.get("comparable_tickers") or [])], md)
    card.comparables = comps
    if rejected:
        card.warnings.append("Comparables discarded: " + ", ".join(rejected))

    peer_ev_sales = _median([c.ev_to_sales for c in comps])
    revenue = facts.get("revenue_latest")
    shares = facts.get("shares_offered") or listing.shares_offered
    price = facts.get("price_high") or facts.get("price_low") or listing.price_high

    implied_ev_sales = None
    if revenue and shares and price and revenue > 0:
        # Deal-implied sales multiple on the offered slice. A rough proxy -- the
        # full market capitalisation needs the post-offering share count, which
        # the summary sections rarely state -- so it is reported as indicative.
        implied_ev_sales = (shares * price) / revenue

    # -- dimensions ----------------------------------------------------------
    dims: list[Dimension] = []

    if implied_ev_sales and peer_ev_sales:
        premium = implied_ev_sales / peer_ev_sales - 1
        score = _band(-premium, [(0.10, 9), (0.0, 7), (-0.15, 5), (-0.35, 3)])
        comment = (
            f"Implied {implied_ev_sales:.1f}x sales against a peer median of "
            f"{peer_ev_sales:.1f}x, a {premium:+.0%} "
            f"{'premium' if premium > 0 else 'discount'}."
        )
        evidence = f"{len(comps)} comparables: " + ", ".join(c.ticker for c in comps)
    else:
        gaps = []
        if not revenue:
            gaps.append("no revenue figure in the extracted sections")
        if not price:
            gaps.append("no offer price (the exchange calendar lists it as "
                        "null and the range is not in the summary)")
        if not shares:
            gaps.append("no share count")
        if not peer_ev_sales:
            gaps.append("no usable peer multiples")

        peer_line = (
            f"Peer median EV/Sales is {peer_ev_sales:.1f}x across "
            f"{len(comps)} comparables ("
            + ", ".join(c.ticker for c in comps) + "), but the deal cannot be "
            "priced against it: "
        ) if peer_ev_sales else "Cannot price the deal against peers: "

        score = 5.0
        comment = (peer_line + "; ".join(gaps)
                   + ". Scored neutral so the gap neither flatters nor "
                     "penalises the deal.")
        evidence = ", ".join(c.ticker for c in comps) if comps else ""
    dims.append(Dimension("valuation", "Valuation vs. listed comps", score,
                          DIMENSION_WEIGHTS["valuation"], comment, evidence))

    # Use of proceeds -- the dimension a screener would skip.
    to_company = facts.get("proceeds_to_company_pct")
    secondary = facts.get("secondary_selling_shareholders")
    if to_company is not None:
        pct = to_company if to_company > 1 else to_company * 100
        score = _band(pct, [(90, 10), (70, 8), (50, 6), (25, 3)])
        comment = (
            f"{pct:.0f}% of proceeds go to the company."
            + ("" if pct >= 70 else
               " A large secondary component means existing holders are selling "
               "rather than the business being funded.")
        )
    elif secondary is True:
        score, comment = 4.0, (
            "Selling shareholders are participating, but the split between "
            "primary and secondary is not stated in the extracted sections."
        )
    else:
        score, comment = 6.0, "Proceeds appear to be primary; split not stated."
    dims.append(Dimension(
        "use_of_proceeds", "Use of proceeds", score,
        DIMENSION_WEIGHTS["use_of_proceeds"], comment,
        str(facts.get("use_of_proceeds_evidence") or "")[:400]))

    # Growth. Only computed across periods of the same length -- see
    # _periods_comparable for why this guard exists.
    latest, prior = facts.get("revenue_latest"), facts.get("revenue_prior")
    p_latest = str(facts.get("revenue_latest_period") or "")
    p_prior = str(facts.get("revenue_prior_period") or "")
    comparable, mismatch = _periods_comparable(p_latest, p_prior)

    if latest and prior and prior > 0 and comparable:
        growth = latest / prior - 1
        score = _band(growth, [(0.40, 10), (0.20, 8), (0.10, 6), (0.0, 4)])
        comment = (f"Revenue {growth:+.0%}"
                   + (f" ({p_latest} vs {p_prior})." if p_latest else " year on year."))
    elif latest and prior and not comparable:
        score = 5.0
        comment = (
            f"Revenue not scored: the two figures cover different periods "
            f"({p_latest or 'unlabelled'} vs {p_prior or 'unlabelled'}), so the "
            f"{mismatch} Comparing them would report a decline that is an "
            "artefact of the reporting period, not the business."
        )
    else:
        score, comment = 5.0, "Revenue history not found in the extracted sections."
    dims.append(Dimension("growth", "Growth quality", score,
                          DIMENSION_WEIGHTS["growth"], comment,
                          str(facts.get("revenue_evidence") or "")[:400]))

    # Profitability.
    profitable = facts.get("is_profitable")
    net_income = facts.get("net_income_latest")
    if profitable is True:
        margin = (net_income / latest) if (net_income and latest) else None
        # An implausible margin means the two figures came from different
        # statements -- a segment result over group revenue, or a pro-forma line
        # over a reported one. Reporting it as fact would put an absurd number
        # in front of a client, so it is withheld and flagged for checking.
        if margin is not None and not (-2.0 <= margin <= 0.40):
            card.warnings.append(
                f"Net margin extracted as {margin:.0%}, which is outside the "
                "plausible range. The income and revenue figures probably come "
                "from different statements or periods; margin not scored."
            )
            margin = None
        score = 8.0 if margin is None else _band(margin, [(0.15, 10), (0.08, 8), (0.02, 6)])
        comment = ("Profitable"
                   + (f", net margin {margin:.1%}." if margin is not None
                      else "; margin not reliably extractable."))
    elif profitable is False:
        score, comment = 3.0, "Loss-making at the latest reported period."
    else:
        score, comment = 5.0, "Profitability not stated in the extracted sections."
    dims.append(Dimension("profitability", "Profitability", score,
                          DIMENSION_WEIGHTS["profitability"], comment,
                          str(facts.get("financials_evidence") or "")[:400]))

    # Balance sheet.
    debt, equity = facts.get("total_debt"), facts.get("total_equity")
    if debt is not None and equity and equity > 0:
        gearing = debt / equity
        score = _band(-gearing, [(-0.5, 10), (-1.0, 8), (-2.0, 6), (-3.0, 4)])
        comment = f"Debt to equity {gearing:.1f}x."
    else:
        score, comment = 5.0, "Capital structure not found in the extracted sections."
    dims.append(Dimension("balance_sheet", "Balance sheet", score,
                          DIMENSION_WEIGHTS["balance_sheet"], comment, ""))

    # Market position -- qualitative, so it is scored conservatively.
    position = str(facts.get("market_position") or "").strip()
    dims.append(Dimension(
        "market_position", "Market position", 6.0 if position else 5.0,
        DIMENSION_WEIGHTS["market_position"],
        position[:220] or "Competitive position not described in the extracts.",
        position[:400]))

    # Governance.
    lockup = facts.get("lockup_days")
    dual = facts.get("dual_class_shares")
    score = 6.0
    notes = []
    if lockup:
        notes.append(f"{int(lockup)}-day lockup")
        score += 1.5 if lockup >= 180 else -1.0
    if dual is True:
        notes.append("dual-class share structure concentrates voting control")
        score -= 2.0
    dims.append(Dimension(
        "governance", "Governance and lockup", max(0.0, min(10.0, score)),
        DIMENSION_WEIGHTS["governance"],
        "; ".join(notes) or "Lockup and share structure not stated.",
        str(facts.get("governance_evidence") or "")[:400]))

    # Risk density.
    risks = [str(r) for r in (facts.get("top_risks") or [])]
    score = _band(-len(risks), [(-3, 8), (-6, 6), (-10, 4)]) if risks else 5.0
    dims.append(Dimension(
        "risk_density", "Risk density", score, DIMENSION_WEIGHTS["risk_density"],
        (f"{len(risks)} principal risks identified: " + "; ".join(risks[:3]))
        if risks else "Risk factors not extracted.",
        "; ".join(risks[:6])[:400]))

    card.dimensions = dims

    if portfolio is not None:
        card.portfolio_note = _portfolio_fit(facts, listing, portfolio)

    return card


def _portfolio_fit(facts: dict, listing: IPOListing, portfolio) -> str:
    """How this deal sits against what the client already owns.

    The differentiator. A generic screener scores an IPO in isolation; this one
    can say that the sleeve it belongs to is already large and already lagging.
    """
    country = "SA" if listing.market == "SA" else "US"
    same_country = [h for h in portfolio.scored if h.stream.country == country]
    if not same_country:
        return (f"No existing {country} holdings, so this would add new "
                "geographic exposure.")

    total = sum(h.contributions_base for h in portfolio.scored) or 1.0
    share = sum(h.contributions_base for h in same_country) / total
    weighted = sum(h.direct_alpha * h.contributions_base for h in same_country)
    denominator = sum(h.contributions_base for h in same_country) or 1.0
    sleeve_alpha = weighted / denominator

    verdict = ("adds correlation to a sleeve that is already underperforming"
               if sleeve_alpha < 0 else
               "adds to a sleeve that is currently outperforming")
    return (
        f"You already hold {len(same_country)} {country} position"
        f"{'s' if len(same_country) != 1 else ''}, "
        f"{share:.0%} of committed capital, with a contribution-weighted Direct "
        f"Alpha of {sleeve_alpha * 100:+.1f}%. On that basis this {verdict}."
    )
