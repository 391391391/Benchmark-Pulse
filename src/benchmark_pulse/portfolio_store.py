"""The register of client portfolios the tool knows about.

A firm running this across six client segments is not looking at one book; an
analyst covering several mandates needs to move between them without re-reading
a file each time. So every upload becomes a durable record: the original file is
kept alongside the canonical workbook derived from it, and the registry is a
plain JSON file on disk rather than session state.

Keeping the original matters for more than tidiness. When a figure in a report
is questioned, the answer has to be traceable to the document the client sent,
not to an intermediate the tool produced.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

from .paths import DEMO_BOOK, PORTFOLIOS_DIR as STORE_DIR, PROJECT_ROOT as ROOT

REGISTRY = STORE_DIR / "registry.json"

#: Where in-app edits are recorded. Kept apart from the registry on purpose:
#: the registry describes the file the client sent, which never changes, and
#: this says which working copy the tool is currently reading instead. Losing
#: this file loses the edits and falls back to the upload, which is the right
#: way round for something that must never lose the original.
EDITS = STORE_DIR / "edits.json"

DEMO_ID = "demo-xyz"
DEMO_NAME = "XYZ Investments (sample)"

#: The filename every edited book is written to, inside the portfolio's own
#: workspace.
EDITED_NAME = "edited.xlsx"


@dataclass
class PortfolioRecord:
    id: str
    name: str
    source_filename: str
    source_format: str
    canonical_path: str
    original_path: str | None = None
    uploaded_at: str = ""
    rows: int = 0
    warnings: list[str] | None = None
    used_model: bool = False
    is_demo: bool = False
    #: The working copy, once someone has edited the holdings in the app. Not
    #: stored in registry.json -- it is applied from EDITS at load.
    edited_path: str | None = None

    @property
    def workbook(self) -> Path:
        """The book the analysis reads: the working copy if one exists."""
        return Path(self.edited_path or self.canonical_path)

    @property
    def edited(self) -> bool:
        return bool(self.edited_path)

    @property
    def source_workbook(self) -> Path:
        """The file as uploaded, whatever has been edited since."""
        return Path(self.canonical_path)

    @property
    def exists(self) -> bool:
        return self.workbook.exists()

    @property
    def uploaded_label(self) -> str:
        if not self.uploaded_at:
            return ""
        try:
            return datetime.fromisoformat(self.uploaded_at).strftime("%d %b %Y, %H:%M")
        except ValueError:
            return self.uploaded_at


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return cleaned[:48] or "portfolio"


def _demo_record() -> PortfolioRecord:
    """The bundled sample, always present so the app is never empty.

    Marked as a demo so the UI can say plainly that it is synthetic -- a
    prospect should never wonder whether they are looking at another client's
    positions.
    """
    return PortfolioRecord(
        id=DEMO_ID,
        name=DEMO_NAME,
        source_filename=DEMO_BOOK.name,
        source_format="excel",
        canonical_path=str(DEMO_BOOK),
        uploaded_at="",
        rows=0,
        warnings=[],
        is_demo=True,
    )


def _load_edits() -> dict[str, str]:
    if not EDITS.exists():
        return {}
    try:
        raw = json.loads(EDITS.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, str)}


def _save_edits(edits: dict[str, str]) -> None:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    EDITS.write_text(json.dumps(edits, indent=2), encoding="utf-8")


def workspace_for(portfolio_id: str) -> Path:
    """Where this portfolio's files live. Created on demand, because the
    bundled sample has no workspace until someone edits it."""
    path = STORE_DIR / portfolio_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def record_edit(portfolio_id: str, path: Path | str) -> None:
    """Point a portfolio at an edited working copy."""
    edits = _load_edits()
    edits[portfolio_id] = str(path)
    _save_edits(edits)


def clear_edit(portfolio_id: str) -> bool:
    """Discard the working copy and go back to the file as uploaded."""
    edits = _load_edits()
    stale = edits.pop(portfolio_id, None)
    if stale is None:
        return False
    _save_edits(edits)
    Path(stale).unlink(missing_ok=True)
    return True


def _apply_edits(records: list[PortfolioRecord]) -> list[PortfolioRecord]:
    edits = _load_edits()
    for record in records:
        working = edits.get(record.id)
        # An edit whose file has gone is not an edit. Falling back to the
        # upload is better than a portfolio that will not open.
        if working and Path(working).exists():
            record.edited_path = working
    return records


def load_registry() -> list[PortfolioRecord]:
    """Every known portfolio, the bundled sample first."""
    records = [_demo_record()]
    if REGISTRY.exists():
        try:
            raw = json.loads(REGISTRY.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return _apply_edits(records)
        for item in raw:
            item.pop("edited_path", None)
            try:
                record = PortfolioRecord(**item)
            except TypeError:
                continue          # a record from an older shape; skip it
            records.append(record)
    # The sample stays in the list whether or not its file is present, so the
    # picker is never empty and the app can say what is missing instead of
    # failing on an empty registry.
    return [r for r in _apply_edits(records) if r.is_demo or r.exists]


def _save_registry(records: list[PortfolioRecord]) -> None:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    kept = []
    for record in records:
        if record.is_demo:
            continue
        item = asdict(record)
        # The registry describes the upload. Which working copy is in use is
        # the edits file's business, and duplicating it here would let the two
        # drift.
        item.pop("edited_path", None)
        kept.append(item)
    REGISTRY.write_text(json.dumps(kept, indent=2), encoding="utf-8")


def get(portfolio_id: str) -> PortfolioRecord | None:
    return next((r for r in load_registry() if r.id == portfolio_id), None)


def add(uploaded_name: str, data: bytes, display_name: str | None = None,
        client=None) -> PortfolioRecord:
    """Store an uploaded file and convert it to the canonical workbook.

    Raises IngestError if the file cannot be read, so the caller can show the
    reason rather than registering a portfolio that will fail on every open.
    """
    from .ingest import ingest

    source_name = Path(uploaded_name).name
    stamp = datetime.now(timezone.utc)
    record_id = f"{_slug(Path(source_name).stem)}-{stamp.strftime('%Y%m%d%H%M%S')}"
    workspace = STORE_DIR / record_id
    workspace.mkdir(parents=True, exist_ok=True)

    original = workspace / source_name
    original.write_bytes(data)

    try:
        result = ingest(original, workspace, client=client)
    except Exception:
        # Do not leave a half-registered portfolio behind on failure.
        shutil.rmtree(workspace, ignore_errors=True)
        raise

    record = PortfolioRecord(
        id=record_id,
        name=display_name or Path(source_name).stem,
        source_filename=source_name,
        source_format=result.source_format,
        canonical_path=str(result.canonical_path),
        original_path=str(original),
        uploaded_at=stamp.isoformat(),
        rows=max(result.total_rows, 0),
        warnings=result.warnings,
        used_model=result.used_model,
    )

    records = [r for r in load_registry() if r.id != record_id]
    records.append(record)
    _save_registry(records)
    return record


def save_holdings(record: PortfolioRecord, frame) -> Path:
    """Write an edited holdings grid to this portfolio's working copy.

    The uploaded file is read for its other sheets and then left alone. What
    the analysis reads afterwards is the working copy, and `clear_edit` puts it
    back.
    """
    from .holdings_edit import write_holdings

    out = workspace_for(record.id) / EDITED_NAME
    write_holdings(frame, out, source=record.workbook)
    record_edit(record.id, out)
    return out


def remove(portfolio_id: str) -> bool:
    """Forget a portfolio and delete the files stored for it."""
    if portfolio_id == DEMO_ID:
        return False
    records = load_registry()
    keep = [r for r in records if r.id != portfolio_id]
    if len(keep) == len(records):
        return False
    edits = _load_edits()
    if edits.pop(portfolio_id, None) is not None:
        _save_edits(edits)
    shutil.rmtree(STORE_DIR / portfolio_id, ignore_errors=True)
    _save_registry(keep)
    return True


def rename(portfolio_id: str, name: str) -> bool:
    records = load_registry()
    for record in records:
        if record.id == portfolio_id and not record.is_demo:
            record.name = name.strip() or record.name
            _save_registry(records)
            return True
    return False
