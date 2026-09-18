"""Company news: what is being written about each holding, and where it sits.

Two kinds of thing count as news about a company, and this module carries both
because they answer different questions.

  Articles    What the press is reporting -- an analyst's first question when a
              holding moves is "what happened?", and the answer usually reaches
              them as a headline rather than as a document. This is the
              default view.

  Filings     What the company was legally required to disclose. A filing is
              the event; an article is somebody's account of it. Kept, behind a
              disclosure on the same page, because when a figure is questioned
              the answer has to be traceable to the document.

Where the articles come from
----------------------------

  Google News   A search feed per company. The widest coverage by far, and the
                only one that reaches the Saudi listings: Al Rajhi Bank returns
                53 items here and one on Yahoo. Links resolve through Google to
                the publisher.

  Yahoo Finance The ticker's own news list. Fewer items, but they arrive with
                the publisher named and a direct link to the article, so where
                both feeds carry a story the Yahoo copy is preferred.

Both are aggregators, not publishers, and the app says so: every row names the
outlet that actually wrote the piece, and none of them is marked official the
way an exchange filing is.

Relevance is the hard part
--------------------------

A search for a company returns a great deal that merely mentions it. "European
Indexes Rise as Banks, AI Stocks Recover" is not ASML news even though ASML
moved it. Every item is therefore checked against the company's own name before
it is kept, matched on word stems so that "Sun Pharma" finds an article about
"Sun Pharmaceutical Industries".

Categories come from the headline
---------------------------------

With filings the regulator had already classified the event -- an SEC 8-K item
code, an NSE subject field -- and the mapping only had to not lose it. An
article carries no such label, so its category is matched from wording in the
headline and is marked as inferred. Where nothing matches it is filed under
other disclosures rather than guessed at.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path

from .paths import NEWS_DIR

#: SEC asks automated callers to identify themselves with a real contact. A
#: request without this is refused, and rightly.
SEC_UA = "Preferred Square Analytics benchmark-pulse aditya@preferredsquare.com"

_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

#: How long a cached feed stays fresh. Filings appear through the day, so an
#: hour keeps the view current without hammering a public regulator.
CACHE_MINUTES = 60


class Category(str, Enum):
    """The six categories, in the order an analyst reads them."""

    FINANCIAL = "A. Financial performance"
    CAPITAL = "B. Capital markets activity"
    CORPORATE = "C. Corporate actions"
    LEADERSHIP = "D. Leadership and governance"
    CREDIT = "E. Credit and liquidity"
    LEGAL = "F. Legal and regulatory"
    OTHER = "G. Other"


#: Display order, highest priority first.
ORDER = [Category.FINANCIAL, Category.CAPITAL, Category.CORPORATE,
         Category.LEADERSHIP, Category.CREDIT, Category.LEGAL, Category.OTHER]


@dataclass
class NewsItem:
    """One disclosure, with everything needed to check it."""

    when: str                     # ISO date
    headline: str
    category: str                 # a Category value
    subject: str                  # the exchange's own label, verbatim
    source: str                   # who published it
    url: str                      # the document itself
    form: str = ""                # filing type, where there is one
    official: bool = True         # a regulator or exchange, not an aggregator
    classified_by: str = "exchange"   # "exchange" | "form" | "wording"

    @property
    def day(self) -> date:
        try:
            return date.fromisoformat(self.when)
        except ValueError:
            return date.min

    # Derived from the headline rather than stored, so a feed already sitting
    # in the cache gains highlights without being refetched and without a
    # migration. The rules change more often than the coverage does.

    @property
    def signal(self) -> str:
        """What makes this material, or "" for the ordinary run of coverage."""
        return highlight(self.headline)[0]

    @property
    def tone(self) -> str:
        """good | bad | neutral, and "" when the item is not material."""
        return highlight(self.headline)[1]

    @property
    def material(self) -> bool:
        return bool(self.signal)


@dataclass
class NewsFeed:
    """Everything known about one holding's disclosures."""

    asset_id: str
    name: str
    items: list[NewsItem] = field(default_factory=list)
    source_label: str = ""
    note: str = ""                # why a feed is empty, when it is
    covered: bool = True          # is a primary feed available at all

    def by_category(self) -> dict[str, list[NewsItem]]:
        out: dict[str, list[NewsItem]] = {}
        for item in sorted(self.items, key=lambda i: i.when, reverse=True):
            out.setdefault(item.category, []).append(item)
        return {c.value: out[c.value] for c in ORDER if c.value in out}

    def highlights(self, limit: int = 6) -> list[NewsItem]:
        """The items that would change the investment case, newest first.

        Not a separate feed: every one of these also appears under its own
        category below, with the same link. This is the same coverage read in
        priority order, which is the order somebody with four minutes wants it.

        Ties on the same day break towards bad news. A reader skimming five
        rows should meet the lawsuit before the press release.

        At most two per signal. A running buyback generates a story a day --
        six of ING's ten most recent items were the same programme -- and one
        event repeated six times is not six highlights, it is one highlight
        crowding out the rest of the page.
        """
        picked = [i for i in self.items if i.material]
        picked.sort(key=lambda i: (i.when, -TONE_RANK.get(i.tone, 1)),
                    reverse=True)

        seen: dict[str, int] = {}
        out: list[NewsItem] = []
        for row in picked:
            if seen.get(row.signal, 0) >= 2:
                continue
            seen[row.signal] = seen.get(row.signal, 0) + 1
            out.append(row)
            if len(out) >= limit:
                break
        return out


# -- SEC 8-K item codes -----------------------------------------------------
# Straight from the SEC's own form instructions. The comment is the item's
# official title, so a reader can check the mapping against the regulation
# rather than against an opinion.

_ITEM_CATEGORY: dict[str, Category] = {
    "1.01": Category.CORPORATE,    # Entry into a Material Definitive Agreement
    "1.02": Category.CORPORATE,    # Termination of a Material Agreement
    "1.03": Category.CREDIT,       # Bankruptcy or Receivership
    "1.05": Category.LEGAL,        # Material Cybersecurity Incidents
    "2.01": Category.CORPORATE,    # Completion of Acquisition or Disposition
    "2.02": Category.FINANCIAL,    # Results of Operations and Financial Condition
    "2.03": Category.CREDIT,       # Creation of a Direct Financial Obligation
    "2.04": Category.CREDIT,       # Triggering Events That Accelerate an Obligation
    "2.05": Category.CORPORATE,    # Costs Associated with Exit or Disposal
    "2.06": Category.CREDIT,       # Material Impairments
    "3.01": Category.CAPITAL,      # Notice of Delisting or Listing Deficiency
    "3.02": Category.CAPITAL,      # Unregistered Sales of Equity Securities
    "3.03": Category.CAPITAL,      # Material Modification to Rights of Holders
    "4.01": Category.LEADERSHIP,   # Changes in Certifying Accountant (auditor)
    "4.02": Category.FINANCIAL,    # Non-Reliance on Previously Issued Financials
    "5.01": Category.LEADERSHIP,   # Changes in Control of Registrant
    "5.02": Category.LEADERSHIP,   # Departure or Election of Directors/Officers
    "5.03": Category.LEADERSHIP,   # Amendments to Articles or Bylaws
    "5.04": Category.LEADERSHIP,   # Trading Suspension under Employee Plans
    "5.05": Category.LEADERSHIP,   # Amendment to Code of Ethics
    "5.07": Category.LEADERSHIP,   # Submission of Matters to a Vote
    "5.08": Category.LEADERSHIP,   # Shareholder Director Nominations
    "7.01": Category.OTHER,        # Regulation FD Disclosure
    "8.01": Category.OTHER,        # Other Events
    "9.01": Category.OTHER,        # Financial Statements and Exhibits
}

#: Item titles, so the app can name what a code means without a lookup table of
#: its own.
_ITEM_TITLE: dict[str, str] = {
    "1.01": "Entry into a material definitive agreement",
    "1.02": "Termination of a material definitive agreement",
    "1.03": "Bankruptcy or receivership",
    "1.05": "Material cybersecurity incident",
    "2.01": "Completion of acquisition or disposition of assets",
    "2.02": "Results of operations and financial condition",
    "2.03": "Creation of a direct financial obligation",
    "2.04": "Triggering events that accelerate an obligation",
    "2.05": "Costs associated with exit or disposal activities",
    "2.06": "Material impairments",
    "3.01": "Notice of delisting or failure to satisfy a listing rule",
    "3.02": "Unregistered sales of equity securities",
    "3.03": "Material modification to rights of security holders",
    "4.01": "Changes in the registrant's certifying accountant",
    "4.02": "Non-reliance on previously issued financial statements",
    "5.01": "Changes in control of registrant",
    "5.02": "Departure or election of directors and officers",
    "5.03": "Amendments to articles of incorporation or bylaws",
    "5.04": "Temporary suspension of trading under employee plans",
    "5.05": "Amendment to the code of ethics",
    "5.07": "Submission of matters to a vote of security holders",
    "5.08": "Shareholder director nominations",
    "7.01": "Regulation FD disclosure",
    "8.01": "Other events",
    "9.01": "Financial statements and exhibits",
}

#: Periodic reports, which are financial performance by definition.
_FORM_CATEGORY: dict[str, Category] = {
    "10-K": Category.FINANCIAL, "10-K/A": Category.FINANCIAL,
    "10-Q": Category.FINANCIAL, "10-Q/A": Category.FINANCIAL,
    "20-F": Category.FINANCIAL, "20-F/A": Category.FINANCIAL,
    "40-F": Category.FINANCIAL, "11-K": Category.FINANCIAL,
    "DEF 14A": Category.LEADERSHIP, "DEFA14A": Category.LEADERSHIP,
    "DEFM14A": Category.CORPORATE, "PRE 14A": Category.LEADERSHIP,
    "SC 13D": Category.CORPORATE, "SC 13D/A": Category.CORPORATE,
    "SC TO-I": Category.CAPITAL, "SC TO-T": Category.CORPORATE,
    "S-4": Category.CORPORATE, "S-1": Category.CAPITAL,
    "S-3ASR": Category.CAPITAL, "S-8": Category.CAPITAL,
}

_FORM_TITLE: dict[str, str] = {
    "10-K": "Annual report on Form 10-K",
    "10-Q": "Quarterly report on Form 10-Q",
    "20-F": "Annual report on Form 20-F",
    "40-F": "Annual report on Form 40-F",
    "11-K": "Annual report of an employee stock plan",
    "DEF 14A": "Definitive proxy statement",
    "DEFM14A": "Definitive merger proxy statement",
    "S-4": "Registration statement for a business combination",
    "S-1": "Registration statement",
    "S-3ASR": "Automatic shelf registration statement",
    "S-8": "Registration of securities for an employee plan",
    "SC 13D": "Acquisition of beneficial ownership above 5%",
    "SC TO-I": "Issuer tender offer",
}

#: Forms that are filings but not news. An unfiltered feed is dominated by
#: these: they are routine, high-volume, and about somebody other than the
#: company -- an insider's own trade, a fund's quarterly holdings disclosure, a
#: structured-note prospectus supplement issued several times a day.
_NOISE_FORMS = {
    "3", "3/A", "4", "4/A", "5", "5/A",            # insider transactions
    "144",                                          # notice of proposed sale
    "13F-HR", "13F-HR/A", "13F-NT",                 # institutional holdings
    "SC 13G", "SC 13G/A",                           # passive stakes
    "FWP", "424B1", "424B2", "424B3", "424B4",      # note prospectuses
    "424B5", "424B7", "424B8", "424A", "425",
    "UPLOAD", "CORRESP",                            # SEC correspondence
    "S-8 POS", "POS AM", "POSASR", "EFFECT", "RW",
    "PX14A6G", "PX14A6N",                           # exempt solicitations
    "ARS", "NT 10-K", "NT 10-Q", "CERT", "8-A12B",
    "SD", "SE", "MA-I", "ABS-15G", "X-17A-5",
}

#: Words to look for, in a filing's own title or in a press headline. Ordered:
#: the first match wins, so the most specific terms come first.
#:
#: Tuned for both vocabularies. A filing says "Intimation of Credit Rating";
#: an article says "Moody's cuts outlook". A filing says "Departure of Certain
#: Officers"; an article says "CEO steps down". Matching only the first
#: vocabulary filed nine headlines in ten under Other.
_WORD_CATEGORY: list[tuple[tuple[str, ...], Category]] = [
    # Credit. Deliberately narrow: an "upgrade" or "downgrade" in a headline is
    # almost always an analyst changing a recommendation, which is not a credit
    # event. Only the rating agencies and debt language count here.
    (("credit rating", "rating action", "outlook revised", "covenant",
      "liquidity", "default", "moody", "fitch", "s&p global ratings",
      "crisil", "icra", "care ratings", "refinanc", "bond issue",
      "debt restructur", "creditworth"), Category.CREDIT),
    # Legal. "SEBI" is deliberately absent: every Indian disclosure cites a
    # SEBI regulation, so matching it filed routine notices under Legal.
    (("lawsuit", "litigation", "class action", "investigation", "antitrust",
      "competition authority", "tax dispute", "penalty", "fine imposed",
      "show cause", "regulatory action", "enforcement", "settlement with",
      "consent order", "adjudicat", "tribunal", "court", "sued", "sues",
      "probe", "fined", "regulator", "watchdog", "compliance breach",
      "subpoena", "injunction"), Category.LEGAL),
    # Leadership and governance.
    (("resignation", "resigns", "steps down", "appointment", "appoints",
      "names new", "cessation", "retires", "retirement of", "succession",
      "board of directors", "managing director", "chief executive",
      "chief financial", "ceo", "cfo", "chairman", "auditor",
      "director change", "reconstitution", "governance", "postal ballot",
      "annual general meeting", "egm", "shareholders meeting",
      "voting results", "boardroom"), Category.LEADERSHIP),
    # Corporate actions.
    # "to buy" is deliberately absent. It matched "Best Stocks to Buy" and
    # "Is Apple a Good Stock To Buy Now?", filing broker opinion pieces as
    # corporate acquisitions -- a wrong label on a confident-looking row.
    (("acquisition", "acquires", "acquire ", "merger", "merges",
      "amalgamation", "divest", "demerger", "spin-off", "spin off",
      "joint venture", "stake sale", "stake acquisition", "slump sale",
      "scheme of arrangement", "incorporation of", "subsidiary",
      "asset purchase", "takeover", "buys stake", "acquisition of"),
     Category.CORPORATE),
    # Capital markets.
    (("dividend", "buyback", "buy-back", "rights issue", "bonus issue",
      "share repurchase", "repurchase program", "offering of", "placement",
      "debentures", "commercial paper", "notes due", "capital raise",
      "capital increase", "allotment", "redemption", "qip",
      "preferential issue", "fund raising", "stock split", "share sale",
      "secondary offering", "follow-on"), Category.CAPITAL),
    # Financial performance, last because its words are the most common and
    # would otherwise swallow headlines that are really about something else.
    (("financial result", "quarterly result", "annual result", "earnings",
      "net sales", "revenue", "guidance", "outlook for", "profit",
      "margin", "order backlog", "bookings", "trading update",
      "integrated report", "annual report", "beats estimate", "misses",
      "beat estimates", "tops estimate", "q1 ", "q2 ", "q3 ", "q4 ",
      "first quarter", "second quarter", "third quarter", "fourth quarter",
      "half-year", "full-year", "forecast", "sales rise", "sales fall",
      "income rose", "income fell"), Category.FINANCIAL),
]

# -- materiality ------------------------------------------------------------
#
# Six categories tell a reader what a story is about. They do not tell them
# which three of ninety items would move a valuation, and a feed where the
# lawsuit sits between two broker notes is a feed nobody reads twice.
#
# So a second, narrower pass looks only for events that change the investment
# case: a chief executive leaving, an order book won, a suit filed, a rating
# cut, a guidance change. Everything else stays where it is.
#
# Deliberately biased towards precision over recall. A missed highlight is
# still in the category below, one click away. A false highlight puts a broker
# opinion piece at the top of the page under a red flag, which is worse: it
# costs the section the credibility that makes it worth having. Every phrase
# here is therefore specific enough that it rarely fires by accident --
# "appoints" alone is not enough, "appoints ceo" is.
#
# (phrases, what it is, which way it cuts). First match wins, so the ordering
# runs from least ambiguous to most.
_SIGNALS: list[tuple[tuple[str, ...], str, str]] = [
    # Legal action against the company. Unambiguous and rarely routine.
    (("class action", "lawsuit", "files suit", "sues ", " sued",
      "securities fraud", "accounting fraud", "indicted", "raid on",
      "search and seizure", "show cause notice", "injunction against",
      "subpoena", "antitrust case", "cartel", "criminal charge"),
     "Legal action", "bad"),
    # Enforcement with a number attached.
    (("penalty of", "fined", "imposes penalty", "monetary penalty",
      "disgorgement", "enforcement action", "consent order"),
     "Penalty", "bad"),
    # Senior departures and arrivals. Neutral: a chief executive leaving is
    # material whichever way it eventually reads.
    (("ceo resign", "ceo steps down", "ceo to step down", "chief executive resign",
      "chief executive steps down", "cfo resign", "cfo steps down",
      "chief financial officer resign", "managing director resign",
      "chairman resign", "chairman steps down", "appoints ceo",
      "appoints new ceo", "names ceo", "names new ceo", "appointed ceo",
      "new chief executive", "appoints chief executive", "appoints cfo",
      "appoints managing director", "succeeds as ceo", "top-level exit",
      "leadership rejig", "board overhaul"),
     "Leadership change", "neutral"),
    # Order books and contract wins -- the user's own example, and the one
    # that moves an industrial or an IT services name most.
    (("wins order", "bags order", "secures order", "receives order",
      "order win", "order worth", "order book", "order inflow",
      "wins contract", "bags contract", "secures contract",
      "awarded contract", "contract worth", "wins deal worth",
      "largest ever order", "mega deal", "multi-year deal"),
     "Order win", "good"),
    # Credit, both directions. The agency has to be named, or the wording has
    # to be unmistakably about debt. A bare "Rating Downgrade" in a press
    # headline is almost always a blogger or a broker changing a
    # recommendation: Seeking Alpha files "ING Groep: Executing Well, But No
    # Longer Undervalued (Rating Downgrade)", which is an opinion, not a credit
    # event. Calling that one in front of a committee is the kind of error this
    # section cannot afford.
    (("moody's downgrade", "moody's cuts", "moody's lowers",
      "fitch downgrade", "fitch cuts", "s&p downgrade", "s&p cuts",
      "crisil downgrade", "icra downgrade", "care ratings downgrade",
      "downgraded by moody", "downgraded by fitch", "downgraded by s&p",
      "credit rating downgrade", "outlook revised to negative",
      "placed on watch negative", "defaults on", "debt restructuring",
      "misses debt payment", "bankruptcy", "chapter 11", "insolvency",
      "covenant breach"),
     "Credit event", "bad"),
    (("moody's upgrade", "fitch upgrade", "s&p upgrade",
      "upgraded by moody", "upgraded by fitch", "credit rating upgrade",
      "raised to investment grade", "upgraded to investment grade",
      "outlook revised to positive"), "Credit upgrade", "good"),
    # Earnings that surprised, in either direction. Not results per se --
    # a quarterly filing is a scheduled event and belongs in its category.
    (("cuts guidance", "lowers guidance", "guidance cut", "profit warning",
      "warns on profit", "misses estimate", "missed estimates",
      "profit slumps", "profit plunges", "profit falls", "loss widens",
      "posts loss", "swings to loss", "sales decline"),
     "Earnings miss", "bad"),
    (("beats estimate", "beat estimates", "tops estimate", "record profit",
      "record revenue", "profit jumps", "profit surges", "profit doubles",
      "raises guidance", "lifts guidance", "raises outlook",
      "upgrades forecast"), "Earnings beat", "good"),
    # Deals. Neutral: the market decides whether a price was a good one.
    (("to acquire", "acquires ", "acquisition of", "merger with", "to merge",
      "takeover bid", "open offer", "buys stake in", "sells stake in",
      "stake sale", "divests", "demerger", "spin-off", "spin off",
      "joint venture with"), "Deal", "neutral"),
    # Capital returned to shareholders.
    (("buyback", "buy-back", "share repurchase", "special dividend",
      "raises dividend", "dividend hike", "increases dividend"),
     "Capital return", "good"),
    # Regulatory outcomes with real consequences. "approval" alone is far too
    # common in routine disclosure, so the phrasing has to be specific.
    (("product recall", "recalls ", "licence revoked", "license revoked",
      "banned by", "suspends production", "halts production",
      "plant shutdown", "import alert", "warning letter"),
     "Regulatory setback", "bad"),
    (("receives approval", "wins approval", "gets approval", "approves drug",
      "receives clearance", "regulatory nod", "gets nod for"),
     "Regulatory approval", "good"),
]

#: How a highlight is ordered against the others. Bad news first when two fall
#: on the same day: a reader skimming five rows should meet the problem before
#: the press release.
TONE_RANK = {"bad": 0, "neutral": 1, "good": 2}


def _bounded(phrase: str) -> str:
    """A phrase that has to match whole words.

    Plain substring matching is what the category classifier uses and it is
    wrong here, because these phrases are short. "sues" is inside "issues", so
    "Drugmaker issues product recall" came back as a lawsuit. "fined" is inside
    "refined", which every energy headline carries, and "sued" is inside
    "pursued". A word boundary kills the whole class.

    The boundary goes on the front only. Every false positive here comes from a
    prefix glued on -- is|sues, re|fined, pur|sued -- while the endings have to
    stay open, because the phrases are written as stems and the press writes
    "resigns", "resigned" and "resignation" for the same event.
    """
    stem = phrase.strip()
    escaped = re.escape(stem)
    return (r"\b" + escaped) if stem[:1].isalnum() else escaped


_SIGNAL_RULES: list[tuple[re.Pattern, str, str]] = [
    (re.compile("|".join(_bounded(p) for p in phrases), re.IGNORECASE),
     label, tone)
    for phrases, label, tone in _SIGNALS
]


#: Headlines that carry a material-looking phrase and are not about the company
#: at all. Checked before the signals, because they would otherwise pass.
#:
#: The first group is the one that floods a US feed: aggregators turn every
#: quarterly 13F into "EP Wealth Advisors LLC Acquires 66,424 Shares of
#: JPMorgan Chase". Somebody bought the stock, which is not the company doing a
#: deal. The second is opinion packaging -- listicles and head-to-heads -- which
#: is commentary about a company rather than an event at one.
_NOT_MATERIAL = re.compile(
    r"(\b(?:acquires|buys|sells|purchases|adds|trims|boosts|cuts|lowers|"
    r"raises|reduces|increases|offloads)\s+[\d,\.]+\s+(?:thousand\s+|million\s+)?"
    r"shares\b)"
    r"|(\b(?:takes|builds|boosts|trims|lifts|reduces|grows|acquires)\s+"
    r"(?:a\s+)?(?:new\s+|large\s+)?(?:position|stake|holdings?)\b)"
    r"|(\bshares?\s+(?:of|in)\b.{0,60}\b(?:acquired|sold|purchased|bought)\s+by\b)"
    r"|(\bhas\s+\$[\d\.,]+\s+(?:million|billion)\s+(?:position|stake|holdings?)\b)"
    r"|(\b(?:13f|form\s+13f|institutional\s+(?:investor|ownership)|"
    r"stake\s+(?:raised|trimmed)\s+by)\b)"
    r"|(\bone of the \d+\b)|(\b\d+\s+(?:best|top|cheap|high-yield)\b)"
    r"|(\b(?:best|top)\s+\d*\s*stocks?\b)|(\bstocks?\s+to\s+(?:buy|watch|avoid)\b)"
    r"|(\bshould you (?:buy|sell|hold)\b)|(\s+vs\.?\s+)",
    re.IGNORECASE)


def highlight(headline: str) -> tuple[str, str]:
    """What makes an item material, and which way it cuts.

    Returns ("", "") for the ordinary run of coverage, which is most of it.
    """
    text = (headline or "").strip()
    if not text or _MACHINE_NOISE.search(text) or _NOT_MATERIAL.search(text):
        return "", ""
    for pattern, label, tone in _SIGNAL_RULES:
        if pattern.search(text):
            return label, tone
    return "", ""


#: Headlines that are machine-written price tickers rather than reporting.
#: "XOM|Exxon Mobil Corp|Price:169.320|Chg%:+4.240" is a quote, not news.
_MACHINE_NOISE = re.compile(
    r"(\|\s*price\s*:)|(\bchg%)|(opened (up|down) by )|"
    r"(shares? (rose|fell) \d)|(^\S+\s*\|\s*\S)", re.IGNORECASE)


def classify(subject: str) -> Category:
    """Map an exchange subject or document title onto a category."""
    text = (subject or "").lower()
    for words, category in _WORD_CATEGORY:
        if any(w in text for w in words):
            return category
    return Category.OTHER


# -- plumbing ---------------------------------------------------------------

def _fetch(url: str, ua: str, timeout: int = 25) -> bytes:
    request = urllib.request.Request(url, headers={
        "User-Agent": ua,
        "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _cache_path(key: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:80]
    return NEWS_DIR / f"{safe}.json"


def _cached(key: str, minutes: int = CACHE_MINUTES) -> dict | None:
    path = _cache_path(key)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    age = time.time() - payload.get("stamp", 0)
    if age > minutes * 60:
        return None
    return payload.get("data")


def _store(key: str, data) -> None:
    try:
        NEWS_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(key).write_text(
            json.dumps({"stamp": time.time(), "data": data}), encoding="utf-8")
    except OSError:
        pass          # a cache that cannot be written must not break a report


def _stale(key: str) -> dict | None:
    """Any cached copy, however old. Used when the network is unavailable."""
    try:
        return json.loads(
            _cache_path(key).read_text(encoding="utf-8")).get("data")
    except (OSError, json.JSONDecodeError):
        return None


# -- SEC EDGAR --------------------------------------------------------------

_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"

#: US lines for holdings whose ordinary listing is elsewhere. A foreign issuer
#: files with the SEC under its ADR ticker, not its home one.
US_LINE = {
    "ICICIBANK.NS": "IBN",
    "INFY.NS": "INFY",
    "HDFCBANK.NS": "HDB",
    "TCS.NS": None,               # no US listing
    "SUNPHARMA.NS": None,
    "RELIANCE.NS": None,
    "HINDUNILVR.NS": None,
}


def sec_cik(ticker: str) -> int | None:
    """The SEC's own identifier for a ticker, or None if it does not file."""
    cached = _cached("sec_ticker_map", minutes=60 * 24 * 7)
    if cached is None:
        try:
            raw = json.loads(_fetch(_TICKER_MAP_URL, SEC_UA))
        except Exception:  # noqa: BLE001
            cached = _stale("sec_ticker_map")
            if cached is None:
                return None
        else:
            cached = {v["ticker"].upper(): v["cik_str"] for v in raw.values()}
            _store("sec_ticker_map", cached)
    return cached.get(ticker.upper())


def _exhibit_title(cik: int, accession: str, document: str) -> str:
    """The headline inside a 6-K, which its cover page does not carry.

    A 6-K is a wrapper: the cover is boilerplate and the substance is an
    exhibit, whose description is the company's own press-release title --
    "ASML reports EUR 9.3 billion total net sales". That line is the headline.
    """
    url = (f"https://www.sec.gov/Archives/edgar/data/{cik}/"
           f"{accession.replace('-', '')}/{document}")
    key = f"exh_{cik}_{accession}"
    hit = _cached(key, minutes=60 * 24 * 30)
    if hit is not None:
        return hit.get("title", "")

    title = ""
    try:
        html = _fetch(url, SEC_UA, timeout=20).decode("utf-8", "replace")
        body = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
        body = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</h\d>|</td>", "\n", body)
        body = re.sub(r"<[^>]+>", " ", body)
        body = (body.replace("&nbsp;", " ").replace("&#160;", " ")
                    .replace("&amp;", "&").replace("&#8220;", '"')
                    .replace("&#8221;", '"').replace("&#8364;", "EUR ")
                    .replace("&rsquo;", "'").replace("&#39;", "'"))
        for line in body.split("\n"):
            line = re.sub(r"\s+", " ", line).strip()
            # An exhibit line carries a real exhibit number -- 99.1, 4.2 -- and
            # then the press release's own title. Requiring the number is what
            # separates it from the filename and the cover boilerplate, both of
            # which a looser pattern happily matched.
            match = re.match(
                r'^\d{1,2}\.\d{1,2}\s*[—\-:]?\s*["“]?(.{15,250})$',
                line)
            if not match:
                continue
            candidate = match.group(1).strip().strip('"“”')
            low = candidate.lower()
            if any(skip in low for skip in (
                    "securities and exchange commission", "washington",
                    "report of foreign private issuer", "pursuant to rule",
                    "indicate by check mark", "commission file number",
                    "address of principal", "incorporated by reference",
                    "registration statement", "form 20-f", "form 6-k",
                    "exhibit", "signature", "table of content", ".htm")):
                continue
            # Needs real words, not a reference like "101.INS XBRL instance".
            if len(re.findall(r"[A-Za-z]{3,}", candidate)) < 4:
                continue
            title = candidate
            break
    except Exception:  # noqa: BLE001
        title = ""
    _store(key, {"title": title})
    return title


def sec_items(ticker: str, name: str, limit: int = 40,
              deep: bool = True) -> list[NewsItem]:
    """Recent disclosures for one SEC registrant."""
    cik = sec_cik(ticker)
    if cik is None:
        return []

    key = f"sec_{cik}"
    payload = _cached(key)
    if payload is None:
        try:
            payload = json.loads(_fetch(
                f"https://data.sec.gov/submissions/CIK{cik:010d}.json", SEC_UA))
            _store(key, payload)
        except Exception:  # noqa: BLE001
            payload = _stale(key)
            if payload is None:
                return []

    recent = payload.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    items: list[NewsItem] = []

    for i, form in enumerate(forms):
        if len(items) >= limit:
            break
        if form in _NOISE_FORMS:
            continue

        when = recent["filingDate"][i]
        accession = recent["accessionNumber"][i]
        document = recent.get("primaryDocument", [""] * len(forms))[i]
        codes = [c.strip() for c in (recent.get("items", [""] * len(forms))[i]
                                     or "").split(",") if c.strip()]
        url = (f"https://www.sec.gov/Archives/edgar/data/{cik}/"
               f"{accession.replace('-', '')}/{document}") if document else (
              f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
              f"&CIK={cik:010d}&type={form}")

        if form.startswith("8-K") and codes:
            # The regulator's own classification. 9.01 is an attachment note
            # rather than an event, so it never decides the category alone.
            meaningful = [c for c in codes if c != "9.01"] or codes
            lead = meaningful[0]
            category = _ITEM_CATEGORY.get(lead, Category.OTHER)
            headline = _ITEM_TITLE.get(lead, f"Current report, item {lead}")
            subject = ", ".join(f"Item {c}" for c in meaningful)
            how = "exchange"
        elif form in _FORM_CATEGORY:
            category = _FORM_CATEGORY[form]
            headline = _FORM_TITLE.get(form, f"Filing on Form {form}")
            subject = f"Form {form}"
            how = "form"
        elif form.startswith("6-K"):
            title = _exhibit_title(cik, accession, document) if deep else ""
            headline = title or "Report of a foreign private issuer (Form 6-K)"
            category = classify(title) if title else Category.OTHER
            subject = "Form 6-K"
            how = "wording" if title else "form"
        else:
            continue

        items.append(NewsItem(
            when=when, headline=headline, category=category.value,
            subject=subject, source="SEC EDGAR", url=url, form=form,
            official=True, classified_by=how,
        ))
    return items


# -- NSE India --------------------------------------------------------------

_NSE_URL = ("https://www.nseindia.com/api/corporate-announcements"
            "?index=equities&symbol={symbol}")

#: NSE publishes a subject on every announcement. Mapped straight across.
_NSE_SUBJECT: list[tuple[str, Category]] = [
    ("financial result", Category.FINANCIAL),
    ("credit rating", Category.CREDIT),
    ("dividend", Category.CAPITAL),
    ("buyback", Category.CAPITAL),
    ("raising of funds", Category.CAPITAL),
    ("alteration of capital", Category.CAPITAL),
    ("acquisition", Category.CORPORATE),
    ("amalgamation", Category.CORPORATE),
    ("scheme of arrangement", Category.CORPORATE),
    ("change in directors", Category.LEADERSHIP),
    ("change in management", Category.LEADERSHIP),
    ("board meeting", Category.LEADERSHIP),
    ("shareholders meeting", Category.LEADERSHIP),
    ("postal ballot", Category.LEADERSHIP),
    ("voting results", Category.LEADERSHIP),
    ("resignation", Category.LEADERSHIP),
    ("litigation", Category.LEGAL),
    ("action initiated", Category.LEGAL),
    ("regulatory", Category.LEGAL),
]


def nse_items(symbol: str, limit: int = 40) -> list[NewsItem]:
    """Corporate announcements for one NSE listing."""
    key = f"nse_{symbol}"
    rows = _cached(key)
    if rows is None:
        try:
            rows = json.loads(_fetch(_NSE_URL.format(symbol=symbol),
                                     _BROWSER_UA))
            _store(key, rows)
        except Exception:  # noqa: BLE001
            rows = _stale(key)
            if rows is None:
                return []

    items: list[NewsItem] = []
    for row in rows[:limit * 2]:
        if len(items) >= limit:
            break
        raw_date = str(row.get("an_dt") or row.get("sort_date") or "")
        when = _nse_date(raw_date)
        if not when:
            continue
        subject = str(row.get("desc") or "").strip()
        headline = _tidy(str(row.get("attchmntText") or "").strip() or subject)
        if not headline:
            continue

        category = Category.OTHER
        how = "exchange"
        low = subject.lower()
        for needle, mapped in _NSE_SUBJECT:
            if needle in low:
                category = mapped
                break
        else:
            category = classify(f"{subject} {headline}")
            how = "wording"

        items.append(NewsItem(
            when=when, headline=headline[:300], category=category.value,
            subject=subject or "Corporate announcement",
            source="NSE India", url=str(row.get("attchmntFile") or "").strip(),
            form="", official=True, classified_by=how,
        ))
    return items


#: Every NSE announcement opens with the same clause. Left in, it eats the
#: readable width of the column and pushes the actual subject out of view --
#: and it sits in front of the words the classifier needs.
_NSE_PREFIX = re.compile(
    r"^.{0,80}?\b(?:has\s+)?informed\s+the\s+exchange\s+"
    r"(?:about|regarding|that|of|on)?\s*", re.IGNORECASE)


def _tidy(headline: str) -> str:
    text = _NSE_PREFIX.sub("", headline).strip().strip("'\"‘’")
    text = re.sub(r"\s+", " ", text)
    if not text:
        return headline.strip()
    return text[0].upper() + text[1:]


def _nse_date(raw: str) -> str:
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return ""


# -- press articles ---------------------------------------------------------

#: Words that name a company's legal wrapper rather than the company. Stripped
#: before matching, so "ING Groep NV" is matched on "groep" and not on "nv".
_SUFFIXES = {
    "inc", "inc.", "corp", "corp.", "corporation", "co", "co.", "company",
    "ltd", "ltd.", "limited", "plc", "nv", "n.v.", "sa", "s.a.", "ag", "as",
    "a/s", "se", "spa", "llc", "lp", "holding", "holdings", "group", "the",
    "industries", "inds", "international",
}


def _stems(name: str) -> list[str]:
    """Match keys for a company name.

    Truncated to five characters so a headline that shortens the name still
    matches: "Sun Pharma" against "Sun Pharmaceutical Industries", "Aramco"
    against "Saudi Aramco". Words shorter than four characters are dropped --
    "Sun" or "ING" on its own would match half the internet.
    """
    words = re.findall(r"[A-Za-z]+", (name or "").lower())
    keep = [w for w in words if w not in _SUFFIXES and len(w) >= 4]
    return sorted({w[:5] for w in keep}, key=len, reverse=True)


def _about(title: str, stems: list[str]) -> bool:
    """Is this headline actually about the company?

    A search returns a great deal that merely mentions a name in passing, and a
    market wrap that moved because of a holding is not news about the holding.
    """
    if not stems:
        return True
    tokens = re.findall(r"[a-z]+", (title or "").lower())
    return any(tok.startswith(stem) for tok in tokens for stem in stems)


def _iso(raw) -> str:
    """A date from whatever the feed supplied."""
    if isinstance(raw, (int, float)) and raw > 0:
        return datetime.fromtimestamp(raw, timezone.utc).date().isoformat()
    text = str(raw or "").strip()
    if not text:
        return ""
    # RFC 822, as RSS uses: "Tue, 15 Sep 2026 14:18:00 GMT"
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z",
                "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}" if match else ""


def google_items(name: str, limit: int = 40) -> list[NewsItem]:
    """Press coverage from Google News, the widest free feed."""
    import urllib.parse
    import xml.etree.ElementTree as ET

    key = f"gnews_{name}"
    body = _cached(key)
    if body is None:
        query = urllib.parse.quote(f'"{name}" stock')
        url = (f"https://news.google.com/rss/search?q={query}"
               f"&hl=en-US&gl=US&ceid=US:en")
        try:
            body = _fetch(url, _BROWSER_UA).decode("utf-8", "replace")
            _store(key, body)
        except Exception:  # noqa: BLE001
            body = _stale(key)
            if body is None:
                return []

    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []

    stems = _stems(name)
    items: list[NewsItem] = []
    for entry in root.findall(".//item"):
        if len(items) >= limit:
            break
        title = (entry.findtext("title") or "").strip()
        if not title or not _about(title, stems) or _MACHINE_NOISE.search(title):
            continue
        # Google appends " - Publisher" to every headline; the outlet is also
        # in its own element, so the suffix is redundant noise in the column.
        source = entry.find("{*}source")
        publisher = (source.text if source is not None
                     else entry.findtext("source") or "").strip()
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -(len(publisher) + 3)].strip()
        items.append(NewsItem(
            when=_iso(entry.findtext("pubDate")),
            headline=title[:300],
            category=classify(title).value,
            subject="Press coverage",
            source=publisher or "Google News",
            url=(entry.findtext("link") or "").strip(),
            form="", official=False, classified_by="wording",
        ))
    return items


def yahoo_items(ticker: str, name: str, limit: int = 20) -> list[NewsItem]:
    """The ticker's own news list, which links straight to the publisher."""
    key = f"yahoo_{ticker}"
    raw = _cached(key)
    if raw is None:
        try:
            import yfinance as yf
            raw = []
            for entry in (yf.Ticker(ticker).news or []):
                content = entry.get("content") or entry
                provider = content.get("provider")
                url = content.get("canonicalUrl") or content.get("clickThroughUrl")
                raw.append({
                    "title": content.get("title") or entry.get("title") or "",
                    "when": (content.get("pubDate")
                             or entry.get("providerPublishTime") or ""),
                    "publisher": (provider.get("displayName")
                                  if isinstance(provider, dict)
                                  else entry.get("publisher") or ""),
                    "url": (url.get("url") if isinstance(url, dict)
                            else entry.get("link") or ""),
                })
            _store(key, raw)
        except Exception:  # noqa: BLE001
            raw = _stale(key)
            if raw is None:
                return []

    stems = _stems(name)
    items: list[NewsItem] = []
    for entry in raw[:limit * 2]:
        if len(items) >= limit:
            break
        title = str(entry.get("title") or "").strip()
        if not title or not _about(title, stems) or _MACHINE_NOISE.search(title):
            continue
        items.append(NewsItem(
            when=_iso(entry.get("when")),
            headline=title[:300],
            category=classify(title).value,
            subject="Press coverage",
            source=str(entry.get("publisher") or "Yahoo Finance"),
            url=str(entry.get("url") or ""),
            form="", official=False, classified_by="wording",
        ))
    return items


def _merge(*groups: list[NewsItem]) -> list[NewsItem]:
    """One row per story.

    The same piece reaches both feeds, so items are keyed on a squashed
    headline. The first group wins, which is why Yahoo is passed first: its
    links go straight to the publisher rather than through a redirect.
    """
    seen: dict[str, NewsItem] = {}
    for group in groups:
        for item in group:
            key = re.sub(r"[^a-z0-9]", "", item.headline.lower())[:70]
            if key and key not in seen:
                seen[key] = item
    return sorted(seen.values(), key=lambda i: i.when, reverse=True)


def articles_for(asset_id: str, name: str, limit: int = 50) -> NewsFeed:
    """Press coverage for one holding, from every feed that carries it."""
    yahoo = yahoo_items(asset_id, name)
    google = google_items(name, limit=limit)
    items = _merge(yahoo, google)[:limit]

    sources = sorted({i.source for i in items})
    label = "Google News and Yahoo Finance" if yahoo and google else (
        "Yahoo Finance" if yahoo else "Google News" if google else
        "none available")
    return NewsFeed(
        asset_id=asset_id, name=name, items=items, source_label=label,
        note="" if items else
        "No coverage was returned for this company by either feed.",
        covered=bool(items),
    )


# -- routing ----------------------------------------------------------------

#: Markets with no free primary feed, and why. Stated rather than left as an
#: empty list, because an empty list reads as "nothing happened".
NO_FEED = {
    "SA": ("The Saudi Exchange refuses programmatic access (HTTP 403) and no "
           "free alternative republishes Tadawul issuer announcements. A "
           "licensed Tadawul feed is one adapter away; until then this is "
           "stated rather than filled with press coverage."),
}


def filings_for(asset_id: str, name: str, country: str = "",
                limit: int = 40, deep: bool = True) -> NewsFeed:
    """Every regulated disclosure this tool can reach for one holding."""
    ticker = (asset_id or "").upper()
    country = (country or "").upper()

    if ticker.endswith(".NS"):
        symbol = ticker[:-3]
        items = nse_items(symbol, limit=limit)
        us = US_LINE.get(ticker)
        if us:
            items += sec_items(us, name, limit=limit // 2, deep=deep)
        label = "NSE India" + (" and SEC EDGAR" if us else "")
        return NewsFeed(asset_id=asset_id, name=name, items=items,
                        source_label=label,
                        note="" if items else
                        "No announcements returned for this symbol.",
                        covered=True)

    if country in NO_FEED:
        return NewsFeed(asset_id=asset_id, name=name, items=[],
                        source_label="none available",
                        note=NO_FEED[country], covered=False)

    items = sec_items(ticker, name, limit=limit, deep=deep)
    if items:
        return NewsFeed(asset_id=asset_id, name=name, items=items,
                        source_label="SEC EDGAR", covered=True)
    return NewsFeed(
        asset_id=asset_id, name=name, items=[], source_label="none available",
        note=("This line is not an SEC registrant and no free primary feed "
              "covers its home exchange."), covered=False)


#: Kept so older call sites keep resolving.
feed_for = filings_for


def to_dict(feed: NewsFeed) -> dict:
    return {"asset_id": feed.asset_id, "name": feed.name,
            "source_label": feed.source_label, "note": feed.note,
            "covered": feed.covered,
            "items": [asdict(i) for i in feed.items]}
