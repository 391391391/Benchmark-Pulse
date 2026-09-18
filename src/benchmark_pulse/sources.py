"""Where each number came from, and how much weight it can carry.

A performance report is only as defensible as its inputs, so every series this
tool uses carries a provenance record: the source, whether that source is an
official one, when it was retrieved and what period it covers. Nothing is
presented as simply "the data".

What is actually available, established by probing rather than assumption
(15 Sep 2026, from inside the firm's network):

  ECB       Official euro reference rates from the European Central Bank.
            2,739 daily observations back to 2016, no key, no rate limit.
            Used for every currency it publishes. This is a primary source in
            the proper sense -- the central bank that sets the reference.

  FRED      Unreachable. Requests to fred.stlouisfed.org time out or are reset,
            including the root page, so it is blocked by network policy rather
            than by anything in the request. Worth knowing before a demo given
            from the office.

  Stooq     Serves a JavaScript proof-of-work challenge instead of CSV. Not
            usable from a script.

  Yahoo     Reachable and the only free source covering individual equities in
            the US, India and Saudi Arabia together. It is an unofficial
            endpoint, and this module says so rather than implying otherwise.

Because the honest position is that no free source is authoritative for global
equity prices, the tool does the next best thing: it states provenance for every
series, and where two independent sources exist it compares them and reports the
divergence instead of quietly trusting one.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import pandas as pd
import requests

ECB_BASE = "https://data-api.ecb.europa.eu/service/data/EXR"
ECB_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*"}

#: Currencies pegged to the US dollar. The Saudi riyal has been held at 3.75 by
#: policy for decades, so a market-derived rate would report noise as signal --
#: and the ECB publishes no SAR reference rate in any case.
USD_PEGS = {"SAR": 3.75, "AED": 3.6725}

#: Above this, two sources disagree enough that a number should not be used
#: without a human looking at it. Set from the observed ECB/Yahoo spread on
#: USD/INR: mean 0.17%, and only 8 days in 1,280 above 1%.
DIVERGENCE_THRESHOLD = 0.01


class SourceError(RuntimeError):
    """A source could not be reached or returned nothing usable."""


@dataclass
class Provenance:
    """Where one series came from. Carried alongside the data, not beside it."""

    series: str
    source: str
    #: What the figure is: published as-is, derived from published figures,
    #: fixed by policy, or taken from an unofficial feed.
    authority: str            # "official" | "unofficial" | "derived" | "policy"
    #: Whether the *publisher* is an official institution. Kept separate from
    #: `authority` because a cross of two ECB-published rates is a derivation
    #: but is still ECB data -- conflating the two reported a book resting
    #: entirely on central-bank rates as having no official sources at all.
    publisher_official: bool = False
    source_url: str = ""
    retrieved: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    observations: int = 0
    first: date | None = None
    last: date | None = None
    note: str = ""

    @property
    def is_official(self) -> bool:
        """True when an official institution published the underlying figures."""
        return self.publisher_official or self.authority in ("official", "policy")

    def summary(self) -> str:
        span = (f"{self.first} to {self.last}"
                if self.first and self.last else "no observations")
        return f"{self.series}: {self.source} ({self.authority}), {span}"


@dataclass
class CrossCheck:
    """Agreement between two independent sources for the same series."""

    series: str
    primary: str
    secondary: str
    overlapping_days: int
    mean_abs_diff: float
    max_abs_diff: float
    days_beyond_threshold: int
    threshold: float = DIVERGENCE_THRESHOLD

    @property
    def agrees(self) -> bool:
        return self.mean_abs_diff <= self.threshold

    def summary(self) -> str:
        verdict = "agree" if self.agrees else "DIVERGE"
        return (f"{self.series}: {self.primary} and {self.secondary} {verdict} "
                f"({self.mean_abs_diff * 100:.2f}% mean difference across "
                f"{self.overlapping_days} common days, "
                f"{self.days_beyond_threshold} beyond "
                f"{self.threshold:.0%})")


# -- European Central Bank --------------------------------------------------

def ecb_reference_rate(currency: str, start: str = "2015-01-01") -> pd.Series:
    """Units of ``currency`` per euro, from the ECB's published reference rates."""
    currency = currency.upper()
    if currency == "EUR":
        raise SourceError("EUR per EUR is not a published series")

    try:
        response = requests.get(
            f"{ECB_BASE}/D.{currency}.EUR.SP00.A",
            params={"format": "csvdata", "startPeriod": start},
            headers=ECB_HEADERS, timeout=60,
        )
    except requests.RequestException as exc:
        raise SourceError(f"ECB unreachable: {exc}") from exc

    if response.status_code == 404:
        raise SourceError(f"ECB publishes no reference rate for {currency}")
    if not response.ok:
        raise SourceError(f"ECB returned HTTP {response.status_code}")

    frame = pd.read_csv(io.StringIO(response.text))
    if "OBS_VALUE" not in frame.columns:
        raise SourceError("ECB response did not contain observations")

    series = pd.Series(
        pd.to_numeric(frame["OBS_VALUE"], errors="coerce").values,
        index=pd.to_datetime(frame["TIME_PERIOD"]),
    ).dropna().sort_index()

    if series.empty:
        raise SourceError(f"ECB returned no observations for {currency}")
    return series


def ecb_cross_rate(base: str, quote: str,
                   start: str = "2015-01-01") -> tuple[pd.Series, Provenance]:
    """Units of ``quote`` per one unit of ``base``, derived through the euro.

    The ECB publishes everything against the euro, so a dollar-based rate is a
    cross of two published series. That is a derivation, not a quoted rate, and
    the provenance says so.
    """
    base, quote = base.upper(), quote.upper()
    if base == quote:
        raise SourceError("a currency against itself is not a rate")

    legs: dict[str, pd.Series] = {}
    for code in (base, quote):
        if code != "EUR":
            legs[code] = ecb_reference_rate(code, start)

    if base == "EUR":
        rate = legs[quote]
        derivation = "published"
    elif quote == "EUR":
        rate = 1.0 / legs[base]
        derivation = "inverted"
    else:
        joined = pd.concat([legs[quote], legs[base]], axis=1,
                           keys=["quote", "base"]).ffill().dropna()
        rate = joined["quote"] / joined["base"]
        derivation = "crossed through EUR"

    rate = rate.dropna()
    return rate, Provenance(
        series=f"{base}/{quote}",
        source="European Central Bank reference rates",
        authority="official" if derivation == "published" else "derived",
        publisher_official=True,
        source_url="https://data.ecb.europa.eu/",
        observations=len(rate),
        first=rate.index[0].date() if len(rate) else None,
        last=rate.index[-1].date() if len(rate) else None,
        note=(f"Rate {derivation}. The ECB publishes against the euro, so a "
              "non-euro pair is a cross of two published series."
              if derivation != "published" else ""),
    )


def pegged_rate(base: str, quote: str) -> tuple[float, Provenance] | None:
    """A policy-fixed rate, where one exists for the pair."""
    base, quote = base.upper(), quote.upper()
    if base in USD_PEGS and quote == "USD":
        rate = 1.0 / USD_PEGS[base]
    elif quote in USD_PEGS and base == "USD":
        rate = USD_PEGS[quote]
    else:
        return None

    return rate, Provenance(
        series=f"{base}/{quote}",
        source="Currency peg",
        authority="policy",
        publisher_official=True,
        observations=0,
        note=(f"Held at {USD_PEGS.get(base) or USD_PEGS.get(quote)} against the "
              "US dollar by monetary policy. A market-derived rate would report "
              "noise as signal, and the ECB publishes no reference rate for it."),
    )


# -- cross-validation -------------------------------------------------------

def compare(primary: pd.Series, secondary: pd.Series, series_name: str,
            primary_name: str, secondary_name: str,
            threshold: float = DIVERGENCE_THRESHOLD) -> CrossCheck | None:
    """Measure agreement between two independent sources for the same series.

    The point is not to pick a winner. It is to be able to say, when a client
    questions a figure, how far two independent feeds were from each other on
    the day it was struck.
    """
    a = primary.copy()
    b = secondary.copy()
    for s in (a, b):
        s.index = pd.to_datetime(s.index)
        if getattr(s.index, "tz", None) is not None:
            s.index = s.index.tz_localize(None)

    common = a.index.intersection(b.index)
    if len(common) < 30:
        return None

    diff = (b.loc[common] / a.loc[common] - 1).abs().dropna()
    if diff.empty:
        return None

    return CrossCheck(
        series=series_name,
        primary=primary_name,
        secondary=secondary_name,
        overlapping_days=len(diff),
        mean_abs_diff=float(diff.mean()),
        max_abs_diff=float(diff.max()),
        days_beyond_threshold=int((diff > threshold).sum()),
        threshold=threshold,
    )


def yahoo_provenance(ticker: str, series: pd.Series) -> Provenance:
    return Provenance(
        series=ticker,
        source="Yahoo Finance",
        authority="unofficial",
        source_url=f"https://finance.yahoo.com/quote/{ticker}",
        observations=len(series),
        first=series.index[0].date() if len(series) else None,
        last=series.index[-1].date() if len(series) else None,
        note="Unofficial endpoint. The only free source covering US, Indian and "
             "Saudi listings together; prices are split- and dividend-adjusted.",
    )
