"""End-to-end: workbook -> portfolio-level and holding-level benchmarking.

The product in one script.

Run:  python scripts/run_benchmark.py
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from benchmark_pulse.analysis import analyse  # noqa: E402
from benchmark_pulse.paths import DEMO_BOOK, describe  # noqa: E402


def pct(x: float | None) -> str:
    return "    n/a" if x is None or pd.isna(x) else f"{x * 100:>6.1f}%"


def main() -> None:
    print(f"data root: {describe()}\n")
    result = analyse(DEMO_BOOK, portfolio_name="PS Investments")

    print(f"Loaded: {result.load_report}")
    for s in result.load_report.skipped:
        print(f"  SKIPPED: {s}")
    print(f"Benchmark reasoning: {result.provider}")

    # ---------------------------------------------------------- level 1 --
    p = result.portfolio
    print("\n" + "=" * 100)
    print("LEVEL 1  -  THE PORTFOLIO AGAINST ITS MANDATE")
    print("=" * 100)
    if p is None:
        print("  no portfolio-level result")
    else:
        e = p.exposure
        print(f"  Composition   developed {e['developed_pct']:.0%}"
              f" | emerging {e['emerging_pct']:.0%}"
              f" | unclassified {e['unclassified_pct']:.0%}")
        print("  By country    " + ", ".join(
            f"{c} {w:.0%}" for c, w in list(e["by_country"].items())[:6]))
        print()
        print(f"  Mandate benchmark : {p.decision.benchmark_ticker} "
              f"({p.decision.benchmark_name})")
        print(f"  Chosen by         : {p.decision.source}, confidence "
              f"{p.decision.confidence:.0%}")
        for line in textwrap.wrap(p.decision.rationale, 88):
            print(f"      {line}")
        print()
        print(f"  Portfolio IRR     {pct(p.irr)}")
        print(f"  Benchmark IRR     {pct(p.benchmark_irr)}")
        print(f"  DIRECT ALPHA      {pct(p.direct_alpha)}   "
              f"{'BEAT the mandate' if p.beat_mandate else 'LOST to the mandate'}")
        if p.pme and p.pme.ks_pme:
            print(f"  KS-PME            {p.pme.ks_pme:>6.2f}")
        for n in p.notes:
            print(f"  NOTE: {n}")

    # ---------------------------------------------------------- level 2 --
    print("\n" + "=" * 100)
    print("LEVEL 2  -  EACH HOLDING AGAINST ITS OWN SECTOR")
    print("=" * 100)
    print(f"  {'Holding':<30}{'Sector':<24}{'Benchmark':<14}"
          f"{'IRR':>8}{'Bmk':>8}{'Alpha':>8}")
    print("  " + "-" * 90)
    for h in sorted(result.scored, key=lambda x: -x.direct_alpha):
        print(f"  {h.name[:29]:<30}{h.sector[:23]:<24}"
              f"{h.decision.benchmark_ticker[:13]:<14}"
              f"{pct(h.metrics.get('irr'))}{pct(h.pme.benchmark_irr)}"
              f"{pct(h.direct_alpha)}")
    print("  " + "-" * 90)
    print(f"  {len(result.outperformers)} of {len(result.scored)} holdings beat "
          f"their sector benchmark")

    # --------------------------------------------------- the two levels --
    print("\n" + "=" * 100)
    print("WHAT THE TWO LEVELS SAY TOGETHER")
    print("=" * 100)
    if p and p.direct_alpha is not None:
        share = len(result.outperformers) / max(len(result.scored), 1)
        verdict = "beat" if p.direct_alpha > 0 else "lost to"
        print(textwrap.fill(
            f"The book {verdict} its mandate by {abs(p.direct_alpha) * 100:.1f}% "
            f"a year, while {len(result.outperformers)} of {len(result.scored)} "
            f"holdings ({share:.0%}) beat their own sector. "
            + ("Allocation, not selection, is carrying the result."
               if (p.direct_alpha > 0) and share < 0.5 else
               "Selection is doing the work."
               if share >= 0.5 and p.direct_alpha > 0 else
               "Both levels are weak, which points at strategy rather than "
               "individual names."
               if share < 0.5 else
               "Stock picking is working but the allocation is offsetting it."),
            96, initial_indent="  ", subsequent_indent="  "))

    print("\n  BY SECTOR (capital-weighted)")
    sectors = result.sector_summary()
    if not sectors.empty:
        print(f"  {'Sector':<26}{'Holdings':>9}{'Beat':>6}{'Capital':>14}{'Alpha':>9}")
        for _, r in sectors.iterrows():
            print(f"  {r['group'][:25]:<26}{int(r['holdings']):>9}"
                  f"{int(r['beat_benchmark']):>6}{r['capital']:>14,.0f}"
                  f"{pct(r['weighted_alpha'])}")

    if result.benchmark_note:
        print(f"\n  Benchmark notes: {result.benchmark_note}")


if __name__ == "__main__":
    main()
