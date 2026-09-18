"""Generate the demo equity portfolio.

XYZ Investments is fictional, but its numbers are not invented. Purchase prices
are the *actual* closing price on the actual purchase date, pulled from free
market data, and quantities are solved to hit a target position size. When a
judge asks whether a return is right, the answer is that it was computed from
real prices over a real holding period.

Synthetic-but-real-priced data is also the compliance answer: no client
information touches the tool during the pilot, so nothing sensitive reaches a
model provider and the demo can be shown to anyone.

Ten holdings, deliberately. A book you can check by hand is worth more than a
book that looks impressive: every figure on screen can be traced to one of ten
positions in a minute, which is what makes a wrong number findable.

The book is built to make both levels of benchmarking say something:

  Portfolio level   close to an even split of developed and emerging capital, so
                    the mandate question is genuinely open -- neither a developed
                    nor an emerging benchmark is obviously right, and an
                    all-country index has to be argued for.

  Holding level     FOUR BANKS, in four different markets. JPMorgan against US
                    financials, ICICI against Indian banks, Al Rajhi against the
                    Saudi market, ING against the Dutch. Same sector, four
                    benchmarks, because a holding is judged against its peers in
                    its own market and not against a global sector that folds in
                    a country bet it never made. A tool with one benchmark would
                    have measured all four against the same index.

                    Two of the ten also flip verdict under that rule -- ICICI
                    Bank from laggard to winner, Sun Pharmaceutical from winner
                    to laggard -- which is the point made with numbers rather
                    than argued.

Run:  python scripts/make_demo_book.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from benchmark_pulse.marketdata import MarketData  # noqa: E402
from benchmark_pulse.paths import DEMO_BOOK_GENERATED as DEMO_BOOK  # noqa: E402

# (ticker, name, country, currency, sector, purchase date, target local value)
#
# Targets are sized so developed and emerging capital land close to even. That
# is the whole point of the mandate question: skew it either way and the answer
# becomes obvious, and an obvious answer demonstrates nothing.
HOLDINGS = [
    # --- United States: developed ----------------------------------------
    ("AAPL",         "Apple Inc.",               "US", "USD", "Technology",  date(2022, 5, 16),  1_100_000),
    ("JPM",          "JPMorgan Chase & Co.",     "US", "USD", "Financials",  date(2022, 7, 11),    850_000),
    ("XOM",          "Exxon Mobil Corp.",        "US", "USD", "Energy",      date(2022, 1, 10),    700_000),
    # --- Netherlands: developed, non-US -----------------------------------
    # The Amsterdam lines, not the New York ADRs. A holding is priced in the
    # currency of the market it trades in, so a Dutch company is a euro
    # position -- reading it in dollars mixes a currency view into what is
    # meant to be a view of the business.
    ("ASML.AS",      "ASML Holding NV",          "NL", "EUR", "Technology",  date(2023, 3, 20),    830_000),
    ("INGA.AS",      "ING Groep NV",             "NL", "EUR", "Financials",  date(2022, 11, 14),   510_000),
    # --- India: emerging --------------------------------------------------
    ("ICICIBANK.NS", "ICICI Bank Ltd",           "IN", "INR", "Financials",  date(2022, 6, 20),  78_000_000),
    ("INFY.NS",      "Infosys Ltd",              "IN", "INR", "Technology",  date(2023, 7, 17),  62_000_000),
    ("SUNPHARMA.NS", "Sun Pharmaceutical Inds",  "IN", "INR", "Health Care", date(2023, 9, 11),  40_000_000),
    # --- Saudi Arabia: emerging -------------------------------------------
    ("2222.SR",      "Saudi Aramco",             "SA", "SAR", "Energy",      date(2022, 6, 13),   5_000_000),
    ("1120.SR",      "Al Rajhi Bank",            "SA", "SAR", "Financials",  date(2022, 9, 12),   3_800_000),
]


def build_rows(md: MarketData) -> list[dict]:
    """Resolve each holding against real price history."""
    rows: list[dict] = []
    for ticker, name, country, ccy, sector, bought, target in HOLDINGS:
        try:
            prices = md.prices(ticker)
        except Exception as exc:  # noqa: BLE001
            print(f"  SKIP {ticker}: {str(exc)[:70]}")
            continue

        prior = prices.loc[: pd.Timestamp(bought)]
        if prior.empty:
            print(f"  SKIP {ticker}: no price on or before {bought}")
            continue

        cost = float(prior.iloc[-1])
        qty = max(1, round(target / cost))
        last = float(prices.iloc[-1])

        rows.append({
            "Security Name": name, "Ticker": ticker, "Ccy": ccy,
            "Quantity": qty, "Avg Cost": round(cost, 4),
            "Purchase Date": bought, "Country": country, "Sector": sector,
        })
        move = (last / cost - 1) * 100
        print(f"  {ticker:14} {qty:>10,} @ {cost:>10,.2f} -> {last:>10,.2f} "
              f"{ccy}  {move:+6.1f}%")
    return rows


def main() -> None:
    md = MarketData()
    print("Resolving holdings against real price history...")
    rows = build_rows(md)
    if not rows:
        raise SystemExit("No holdings resolved -- check connectivity.")

    DEMO_BOOK.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(DEMO_BOOK, engine="openpyxl") as xl:
        pd.DataFrame(rows).to_excel(xl, sheet_name="Holdings", index=False)

    frame = pd.DataFrame(rows)
    print(f"\nWrote {DEMO_BOOK}")
    print(f"  {len(rows)} holdings")
    print("  by country:", frame["Country"].value_counts().to_dict())
    print("  by sector :", frame["Sector"].value_counts().to_dict())


if __name__ == "__main__":
    main()
