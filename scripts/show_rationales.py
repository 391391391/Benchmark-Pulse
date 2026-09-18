"""Print the benchmark decision and written reasoning for every holding.

The audit trail, in one place: what was chosen, how confident the model was,
why, and what it considered instead. This is what an analyst reviews and signs.

Run:  python scripts/show_rationales.py
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from benchmark_pulse.benchmarks import load_decisions  # noqa: E402

BADGE = {"model": "proposed by model", "rules": "rules fallback",
         "analyst": "analyst override",
         "universe": "only benchmark for this market"}


def main() -> None:
    decisions = load_decisions()
    if not decisions:
        raise SystemExit("No decisions yet -- run scripts/run_league_table.py first.")

    for d in sorted(decisions.values(), key=lambda x: -x.confidence):
        status = "APPROVED" if d.approved_by else "awaiting review"
        print(f"\n{d.asset_name}")
        print(f"  -> {d.benchmark_ticker}  ({d.benchmark_name})")
        print(f"     {BADGE.get(d.source, d.source)} | confidence "
              f"{d.confidence:.0%} | {status}")
        if d.rationale:
            for line in textwrap.wrap(d.rationale, width=94):
                print(f"     {line}")
        if d.alternatives_considered:
            print(f"     also considered: {', '.join(d.alternatives_considered)}")

    low = [d for d in decisions.values() if d.needs_review]
    print(f"\n{len(decisions)} decisions | {len(low)} need analyst review")


if __name__ == "__main__":
    main()
