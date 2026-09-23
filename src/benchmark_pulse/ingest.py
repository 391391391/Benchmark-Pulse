"""Reading a client portfolio out of whatever file the client actually sent.

This is the unglamorous problem that decides whether a tool gets adopted
firmwide. Every client sends something different -- a custodian's Excel export,
a PDF valuation statement, a quarterly review deck -- and an analyst hand-keys
it into a working format every cycle before any analysis can begin.

The approach differs by how much structure the format preserves:

  Excel and CSV   already tabular. Read directly, with the tolerant reader in
                  portfolio.py handling title rows, in-band section labels,
                  currency symbols and stray totals.

  PDF and PPTX    tables are extracted where they exist and text where they do
                  not, then a model maps what it finds onto the canonical
                  schema. This is a reading task, which is what a model is for;
                  it never computes anything.

Everything converges on one canonical workbook with the same four sheets. That
matters more than it looks: analysis, memo and UI all run off a single shape, so
a PDF-sourced portfolio and an Excel-sourced one cannot diverge downstream.

Extraction is cached by content hash, so re-opening a portfolio costs nothing
and the same file always produces the same result.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .llm import LLMClient, get_client

SUPPORTED = {".xlsx": "excel", ".xlsm": "excel", ".xls": "excel",
             ".csv": "csv", ".pdf": "pdf", ".pptx": "powerpoint"}

CANONICAL_SHEETS = ("Holdings", "Fund Commitments", "Fund Cashflows",
                    "Direct Real Estate")

#: A prospectus-sized document would blow any context window and is mostly
#: boilerplate. Statements are short; this cap is generous for one.
MAX_EXTRACT_CHARS = 26000


class IngestError(RuntimeError):
    """The file could not be turned into a portfolio."""


@dataclass
class IngestResult:
    canonical_path: Path
    source_format: str
    rows_found: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    used_model: bool = False

    @property
    def total_rows(self) -> int:
        return sum(self.rows_found.values())


def detect_format(path: Path) -> str:
    fmt = SUPPORTED.get(path.suffix.lower())
    if fmt is None:
        raise IngestError(
            f"{path.suffix} is not supported. Upload Excel (.xlsx), CSV, "
            "PDF or PowerPoint (.pptx)."
        )
    return fmt


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


# -- text and table extraction ---------------------------------------------

def extract_from_pdf(path: Path) -> tuple[str, list[pd.DataFrame]]:
    """Pull tables and text from a PDF statement.

    Tables are tried first because a valuation statement is a table, and a
    recovered table needs no interpretation. Text is kept as well: the parts a
    table extractor misses -- currency, valuation date, account name -- are
    usually in the surrounding prose.
    """
    import pdfplumber

    tables: list[pd.DataFrame] = []
    chunks: list[str] = []

    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages[:40]:        # statements are not books
                text = page.extract_text() or ""
                if text:
                    chunks.append(text)
                for raw in page.extract_tables() or []:
                    if len(raw) < 2:
                        continue
                    frame = pd.DataFrame(raw[1:], columns=raw[0])
                    if not frame.empty:
                        tables.append(frame)
    except Exception as exc:  # noqa: BLE001
        raise IngestError(f"could not read the PDF: {exc}") from exc

    return "\n".join(chunks), tables


def extract_from_pptx(path: Path) -> tuple[str, list[pd.DataFrame]]:
    """Pull tables and text from a slide deck.

    Quarterly review decks routinely carry the holdings table on one slide, so
    tables are worth extracting properly rather than flattening to text.
    """
    from pptx import Presentation

    tables: list[pd.DataFrame] = []
    chunks: list[str] = []

    try:
        deck = Presentation(str(path))
        for slide in deck.slides:
            for shape in slide.shapes:
                if shape.has_text_frame and shape.text_frame.text.strip():
                    chunks.append(shape.text_frame.text)
                if getattr(shape, "has_table", False):
                    rows = [[cell.text for cell in row.cells]
                            for row in shape.table.rows]
                    if len(rows) >= 2:
                        tables.append(pd.DataFrame(rows[1:], columns=rows[0]))
    except Exception as exc:  # noqa: BLE001
        raise IngestError(f"could not read the deck: {exc}") from exc

    return "\n".join(chunks), tables


# -- model-assisted mapping -------------------------------------------------

MAPPING_SYSTEM = """You are reading a client's investment portfolio statement \
and transcribing it into a fixed schema.

Transcribe only what the document states. Never invent a holding, a quantity, a \
price or a date, and never complete a partial row from general knowledge: a \
fabricated position becomes a real line in a client report.

Where a value is genuinely absent, omit the field. Where a whole row is too \
incomplete to be useful, leave it out and note why in `warnings`.

Classify each position into one of three groups:
  holdings    - listed securities with a ticker or identifier
  funds       - private fund commitments (PE, VC, infrastructure, credit)
  properties  - directly held real estate

Normalise tickers to the exchange suffix convention: Saudi listings end .SR, \
Indian listings .NS, US listings have no suffix."""

MAPPING_SCHEMA = {
    "properties": {
        "portfolio_name": {"type": "string"},
        "reporting_currency": {"type": "string"},
        "as_of_date": {"type": "string"},
        "holdings": {"type": "array"},
        "funds": {"type": "array"},
        "fund_cashflows": {"type": "array"},
        "properties": {"type": "array"},
        "warnings": {"type": "array"},
    }
}

FIELD_GUIDE = """
holdings[]        : name, ticker, currency, country (US/IN/SA), quantity,
                    cost_per_unit, purchase_date (YYYY-MM-DD), sector
funds[]           : fund, vintage, strategy, country, currency, commitment,
                    current_nav, nav_date (YYYY-MM-DD)
fund_cashflows[]  : fund, date (YYYY-MM-DD), type (Capital Call|Distribution),
                    amount (negative for calls, positive for distributions)
properties[]      : asset, city, country, currency, type, acquired (YYYY-MM-DD),
                    cost, annual_net_income, valuation, valuation_date, ltv,
                    debt_rate, ownership
"""


def map_with_model(text: str, tables: list[pd.DataFrame],
                   client: LLMClient) -> dict:
    """Ask the model to transcribe an unstructured statement into the schema."""
    table_text = ""
    for i, frame in enumerate(tables[:8]):
        table_text += (f"\n--- TABLE {i + 1} ---\n"
                       + frame.head(60).to_string(index=False)[:4000])

    prompt = (
        "Transcribe this portfolio statement into the schema.\n"
        f"{FIELD_GUIDE}\n"
        "Dates as YYYY-MM-DD. Numbers plain, without currency symbols or "
        "thousands separators.\n\n"
        f"=== EXTRACTED TABLES ==={table_text}\n\n"
        f"=== DOCUMENT TEXT ===\n{text[:MAX_EXTRACT_CHARS - len(table_text)]}"
    )
    return client.structured(prompt, MAPPING_SCHEMA, system=MAPPING_SYSTEM,
                             max_tokens=8000)


# -- canonical output -------------------------------------------------------

_HOLDING_COLS = ["Security Name", "Ticker", "Ccy", "Quantity", "Avg Cost",
                 "Purchase Date", "Country", "Sector"]
_FUND_COLS = ["Fund", "Vintage", "Strategy", "Country", "Ccy", "Commitment",
              "Current NAV", "NAV Date"]
_FLOW_COLS = ["Fund", "Date", "Type", "Amount"]
_PROP_COLS = ["Asset", "City", "Country", "Ccy", "Type", "Acquired",
              "Acquisition Cost", "Annual Net Income", "Latest Valuation",
              "Valuation Date", "LTV", "Debt Rate", "Ownership %"]

_KEY_MAP = {
    "holdings": (["name", "ticker", "currency", "quantity", "cost_per_unit",
                  "purchase_date", "country", "sector"], _HOLDING_COLS),
    "funds": (["fund", "vintage", "strategy", "country", "currency",
               "commitment", "current_nav", "nav_date"], _FUND_COLS),
    "fund_cashflows": (["fund", "date", "type", "amount"], _FLOW_COLS),
    "properties": (["asset", "city", "country", "currency", "type", "acquired",
                    "cost", "annual_net_income", "valuation", "valuation_date",
                    "ltv", "debt_rate", "ownership"], _PROP_COLS),
}


def _frame_from(records: list, keys: list[str], columns: list[str]) -> pd.DataFrame:
    rows = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        rows.append([record.get(k) for k in keys])
    return pd.DataFrame(rows, columns=columns)


def write_canonical(payload: dict, out_path: Path) -> dict[str, int]:
    """Write the four-sheet workbook every downstream step reads."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}

    frames = {
        "Holdings": _frame_from(payload.get("holdings"), *_KEY_MAP["holdings"]),
        "Fund Commitments": _frame_from(payload.get("funds"), *_KEY_MAP["funds"]),
        "Fund Cashflows": _frame_from(payload.get("fund_cashflows"),
                                      *_KEY_MAP["fund_cashflows"]),
        "Direct Real Estate": _frame_from(payload.get("properties"),
                                          *_KEY_MAP["properties"]),
    }

    with pd.ExcelWriter(out_path, engine="openpyxl") as xl:
        for sheet, frame in frames.items():
            frame.to_excel(xl, sheet_name=sheet, index=False)
            counts[sheet] = len(frame)
    return counts


def ingest(source: Path, workspace: Path,
           client: LLMClient | None = None) -> IngestResult:
    """Turn an uploaded file into a canonical workbook ready for analysis."""
    source = Path(source)
    fmt = detect_format(source)
    workspace.mkdir(parents=True, exist_ok=True)
    canonical = workspace / "canonical.xlsx"

    # Excel and CSV keep their structure, so the tolerant reader handles them
    # directly. Routing them through a model would add cost, latency and a
    # chance of transcription error for no benefit.
    if fmt == "excel":
        return IngestResult(canonical_path=source, source_format=fmt,
                            rows_found={"workbook": -1})

    if fmt == "csv":
        try:
            frame = pd.read_csv(source)
        except Exception as exc:  # noqa: BLE001
            raise IngestError(f"could not read the CSV: {exc}") from exc

        # write_canonical()/_frame_from() expect the AI model's snake_case
        # schema keys ("name", "ticker", "cost_per_unit", ...) -- routing a
        # CSV's own headers ("Security Name", "Ticker", "Avg Cost", ... or
        # any real client naming) through that produced a Holdings sheet with
        # every cell blank, since none of those keys ever match. A CSV needs
        # the same tolerant header-row/alias reader load_listed() already
        # uses for Excel, so its own headers are written through unchanged
        # and interpreted there, not remapped here.
        with pd.ExcelWriter(canonical, engine="openpyxl") as xl:
            frame.to_excel(xl, sheet_name="Holdings", index=False)
        return IngestResult(canonical_path=canonical, source_format=fmt,
                            rows_found={"Holdings": len(frame)})

    client = client or get_client()
    if not client.is_live:
        raise IngestError(
            "Reading a PDF or PowerPoint portfolio needs a language model, "
            "because the layout has to be interpreted rather than parsed. "
            "Set GEMINI_API_KEY, or upload the portfolio as Excel or CSV."
        )

    text, tables = (extract_from_pdf(source) if fmt == "pdf"
                    else extract_from_pptx(source))

    if not text.strip() and not tables:
        raise IngestError(
            "No text or tables could be read from this file. If it is a scanned "
            "document it contains images rather than text, and would need OCR."
        )

    payload = map_with_model(text, tables, client)
    counts = write_canonical(payload, canonical)

    warnings = [str(w) for w in (payload.get("warnings") or [])]
    if counts.get("Holdings", 0) == 0 and counts.get("Fund Commitments", 0) == 0:
        warnings.append(
            "No positions were recognised. Check that the document contains a "
            "holdings table rather than commentary alone."
        )

    (workspace / "extraction.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8")

    return IngestResult(canonical_path=canonical, source_format=fmt,
                        rows_found=counts, warnings=warnings, used_model=True)
