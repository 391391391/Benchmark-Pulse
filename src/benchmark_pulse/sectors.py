"""Working out what business a company is in, from its ticker.

The sector is not decoration. It picks the benchmark: an Indian bank is measured
against the Nifty Bank index and an Indian pharmaceutical company against the
pharma index, and a holding with no sector falls back to the broad market, where
its industry's fortunes are never isolated. A blank cell in a client statement
therefore costs a level of the analysis, and asking an analyst to fill in eleven
of them by hand is how that level quietly stops being used.

Two sources, in order, because they fail in different places.

  Yahoo Finance   The same feed the prices come from, which matters more than it
                  sounds: the sector and the price then agree about which
                  company this is. Probed against this book it answered for US,
                  Indian, Dutch, Danish and -- the market that defeats most
                  sources -- Saudi listings. It is a third party with no SLA and
                  it occasionally mislabels a conglomerate.

  The model       Only where Yahoo returns nothing usable. A newly listed
                  company, a symbol Yahoo files without a sector. It is a
                  judgement with no citation behind it, so it is used last, it
                  is labelled as such wherever the answer is shown, and it never
                  overrides a source that did answer.

Both are recorded per ticker rather than merged into an anonymous answer. This
tool's whole claim is that a figure can be traced to where it came from, and a
sector that silently decided a benchmark is exactly the kind of input that has
to be traceable. What is written into the book is an ordinary editable value: a
wrong label is visible in the grid and can be typed over.

The SEC's SIC code was considered and rejected as a primary source. It is the
most official option available and it covers only US filers -- three of this
book's eleven holdings -- and returns a 1987 taxonomy ("Electronic Computers"
for Apple) that needs its own crosswalk before it says anything about a sector.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .benchmarks import canonical_sector
from .paths import CACHE_DIR

#: Where an answer, once obtained, stays. A sector does not change month to
#: month, and an offline demo must not have to ask the internet what business
#: Saudi Aramco is in.
SECTOR_CACHE: Path = CACHE_DIR / "sectors.json"

#: The eleven the benchmark universe is built on. The model is held to this
#: list; Yahoo's own names are translated onto it by canonical_sector.
CANONICAL: list[str] = [
    "Technology", "Financials", "Energy", "Health Care", "Materials",
    "Industrials", "Consumer Staples", "Consumer Discretionary", "Utilities",
    "Communication Services", "Real Estate",
]

SOURCE_LABEL = {
    "yahoo": "Yahoo Finance",
    "model": "inferred by model",
    "file": "stated in the file",
}


SOURCE_LABEL["suffix"] = "the exchange suffix on the ticker"

#: Exchange suffix to the ISO-2 country the security is LISTED in. Deterministic
#: and offline, so it is tried before the feed: a ticker ending .SR is on
#: Tadawul whether or not anything answers today.
#:
#: Note .SA is Sao Paulo and Saudi Arabia is .SR -- the two are one keystroke
#: apart and would put a Riyadh bank in Brazil.
SUFFIX_MARKET: dict[str, str] = {
    ".SR": "SA", ".NS": "IN", ".BO": "IN", ".L": "GB", ".T": "JP",
    ".AS": "NL", ".DE": "DE", ".F": "DE", ".PA": "FR", ".CO": "DK",
    ".SW": "CH", ".MI": "IT", ".MC": "ES", ".ST": "SE", ".OL": "NO",
    ".HE": "FI", ".BR": "BE", ".VI": "AT", ".LS": "PT", ".IR": "IE",
    ".TO": "CA", ".V": "CA", ".AX": "AU", ".NZ": "NZ", ".SI": "SG",
    ".HK": "HK", ".KS": "KR", ".KQ": "KR", ".TW": "TW", ".SS": "CN",
    ".SZ": "CN", ".SA": "BR", ".MX": "MX", ".JO": "ZA", ".IS": "TR",
    ".TA": "IL", ".QA": "QA", ".KW": "KW", ".AD": "AE", ".DU": "AE",
}

#: Yahoo's `market` field is an ISO-2 code plus "_market" for every venue
#: probed except Saudi, where it uses the exchange suffix instead.
_MARKET_PREFIX: dict[str, str] = {"sr": "SA"}


@dataclass
class Profile:
    """What a ticker is: its sector, where it is listed, what it trades in.

    One object because one call answers all three -- the feed returns them in
    the same payload -- and because they are wanted in the same places: a row
    added to the book needs every one of them before it can be benchmarked.
    """

    ticker: str
    sector: str = ""        # canonical, or "" when nothing usable came back
    raw: str = ""           # the source's own label, before translation
    industry: str = ""      # finer than sector; shown, never used to benchmark
    source: str = ""        # "yahoo" | "model" | ""
    note: str = ""
    market: str = ""        # ISO-2 of the LISTING venue, not the head office
    market_raw: str = ""    # "NSE", "Saudi", "NasdaqGS"
    market_source: str = ""  # "suffix" | "yahoo" | ""
    currency: str = ""
    currency_note: str = ""

    @property
    def found(self) -> bool:
        return bool(self.sector)

    @property
    def market_found(self) -> bool:
        return bool(self.market)

    @property
    def attribution(self) -> str:
        if not self.found:
            return "not identified"
        label = SOURCE_LABEL.get(self.source, self.source)
        if self.raw and self.raw.lower() != self.sector.lower():
            return f"{label}, which calls it {self.raw}"
        return label

    @property
    def market_attribution(self) -> str:
        # Plain text, no entities. These strings are shown both inside HTML
        # notes and in Streamlit's own status messages, and an &mdash; that
        # renders correctly in one appears verbatim in the other.
        if not self.market_found:
            return "not identified"
        label = SOURCE_LABEL.get(self.market_source, self.market_source)
        if self.market_raw:
            return f"{label}, {self.market_raw}"
        return label


#: Kept so the older name still resolves; Profile is what it is now.
SectorGuess = Profile


# -- cache ------------------------------------------------------------------

def _load_cache() -> dict[str, dict]:
    if not SECTOR_CACHE.exists():
        return {}
    try:
        raw = json.loads(SECTOR_CACHE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_cache(cache: dict[str, dict]) -> None:
    try:
        SECTOR_CACHE.parent.mkdir(parents=True, exist_ok=True)
        SECTOR_CACHE.write_text(json.dumps(cache, indent=2, sort_keys=True),
                                encoding="utf-8")
    except OSError:
        pass          # a cache that cannot be written is slow, not broken


def cached() -> dict[str, SectorGuess]:
    """Every sector looked up so far, for the data-sources screen."""
    out: dict[str, SectorGuess] = {}
    for ticker, payload in _load_cache().items():
        try:
            out[ticker] = SectorGuess(**payload)
        except TypeError:
            continue
    return out


def forget(ticker: str = "") -> None:
    """Drop one cached answer, or all of them."""
    if not ticker:
        SECTOR_CACHE.unlink(missing_ok=True)
        return
    cache = _load_cache()
    if cache.pop(ticker.strip().upper(), None) is not None:
        _save_cache(cache)


# -- sources ----------------------------------------------------------------

def market_from_suffix(ticker: str) -> str:
    """The listing country implied by the ticker's exchange suffix, or "".

    Deliberately returns nothing rather than guessing for a bare symbol. The
    older helper in portfolio.py answers "US" for anything it does not
    recognise, which is right often enough to be dangerous: a Tadawul code
    typed without .SR would be filed as a US listing and benchmarked against
    the S&P.
    """
    text = str(ticker or "").strip().upper()
    if "." not in text:
        return ""
    suffix = text[text.rindex("."):]
    return SUFFIX_MARKET.get(suffix, "")


def _market_from_info(info: dict) -> tuple[str, str]:
    """Listing country and venue name from a Yahoo payload.

    Read from `market`, never from `country`. Yahoo's `country` is the head
    office: Alibaba comes back as China and the Infosys ADR as India, when both
    are New York listings trading in dollars. Benchmarking either against its
    home market would compare a US-listed security to an index it does not
    trade in.
    """
    venue = str(info.get("fullExchangeName") or info.get("exchange") or "").strip()
    market = str(info.get("market") or "").strip().lower()
    if not market.endswith("_market"):
        return "", venue
    prefix = market[: -len("_market")]
    if prefix in _MARKET_PREFIX:
        return _MARKET_PREFIX[prefix], venue
    return (prefix.upper(), venue) if len(prefix) == 2 else ("", venue)


def _currency_from_info(info: dict) -> tuple[str, str]:
    """The currency the security trades in, and why it was refused if it was.

    London quotes in pence and Yahoo signals that with a lowercase 'p' on the
    code -- GBp, not GBP. Writing GBP against a price series denominated in
    pence would overstate the position by a factor of a hundred, so a
    fractional-unit code is reported rather than accepted.
    """
    raw = str(info.get("currency") or "").strip()
    if not raw:
        return "", ""
    if len(raw) == 3 and raw.isalpha() and raw.isupper():
        return raw, ""
    # Deliberately not naming the parent currency: GBp is a hundredth of GBP
    # but ZAc is a hundredth of ZAR, and a rule that guesses the third letter
    # is wrong as often as it is right.
    return "", (f"the feed quotes this in {raw}, a fractional unit (pence or "
                "cents rather than the whole currency), so the currency is "
                "left for you to set")


def from_yahoo(ticker: str) -> Profile:
    """Sector, listing venue and trading currency, from one payload.

    Returns an empty profile rather than raising: a source that cannot answer
    is a normal outcome here, and the caller has another one to try.
    """
    code = ticker.strip()
    if not code:
        return Profile(ticker=code)

    suffix_market = market_from_suffix(code)
    try:
        import yfinance as yf

        info = yf.Ticker(code).info or {}
    except Exception:  # noqa: BLE001 - any failure means "no answer"
        # The suffix still stands: it is a fact about the symbol, not something
        # the feed had to supply.
        return Profile(ticker=code, market=suffix_market,
                       market_source="suffix" if suffix_market else "",
                       note="Yahoo Finance did not respond")

    feed_market, venue = _market_from_info(info)
    market = suffix_market or feed_market
    market_source = "suffix" if suffix_market else ("yahoo" if feed_market else "")
    currency, currency_note = _currency_from_info(info)

    common = {
        "ticker": code, "market": market, "market_raw": venue,
        "market_source": market_source, "currency": currency,
        "currency_note": currency_note,
    }

    raw = str(info.get("sector") or "").strip()
    industry = str(info.get("industry") or "").strip()
    if not raw:
        return Profile(industry=industry,
                       note="Yahoo Finance holds no sector for this symbol",
                       **common)

    sector = canonical_sector(raw)
    if not sector:
        # Worth keeping rather than discarding: an unmapped label is a gap in
        # the alias table, and naming it is how that gets fixed.
        return Profile(raw=raw, industry=industry,
                       note=f"Yahoo Finance says {raw!r}, which does not "
                            "match any sector in the benchmark universe",
                       **common)
    return Profile(sector=sector, raw=raw, industry=industry, source="yahoo",
                   **common)


_SYSTEM = (
    "You classify listed companies into the GICS sector they belong to. You "
    "answer from what the company actually does for revenue, not from its "
    "name. You never invent a company: if the ticker is one you do not "
    "recognise, you say so by returning an empty sector."
)

_SCHEMA = {
    "properties": {
        "sector": "one of the listed sectors, or empty string if unknown",
        "reason": "one short sentence naming what the company does",
    }
}


def from_model(ticker: str, name: str = "", country: str = "",
               client=None) -> Profile:
    """Ask the model, for the symbols the feed has no answer for.

    Deliberately narrow: the model is given a closed list and asked which one
    fits, not asked to describe a taxonomy. It is the last source tried and the
    only one whose answer carries no citation, which is why every screen that
    shows it says where it came from.
    """
    code = ticker.strip()
    if not code:
        return SectorGuess(ticker=code)

    if client is None:
        from .llm import get_client

        client = get_client()
    if not getattr(client, "is_live", False):
        return SectorGuess(ticker=code, note="no language model is configured")

    where = f" listed in {country}" if country else ""
    prompt = (
        f"Which sector does this company belong to?\n\n"
        f"  Ticker:  {code}\n"
        f"  Name:    {name or 'not stated'}{where}\n\n"
        f"Choose exactly one of: {', '.join(CANONICAL)}.\n"
        "Return an empty sector if you do not recognise the company."
    )

    try:
        data = client.structured(prompt, _SCHEMA, system=_SYSTEM, max_tokens=200)
    except Exception:  # noqa: BLE001
        return SectorGuess(ticker=code, note="the model could not be reached")

    raw = str(data.get("sector") or "").strip()
    sector = canonical_sector(raw)
    if not sector:
        return SectorGuess(ticker=code, raw=raw,
                           note="the model did not recognise this company")
    return SectorGuess(ticker=code, sector=sector, raw=raw, source="model",
                       note=str(data.get("reason") or "").strip())


# -- the ladder -------------------------------------------------------------

def identify(ticker: str, name: str = "", country: str = "", *,
             client=None, use_model: bool = True,
             use_cache: bool = True, refresh: bool = False) -> Profile:
    """One ticker's profile: the feed first, the model only for the sector.

    The model is never asked where something is listed. That is a fact the
    ticker itself usually carries and the feed otherwise states outright, and a
    remembered answer is not evidence. Judgement goes to the model; facts do
    not.
    """
    code = ticker.strip().upper()
    if not code:
        return Profile(ticker=ticker)

    cache = _load_cache() if use_cache else {}
    if not refresh and code in cache:
        try:
            return Profile(**cache[code])
        except TypeError:
            pass

    guess = from_yahoo(code)
    if not guess.found and use_model:
        asked = from_model(code, name, country, client=client)
        if asked.found:
            # Only the sector is taken. Everything the feed established about
            # the listing stands -- replacing the whole profile here silently
            # dropped the market and currency Yahoo had already answered.
            guess.sector = asked.sector
            guess.raw = asked.raw
            guess.source = asked.source
            guess.note = asked.note
        elif asked.note:
            # Keep Yahoo's reason for failing and add the model's, so the
            # screen can say which sources were tried and what each said.
            guess.note = f"{guess.note}; {asked.note}" if guess.note else asked.note

    # Cached when anything was learned, not only a sector: a listing resolved
    # without a sector is still worth not asking for twice.
    if use_cache and (guess.found or guess.market_found):
        cache[code] = asdict(guess)
        _save_cache(cache)
    return guess


def identify_many(rows: list[tuple[str, str, str]], *, client=None,
                  use_model: bool = True,
                  refresh: bool = False) -> dict[str, SectorGuess]:
    """Sectors for several holdings, as (ticker, name, country) triples."""
    out: dict[str, SectorGuess] = {}
    for ticker, name, country in rows:
        code = str(ticker).strip().upper()
        if not code or code in out:
            continue
        out[code] = identify(code, name, country, client=client,
                             use_model=use_model, refresh=refresh)
    return out
