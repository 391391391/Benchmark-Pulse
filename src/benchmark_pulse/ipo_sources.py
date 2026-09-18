"""Where open IPOs come from, and how their prospectus text is obtained.

Three free sources, each doing the part it is actually good at:

  Nasdaq IPO calendar   the US pipeline -- which deals are filed, upcoming,
                        priced or withdrawn. Thin on detail: the share price is
                        frequently null and the deal size often blank, so this
                        is a list of *what exists*, not analysis fuel.

  SEC EDGAR             the substance. Full-text search resolves a company name
                        to a CIK, the submissions API gives its SIC code and
                        filing history, and the archive serves the S-1 itself.

  Saudi CMA             the Saudi pipeline. Scraped, because no API exists.
                        Coverage is materially weaker than the US -- see
                        fetch_saudi_pipeline for what that means in practice.

Everything is cached to disk. Prospectuses run to several megabytes and the SEC
asks for no more than ten requests a second; re-downloading a 4 MB filing on
every rerun would be both slow and rude.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from pathlib import Path

import requests

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache" / "ipo"

#: SEC requires a genuine contact address in the User-Agent and returns 403
#: without one. Overridable so the firm can put its own address in .env.
import os

SEC_CONTACT = os.getenv("SEC_CONTACT_EMAIL", "research@preferredsquare.com")
UA_SEC = {"User-Agent": f"Preferred Square Benchmark Pulse {SEC_CONTACT}"}
UA_BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

#: SEC's published fair-access limit is 10 requests a second. Staying well under
#: it costs nothing here and avoids an IP block on demo day.
_SEC_DELAY = 0.25
_last_sec_call = 0.0

#: Standard Industrial Classification 6770 is "Blank Checks" -- the SEC's own
#: label for a shell formed to acquire an unidentified business. This is a far
#: better SPAC test than searching the text for "blank check", which also matches
#: prospectuses that merely mention the structure.
SIC_BLANK_CHECK = "6770"

#: Headings that appear in essentially every S-1, used to carve the document into
#: the parts worth reading. Ordered by how much they matter to a scorecard.
SECTION_HEADINGS = {
    "offering": ["The Offering", "THE OFFERING"],
    "use_of_proceeds": ["Use of Proceeds", "USE OF PROCEEDS"],
    "financials": ["Summary Consolidated Financial", "Summary Financial Data",
                   "Selected Consolidated Financial", "Summary Historical Financial",
                   "Summary Financial Information", "Selected Financial Data",
                   "Summary Historical Consolidated Financial",
                   "Summary Consolidated Statements", "Summary Financial and Other Data",
                   # Carve-outs and spin-offs have no standalone operating
                   # history, so they present pro-forma statements instead.
                   "Unaudited Pro Forma Condensed Combined Financial",
                   "Pro Forma Condensed Combined Financial",
                   "Supplementary financial data"],
    "risk_factors": ["Risk Factors", "RISK FACTORS"],
    "business": ["Our Business", "Business Overview", "Company Overview"],
    "dilution": ["Dilution", "DILUTION"],
}


class IPOSourceError(RuntimeError):
    """A source could not be reached or returned nothing usable."""


@dataclass
class IPOListing:
    """One deal in the pipeline, before any analysis."""

    company_name: str
    ticker: str | None
    market: str                  # US | SA
    status: str                  # filed | upcoming | priced | withdrawn
    exchange: str | None = None
    price_low: float | None = None
    price_high: float | None = None
    shares_offered: int | None = None
    deal_size: float | None = None
    currency: str = "USD"
    expected_date: str | None = None
    source: str = ""
    source_url: str | None = None

    @property
    def deal_id(self) -> str:
        base = f"{self.market}:{self.ticker or self.company_name}"
        return re.sub(r"[^A-Za-z0-9:._-]", "_", base)

    @property
    def is_open(self) -> bool:
        """Still investable -- filed or pricing, not already done or pulled."""
        return self.status in ("filed", "upcoming")


@dataclass
class FilingRef:
    cik: str
    company_name: str
    sic: str | None
    sic_description: str | None
    form: str
    filing_date: str
    document_url: str

    @property
    def is_blank_check(self) -> bool:
        return self.sic == SIC_BLANK_CHECK


@dataclass
class ProspectusSections:
    """The parts of a prospectus worth reading, each bounded in length."""

    company_name: str
    document_url: str
    total_chars: int
    sections: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    @property
    def has_financials(self) -> bool:
        return bool(self.sections.get("financials"))


# -- plumbing ---------------------------------------------------------------

def _cache_path(name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:120]
    return CACHE_DIR / safe


def _cached_json(name: str, max_age_hours: float, producer):
    """Read a cached JSON payload, or produce and store one."""
    path = _cache_path(f"{name}.json")
    if path.exists():
        age_h = (time.time() - path.stat().st_mtime) / 3600
        if age_h < max_age_hours:
            return json.loads(path.read_text(encoding="utf-8"))
    payload = producer()
    path.write_text(json.dumps(payload, default=str), encoding="utf-8")
    return payload


def _sec_get(url: str, params: dict | None = None, timeout: int = 40):
    global _last_sec_call
    wait = _SEC_DELAY - (time.time() - _last_sec_call)
    if wait > 0:
        time.sleep(wait)
    _last_sec_call = time.time()
    return requests.get(url, params=params, headers=UA_SEC, timeout=timeout)


def _to_float(value) -> float | None:
    if value is None:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", str(value))
    try:
        return float(cleaned) if cleaned not in ("", "-", ".") else None
    except ValueError:
        return None


def _to_int(value) -> int | None:
    f = _to_float(value)
    return int(f) if f is not None else None


# -- US pipeline ------------------------------------------------------------

def fetch_us_pipeline(month: str | None = None, *,
                      max_age_hours: float = 6.0) -> list[IPOListing]:
    """The US IPO calendar for a month (default: the current one).

    Returns filed, upcoming and priced deals. Withdrawn deals are included with
    that status rather than dropped -- a pulled IPO is itself information, and
    silently omitting it would leave an analyst wondering where a deal went.
    """
    month = month or date.today().strftime("%Y-%m")

    def download() -> dict:
        r = requests.get("https://api.nasdaq.com/api/ipo/calendar",
                         params={"date": month}, headers=UA_BROWSER, timeout=30)
        if not r.ok:
            raise IPOSourceError(f"Nasdaq calendar returned HTTP {r.status_code}")
        return r.json().get("data") or {}

    try:
        data = _cached_json(f"us_pipeline_{month}", max_age_hours, download)
    except Exception as exc:  # noqa: BLE001
        raise IPOSourceError(f"US pipeline unavailable: {exc}") from exc

    listings: list[IPOListing] = []
    for status in ("filed", "upcoming", "priced", "withdrawn"):
        block = data.get(status)
        rows = _rows_from(block)
        for row in rows or []:
            price = _to_float(row.get("proposedSharePrice"))
            listings.append(IPOListing(
                company_name=str(row.get("companyName", "")).strip(),
                ticker=(row.get("proposedTickerSymbol") or None),
                market="US",
                status=status,
                exchange=row.get("proposedExchange"),
                price_low=price, price_high=price,
                shares_offered=_to_int(row.get("sharesOffered")),
                deal_size=_to_float(row.get("dollarValueOfSharesOffered")),
                expected_date=(row.get("expectedPriceDate") or row.get("pricedDate")
                               or row.get("filedDate")),
                source="Nasdaq IPO calendar",
                source_url="https://www.nasdaq.com/market-activity/ipos",
            ))
    return [l for l in listings if l.company_name]


def _rows_from(block) -> list | None:
    """Nasdaq nests rows one level deeper for some sections than others."""
    if not isinstance(block, dict):
        return None
    if "rows" in block:
        return block["rows"]
    for value in block.values():
        if isinstance(value, dict) and "rows" in value:
            return value["rows"]
    return None


# -- Saudi pipeline ---------------------------------------------------------

#: The CMA news feed is dominated by mutual-fund offering approvals rather than
#: company IPOs, so announcements are filtered for the language that
#: distinguishes an equity listing from a fund launch.
_SAUDI_EQUITY_HINTS = re.compile(
    r"(?i)\b(initial public offering|IPO|listing of|direct listing|"
    r"offering of shares|share offering|Nomu|parallel market)\b"
)
_SAUDI_FUND_HINTS = re.compile(
    r"(?i)\b(fund|endowment|waqf|REIT fund|investment fund)\b"
)


def fetch_saudi_pipeline(*, max_age_hours: float = 12.0) -> list[IPOListing]:
    """Saudi offerings announced by the Capital Market Authority.

    Scraped rather than fetched from an API, because none is published. Coverage
    is honestly weaker than the US: the CMA feed carries mostly fund approvals,
    and the equity IPOs it does announce come without offer terms or financials.
    Deals found here are listed for the analyst with a link to the announcement;
    the scorecard is only produced where a prospectus can actually be read.

    Never raises. A scrape that breaks on the morning of a demo should degrade to
    an empty list with a note, not take the page down with it.
    """
    def download() -> list[dict]:
        r = requests.get("https://cma.org.sa/en/Market/NEWS/Pages/default.aspx",
                         headers=UA_BROWSER, timeout=30)
        if not r.ok:
            raise IPOSourceError(f"CMA returned HTTP {r.status_code}")

        text = re.sub(r"<[^>]+>", " ", r.text)
        text = re.sub(r"&(quot|#58|amp|nbsp|rsquo|ldquo|rdquo);", " ", text)
        text = re.sub(r"\s+", " ", text)

        found: list[dict] = []
        for match in re.finditer(
            r"(\d{1,2}-[A-Za-z]+-\d{4})\s+(CMA Announces[^.]{0,200})", text
        ):
            when, headline = match.group(1), match.group(2).strip()
            if not _SAUDI_EQUITY_HINTS.search(headline):
                continue
            if _SAUDI_FUND_HINTS.search(headline):
                continue          # a fund launch, not a company listing
            found.append({"date": when, "headline": headline})
        return found

    try:
        raw = _cached_json("sa_pipeline", max_age_hours, download)
    except Exception:  # noqa: BLE001 - a broken scrape must not break the page
        return []

    listings: list[IPOListing] = []
    for item in raw:
        headline = item.get("headline", "")
        name = re.search(r"[\"“‘]([^\"”’]{3,80})", headline)
        listings.append(IPOListing(
            company_name=(name.group(1).strip() if name else headline[:70]),
            ticker=None, market="SA", status="filed",
            exchange="Tadawul", currency="SAR",
            expected_date=item.get("date"),
            source="Saudi CMA announcements",
            source_url="https://cma.org.sa/en/Market/NEWS/Pages/default.aspx",
        ))
    return listings


def fetch_pipeline(markets=("US", "SA")) -> tuple[list[IPOListing], list[str]]:
    """Every open deal across the requested markets, plus any source warnings."""
    listings: list[IPOListing] = []
    notes: list[str] = []

    if "US" in markets:
        try:
            listings.extend(fetch_us_pipeline())
        except IPOSourceError as exc:
            notes.append(str(exc))

    if "SA" in markets:
        saudi = fetch_saudi_pipeline()
        listings.extend(saudi)
        if not saudi:
            notes.append(
                "No Saudi equity IPOs found. The CMA feed carries mostly fund "
                "approvals and publishes no offer terms, so Saudi coverage is "
                "announcement-level only."
            )
    return listings, notes


# -- EDGAR ------------------------------------------------------------------

def find_filing(company_name: str, *, max_age_hours: float = 168.0) -> FilingRef | None:
    """Resolve a company name to its most recent registration statement.

    Prefers a 424B prospectus (the final, priced document) over an S-1/A, and an
    amendment over the original, because later filings carry the terms that are
    actually on offer.
    """
    def download() -> dict:
        r = _sec_get("https://efts.sec.gov/LATEST/search-index",
                     params={"q": f'"{company_name}"', "forms": "S-1"})
        if not r.ok:
            raise IPOSourceError(f"EDGAR search returned HTTP {r.status_code}")
        hits = r.json().get("hits", {}).get("hits", [])
        if not hits:
            return {}
        ciks = hits[0].get("_source", {}).get("ciks") or []
        return {"cik": ciks[0] if ciks else None}

    try:
        found = _cached_json(f"cik_{company_name}", max_age_hours, download)
    except Exception:  # noqa: BLE001
        return None

    cik = found.get("cik")
    if not cik:
        return None

    padded = str(int(cik)).zfill(10)

    def download_submissions() -> dict:
        r = _sec_get(f"https://data.sec.gov/submissions/CIK{padded}.json")
        if not r.ok:
            raise IPOSourceError(f"submissions HTTP {r.status_code}")
        return r.json()

    try:
        sub = _cached_json(f"sub_{padded}", max_age_hours, download_submissions)
    except Exception:  # noqa: BLE001
        return None

    recent = sub.get("filings", {}).get("recent", {})
    candidates = list(zip(
        recent.get("form", []), recent.get("accessionNumber", []),
        recent.get("primaryDocument", []), recent.get("filingDate", []),
    ))

    def rank(form: str) -> int:
        if form.startswith("424"):
            return 0                      # final prospectus, the best source
        if form.startswith("S-1/A"):
            return 1                      # latest amendment
        if form.startswith("S-1"):
            return 2
        return 9

    usable = [c for c in candidates if rank(c[0]) < 9]
    if not usable:
        return None
    usable.sort(key=lambda c: (rank(c[0]), _neg_date(c[3])))
    form, accession, document, filing_date = usable[0]

    return FilingRef(
        cik=str(int(cik)),
        company_name=sub.get("name", company_name),
        sic=sub.get("sic"),
        sic_description=sub.get("sicDescription"),
        form=form,
        filing_date=filing_date,
        document_url=(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                      f"{accession.replace('-', '')}/{document}"),
    )


def _neg_date(value: str) -> float:
    """Sort key putting the most recent filing first."""
    try:
        return -datetime.strptime(value, "%Y-%m-%d").timestamp()
    except ValueError:
        return 0.0


def fetch_prospectus(ref: FilingRef, *, max_chars_per_section: int = 9000,
                     max_age_hours: float = 720.0) -> ProspectusSections:
    """Download a filing and carve it into the sections worth reading.

    A prospectus runs to one and a half million characters, which is far beyond
    what any model should be handed and mostly boilerplate besides. Only the
    sections a analyst would actually turn to are extracted, each capped, which
    keeps a full scorecard inside a single modest prompt.
    """
    cache_file = _cache_path(f"prospectus_{ref.cik}_{ref.form}.json")
    if cache_file.exists():
        age_h = (time.time() - cache_file.stat().st_mtime) / 3600
        if age_h < max_age_hours:
            stored = json.loads(cache_file.read_text(encoding="utf-8"))
            return ProspectusSections(**stored)

    response = _sec_get(ref.document_url, timeout=90)
    if not response.ok:
        raise IPOSourceError(
            f"{ref.company_name}: filing returned HTTP {response.status_code}")

    text = _html_to_text(response.text)
    sections, missing = _carve_sections(text, max_chars_per_section)

    result = ProspectusSections(
        company_name=ref.company_name, document_url=ref.document_url,
        total_chars=len(text), sections=sections, missing=missing,
    )
    cache_file.write_text(json.dumps(asdict(result)), encoding="utf-8")
    return result


#: Block-level tags whose boundaries carry the document's structure. Preserving
#: them as newlines is what makes a heading recognisable as a heading rather
#: than as three words in the middle of a paragraph.
_BLOCK_TAGS = r"p|div|br|tr|h[1-6]|li|table|section|td"


def _html_to_text(html: str) -> str:
    """Strip markup while keeping line structure.

    Collapsing all whitespace at once -- the obvious implementation -- destroys
    exactly the signal needed later: a section heading sits alone on its own
    line, whereas a cross-reference to that section sits inside a sentence.
    Without the line breaks the two are indistinguishable, and the parser
    happily anchors "Use of Proceeds" to the phrase "...use of proceeds
    therefrom" four hundred pages in.
    """
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    html = re.sub(rf"(?i)</?({_BLOCK_TAGS})\b[^>]*>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&#\d+;|&[a-z]{2,8};", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)          # within lines only
    text = re.sub(r"\n\s*\n+", "\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


#: A line is treated as a heading only if it is essentially just the heading.
#: Table-of-contents rows are the same words followed by dot leaders and a page
#: number, so a small length allowance is enough to separate the two.
_HEADING_SLACK = 12

#: "Dividend Policy 80", "Dilution 81" -- a word or phrase followed by a page
#: number, repeated. Two of these on one line means a contents listing.
_TOC_PAIR = re.compile(r"[A-Za-z]{3,}\s+\d{1,3}(?=\s+[A-Z])")


def _carve_sections(text: str, cap: int) -> tuple[dict[str, str], list[str]]:
    """Locate each known section and take a bounded window after its heading.

    Candidates are scored rather than taken first- or last-found. A genuine
    heading occupies its own line; a contents entry trails dot leaders and a page
    number; a cross-reference is embedded in prose. Preferring the last candidate
    in the document -- the obvious way to skip the contents page -- instead lands
    on the final passing mention, which is how this went wrong before.
    """
    lines = text.split("\n")
    # Character offset of each line, so a window can be cut from the full text.
    offsets: list[int] = []
    running = 0
    for line in lines:
        offsets.append(running)
        running += len(line) + 1

    sections: dict[str, str] = {}
    missing: list[str] = []

    for key, headings in SECTION_HEADINGS.items():
        best_pos, best_score = -1, 0
        wanted = [h.lower() for h in headings]

        for i, line in enumerate(lines):
            stripped = line.strip()
            low = stripped.lower()
            if not any(low.startswith(w) for w in wanted):
                continue
            matched = next(w for w in wanted if low.startswith(w))

            score = 0
            # A heading on its own line, give or take a numbering prefix.
            if len(stripped) <= len(matched) + _HEADING_SLACK:
                score += 5

            # "The offering, we will effect a share dividend..." begins with the
            # heading text but is plainly a sentence. Punctuation straight after
            # the match is the reliable tell.
            #
            # A lower-case continuation is NOT: real headings routinely extend
            # past the matched prefix in lower case, as in "Summary consolidated
            # financial and other data". Penalising that stripped the financials
            # section out of half the filings tested.
            remainder = stripped[len(matched):].lstrip()
            if remainder[:1] in (",", ";", ":"):
                score -= 7
            # Contents entries end in dot leaders or a bare page number.
            if re.search(r"(\.{2,}\s*\d+|\s\d{1,3})$", stripped):
                score -= 6
            # Real sections are followed by substantive prose.
            following = " ".join(lines[i + 1:i + 12]).strip()
            if len(" ".join(lines[i + 1:i + 4])) > 120:
                score += 2

            # The contents page lists a real heading on its own line too, so the
            # heading alone cannot distinguish it -- what follows can. Repeated
            # "Heading 76 Heading 80" pairs are a contents listing; prose never
            # produces them. Checking only the heading line missed this entirely,
            # because the giveaway is in the lines beneath it.
            if len(_TOC_PAIR.findall(f"{stripped} {following}")) >= 2:
                score -= 10
            # Sections proper sit past the summary; the contents page does not.
            if offsets[i] > len(text) * 0.05:
                score += 1

            if score > best_score:
                best_pos, best_score = offsets[i], score

        if best_pos == -1:
            missing.append(key)
            continue
        window = text[best_pos:best_pos + cap]
        sections[key] = re.sub(r"\n+", " ", window).strip()

    return sections, missing
