"""Read a client workbook into CashflowStream objects.

This is the tolerant reader. Custodian statements arrive with title blocks above
the header, section labels sitting in the data, currency symbols inside numeric
columns, three date formats in one column and a total row at the bottom. A
loader that assumes a clean rectangle fails on the first real engagement, so
this one finds the header, skips what is not a holding, and reports what it
rejected instead of silently dropping rows.

The conversion each asset type undergoes is the heart of the product -- three
very different instruments, one shape:

  Listed holding   purchase outflow -> dividends received -> today's market value
  Private fund     capital calls    -> distributions      -> current NAV
  Direct property  acquisition      -> net rental income   -> latest appraisal

After this module, nothing downstream needs to know which is which.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .cashflows import AssetClass, CashflowStream, FlowType
from .marketdata import MarketData, MarketDataError

#: Column header synonyms seen across custodian formats. Matching is on a
#: normalised (lowercased, non-alphanumeric stripped) form.
HEADER_ALIASES = {
    "name": {"securityname", "security", "name", "holding", "description", "asset"},
    "ticker": {"ticker", "symbol", "code", "identifier", "isin", "ric"},
    "currency": {"ccy", "currency", "curr", "ccycode"},
    "quantity": {"quantity", "qty", "units", "shares", "nominal"},
    "cost": {"avgcost", "cost", "costperunit", "averagecost", "purchaseprice", "bookcost"},
    "value": {"marketvalue", "value", "mktvalue", "currentvalue", "mv"},
    "date": {"purchasedate", "date", "tradedate", "acquisitiondate", "bought"},
    # Country and sector drive both levels of benchmarking: country decides the
    # mandate comparator, sector decides each holding's. Reading them from the
    # file is far better than inferring from section headings, which only work
    # when the statement happens to be grouped that way.
    "country": {"country", "domicile", "market", "region", "listingcountry",
                "exchangecountry", "geography"},
    "sector": {"sector", "industry", "gics", "gicssector", "sectorname",
               "industrygroup"},
}

#: Rows that are structure, not holdings.
SECTION_WORDS = {
    "united states", "india", "saudi arabia", "listed equities", "equities",
    "fixed income", "cash", "total", "subtotal", "grand total", "bonds",
}


@dataclass
class LoadReport:
    """What was read, and what was not. Surfaced in the UI, never swallowed."""

    streams: list[CashflowStream] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (f"{len(self.streams)} assets loaded, {len(self.skipped)} rows "
                f"skipped, {len(self.warnings)} warnings")


def _norm(text: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def parse_number(value: object) -> float | None:
    """Strip currency symbols, thousands separators and stray spaces."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^\d.\-]", "", str(value))
    if cleaned in ("", "-", "."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_date(value: object) -> date | None:
    """Handle the formats that actually turn up: ISO, dd/mm/yyyy, 'Mar 03, 2025'."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%b %d, %Y", "%d-%b-%Y",
                "%d %b %Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return pd.to_datetime(text, dayfirst=True).date()
    except Exception:  # noqa: BLE001
        return None


#: Country names and codes seen in statements, mapped to ISO-2. Mandate
#: classification keys off these, so "United States" and "USA" must not land in
#: a different bucket from "US".
_COUNTRY_ALIASES = {
    "UNITEDSTATES": "US", "USA": "US", "US": "US", "UNITEDSTATESOFAMERICA": "US",
    "INDIA": "IN", "IN": "IN", "IND": "IN",
    "SAUDIARABIA": "SA", "SAUDI": "SA", "SA": "SA", "KSA": "SA",
    "UNITEDKINGDOM": "GB", "UK": "GB", "GB": "GB", "BRITAIN": "GB",
    "JAPAN": "JP", "JP": "JP",
    "NETHERLANDS": "NL", "NL": "NL",
    "DENMARK": "DK", "DK": "DK",
    "GERMANY": "DE", "DE": "DE", "FRANCE": "FR", "FR": "FR",
    "SWITZERLAND": "CH", "CH": "CH", "CANADA": "CA", "CA": "CA",
    "AUSTRALIA": "AU", "AU": "AU", "CHINA": "CN", "CN": "CN",
    "BRAZIL": "BR", "BR": "BR", "SOUTHAFRICA": "ZA", "ZA": "ZA",
    "UAE": "AE", "AE": "AE", "QATAR": "QA", "QA": "QA",
    "SINGAPORE": "SG", "SG": "SG", "HONGKONG": "HK", "HK": "HK",
    "SOUTHKOREA": "KR", "KR": "KR", "TAIWAN": "TW", "TW": "TW",
}


def _normalise_country(raw: str) -> str:
    key = re.sub(r"[^A-Za-z]", "", raw).upper()
    return _COUNTRY_ALIASES.get(key, raw.strip().upper()[:2])


#: The currency a market trades in. Used both to prefill a new row in Manage
#: holdings (an analyst adding a Tadawul line should not have to remember that
#: it settles in riyals, and the value stays editable there since a
#: cross-listing can contradict this) and, here, as the ingest-time fallback
#: when a workbook has no Currency column or leaves a cell blank -- better
#: than defaulting every holding to USD regardless of where it actually
#: trades.
MARKET_CURRENCY: dict[str, str] = {
    "US": "USD", "IN": "INR", "SA": "SAR", "GB": "GBP", "JP": "JPY",
    "NL": "EUR", "DE": "EUR", "FR": "EUR", "IE": "EUR", "BE": "EUR",
    "AT": "EUR", "IT": "EUR", "ES": "EUR", "FI": "EUR", "GR": "EUR",
    "PT": "EUR", "DK": "DKK", "SE": "SEK", "NO": "NOK", "CH": "CHF",
    "CA": "CAD", "AU": "AUD", "NZ": "NZD", "SG": "SGD", "HK": "HKD",
    "CN": "CNY", "KR": "KRW", "TW": "TWD", "AE": "AED", "QA": "QAR",
    "KW": "KWD", "BR": "BRL", "MX": "MXN", "ZA": "ZAR", "TR": "TRY",
}


def _country_from_ticker(ticker: str) -> str:
    """Last-resort country, inferred from the exchange suffix."""
    upper = ticker.upper()
    if upper.endswith(".SR"):
        return "SA"
    if upper.endswith((".NS", ".BO")):
        return "IN"
    if upper.endswith(".L"):
        return "GB"
    if upper.endswith(".T"):
        return "JP"
    if upper.endswith(".AS"):
        return "NL"
    if upper.endswith(".DE"):
        return "DE"
    if upper.endswith(".PA"):
        return "FR"
    if upper.endswith(".CO"):
        return "DK"
    if upper.endswith(".SW"):
        return "CH"
    return "US"


def normalise_ticker(raw: object, country: str = "") -> str:
    """Repair the symbol spellings that break a data lookup.

    '2222 SR' and '2222.SR' are the same security; one of them returns nothing.
    Yahoo needs .SR for Tadawul and .NS for the NSE, and custodian files supply
    neither consistently.
    """
    text = str(raw).strip().upper()
    if not text or text in ("-", "NAN", "NONE", "—"):
        return ""

    text = re.sub(r"\s+", " ", text)
    if " " in text:
        head, _, tail = text.rpartition(" ")
        if tail in ("SR", "NS", "BO", "L", "DE"):
            text = f"{head}.{tail}"

    if "." not in text and not text.startswith("^"):
        if country.upper() == "SA" and text.isdigit():
            text = f"{text}.SR"
        elif country.upper() == "IN":
            text = f"{text}.NS"
    return text


def _find_header_row(df: pd.DataFrame, max_scan: int = 12) -> int:
    """Locate the real header by scoring each row against known column names."""
    best_row, best_score = 0, 0
    for i in range(min(max_scan, len(df))):
        cells = {_norm(c) for c in df.iloc[i].tolist() if pd.notna(c)}
        score = sum(1 for aliases in HEADER_ALIASES.values() if cells & aliases)
        if score > best_score:
            best_row, best_score = i, score
    if best_score < 3:
        raise ValueError(
            "could not find a header row -- expected columns like "
            "Security / Ticker / Quantity / Purchase Date"
        )
    return best_row


def _map_columns(header: list) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, cell in enumerate(header):
        key = _norm(cell)
        for field_name, aliases in HEADER_ALIASES.items():
            if key in aliases and field_name not in mapping:
                mapping[field_name] = idx
    return mapping


def load_listed(path: Path, md: MarketData, report: LoadReport,
                sheet: str = "Holdings") -> None:
    """Read listed holdings and turn each into a cashflow stream."""
    raw = pd.read_excel(path, sheet_name=sheet, header=None)
    header_row = _find_header_row(raw)
    cols = _map_columns(raw.iloc[header_row].tolist())

    missing = {"name", "ticker", "quantity", "date"} - cols.keys()
    if missing:
        raise ValueError(f"{sheet}: missing required columns {sorted(missing)}")

    country = ""
    for i in range(header_row + 1, len(raw)):
        row = raw.iloc[i].tolist()
        first = str(row[cols["name"]]).strip() if pd.notna(row[cols["name"]]) else ""

        if not first or first.lower() == "nan":
            continue

        # A row with a label and nothing else is a section heading; remember the
        # country it implies, because the ticker column may not encode it.
        populated = sum(1 for c in row if pd.notna(c) and str(c).strip())
        if populated <= 2 and _norm(first) in {_norm(s) for s in SECTION_WORDS}:
            country = {"unitedstates": "US", "india": "IN",
                       "saudiarabia": "SA"}.get(_norm(first), country)
            continue
        if _norm(first) in {"total", "subtotal", "grandtotal"}:
            continue

        # An explicit country column wins over the section heading the row sits
        # under; the heading is only a fallback for statements grouped by market.
        row_country = country
        if "country" in cols and pd.notna(row[cols["country"]]):
            stated = str(row[cols["country"]]).strip().upper()
            if stated and stated != "NAN":
                row_country = _normalise_country(stated)

        sector = None
        if "sector" in cols and pd.notna(row[cols["sector"]]):
            candidate = str(row[cols["sector"]]).strip()
            if candidate and candidate.lower() != "nan":
                sector = candidate

        ticker = normalise_ticker(row[cols["ticker"]], row_country)
        qty = parse_number(row[cols["quantity"]])
        cost = parse_number(row[cols["cost"]]) if "cost" in cols else None
        bought = parse_date(row[cols["date"]])

        # A blank cell here used to become the literal string "NAN" (str(nan)
        # upper-cased), which then crashed the whole analysis several layers
        # down as an unresolvable FX pair rather than just this one row. And a
        # missing column defaulted every holding to USD with no inference at
        # all, unlike country and sector just above -- silently overstating a
        # foreign holding's weight in every portfolio-level figure while its
        # own row-level return looked completely normal.
        effective_country = row_country or _country_from_ticker(ticker)
        ccy = ""
        if "currency" in cols and pd.notna(row[cols["currency"]]):
            stated_ccy = str(row[cols["currency"]]).strip().upper()
            if stated_ccy and stated_ccy != "NAN":
                ccy = stated_ccy
        if not ccy:
            ccy = MARKET_CURRENCY.get(effective_country, "USD")

        if not ticker or qty is None or bought is None:
            report.skipped.append(
                f"{sheet} row {i + 1}: {first!r} -- missing "
                f"{'ticker' if not ticker else 'quantity' if qty is None else 'date'}"
            )
            continue

        try:
            prices = md.prices(ticker)
        except MarketDataError as exc:
            report.skipped.append(f"{sheet} row {i + 1}: {ticker} -- {exc}")
            continue

        if cost is None:
            prior = prices.loc[: pd.Timestamp(bought)]
            if prior.empty:
                report.skipped.append(f"{sheet} row {i + 1}: {ticker} -- no cost basis")
                continue
            cost = float(prior.iloc[-1])
            report.warnings.append(
                f"{ticker}: no cost in file; used closing price on {bought}"
            )

        stream = CashflowStream(
            asset_id=ticker, name=first, asset_class=AssetClass.PUBLIC_EQUITY,
            currency=ccy, country=effective_country, sector=sector,
        )
        stream.add(bought, -(qty * cost), FlowType.PURCHASE)

        # Dividends are deliberately NOT added as separate cashflows. The price
        # series comes from yfinance with auto_adjust=True, which back-adjusts
        # historical prices so the series already represents total return with
        # dividends reinvested. Adding them again inflates the IRR by roughly
        # the dividend yield -- which on a 7%-yielding Saudi telco is the
        # difference between a 13% return and a fictitious 24% one.
        last_price = float(prices.iloc[-1])
        stream.set_terminal_value(prices.index[-1].date(), qty * last_price)
        stream.quantity = qty
        stream.cost_per_unit = cost
        stream.last_price = last_price
        report.streams.append(stream)


def load_funds(path: Path, report: LoadReport,
               commitments_sheet: str = "Fund Commitments",
               flows_sheet: str = "Fund Cashflows") -> None:
    """Read private funds. Cashflows plus a NAV are all that PME needs."""
    commitments = pd.read_excel(path, sheet_name=commitments_sheet)
    flows = pd.read_excel(path, sheet_name=flows_sheet)

    commitments.columns = [_norm(c) for c in commitments.columns]
    flows.columns = [_norm(c) for c in flows.columns]

    for _, meta in commitments.iterrows():
        name = str(meta["fund"]).strip()
        own = flows[flows["fund"].astype(str).str.strip() == name]
        if own.empty:
            report.skipped.append(f"{name}: no cashflows found")
            continue

        stream = CashflowStream(
            asset_id=f"FUND:{name}", name=name, asset_class=AssetClass.PRIVATE_FUND,
            currency=str(meta.get("ccy", "USD")).strip().upper(),
            country=str(meta.get("country", "US")).strip().upper(),
            vintage_year=int(meta["vintage"]) if pd.notna(meta.get("vintage")) else None,
        )

        for _, f in own.iterrows():
            when = parse_date(f["date"])
            amount = parse_number(f["amount"])
            if when is None or amount is None:
                continue
            kind = (FlowType.CAPITAL_CALL if amount < 0 else FlowType.DISTRIBUTION)
            stream.add(when, amount, kind)

        nav = parse_number(meta.get("currentnav"))
        nav_date = parse_date(meta.get("navdate"))
        if nav is not None and nav_date is not None:
            stream.set_terminal_value(nav_date, nav)
        else:
            report.warnings.append(f"{name}: no NAV; unrealised value treated as zero")

        problems = stream.validate()
        if problems:
            report.warnings.extend(problems)
        report.streams.append(stream)


def load_properties(path: Path, report: LoadReport,
                    sheet: str = "Direct Real Estate") -> None:
    """Read direct property, modelling the investor's actual equity position.

    Property is almost always bought with debt, and the cashflows the investor
    experiences are the levered ones: they fund only the equity slice, receive
    net income after interest, and take the sale proceeds after repaying the
    loan. Treating the purchase as all-cash would understate both the return and
    the risk, and would not be what the client sees on their own statement.

    Debt is assumed interest-only with the principal repaid at exit, which is
    the standard structure for institutional real estate and avoids inventing an
    amortisation schedule the source file does not contain. The unlevered
    comparison -- necessary because the benchmark index is unlevered -- is
    computed in adjustments.unlever_return rather than here.

    The ownership percentage is applied to cost, income and valuation alike, so
    every figure is the investor's economic share rather than the whole asset.
    """
    df = pd.read_excel(path, sheet_name=sheet)
    df.columns = [_norm(c) for c in df.columns]

    for _, row in df.iterrows():
        name = str(row["asset"]).strip()
        acquired = parse_date(row["acquired"])
        cost = parse_number(row["acquisitioncost"])
        noi = parse_number(row.get("annualnetincome")) or 0.0
        valuation = parse_number(row["latestvaluation"])
        val_date = parse_date(row["valuationdate"])
        share = parse_number(row.get("ownership")) or 1.0
        ltv = parse_number(row.get("ltv")) or 0.0
        debt_rate = parse_number(row.get("debtrate"))

        if acquired is None or cost is None or valuation is None or val_date is None:
            report.skipped.append(f"{name}: incomplete property record")
            continue

        if ltv > 0 and debt_rate is None:
            debt_rate = 0.05
            report.warnings.append(
                f"{name}: LTV {ltv:.0%} given but no debt rate; assumed 5.0%"
            )
        debt_rate = debt_rate or 0.0

        stream = CashflowStream(
            asset_id=f"RE:{name}", name=name,
            asset_class=AssetClass.DIRECT_REAL_ESTATE,
            currency=str(row.get("ccy", "USD")).strip().upper(),
            country=str(row.get("country", "US")).strip().upper(),
            leverage_ratio=ltv,
            debt_rate=debt_rate,
            sector=str(row.get("type", "")).strip() or None,
        )

        gross_cost = cost * share
        debt = gross_cost * ltv
        annual_interest = debt * debt_rate

        stream.add(acquired, -(gross_cost - debt), FlowType.PURCHASE)

        # Net income after interest, paid each 31 December the asset was held
        # through. The first year is pro-rated from the acquisition date.
        year = acquired.year
        while year < val_date.year:
            pay = date(year, 12, 31)
            if pay > acquired:
                fraction = 1.0
                if year == acquired.year:
                    fraction = (pay - acquired).days / 365.0
                net = (noi * share - annual_interest) * fraction
                if net != 0:
                    stream.add(pay, net, FlowType.RENTAL_INCOME)
            year += 1

        # Exit proceeds are net of repaying the loan.
        stream.set_terminal_value(val_date, valuation * share - debt)
        stream.gross_cost = gross_cost
        stream.gross_valuation = valuation * share
        stream.annual_noi = noi * share
        report.streams.append(stream)


def load_workbook(path: Path | str, md: MarketData | None = None) -> LoadReport:
    """Read every sheet into one list of comparable cashflow streams."""
    path = Path(path)
    md = md or MarketData()
    report = LoadReport()

    for label, loader, required in (
        ("listed holdings", lambda: load_listed(path, md, report), True),
        ("private funds", lambda: load_funds(path, report), False),
        ("direct property", lambda: load_properties(path, report), False),
    ):
        try:
            loader()
        except Exception as exc:  # noqa: BLE001 - one bad sheet must not kill the rest
            # A workbook with no private-fund or property sheet is the normal
            # case, not a fault: this tool is public equity only, and those
            # loaders are kept for books that happen to carry the sheets. An
            # absent optional sheet used to surface as "could not read private
            # funds", which reads as a failure on a page of caveats and invites
            # exactly the wrong question in a review.
            missing = "not found" in str(exc).lower()
            if required or not missing:
                report.warnings.append(f"could not read {label}: {exc}")

    return report
