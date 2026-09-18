"""End-to-end run: workbook -> benchmarked league table + fairness caveats.

This is the product in one script, and the screen the pitch is built around.

Run:  python scripts/run_league_table.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from benchmark_pulse.analysis import analyse  # noqa: E402

BOOK = Path(__file__).resolve().parents[1] / "data" / "demo" / "meridian_family_office.xlsx"

CLASS_LABEL = {
    "public_equity": "Public",
    "private_fund": "Private fund",
    "direct_real_estate": "Direct RE",
}


def pct(x: float | None) -> str:
    return "     n/a" if x is None or pd.isna(x) else f"{x * 100:>7.1f}%"


def main() -> None:
    result = analyse(BOOK)

    print(f"Loaded: {result.load_report}")
    for s in result.load_report.skipped:
        print(f"  SKIPPED: {s}")
    for w in result.load_report.warnings:
        print(f"  WARN:    {w}")
    print(f"Benchmark rationale source: {result.provider}")

    print("\n" + "=" * 112)
    print("MERIDIAN FAMILY OFFICE  -  every holding, one measure, ranked by Direct Alpha")
    print("=" * 112)
    print(f"{'Asset':<33}{'Class':<14}{'Benchmark':<14}{'IRR':>9}{'Bmk IRR':>9}"
          f"{'D.Alpha':>9}{'KS-PME':>8}  Caveats")
    print("-" * 112)

    for holding in sorted(result.scored, key=lambda h: -h.direct_alpha):
        caveats = ", ".join(a.kind for a in holding.material_caveats) or "-"
        print(f"{holding.name[:32]:<33}"
              f"{CLASS_LABEL.get(holding.stream.asset_class.value, '-'):<14}"
              f"{holding.decision.benchmark_ticker[:13]:<14}"
              f"{pct(holding.metrics.get('irr'))}"
              f"{pct(holding.pme.benchmark_irr)}"
              f"{pct(holding.direct_alpha)}"
              f"{holding.pme.ks_pme:>8.2f}  {caveats}")

    print("-" * 112)
    print(f"{len(result.outperformers)} of {len(result.scored)} holdings beat "
          f"their benchmark.")

    print("\n" + "=" * 112)
    print("WHERE IS ALPHA ACTUALLY COMING FROM?  (contribution-weighted)")
    print("=" * 112)
    sleeves = result.sleeve_summary()
    if not sleeves.empty:
        print(f"  {'Sleeve':<24}{'Holdings':>10}{'Beat bmk':>11}"
              f"{'Capital (' + result.reporting_currency + ')':>18}"
              f"{'Weighted alpha':>17}")
        for _, row in sleeves.iterrows():
            print(f"  {row['sleeve']:<24}{int(row['holdings']):>10}"
                  f"{int(row['beat_benchmark']):>11}"
                  f"{row['capital']:>18,.0f}"
                  f"{row['weighted_alpha'] * 100:>16.1f}%")

    print("\n" + "=" * 112)
    print("WHEN THE HEADLINE NUMBER MISLEADS  -  material caveats only")
    print("=" * 112)
    shown = 0
    for holding in result.holdings:
        for adj in holding.material_caveats:
            print(f"\n  [{adj.kind.upper()}] {adj.headline}")
            print(f"      {adj.detail}")
            shown += 1
    if not shown:
        print("  None.")

    if result.benchmark_note:
        print(f"\nBenchmark notes: {result.benchmark_note}")


if __name__ == "__main__":
    main()
