"""Price, index and FX data from free sources, cached to disk.

Everything here is free: Yahoo Finance via yfinance covers US, Indian (.NS) and
Saudi (.SR) listings, the major indices, and currency pairs. No subscription is
required anywhere in Benchmark Pulse, which is what makes the whole thing
deployable without a data budget.

Two behaviours exist for the sake of the demo rather than for elegance:

  Caching     every series is written to CSV and re-read for ``max_age_hours``.
              Repeated runs during a rehearsal cost nothing and are reproducible.

  Offline     with ``offline=True`` no network call is attempted at all and the
              cache is the only source. Run this once before presenting and a
              dead conference-room connection cannot break anything.

Known quirk, discovered by probing rather than assumed: ``^TASI.SR`` (Tadawul
All Share) returns a full history but with the current day's close as NaN. Every
series is therefore dropna()'d on the way out. Dropping the tail is correct --
a NaN close is an unstruck price, not a zero.
"""

from __future__ import annotations

import time
import warnings
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning, module="yfinance")

from .paths import PRICES_DIR as CACHE_DIR
from .sources import (
    CrossCheck, Provenance, SourceError, compare, ecb_cross_rate, pegged_rate,
    yahoo_provenance,
)

#: Currencies whose USD rate is effectively fixed by policy. The Saudi riyal has
#: been pegged at 3.75/USD for decades, so an apparent "currency effect" on a
#: Saudi holding is noise, not signal -- the FX decomposition says so explicitly
#: rather than reporting a spurious few basis points.
PEGGED_TO_USD = {"SAR": 3.75, "AED": 3.6725, "HKD": None}


class MarketDataError(RuntimeError):
    """Data could not be obtained, and the caller must decide what to do."""


#: What Yahoo's marketState means, in words a reader does not have to decode.
#: The distinction matters on a book spread across four timezones: at any hour
#: some of these exchanges are trading and some closed hours ago, and a screen
#: that calls all of them "live" is wrong about most of them.
MARKET_STATE = {
    "REGULAR": "market open",
    "PRE": "pre-market",
    "POST": "after hours",
    "POSTPOST": "after hours",
    "PREPRE": "pre-market",
    "CLOSED": "market closed",
}


@dataclass
class Quote:
    """The current price of one security, and how current it actually is.

    ``live`` is the whole point of this object. A quote that could not be
    fetched falls back to the last close, which is a perfectly good number and
    a different claim -- so it is labelled, never presented as the market.
    """

    ticker: str
    price: float
    currency: str = ""
    previous_close: float | None = None
    state: str = ""                  # Yahoo's marketState, raw
    when: datetime | None = None     # exchange time of the quote
    timezone_label: str = ""         # "EDT", "CEST"
    live: bool = False
    note: str = ""
    as_of: date | None = None        # the close's date, when not live

    @property
    def change(self) -> float | None:
        if self.previous_close is None or not self.previous_close:
            return None
        return self.price - self.previous_close

    @property
    def change_pct(self) -> float | None:
        if self.previous_close is None or not self.previous_close:
            return None
        return self.price / self.previous_close - 1.0

    @property
    def state_label(self) -> str:
        return MARKET_STATE.get(str(self.state).upper(), "")

    @property
    def trading(self) -> bool:
        """True only while the exchange is actually open.

        Outside regular hours the feed's "current price" is the last completed
        session's, not a trade happening now. Calling that live would be an
        overclaim of exactly the kind this tool exists to avoid -- so a quote
        pulled at eight in the morning New York time is a last price with a
        timestamp, and says so.
        """
        return self.live and str(self.state).upper() == "REGULAR"

    @property
    def stamp(self) -> str:
        """When this price is from, said plainly."""
        if self.live and self.when is not None:
            label = f" {self.timezone_label}" if self.timezone_label else ""
            return self.when.strftime("%d %b %H:%M") + label
        if self.as_of is not None:
            return f"close of {self.as_of:%d %b %Y}"
        return "time unknown"


class MarketData:
    def __init__(self, cache_dir: Path | None = None, *, offline: bool = False,
                 max_age_hours: int = 12, live: bool = False) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        #: In live mode the on-disk cache is ignored for prices, so opening the
        #: app fetches the market rather than replaying it. Affordable because
        #: prefetch() pulls the whole universe in one batched request: roughly
        #: five seconds for fifty tickers, against a minute one at a time.
        self.live = live and not offline
        self.max_age_hours = 0 if self.live else max_age_hours
        self.fetched_at: datetime | None = None
        self._memo: dict[str, pd.DataFrame] = {}
        self._fx_cache: dict[tuple[str, str], pd.Series] = {}
        #: ticker -> (monotonic time fetched, quote). Short-lived on purpose;
        #: see quote().
        self._quotes: dict[str, tuple[float, Quote]] = {}

        #: Where every series came from, keyed by ticker or currency pair. A
        #: performance report is only as defensible as its inputs, so this is
        #: recorded as data rather than assumed.
        self.provenance: dict[str, Provenance] = {}
        self.notes: list[str] = []

    # -- caching ------------------------------------------------------------

    def _cache_path(self, ticker: str) -> Path:
        safe = ticker.replace("^", "IDX_").replace("=", "_").replace("/", "_")
        return self.cache_dir / f"{safe}.csv"

    def _cache_is_fresh(self, path: Path) -> bool:
        if not path.exists():
            return False
        if self.offline:
            return True          # offline: any cache beats no data
        if self.max_age_hours <= 0:
            return False         # live: always refetch
        age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
        return age < timedelta(hours=self.max_age_hours)

    def prefetch(self, tickers: list[str], *, period: str = "10y") -> None:
        """Download many series in one request and seed the cache.

        Fetching one ticker at a time is what makes a live refresh unaffordable:
        fifty sequential requests take about a minute, one batched request takes
        about five seconds. Failures are left to the per-ticker path, which
        reports them properly rather than losing them in a bulk result.
        """
        if self.offline:
            return

        wanted = sorted({t for t in tickers if t and t not in self._memo})
        if not wanted:
            return

        import yfinance as yf

        try:
            raw = yf.download(wanted, period=period, auto_adjust=True,
                              group_by="ticker", threads=True, progress=False)
        except Exception as exc:  # noqa: BLE001 - fall back to per-ticker
            self.notes.append(f"batch download failed ({exc}); "
                              "falling back to individual requests")
            return

        for ticker in wanted:
            try:
                frame = raw[ticker] if len(wanted) > 1 else raw
                keep = [c for c in ("Close", "Dividends") if c in frame.columns]
                out = frame[keep].dropna(how="all")
                if out.empty:
                    continue
                if getattr(out.index, "tz", None) is not None:
                    out.index = out.index.tz_localize(None)
                out.index = pd.to_datetime(out.index).normalize()
                out = out.sort_index()
                self._memo[ticker] = out
                try:
                    out.to_csv(self._cache_path(ticker))
                except OSError:
                    pass
            except Exception:  # noqa: BLE001 - per-ticker path will retry it
                continue

        self.fetched_at = datetime.now()

    # -- fetching -----------------------------------------------------------

    def history(self, ticker: str, *, period: str = "10y") -> pd.DataFrame:
        """OHLC + dividends for ``ticker``, cached. Index is tz-naive dates."""
        if ticker in self._memo:
            return self._memo[ticker]

        path = self._cache_path(ticker)

        if self._cache_is_fresh(path):
            # Parsed explicitly rather than via parse_dates=True: a cached series
            # spanning a daylight-saving change contains two different UTC
            # offsets, which pandas refuses to parse into one index. Normalising
            # through UTC first makes the round-trip lossless and idempotent for
            # values that were already naive.
            df = pd.read_csv(path, index_col=0)
            df.index = pd.to_datetime(
                df.index, utc=True, errors="coerce"
            ).tz_convert(None)
            df = df[df.index.notna()]
        else:
            if self.offline:
                raise MarketDataError(
                    f"{ticker}: offline mode and nothing cached. Run once with "
                    "a connection to populate the cache before presenting."
                )
            df = self._download(ticker, period)
            df.to_csv(path)

        df = df.sort_index()
        self._memo[ticker] = df
        return df

    def _download(self, ticker: str, period: str) -> pd.DataFrame:
        import yfinance as yf

        try:
            raw = yf.Ticker(ticker).history(period=period, auto_adjust=True)
        except Exception as exc:  # noqa: BLE001 - yfinance raises broadly
            raise MarketDataError(f"{ticker}: download failed: {exc}") from exc

        if raw is None or raw.empty:
            raise MarketDataError(
                f"{ticker}: no data returned. Check the symbol -- Saudi listings "
                "need a .SR suffix and Indian listings .NS."
            )

        keep = [c for c in ("Close", "Dividends") if c in raw.columns]
        out = raw[keep].copy()

        # Drop the exchange timezone before anything is cached. Local trading
        # times are irrelevant here -- every calculation works in whole days --
        # and keeping them is what produces the mixed-offset CSV above.
        if getattr(out.index, "tz", None) is not None:
            out.index = out.index.tz_localize(None)
        out.index = pd.to_datetime(out.index).normalize()
        return out

    # -- series accessors ---------------------------------------------------

    def prices(self, ticker: str, *, period: str = "10y") -> pd.Series:
        """Adjusted close, NaN-free.

        auto_adjust=True means dividends and splits are already reflected, so
        this is a total-return series and can be compared against a fund's
        cashflows without double-counting income.
        """
        s = self.history(ticker, period=period)["Close"].dropna()
        if s.empty:
            raise MarketDataError(f"{ticker}: price series is empty after dropna")
        if ticker not in self.provenance:
            self.provenance[ticker] = yahoo_provenance(ticker, s)
        return s

    # -- live quotes --------------------------------------------------------

    def _last_close_quote(self, ticker: str, note: str) -> Quote:
        """The fallback every path lands on: yesterday's answer, labelled."""
        series = self.prices(ticker)
        previous = float(series.iloc[-2]) if len(series) > 1 else None
        return Quote(ticker=ticker, price=float(series.iloc[-1]),
                     previous_close=previous, live=False, note=note,
                     as_of=series.index[-1].date())

    def quote(self, ticker: str, *, max_age_seconds: int = 60) -> Quote:
        """The current price, or the last close if one cannot be had.

        Held for a minute in memory. The endpoint behind this is the one Yahoo
        rate-limits hardest, and a Streamlit page re-runs on every widget touch
        -- without the hold, moving a dropdown would fetch a quote each time and
        earn a 429 within the minute.

        Never raises. A holding page that cannot reach the market should show
        the last close and say so, not an exception.
        """
        now = time.monotonic()
        cached = self._quotes.get(ticker)
        if cached and now - cached[0] < max_age_seconds:
            return cached[1]

        if self.offline:
            return self._last_close_quote(
                ticker, "offline mode: this is the last cached close, not a "
                        "live quote")

        try:
            import yfinance as yf

            info = yf.Ticker(ticker).info or {}
        except Exception as exc:  # noqa: BLE001 - the feed raises broadly
            return self._last_close_quote(
                ticker, f"the quote feed did not answer ({exc}); showing the "
                        "last close")

        price = info.get("regularMarketPrice")
        if price is None:
            return self._last_close_quote(
                ticker, "the feed returned no current price; showing the last "
                        "close")

        when = None
        stamp = info.get("regularMarketTime")
        if isinstance(stamp, (int, float)):
            when = datetime.fromtimestamp(float(stamp), tz=timezone.utc)
            zone = info.get("exchangeTimezoneName")
            if zone:
                try:
                    from zoneinfo import ZoneInfo

                    when = when.astimezone(ZoneInfo(str(zone)))
                except Exception:  # noqa: BLE001 - UTC is a fair fallback
                    pass

        quote = Quote(
            ticker=ticker, price=float(price),
            currency=str(info.get("currency") or "").strip(),
            previous_close=(float(info["regularMarketPreviousClose"])
                            if info.get("regularMarketPreviousClose") is not None
                            else None),
            state=str(info.get("marketState") or ""),
            when=when,
            timezone_label=str(info.get("exchangeTimezoneShortName") or ""),
            live=True,
        )
        self._quotes[ticker] = (now, quote)
        return quote

    def dividends(self, ticker: str, *, period: str = "10y") -> pd.Series:
        df = self.history(ticker, period=period)
        if "Dividends" not in df.columns:
            return pd.Series(dtype=float)
        return df["Dividends"].fillna(0.0)

    def latest_price(self, ticker: str) -> tuple[float, date]:
        s = self.prices(ticker)
        return float(s.iloc[-1]), s.index[-1].date()

    # -- FX -----------------------------------------------------------------

    def fx_series(self, base: str, quote: str) -> pd.Series:
        """Units of ``quote`` per one unit of ``base``, as a daily series.

        The European Central Bank is tried first: it is the institution that
        publishes the reference rate, which makes it a primary source in a way
        a scraped quote feed is not. Yahoo is the fallback for pairs the ECB
        does not publish. A same-currency request returns an empty series rather
        than failing, so callers need no special case for a single-currency book.
        """
        base, quote = base.upper(), quote.upper()
        if base == quote:
            return pd.Series(dtype=float)

        cached = self._fx_cache.get((base, quote))
        if cached is not None:
            return cached

        series = self._fx_from_ecb(base, quote)
        if series is None:
            series = self._fx_from_yahoo(base, quote)

        self._fx_cache[(base, quote)] = series
        return series

    def _fx_from_ecb(self, base: str, quote: str) -> pd.Series | None:
        """ECB reference rates, cached to disk on the same terms as prices.

        Caching matters more here than for a price series: an ECB cross needs
        two full downloads of roughly 3,000 observations each, and without a
        cache a single analysis spent a minute on rates that change once a day.
        """
        path = self._cache_path(f"ECB_{base}_{quote}")

        if self._cache_is_fresh(path):
            frame = pd.read_csv(path, index_col=0)
            frame.index = pd.to_datetime(frame.index, errors="coerce")
            series = frame.iloc[:, 0].dropna()
            series = series[series.index.notna()]
            self.provenance.setdefault(f"{base}/{quote}", Provenance(
                series=f"{base}/{quote}",
                source="European Central Bank reference rates",
                authority="derived", publisher_official=True,
                source_url="https://data.ecb.europa.eu/",
                observations=len(series),
                first=series.index[0].date() if len(series) else None,
                last=series.index[-1].date() if len(series) else None,
                note="Read from local cache of the ECB series.",
            ))
            return series

        if self.offline:
            return None

        try:
            series, record = ecb_cross_rate(base, quote)
        except SourceError as exc:
            self.notes.append(f"{base}/{quote}: ECB unavailable ({exc}); "
                              "falling back to Yahoo Finance")
            return None

        self.provenance[f"{base}/{quote}"] = record
        try:
            series.to_frame("rate").to_csv(path)
        except OSError:
            pass          # a cache write failure must not lose the data
        return series

    def _fx_from_yahoo(self, base: str, quote: str) -> pd.Series:
        """Fallback for pairs the ECB does not publish, such as the riyal."""
        if base == "USD":
            series = self.prices(f"{quote}=X")
        elif quote == "USD":
            series = 1.0 / self.prices(f"{base}=X")
        else:
            to_usd = 1.0 / self.prices(f"{base}=X")
            from_usd = self.prices(f"{quote}=X")
            joined = pd.concat([to_usd, from_usd], axis=1).ffill().dropna()
            series = joined.iloc[:, 0] * joined.iloc[:, 1]

        # Provenance has to describe the data actually used. Where a peg exists
        # the observed series is still preferred -- it varies only within the
        # band the central bank defends, which is more honest than substituting
        # a constant -- so the peg is recorded as a note on the Yahoo series
        # rather than as a source in its own right.
        peg = pegged_rate(base, quote)
        note = "The ECB publishes no reference rate for this pair."
        if peg is not None:
            note += " " + peg[1].note

        self.provenance[f"{base}/{quote}"] = Provenance(
            series=f"{base}/{quote}", source="Yahoo Finance",
            authority="unofficial",
            source_url="https://finance.yahoo.com/",
            observations=len(series),
            first=series.index[0].date() if len(series) else None,
            last=series.index[-1].date() if len(series) else None,
            note=note,
        )
        return series

    def check_fx_sources(self, base: str, quote: str) -> CrossCheck | None:
        """Compare the ECB against Yahoo for the same pair.

        Not to pick a winner, but so that when a client questions a figure the
        answer can state how far two independent feeds were from each other.
        """
        if self.offline:
            return None

        # Reuse the series already loaded rather than downloading the ECB legs
        # a second time; re-fetching them was the whole cost of this check.
        official = self._fx_from_ecb(base, quote)
        if official is None or official.empty:
            return None

        try:
            if base == "USD":
                other = self.prices(f"{quote}=X")
            elif quote == "USD":
                other = 1.0 / self.prices(f"{base}=X")
            else:
                return None
        except MarketDataError:
            return None

        return compare(official, other, f"{base}/{quote}",
                       "ECB", "Yahoo Finance")

    def fx_rate(self, base: str, quote: str, on: date | None = None) -> float:
        """Rate on ``on`` (or latest), carrying the last observation forward."""
        base, quote = base.upper(), quote.upper()
        if base == quote:
            return 1.0

        series = self.fx_series(base, quote)
        if series.empty:
            raise MarketDataError(f"no FX data for {base}/{quote}")

        if on is None:
            return float(series.iloc[-1])

        prior = series.loc[: pd.Timestamp(on)]
        return float(prior.iloc[-1] if not prior.empty else series.iloc[0])

    @staticmethod
    def is_pegged(currency: str) -> bool:
        """True when FX movement against USD is policy-fixed, not market-driven.

        Matters for the Saudi book: reporting a 'currency effect' on a riyal
        holding would be inventing a signal that the peg precludes.
        """
        return currency.upper() in PEGGED_TO_USD

    # -- diagnostics --------------------------------------------------------

    def probe(self, tickers: list[str]) -> pd.DataFrame:
        """Check a list of symbols and report what is actually usable.

        Run before a demo. Silent failure on one holding is how a portfolio
        report ends up quietly missing a position.
        """
        rows = []
        for t in tickers:
            try:
                s = self.prices(t)
                rows.append({
                    "ticker": t, "ok": True, "rows": len(s),
                    "first": s.index[0].date(), "last": s.index[-1].date(),
                    "latest": round(float(s.iloc[-1]), 4), "error": "",
                })
            except Exception as exc:  # noqa: BLE001
                rows.append({
                    "ticker": t, "ok": False, "rows": 0, "first": None,
                    "last": None, "latest": None, "error": str(exc)[:90],
                })
        return pd.DataFrame(rows)
