"""IPO Radar: the open pipeline, and a research-grade scorecard for one deal.

Run:  python scripts/run_ipo.py                    # show the pipeline
      python scripts/run_ipo.py "Holtec Nuclear"   # score one deal
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from benchmark_pulse.analysis import analyse  # noqa: E402
from benchmark_pulse.ipo_score import score_ipo, extract_facts  # noqa: E402
from benchmark_pulse.ipo_sources import (  # noqa: E402
    fetch_pipeline, fetch_prospectus, find_filing,
)
from benchmark_pulse.marketdata import MarketData  # noqa: E402

BOOK = Path(__file__).resolve().parents[1] / "data" / "demo" / "meridian_family_office.xlsx"


def show_pipeline(listings, notes) -> None:
    print("=" * 96)
    print("IPO RADAR  -  open deals in Preferred Square's core markets")
    print("=" * 96)
    print(f"{'Mkt':<5}{'Status':<11}{'Ticker':<9}{'Company':<44}{'Date':<12}Size")
    print("-" * 96)
    for l in sorted(listings, key=lambda x: (x.market, x.status)):
        if not l.is_open:
            continue
        size = f"${l.deal_size:,.0f}" if l.deal_size else "-"
        print(f"{l.market:<5}{l.status:<11}{(l.ticker or '-'):<9}"
              f"{l.company_name[:43]:<44}{(l.expected_date or '-'):<12}{size}")
    print("-" * 96)
    open_deals = [l for l in listings if l.is_open]
    print(f"{len(open_deals)} open of {len(listings)} tracked")
    for n in notes:
        print(f"\nNOTE: {n}")


def show_scorecard(card) -> None:
    l = card.listing
    print("\n" + "=" * 96)
    print(f"{l.company_name.upper()}")
    if card.filing:
        print(f"{card.filing.form} filed {card.filing.filing_date}  |  "
              f"SIC {card.filing.sic} {card.filing.sic_description}")
        print(f"{card.filing.document_url}")
    print("=" * 96)

    if card.blocked_reason:
        print("\n  NOT SCORED")
        for line in _wrap(card.blocked_reason, 90):
            print(f"  {line}")
        return

    if not card.dimensions:
        for w in card.warnings:
            print(f"  WARNING: {w}")
        return

    print(f"\n  COMPOSITE {card.composite} / 100      {card.verdict.upper()}")
    print(f"  confidence: {card.confidence}\n")

    for d in card.dimensions:
        filled = int(round(d.score))
        bar = "#" * filled + "." * (10 - filled)
        print(f"  {d.label:<28} {bar}  {d.score:>4.1f}  (w{d.weight})")
        for line in _wrap(d.comment, 74):
            print(f"  {'':<30}{line}")
        print()

    if card.comparables:
        print("  COMPARABLES USED")
        print(f"  {'Ticker':<9}{'Name':<30}{'EV/Sales':>10}{'EV/EBITDA':>11}{'P/B':>8}")
        for c in card.comparables:
            print(f"  {c.ticker:<9}{c.name[:29]:<30}"
                  f"{_num(c.ev_to_sales):>10}{_num(c.ev_to_ebitda):>11}"
                  f"{_num(c.price_to_book):>8}")
        print()

    if card.portfolio_note:
        print("  PORTFOLIO FIT")
        for line in _wrap(card.portfolio_note, 90):
            print(f"    {line}")
        print()

    for w in card.warnings:
        print(f"  WARNING: {w}")


def _num(v) -> str:
    return f"{v:.1f}" if isinstance(v, (int, float)) else "-"


def _wrap(text: str, width: int) -> list[str]:
    import textwrap
    return textwrap.wrap(text, width) or [""]


def main() -> None:
    md = MarketData()
    listings, notes = fetch_pipeline()

    if len(sys.argv) < 2:
        show_pipeline(listings, notes)
        print("\nScore one:  python scripts/run_ipo.py \"<company name>\"")
        return

    query = sys.argv[1].lower()
    match = next((l for l in listings if query in l.company_name.lower()), None)
    if match is None:
        print(f"No deal matching {sys.argv[1]!r}. Run without arguments to list.")
        return

    print(f"Resolving {match.company_name} against SEC EDGAR...")
    ref = find_filing(match.company_name)
    if ref is None:
        print("  no registration statement found")
        return

    sections = fetch_prospectus(ref)
    print(f"  {ref.form}, {sections.total_chars:,} chars, "
          f"sections: {', '.join(sections.sections)}")

    print("  reading prospectus...")
    facts = extract_facts(sections)

    print("  loading portfolio for fit check...")
    portfolio = analyse(BOOK, md=md)

    card = score_ipo(match, ref, sections, facts, md, portfolio)
    show_scorecard(card)


if __name__ == "__main__":
    main()
