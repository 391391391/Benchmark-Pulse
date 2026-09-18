"""Choosing the right yardstick, at two levels -- and defending the choice.

A portfolio is judged twice, and the two questions are different:

  Portfolio level   Did the book beat the market it was hired to beat? That is
                    set by the mandate -- a developed-markets fund is measured
                    against developed markets, an emerging-markets fund against
                    emerging markets. Measuring an EM book against the S&P
                    would report skill that is really just an asset-class bet.

  Holding level     Did each company beat its own peers? Peers means the same
                    sector IN THE SAME MARKET. ICICI Bank is measured against
                    the Nifty Bank index, not a global financials index: a
                    global comparator folds the India-versus-world trade into
                    what is meant to be a judgement on one bank.

The holding-level rule, stated once because it is the one most often got wrong:

    An Indian company is measured against an Indian benchmark. A US company
    against a US benchmark. Only the PORTFOLIO is measured globally, because
    only the portfolio actually made a country-allocation decision.

A holding never chose its country -- the allocator did. Charging a stock for its
market's performance measures the allocator twice and the stock not at all. The
country bet belongs at level one, where someone is accountable for it.

This is enforced structurally rather than by instruction: ``_holding_candidates``
filters the universe to the holding's own market before the model ever sees it,
so a global index cannot be chosen for ICICI Bank even if the model wants to.

Both levels use the same Direct Alpha engine; only the comparator changes.
Selection is treated as what it actually is -- an analyst's judgement -- so the
model proposes from a verified universe, states a confidence, writes its
reasoning, and an analyst approves or overrides once.

Every ticker below returned usable history when probed on 15 Sep 2026, with at
least five years of daily closes. The universe is closed on purpose: the model
chooses from tickers already proven to carry data, so it cannot invent a
plausible index that does not exist.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from .cashflows import CashflowStream
from .llm import LLMClient, get_client
from .paths import DECISIONS_PATH


class Level(str, Enum):
    MANDATE = "mandate"      # portfolio level
    SECTOR = "sector"        # holding level, preferred
    MARKET = "market"        # holding level, fallback when sector is unknown


@dataclass(frozen=True)
class Benchmark:
    """A comparator, named by the index it actually represents.

    Two fields carry the credibility of a benchmark, and they are different
    things: ``index`` is the published index a performance figure is compared
    against, and ``vehicle`` is how its level is obtained.

    Where an index is published only on a price basis -- which is true of every
    free sector index -- the comparator is read from the fund that tracks it,
    because an adjusted fund price includes reinvested dividends and the raw
    index does not. Holdings are measured on a total-return basis, so comparing
    them to a price index would credit the investor with the index's dividend
    yield: worth 1.9 percentage points a year on the S&P 500, measured. Naming
    the index and stating the vehicle is more honest than either quietly using
    a fund or knowingly using the wrong basis.
    """

    ticker: str
    name: str
    level: Level
    scope: str               # region for mandates, sector for sector indices
    currency: str
    total_return: bool
    index: str = ""          # the published index being represented
    provider: str = ""       # who publishes that index
    vehicle: str = "index"   # "index" when read directly, else the fund used
    caveat: str = ""
    #: The listing market this benchmark belongs to, as an ISO country code.
    #: Empty means global. At holding level this is a hard filter: a holding is
    #: only ever offered benchmarks from its own market.
    country: str = ""

    @property
    def basis(self) -> str:
        return "total return" if self.total_return else "price return"

    @property
    def attribution(self) -> str:
        """One line naming the index, its publisher and how it was obtained."""
        parts = [self.index or self.name]
        if self.provider:
            parts.append(f"({self.provider})")
        parts.append(f"- {self.basis}")
        if self.vehicle != "index":
            parts.append(f", read from {self.vehicle}")
        return " ".join(parts)


# -- Mandate benchmarks (portfolio level) -----------------------------------

#: The mandate a portfolio is measured against, one per portfolio type. The
#: type is the question -- "what kind of book is this?" -- and the benchmark is
#: the answer. Each entry names the index it represents, its publisher, and how
#: its level is obtained, because three of the seven have no exact free tracker
#: and a substitution stated is worth more than a substitution hidden.
MANDATES: dict[str, Benchmark] = {b.ticker: b for b in [
    Benchmark("ACWI", "Global Equity - MSCI ACWI", Level.MANDATE,
              "Global Equity", "USD", True,
              index="MSCI All Country World Index", provider="MSCI",
              vehicle="iShares MSCI ACWI ETF"),
    Benchmark("^SP500TR", "US Focused - S&P 500", Level.MANDATE,
              "US Focused", "USD", True,
              index="S&P 500 Total Return Index",
              provider="S&P Dow Jones Indices", vehicle="index"),
    Benchmark("KSA", "GCC Equity - S&P GCC Composite", Level.MANDATE,
              "GCC Equity", "USD", True,
              index="MSCI Saudi Arabia IMI 25/50 Index", provider="MSCI",
              vehicle="iShares MSCI Saudi Arabia ETF",
              caveat="No free tracker follows the S&P GCC Composite: the two "
                     "that did, GULF and MES, are both delisted. Saudi Arabia "
                     "is the dominant GCC constituent, so this stands in for "
                     "the region and understates Kuwait, Qatar and the UAE. "
                     "USD-denominated, but the riyal peg makes the currency "
                     "effect immaterial."),
    Benchmark("^CRSLDX", "India Equity - Nifty 500", Level.MANDATE,
              "India Equity", "INR", False,
              index="Nifty 500 Index", provider="NSE Indices", vehicle="index",
              caveat="NSE publishes the Nifty 500 price-only, and no ETF "
                     "tracking it carries usable history. It therefore "
                     "excludes dividends and understates the benchmark by "
                     "roughly the index yield, which flatters the portfolio. "
                     "Use the Nifty 50 total-return line instead where a "
                     "total-return comparison matters more than breadth."),
    Benchmark("NIFTYBEES.NS", "India Equity - Nifty 50 (total return)",
              Level.MANDATE, "India Equity", "INR", True,
              index="Nifty 50 Index", provider="NSE Indices",
              vehicle="Nippon India ETF Nifty 50 BeES",
              caveat="Narrower than the Nifty 500 -- 50 names rather than 500 "
                     "-- but read on a total-return basis."),
    Benchmark("REET", "REIT Portfolio - FTSE EPRA/NAREIT", Level.MANDATE,
              "REIT Portfolio", "USD", True,
              index="FTSE EPRA Nareit Global REITS Index",
              provider="FTSE Russell", vehicle="iShares Global REIT ETF"),
    Benchmark("IXG", "Banking Portfolio - MSCI Banks", Level.MANDATE,
              "Banking Portfolio", "USD", True,
              index="S&P Global 1200 Financials Index",
              provider="S&P Dow Jones Indices",
              vehicle="iShares Global Financials ETF",
              caveat="No free tracker follows an MSCI Banks index. Global "
                     "financials is the closest available and is broader than "
                     "banking: it includes insurers, asset managers and "
                     "exchanges."),
    Benchmark("QQQ", "Technology Portfolio - Nasdaq 100", Level.MANDATE,
              "Technology Portfolio", "USD", True,
              index="Nasdaq-100 Index", provider="Nasdaq",
              vehicle="Invesco QQQ Trust",
              caveat="The Nasdaq-100 is a large-cap non-financial index rather "
                     "than a pure technology one; it is US-only and carries "
                     "consumer and communications weight."),
    # Frontier is deliberately excluded. The only free frontier tracker (FM)
    # stopped updating in January 2025, and a benchmark frozen twenty months ago
    # manufactures alpha out of nothing.
]}


# -- Sector benchmarks (holding level) --------------------------------------
# Organised by listing market, because that is how they are selected: a holding
# is offered its own market's sector indices and nothing else.
#
# Where a market publishes no total-return sector index, the holding falls back
# to that market's broad index rather than borrowing a foreign sector one. A
# home-market broad benchmark is a fair comparison with a stated limitation; a
# foreign sector benchmark is an unfair comparison dressed as a precise one.

SECTORS: dict[str, Benchmark] = {b.ticker: b for b in [
    # --- India ------------------------------------------------------------
    # NSE sector indices, read through the ETFs that track them so the level is
    # total return. The raw Nifty sector indices (^NSEBANK, ^CNXIT and the rest)
    # all carry history but are published price-only, which would understate the
    # benchmark by the sector's dividend yield and flatter every Indian holding.
    Benchmark("BANKBEES.NS", "Nifty Bank", Level.SECTOR, "Financials", "INR",
              True, index="Nifty Bank Index", provider="NSE Indices",
              vehicle="Nippon India ETF Nifty Bank BeES", country="IN",
              caveat="Banks only; excludes NBFCs and insurers."),
    Benchmark("PSUBNKBEES.NS", "Nifty PSU Bank", Level.SECTOR,
              "Financials (public sector)", "INR", True,
              index="Nifty PSU Bank Index", provider="NSE Indices",
              vehicle="Nippon India ETF Nifty PSU Bank BeES", country="IN",
              caveat="State-owned banks only; the right comparator for a PSU "
                     "bank and the wrong one for a private one."),
    Benchmark("ITBEES.NS", "Nifty IT", Level.SECTOR, "Technology", "INR", True,
              index="Nifty IT Index", provider="NSE Indices",
              vehicle="Nippon India ETF Nifty IT BeES", country="IN"),
    Benchmark("PHARMABEES.NS", "Nifty Pharma", Level.SECTOR, "Health Care",
              "INR", True, index="Nifty Pharma Index", provider="NSE Indices",
              vehicle="Nippon India ETF Nifty Pharma BeES", country="IN",
              caveat="Pharmaceutical-weighted; lighter on hospitals and "
                     "diagnostics than a full healthcare index."),
    Benchmark("FMCGIETF.NS", "Nifty FMCG", Level.SECTOR, "Consumer Staples",
              "INR", True, index="Nifty FMCG Index", provider="NSE Indices",
              vehicle="ICICI Prudential Nifty FMCG ETF", country="IN"),
    Benchmark("CONSUMBEES.NS", "Nifty India Consumption", Level.SECTOR,
              "Consumer Discretionary", "INR", True,
              index="Nifty India Consumption Index", provider="NSE Indices",
              vehicle="Nippon India ETF Nifty India Consumption",
              country="IN",
              caveat="A consumption basket spanning staples and discretionary "
                     "rather than a pure discretionary index."),
    Benchmark("INFRABEES.NS", "Nifty Infrastructure", Level.SECTOR,
              "Industrials", "INR", True,
              index="Nifty Infrastructure Index", provider="NSE Indices",
              vehicle="Nippon India ETF Nifty Infrastructure", country="IN",
              caveat="Infrastructure-weighted; broader than industrials and "
                     "includes utilities and telecom constituents."),

    # --- United States ----------------------------------------------------
    # S&P Select Sector indices, read through the SPDR funds that track them.
    Benchmark("XLK", "S&P Technology Select Sector", Level.SECTOR, "Technology",
              "USD", True, index="S&P Technology Select Sector Index",
              provider="S&P Dow Jones Indices",
              vehicle="Technology Select Sector SPDR Fund", country="US"),
    Benchmark("XLF", "S&P Financial Select Sector", Level.SECTOR, "Financials",
              "USD", True, index="S&P Financial Select Sector Index",
              provider="S&P Dow Jones Indices",
              vehicle="Financial Select Sector SPDR Fund", country="US"),
    Benchmark("XLE", "S&P Energy Select Sector", Level.SECTOR, "Energy", "USD",
              True, index="S&P Energy Select Sector Index",
              provider="S&P Dow Jones Indices",
              vehicle="Energy Select Sector SPDR Fund", country="US"),
    Benchmark("XLV", "S&P Health Care Select Sector", Level.SECTOR,
              "Health Care", "USD", True,
              index="S&P Health Care Select Sector Index",
              provider="S&P Dow Jones Indices",
              vehicle="Health Care Select Sector SPDR Fund", country="US"),
    Benchmark("XLB", "S&P Materials Select Sector", Level.SECTOR, "Materials",
              "USD", True, index="S&P Materials Select Sector Index",
              provider="S&P Dow Jones Indices",
              vehicle="Materials Select Sector SPDR Fund", country="US"),
    Benchmark("XLI", "S&P Industrial Select Sector", Level.SECTOR,
              "Industrials", "USD", True,
              index="S&P Industrial Select Sector Index",
              provider="S&P Dow Jones Indices",
              vehicle="Industrial Select Sector SPDR Fund", country="US"),
    Benchmark("XLP", "S&P Consumer Staples Select Sector", Level.SECTOR,
              "Consumer Staples", "USD", True,
              index="S&P Consumer Staples Select Sector Index",
              provider="S&P Dow Jones Indices",
              vehicle="Consumer Staples Select Sector SPDR Fund", country="US"),
    Benchmark("XLY", "S&P Consumer Discretionary Select Sector", Level.SECTOR,
              "Consumer Discretionary", "USD", True,
              index="S&P Consumer Discretionary Select Sector Index",
              provider="S&P Dow Jones Indices",
              vehicle="Consumer Discretionary Select Sector SPDR Fund",
              country="US"),
    Benchmark("XLU", "S&P Utilities Select Sector", Level.SECTOR, "Utilities",
              "USD", True, index="S&P Utilities Select Sector Index",
              provider="S&P Dow Jones Indices",
              vehicle="Utilities Select Sector SPDR Fund", country="US"),
    Benchmark("XLC", "S&P Communication Services Select Sector", Level.SECTOR,
              "Communication Services", "USD", True,
              index="S&P Communication Services Select Sector Index",
              provider="S&P Dow Jones Indices",
              vehicle="Communication Services Select Sector SPDR Fund",
              country="US"),
    Benchmark("XLRE", "S&P Real Estate Select Sector", Level.SECTOR,
              "Real Estate", "USD", True,
              index="S&P Real Estate Select Sector Index",
              provider="S&P Dow Jones Indices",
              vehicle="Real Estate Select Sector SPDR Fund", country="US"),

    # --- Global -----------------------------------------------------------
    # Last resort only, for a holding listed somewhere this universe does not
    # cover. Never offered to a holding whose own market is represented above.
    Benchmark("IXN", "S&P Global 1200 Information Technology", Level.SECTOR,
              "Technology", "USD", True,
              index="S&P Global 1200 Information Technology Index",
              provider="S&P Dow Jones Indices",
              vehicle="iShares Global Tech ETF"),
    Benchmark("IXG", "S&P Global 1200 Financials", Level.SECTOR, "Financials",
              "USD", True, index="S&P Global 1200 Financials Index",
              provider="S&P Dow Jones Indices",
              vehicle="iShares Global Financials ETF"),
    Benchmark("IXC", "S&P Global 1200 Energy", Level.SECTOR, "Energy", "USD",
              True, index="S&P Global 1200 Energy Index",
              provider="S&P Dow Jones Indices",
              vehicle="iShares Global Energy ETF"),
    Benchmark("IXJ", "S&P Global 1200 Health Care", Level.SECTOR, "Health Care",
              "USD", True, index="S&P Global 1200 Health Care Index",
              provider="S&P Dow Jones Indices",
              vehicle="iShares Global Healthcare ETF"),
    Benchmark("MXI", "S&P Global 1200 Materials", Level.SECTOR, "Materials",
              "USD", True, index="S&P Global 1200 Materials Index",
              provider="S&P Dow Jones Indices",
              vehicle="iShares Global Materials ETF"),
    Benchmark("EXI", "S&P Global 1200 Industrials", Level.SECTOR, "Industrials",
              "USD", True, index="S&P Global 1200 Industrials Index",
              provider="S&P Dow Jones Indices",
              vehicle="iShares Global Industrials ETF"),
    Benchmark("KXI", "S&P Global 1200 Consumer Staples", Level.SECTOR,
              "Consumer Staples", "USD", True,
              index="S&P Global 1200 Consumer Staples Index",
              provider="S&P Dow Jones Indices",
              vehicle="iShares Global Consumer Staples ETF"),
    Benchmark("JXI", "S&P Global 1200 Utilities", Level.SECTOR, "Utilities",
              "USD", True, index="S&P Global 1200 Utilities Index",
              provider="S&P Dow Jones Indices",
              vehicle="iShares Global Utilities ETF"),
    Benchmark("IXP", "S&P Global 1200 Communication Services", Level.SECTOR,
              "Communication Services", "USD", True,
              index="S&P Global 1200 Communication Services Index",
              provider="S&P Dow Jones Indices",
              vehicle="iShares Global Comm Services ETF"),
]}


# -- Market benchmarks (holding level fallback) -----------------------------
# Used when a holding's market publishes no total-return index for its sector.
# Still the holding's own market -- the country rule is never broken to get a
# more precise sector.

MARKETS: dict[str, Benchmark] = {b.ticker: b for b in [
    Benchmark("^SP500TR", "S&P 500 Total Return", Level.MARKET, "US", "USD",
              True, index="S&P 500 Total Return Index",
              provider="S&P Dow Jones Indices", vehicle="index", country="US"),
    Benchmark("NIFTYBEES.NS", "Nifty 50", Level.MARKET, "IN", "INR", True,
              index="Nifty 50 Index", provider="NSE Indices",
              vehicle="Nippon India ETF Nifty 50 BeES", country="IN"),
    # A Saudi benchmark: Saudi companies, Saudi market, on a total-return
    # basis. The only thing about it that is not Saudi is the currency it is
    # quoted in, and that costs nothing -- the riyal has been pegged at 3.75 to
    # the dollar since 1986, so a Saudi return in riyals and the same return in
    # dollars are the same number.
    #
    # The riyal-quoted alternative is Tadawul's own TASI. It is not used for
    # two reasons, in order: it returned a single usable observation when
    # probed on 16 Sep 2026, having returned five years of history earlier the
    # same day, so the free feed for it cannot be relied on; and it is
    # published price-only, which would understate the benchmark by the Saudi
    # market's dividend yield and flatter every Saudi holding.
    Benchmark("KSA", "MSCI Saudi Arabia", Level.MARKET, "SA", "USD", True,
              index="MSCI Saudi Arabia IMI 25/50 Index", provider="MSCI",
              vehicle="iShares MSCI Saudi Arabia ETF", country="SA",
              caveat="A Saudi index quoted in USD while the holdings are in "
                     "SAR, which has no effect: the riyal is pegged at 3.75 to "
                     "the dollar, so the two returns are the same number. "
                     "Tadawul's riyal-quoted TASI is not used because its free "
                     "feed is unreliable and it is published price-only. No "
                     "free Saudi sector index exists, so Saudi holdings are "
                     "measured against the market rather than their sector."),
    # The Amsterdam-listed tracker, in euro. The US-listed EWN follows the same
    # market but quotes in dollars, which would measure a euro holding against
    # a dollar return and fold the currency into the alpha.
    Benchmark("IAEX.AS", "AEX", Level.MARKET, "NL", "EUR", True,
              index="AEX Index", provider="Euronext",
              vehicle="iShares AEX UCITS ETF", country="NL"),
    Benchmark("EDEN", "MSCI Denmark", Level.MARKET, "DK", "USD", True,
              index="MSCI Denmark IMI 25/50 Index", provider="MSCI",
              vehicle="iShares MSCI Denmark ETF", country="DK"),
    Benchmark("VGK", "FTSE Developed Europe", Level.MARKET, "EU", "USD", True,
              index="FTSE Developed Europe All Cap Index",
              provider="FTSE Russell", vehicle="Vanguard FTSE Europe ETF"),
    Benchmark("EWJ", "MSCI Japan", Level.MARKET, "JP", "USD", True,
              index="MSCI Japan Index", provider="MSCI",
              vehicle="iShares MSCI Japan ETF", country="JP"),
    Benchmark("ACWI", "MSCI ACWI", Level.MARKET, "WW", "USD", True,
              index="MSCI All Country World Index", provider="MSCI",
              vehicle="iShares MSCI ACWI ETF"),
]}


#: A holding's home-market benchmark, used when its sector has no total-return
#: index in that market. European markets without a dedicated fund fall back to
#: developed Europe, which is still nearer home than a global sector index.
COUNTRY_MARKET: dict[str, str] = {
    "US": "^SP500TR", "IN": "NIFTYBEES.NS", "SA": "KSA",
    "NL": "IAEX.AS", "DK": "EDEN", "JP": "EWJ",
    "GB": "VGK", "UK": "VGK", "DE": "VGK", "FR": "VGK", "CH": "VGK",
    "SE": "VGK", "IT": "VGK", "ES": "VGK", "FI": "VGK", "NO": "VGK",
    "IE": "VGK", "BE": "VGK", "AT": "VGK",
}


#: Every benchmark, for lookup by ticker regardless of level.
#:
#: Several tickers serve at two levels: the Nifty 50 is both an India mandate
#: and the Indian home market, and the S&P 500 is both a US mandate and the US
#: home market. MARKETS wins the lookup because the market reading is the one
#: that carries a country tag and the MARKET level, and the mandate path records
#: its own level explicitly rather than reading it back from here. Resolved the
#: other way round, an Indian holding that fell back to the Nifty 50 was filed
#: as a mandate-level decision with no country.
UNIVERSE: dict[str, Benchmark] = {**MANDATES, **SECTORS, **MARKETS}

#: Countries classified as developed for mandate inference. Deliberately short:
#: it covers the markets this firm actually works in rather than attempting a
#: full MSCI classification.
DEVELOPED = {"US", "GB", "UK", "JP", "DE", "FR", "CH", "NL", "SE", "CA", "AU",
             "IT", "ES", "DK", "FI", "NO", "IE", "BE", "AT", "NZ", "SG", "HK"}
EMERGING = {"IN", "SA", "CN", "BR", "ZA", "MX", "ID", "TH", "MY", "PH", "TR",
             "AE", "QA", "KW", "TW", "KR", "PL", "CL", "GR", "EG"}


@dataclass
class BenchmarkDecision:
    """One benchmark choice, with the reasoning that justifies it."""

    asset_id: str
    asset_name: str
    benchmark_ticker: str
    benchmark_name: str
    confidence: float
    rationale: str
    alternatives_considered: list[str]
    source: str                      # "model" | "rules" | "analyst" | "universe"
    level: str = Level.SECTOR.value
    approved_by: str | None = None
    approved_at: str | None = None

    @property
    def needs_review(self) -> bool:
        if self.approved_by is not None:
            return False
        if self.source == "universe":
            # Its market publishes exactly one usable benchmark, so there was
            # no judgement to make and nothing for an analyst to second-guess.
            return False
        return self.confidence < 0.7 or self.source == "rules"


# -- Holding-level selection ------------------------------------------------

HOLDING_SYSTEM = """You are a senior equity analyst selecting the performance \
benchmark for a single listed holding.

The purpose is to isolate stock selection from everything the stock did not \
choose. A holding did not choose its country -- the allocator did -- and it did \
not choose its sector's fortunes. So it is measured against its own sector IN \
ITS OWN MARKET. ICICI Bank is judged against Indian banks, not against global \
financials; measured globally, an Indian bank in a strong year for India looks \
skilful when the country did the work, and that country bet is already judged \
separately at portfolio level.

Every candidate you are given is from the holding's own market. That part is \
settled before you see the list; your judgement is which one within it.

Rules, in order:
1. Choose the SECTOR index that matches the company's actual business.
2. If no sector in the list genuinely matches the business, choose the broad \
market index for that country. A fair comparison with a stated limitation beats \
a precise-looking comparison against the wrong peer group.
3. Prefer a TOTAL RETURN benchmark over a price-only index. Holdings are \
measured on a total-return basis, so a price index silently credits the \
investor with the index's dividend yield -- worth 1 to 2 percentage points a \
year, enough to reverse a verdict.

Read the caveats: a bank index that excludes insurers is the wrong choice for an \
insurer, and a public-sector bank index is the wrong choice for a private bank.

Be specific about the company's business. State the actual reason."""

HOLDING_SCHEMA = {
    "properties": {
        "benchmark_ticker": {"type": "string"},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
        "alternatives_considered": {"type": "array"},
    }
}


#: Free-text sector labels reduced to the names used as ``scope`` above. Client
#: workbooks say "Banking", "IT Services" and "Oil & Gas"; the universe says
#: "Financials", "Technology" and "Energy".
_SECTOR_ALIASES: dict[str, str] = {
    "tech": "Technology", "information technology": "Technology",
    "software": "Technology", "it services": "Technology",
    "semiconductor": "Technology",
    "financ": "Financials", "bank": "Financials", "insur": "Financials",
    "energy": "Energy", "oil": "Energy", "gas": "Energy",
    "health": "Health Care", "pharma": "Health Care", "biotech": "Health Care",
    "material": "Materials", "chemical": "Materials", "mining": "Materials",
    "metal": "Materials", "cement": "Materials",
    "industrial": "Industrials", "aerospace": "Industrials",
    "capital goods": "Industrials", "infrastructure": "Industrials",
    "staple": "Consumer Staples", "fmcg": "Consumer Staples",
    "food": "Consumer Staples", "beverage": "Consumer Staples",
    "utilit": "Utilities", "power": "Utilities",
    "telecom": "Communication Services", "communication": "Communication Services",
    "media": "Communication Services",
    "discretionary": "Consumer Discretionary", "retail": "Consumer Discretionary",
    "auto": "Consumer Discretionary",
    # Yahoo Finance's own names for two of the eleven. Without these, every
    # carmaker and every supermarket came back from the feed unclassified and
    # fell through to the broad market benchmark.
    "consumer cyclical": "Consumer Discretionary",
    "consumer defensive": "Consumer Staples",
    "real estate": "Real Estate", "reit": "Real Estate",
}

#: Last-resort sector indices, by canonical sector name, for a holding listed in
#: a market this universe does not cover.
_GLOBAL_SECTOR: dict[str, str] = {
    "Technology": "IXN", "Financials": "IXG", "Energy": "IXC",
    "Health Care": "IXJ", "Materials": "MXI", "Industrials": "EXI",
    "Consumer Staples": "KXI", "Utilities": "JXI",
    "Communication Services": "IXP", "Consumer Discretionary": "XLY",
}


def canonical_sector(sector: str) -> str:
    """Map a free-text sector label onto the universe's own naming, or ""."""
    text = (sector or "").strip().lower()
    if not text:
        return ""
    for benchmark in SECTORS.values():           # an exact name wins outright
        if benchmark.scope.lower() == text:
            return benchmark.scope
    for key, name in _SECTOR_ALIASES.items():
        if key in text:
            return name
    return ""


def sectors_in(country: str) -> list[Benchmark]:
    """Every sector benchmark published for one market."""
    code = (country or "").upper()
    return [b for b in SECTORS.values() if b.country == code]


def _rules_holding_benchmark(stream: CashflowStream) -> tuple[str, str]:
    """Deterministic fallback when no model is configured or a call fails.

    Follows the same country-first rule as the model path, so turning the model
    off changes how well a benchmark is explained, never which market it is
    drawn from.
    """
    country = (stream.country or "").upper()
    sector = canonical_sector(stream.sector)
    home = COUNTRY_MARKET.get(country)

    if home:
        local = {b.scope: b for b in sectors_in(country)}
        if sector in local:
            benchmark = local[sector]
            return benchmark.ticker, (
                f"Rules: {sector} company listed in {country}, measured against "
                f"the {benchmark.index} -- its own sector in its own market.")
        if sector:
            return home, (
                f"Rules: no total-return {sector} index is published for "
                f"{country}, so the holding is measured against the broad "
                f"{UNIVERSE[home].name} rather than borrowing a foreign sector "
                "index and charging the stock for a country bet it did not make.")
        return home, (
            f"Rules: no sector was stated, so the holding falls back to the "
            f"broad {UNIVERSE[home].name} for its own market.")

    # A market this universe does not cover: a global sector index is now the
    # closest fair comparison available.
    where = country or "this market"
    if sector in _GLOBAL_SECTOR:
        ticker = _GLOBAL_SECTOR[sector]
        return ticker, (
            f"Rules: no benchmark universe is configured for {where}, so the "
            f"holding falls back to the global {sector} index.")
    return "ACWI", ("Rules: neither the market nor the sector could be matched, "
                    "so the holding falls back to an all-country index.")


def holding_candidates(country: str = "") -> list[Benchmark]:
    """The benchmarks a holding is allowed to be measured against.

    This is where the country rule is actually enforced. A holding listed in a
    covered market is offered that market's sector indices and that market's
    broad index -- nothing else. The model cannot reach a global sector index
    for an Indian bank because it is never shown one.
    """
    code = (country or "").upper()
    home = COUNTRY_MARKET.get(code)
    if home:
        return sectors_in(code) + [MARKETS[home]]
    return ([b for b in SECTORS.values() if not b.country]
            + list(MARKETS.values()))


#: Kept so the private name still resolves for existing call sites.
_holding_candidates = holding_candidates


def _format_candidates(candidates: list[Benchmark]) -> str:
    return "\n".join(
        f"  {b.ticker:14} {b.index or b.name} [{b.scope}, {b.currency}, "
        f"{'total return' if b.total_return else 'PRICE INDEX - excludes dividends'}]"
        + (f"; {b.caveat}" if b.caveat else "")
        for b in candidates
    )


def assign_holding_benchmark(stream: CashflowStream,
                             client: LLMClient | None = None) -> BenchmarkDecision:
    """Pick a sector-level benchmark for one listed holding."""
    client = client or get_client()

    # Where a market publishes exactly one usable benchmark -- Saudi Arabia, the
    # Netherlands, Denmark, Japan -- there is no choice to make. Asking a model
    # to pick from a list of one spends a call to be told the only answer, and
    # filing the result as a "rules fallback" misreports a determined outcome as
    # a degraded one.
    only = _holding_candidates(stream.country)
    if len(only) == 1:
        benchmark = only[0]
        return BenchmarkDecision(
            asset_id=stream.asset_id, asset_name=stream.name,
            benchmark_ticker=benchmark.ticker, benchmark_name=benchmark.name,
            confidence=1.0,
            rationale=(
                f"{benchmark.index} is the only total-return benchmark "
                f"published for {stream.country}; no sector index is freely "
                "available for that market. The holding is measured against "
                "its own market, with the sector left unisolated."),
            alternatives_considered=[], source="universe",
            level=benchmark.level.value,
        )

    if not client.is_live:
        ticker, rationale = _rules_holding_benchmark(stream)
        return BenchmarkDecision(
            asset_id=stream.asset_id, asset_name=stream.name,
            benchmark_ticker=ticker, benchmark_name=UNIVERSE[ticker].name,
            confidence=0.5, rationale=rationale, alternatives_considered=[],
            source="rules", level=UNIVERSE[ticker].level.value,
        )

    candidates = _holding_candidates(stream.country)
    allowed = {b.ticker for b in candidates}

    prompt = f"""Holding to benchmark:
  Name:     {stream.name}
  Ticker:   {stream.asset_id}
  Country:  {stream.country}
  Currency: {stream.currency}
  Sector:   {stream.sector or "not stated -- infer it from the company"}

Benchmarks published for this holding's own market (choose exactly one ticker):
{_format_candidates(candidates)}"""

    answer: dict = {}
    ticker = ""
    for attempt in range(2):
        try:
            answer = client.structured(
                prompt if attempt == 0
                else prompt + "\n\nReturn a non-empty benchmark_ticker copied "
                              "exactly from the list above.",
                HOLDING_SCHEMA, system=HOLDING_SYSTEM,
            )
        except Exception:  # noqa: BLE001 - never let this break the report
            break
        ticker = str(answer.get("benchmark_ticker", "")).strip()
        if ticker in allowed:
            break

    # Checked against the holding's own market, not the whole universe: a real
    # index from the wrong country is the exact mistake this rewrite exists to
    # prevent, and it is the one a model is most likely to make.
    if not answer or ticker not in allowed:
        fallback, rationale = _rules_holding_benchmark(stream)
        if ticker in UNIVERSE:
            note = (f" Model proposed {ticker} ({UNIVERSE[ticker].name}), which "
                    f"is not a {stream.country or 'home'}-market benchmark, so "
                    "it was rejected.")
        elif ticker:
            note = (f" Model proposed {ticker!r}, which is not in the verified "
                    "universe, so it was rejected.")
        else:
            note = " The model call did not return a usable answer."
        return BenchmarkDecision(
            asset_id=stream.asset_id, asset_name=stream.name,
            benchmark_ticker=fallback, benchmark_name=UNIVERSE[fallback].name,
            confidence=0.45, rationale=rationale + note,
            alternatives_considered=[], source="rules",
            level=UNIVERSE[fallback].level.value,
        )

    confidence = float(answer.get("confidence", 0.0) or 0.0)
    if confidence > 1.0:
        confidence /= 100.0

    return BenchmarkDecision(
        asset_id=stream.asset_id, asset_name=stream.name,
        benchmark_ticker=ticker, benchmark_name=UNIVERSE[ticker].name,
        confidence=round(min(confidence, 1.0), 3),
        rationale=str(answer.get("rationale", "")).strip(),
        alternatives_considered=[str(a) for a in
                                 (answer.get("alternatives_considered") or [])],
        source="model", level=UNIVERSE[ticker].level.value,
    )


# -- Mandate-level selection ------------------------------------------------

MANDATE_SYSTEM = """You are setting the benchmark for a whole equity portfolio.

First decide what KIND of portfolio it is, then take that type's benchmark. The \
types are:

    Global Equity          a book spread across markets and sectors
    US Focused             concentrated in the United States
    GCC Equity             concentrated in the Gulf states
    India Equity           concentrated in India
    REIT Portfolio         concentrated in real estate
    Banking Portfolio      concentrated in banks and financials
    Technology Portfolio   concentrated in technology

The type must come from the composition supplied, never from the portfolio's \
name. This is the most consequential choice in performance reporting: a \
technology book measured against a broad world index will look brilliant or \
hopeless depending purely on whether technology was in favour, and neither \
verdict says anything about the manager.

Rules, in order:
1. A SECTOR concentration of about 60% or more makes it a sector mandate -- \
REIT, banking or technology. Test this first: it is the more specific claim, \
because a book that is mostly banks is a banking mandate whichever countries \
those banks are in.
2. Otherwise a GEOGRAPHIC concentration of about 70% or more makes it a \
single-market mandate -- US, India, or the GCC taken as a bloc.
3. Otherwise it is Global Equity. A book with no dominant market and no \
dominant sector is a diversified portfolio, and saying so is a finding rather \
than a failure to choose.

Several candidates carry a caveat because no free tracker follows the named \
index exactly. Read them: a substitution that is stated is acceptable, and \
choosing one without mentioning it is not.

State the shares you observed -- the largest market and the largest sector, \
with their percentages -- and name the type before the benchmark."""

MANDATE_SCHEMA = {
    "properties": {
        "benchmark_ticker": {"type": "string"},
        "mandate_label": {"type": "string"},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
        "alternatives_considered": {"type": "array"},
    }
}


#: The Gulf Cooperation Council. A book spread across these is a GCC mandate
#: even though no single country dominates it.
GCC = {"SA", "AE", "KW", "QA", "BH", "OM"}


def classify_exposure(streams: list[CashflowStream],
                      weights: dict[str, float] | None = None) -> dict:
    """Where the capital actually sits, by country and by sector.

    Both are needed because a mandate is a portfolio *type*, and a type can be
    set by either dimension: a book 80% in India is an India mandate, and a
    book 80% in banks is a banking mandate whichever countries it spans.
    """
    totals: dict[str, float] = {}
    by_country: dict[str, float] = {}
    by_sector: dict[str, float] = {}

    for s in streams:
        weight = (weights or {}).get(s.asset_id, 1.0)
        country = (s.country or "").upper()
        by_country[country] = by_country.get(country, 0.0) + weight
        sector = canonical_sector(s.sector) or "Unclassified"
        by_sector[sector] = by_sector.get(sector, 0.0) + weight
        bucket = ("developed" if country in DEVELOPED
                  else "emerging" if country in EMERGING else "unclassified")
        totals[bucket] = totals.get(bucket, 0.0) + weight

    grand = sum(totals.values()) or 1.0
    gcc = sum(v for k, v in by_country.items() if k in GCC)
    return {
        "developed_pct": round(totals.get("developed", 0.0) / grand, 4),
        "emerging_pct": round(totals.get("emerging", 0.0) / grand, 4),
        "unclassified_pct": round(totals.get("unclassified", 0.0) / grand, 4),
        "gcc_pct": round(gcc / grand, 4),
        "by_country": {k: round(v / grand, 4)
                       for k, v in sorted(by_country.items(),
                                          key=lambda kv: -kv[1])},
        "by_sector": {k: round(v / grand, 4)
                      for k, v in sorted(by_sector.items(),
                                         key=lambda kv: -kv[1])},
    }


#: A sector has to be this much of the book before the book is *about* that
#: sector. Set lower and a merely overweight portfolio gets measured against a
#: single industry; set higher and a genuine sector fund escapes it.
SECTOR_MANDATE_THRESHOLD = 0.60

#: Geography needs a higher bar than sector. Most books lean towards a home
#: market without being a single-country mandate.
COUNTRY_MANDATE_THRESHOLD = 0.70

#: Sector concentration to the mandate that measures it.
_SECTOR_MANDATE = {
    "Real Estate": ("REET", "REIT Portfolio"),
    "Financials": ("IXG", "Banking Portfolio"),
    "Technology": ("QQQ", "Technology Portfolio"),
}

#: Geographic concentration to the mandate that measures it.
_COUNTRY_MANDATE = {
    "US": ("^SP500TR", "US Focused"),
    "IN": ("^CRSLDX", "India Equity"),
}


def _rules_mandate(exposure: dict) -> tuple[str, str, str]:
    """Deterministic mandate choice. Returns (ticker, type, rationale).

    The question is what *kind* of book this is, and the answer can come from
    either dimension. A sector bet is tested first because it is the more
    specific claim: a book that is 80% banks is a banking mandate whether those
    banks are American or Indian, whereas a book that is 80% American may still
    be an ordinary diversified equity portfolio.
    """
    sectors = exposure.get("by_sector") or {}
    countries = exposure.get("by_country") or {}
    gcc = exposure.get("gcc_pct", 0.0)

    if sectors:
        top_sector, sector_weight = next(iter(sectors.items()))
        if (sector_weight >= SECTOR_MANDATE_THRESHOLD
                and top_sector in _SECTOR_MANDATE):
            ticker, label = _SECTOR_MANDATE[top_sector]
            return ticker, label, (
                f"Rules: {sector_weight:.0%} of capital is in {top_sector}, so "
                f"this is a {label.lower()} rather than a diversified book, "
                f"and is measured against {UNIVERSE[ticker].index}.")

    # The GCC is a region rather than a country, so it is tested on the bloc.
    if gcc >= COUNTRY_MANDATE_THRESHOLD:
        return "KSA", "GCC Equity", (
            f"Rules: {gcc:.0%} of capital is in GCC markets, so the book is "
            "measured against the Gulf rather than against the world.")

    if countries:
        top_country, country_weight = next(iter(countries.items()))
        if (country_weight >= COUNTRY_MANDATE_THRESHOLD
                and top_country in _COUNTRY_MANDATE):
            ticker, label = _COUNTRY_MANDATE[top_country]
            return ticker, label, (
                f"Rules: {country_weight:.0%} of capital is in {top_country}, "
                f"so this is effectively a single-country mandate and is "
                f"measured against {UNIVERSE[ticker].index}.")

    spread = ", ".join(f"{k or 'unknown'} {v:.0%}"
                       for k, v in list(countries.items())[:4])
    top_sector_txt = ""
    if sectors:
        name, share = next(iter(sectors.items()))
        top_sector_txt = (f" The largest sector is {name} at {share:.0%}, "
                          f"below the {SECTOR_MANDATE_THRESHOLD:.0%} that "
                          "would make this a sector mandate.")
    return "ACWI", "Global Equity", (
        f"Rules: no single market or sector dominates ({spread})."
        f"{top_sector_txt} A global all-country benchmark is the honest "
        "comparator.")


def assign_mandate_benchmark(streams: list[CashflowStream], exposure: dict,
                             portfolio_name: str = "Portfolio",
                             client: LLMClient | None = None) -> BenchmarkDecision:
    """Pick the portfolio-level benchmark from the book's actual composition."""
    client = client or get_client()

    if not client.is_live:
        ticker, label, rationale = _rules_mandate(exposure)
        return BenchmarkDecision(
            asset_id="PORTFOLIO", asset_name=portfolio_name,
            benchmark_ticker=ticker, benchmark_name=UNIVERSE[ticker].name,
            confidence=0.5, rationale=f"{label}. {rationale}",
            alternatives_considered=[], source="rules", level=Level.MANDATE.value,
        )

    country_lines = "\n".join(
        f"    {c or 'unknown'}: {w:.1%} of capital"
        for c, w in exposure["by_country"].items()
    )
    sector_lines = "\n".join(
        f"    {s}: {w:.1%} of capital"
        for s, w in (exposure.get("by_sector") or {}).items()
    ) or "    not stated"
    prompt = f"""Portfolio: {portfolio_name}
Holdings: {len(streams)} listed equities

Composition by capital committed, by market:
{country_lines}

  GCC bloc total:     {exposure.get('gcc_pct', 0):.1%}
  Developed markets:  {exposure['developed_pct']:.1%}
  Emerging markets:   {exposure['emerging_pct']:.1%}

Composition by capital committed, by sector:
{sector_lines}

Available mandate benchmarks (choose exactly one ticker):
{_format_candidates(list(MANDATES.values()))}"""

    try:
        answer = client.structured(prompt, MANDATE_SCHEMA, system=MANDATE_SYSTEM)
        ticker = str(answer.get("benchmark_ticker", "")).strip()
    except Exception:  # noqa: BLE001
        answer, ticker = {}, ""

    if ticker not in MANDATES:
        fallback, label, rationale = _rules_mandate(exposure)
        return BenchmarkDecision(
            asset_id="PORTFOLIO", asset_name=portfolio_name,
            benchmark_ticker=fallback, benchmark_name=UNIVERSE[fallback].name,
            confidence=0.5, rationale=f"{label}. {rationale}",
            alternatives_considered=[], source="rules", level=Level.MANDATE.value,
        )

    confidence = float(answer.get("confidence", 0.0) or 0.0)
    if confidence > 1.0:
        confidence /= 100.0

    label = str(answer.get("mandate_label", "")).strip()
    rationale = str(answer.get("rationale", "")).strip()

    return BenchmarkDecision(
        asset_id="PORTFOLIO", asset_name=portfolio_name,
        benchmark_ticker=ticker, benchmark_name=UNIVERSE[ticker].name,
        confidence=round(min(confidence, 1.0), 3),
        rationale=f"{label + '. ' if label else ''}{rationale}",
        alternatives_considered=[str(a) for a in
                                 (answer.get("alternatives_considered") or [])],
        source="model", level=Level.MANDATE.value,
    )


# -- persistence ------------------------------------------------------------

def load_decisions(path: Path | None = None) -> dict[str, BenchmarkDecision]:
    path = path or DECISIONS_PATH
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    decisions: dict[str, BenchmarkDecision] = {}
    for key, value in raw.items():
        try:
            decisions[key] = BenchmarkDecision(**value)
        except TypeError:
            continue          # a record from an older shape; let it be re-made
    return decisions


def save_decisions(decisions: dict[str, BenchmarkDecision],
                   path: Path | None = None) -> None:
    path = path or DECISIONS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({k: asdict(v) for k, v in decisions.items()}, indent=2),
        encoding="utf-8",
    )


def approve(decision: BenchmarkDecision, analyst: str) -> BenchmarkDecision:
    """Record who signed off and when -- the audit trail the pitch promises."""
    decision.approved_by = analyst
    decision.approved_at = datetime.now(timezone.utc).isoformat()
    return decision


def override(decision: BenchmarkDecision, ticker: str, analyst: str,
             reason: str) -> BenchmarkDecision:
    """Replace a proposed benchmark with the analyst's own choice.

    Overrides are the most valuable data this system produces: each one records
    an expert disagreeing with the model, which is exactly what a later review
    of benchmark policy needs to look at.
    """
    if ticker not in UNIVERSE:
        raise ValueError(f"{ticker} is not in the verified benchmark universe")
    benchmark = UNIVERSE[ticker]
    decision.benchmark_ticker = ticker
    decision.benchmark_name = benchmark.name
    decision.level = benchmark.level.value
    # Say what the benchmark IS, then who chose it. The previous wording led
    # with the override and trailed off into "no reason given", which told a
    # reader nothing about the comparator they were now looking at.
    decision.rationale = f"{benchmark.attribution}. Chosen by {analyst}."
    if reason and reason != "selected from the benchmark list":
        decision.rationale += f" {reason}"
    if benchmark.caveat:
        decision.rationale += f" {benchmark.caveat}"
    decision.source = "analyst"
    decision.confidence = 1.0
    return approve(decision, analyst)
