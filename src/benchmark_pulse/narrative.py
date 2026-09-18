"""Written commentary that cannot contain a number the engine did not produce.

The objection to a language model anywhere near a client report is always the
same: what if it invents a figure? Prompting is not an answer to that, because a
prompt is a request and a request can be ignored.

So the architecture removes the possibility instead of asking for it:

  1. The engine computes every figure and records each one as a fact with an
     identifier, a label, a raw value and the exact string it should appear as.
  2. The model is given only those facts and asked to write prose over them. It
     never calculates, and it never sees the portfolio.
  3. A guard then reads the finished text, extracts every number in it, and
     checks each against the fact table. Anything unaccounted for is a
     violation, and a draft with violations does not publish.

Step three is the load-bearing one. Steps one and two make the right behaviour
easy; step three makes the wrong behaviour impossible to publish. The guard is
arithmetic over strings -- no model involved -- so it cannot be talked out of a
refusal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .llm import LLMClient, get_client
from .reference import country_name


@dataclass(frozen=True)
class Fact:
    """One figure the commentary is permitted to cite."""

    id: str
    label: str
    value: float | int | str
    shown: str          # exactly how it should appear in prose

    @property
    def tokens(self) -> set[str]:
        """Numeric tokens this fact licenses in the text.

        A fact stated as "16.7%" licenses 16.7. It also licenses 16.65 to 16.74
        through rounding, but rather than model that, the guard compares on the
        rounded string, which is what a reader actually sees.
        """
        return set(_numbers_in(self.shown))


@dataclass
class Violation:
    number: str
    context: str

    def __str__(self) -> str:
        return f"{self.number} (in \"{self.context}\")"


@dataclass
class Narrative:
    text: str
    facts: list[Fact]
    violations: list[Violation] = field(default_factory=list)
    generated_by: str = ""

    @property
    def publishable(self) -> bool:
        """A draft publishes only when every number in it is traceable."""
        return not self.violations

    @property
    def status(self) -> str:
        if self.publishable:
            return "verified"
        return f"blocked: {len(self.violations)} unverified figure" + (
            "s" if len(self.violations) != 1 else "")


#: Numbers that need no fact behind them: small counts, years, and the ordinals
#: that appear naturally in prose. Without this the guard would flag "two of the
#: three" and become noise that people learn to ignore.
_ALWAYS_ALLOWED = {str(n) for n in range(0, 13)} | {
    str(y) for y in range(1990, 2101)
}

#: Digits after a decimal point are required, so a number ending a sentence
#: ("...opened in 2022.") does not capture the full stop and read as a decimal.
_NUMBER_PATTERN = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _scan_numbers(text: str) -> list[tuple[str, str]]:
    """Every numeric token as (normalised, raw).

    The raw form is kept because it carries information the normalised form
    loses: whether the writer expressed a *count* or a *measurement*. "1.0
    percentage points" and "1 holding" normalise identically, but only the
    first is a computed quantity that needs a fact behind it.
    """
    found: list[tuple[str, str]] = []
    for raw in _NUMBER_PATTERN.findall(text):
        cleaned = raw.replace(",", "").lstrip("-").rstrip(".")
        if not cleaned or cleaned == ".":
            continue
        # Trailing zeros are a formatting choice, not a different number.
        if "." in cleaned:
            cleaned = cleaned.rstrip("0").rstrip(".")
        found.append((cleaned or "0", raw))
    return found


def _numbers_in(text: str) -> list[str]:
    """Every numeric token in a string, normalised for comparison."""
    return [normalised for normalised, _ in _scan_numbers(text)]


def build_facts(analysis) -> list[Fact]:
    """Every figure the commentary may use, computed by the engine.

    Nothing is included that the engine did not calculate, and nothing the
    commentary might want is left out -- an incomplete fact table makes the
    guard fire on legitimate prose, which would train the reader to ignore it.
    """
    facts: list[Fact] = []

    def add(fact_id: str, label: str, value, shown: str) -> None:
        facts.append(Fact(fact_id, label, value, shown))

    p = analysis.portfolio
    if p is not None:
        add("portfolio.irr", "Portfolio IRR", p.irr, f"{(p.irr or 0) * 100:.1f}%")
        add("portfolio.benchmark_irr", "Mandate benchmark return",
            p.benchmark_irr, f"{(p.benchmark_irr or 0) * 100:.1f}%")
        add("portfolio.alpha", "Direct Alpha vs mandate", p.direct_alpha,
            f"{(p.direct_alpha or 0) * 100:.1f}%")
        if p.pme and p.pme.ks_pme:
            add("portfolio.ks_pme", "KS-PME vs mandate", p.pme.ks_pme,
                f"{p.pme.ks_pme:.2f}")
        add("mandate.ticker", "Mandate benchmark",
            p.decision.benchmark_ticker, p.decision.benchmark_ticker)
        add("mandate.name", "Mandate benchmark name",
            p.decision.benchmark_name, p.decision.benchmark_name)
        add("exposure.developed", "Developed markets share",
            p.exposure["developed_pct"], f"{p.exposure['developed_pct'] * 100:.0f}%")
        add("exposure.emerging", "Emerging markets share",
            p.exposure["emerging_pct"], f"{p.exposure['emerging_pct'] * 100:.0f}%")
        # Country labels use the full name: the model writes what the label
        # says, and "32% in SA" is not a sentence anyone would put in front of
        # a client.
        for country, weight in list(p.exposure["by_country"].items())[:6]:
            add(f"exposure.country.{country}",
                f"share of capital in {country_name(country)}",
                weight, f"{weight * 100:.0f}%")

    scored = analysis.scored
    add("count.holdings", "Holdings scored", len(scored), str(len(scored)))
    add("count.beating", "Holdings beating their sector",
        len(analysis.outperformers), str(len(analysis.outperformers)))
    add("count.trailing", "Holdings trailing their sector",
        len(scored) - len(analysis.outperformers),
        str(len(scored) - len(analysis.outperformers)))
    add("capital.cost", "Capital committed", analysis.total_capital,
        f"{analysis.total_capital:,.0f}")
    add("capital.value", "Current market value", analysis.total_value,
        f"{analysis.total_value:,.0f}")

    ranked = sorted(scored, key=lambda h: -(h.direct_alpha or 0))
    for holding in ranked[:4] + ranked[-4:]:
        key = re.sub(r"[^A-Za-z0-9]", "_", holding.asset_id)
        add(f"holding.{key}.alpha", f"{holding.name} alpha vs sector",
            holding.direct_alpha, f"{(holding.direct_alpha or 0) * 100:.1f}%")
        add(f"holding.{key}.irr", f"{holding.name} IRR",
            holding.metrics.get("irr"),
            f"{(holding.metrics.get('irr') or 0) * 100:.1f}%")
        add(f"holding.{key}.benchmark", f"{holding.name} sector benchmark",
            holding.decision.benchmark_ticker, holding.decision.benchmark_ticker)

    sectors = analysis.sector_summary()
    if not sectors.empty:
        for _, row in sectors.iterrows():
            key = re.sub(r"[^A-Za-z0-9]", "_", str(row["group"]))
            add(f"sector.{key}.alpha", f"{row['group']} weighted alpha",
                row["weighted_alpha"], f"{row['weighted_alpha'] * 100:.1f}%")

    return facts


SYSTEM = """You are an investment analyst writing the commentary section of a \
client's quarterly portfolio review.

You will be given a table of computed figures. Two rules govern the numbers:

  Use only figures from the table. Do not calculate anything -- not a sum, a \
difference, an average or a ratio. If a comparison you want to draw is not \
already in the table, make it in words with no number attached.

  Write each figure exactly as the table shows it. A check reads your text, \
extracts every number and verifies it against the table; an unverifiable number \
blocks the report.

The labels in the table are descriptions for you, NOT phrases to reproduce. \
Write naturally. "Portfolio IRR 16.7%" becomes "the portfolio returned 16.7%". \
Never write phrases like "a Developed markets share of 41%" or "from 24 \
Holdings scored" -- say "41% of capital sits in developed markets" and "seven \
of the twenty-four holdings". Do not name a benchmark by its ticker and its \
full name in the same breath; one or the other reads as English.

Register: a professional review. Plain, specific, declarative. No marketing \
language, no hedging, no restating the same figure twice. Prefer the shorter \
sentence. Three paragraphs, each three or four sentences:

  1. How the portfolio did against the benchmark its mandate implies, and what \
the book is made of.
  2. How individual holdings did against their own sector benchmarks, naming \
the two or three that matter.
  3. What the two levels together say -- whether allocation or stock selection \
drove the result -- and what to examine next. This paragraph carries the \
judgement, so make it the sharpest of the three."""


def generate(analysis, client: LLMClient | None = None) -> Narrative:
    """Write the commentary and verify every number in it."""
    client = client or get_client()
    facts = build_facts(analysis)

    if not client.is_live:
        return Narrative(
            text="[No language model configured. Every figure in this report is "
                 "still fully computed; only the written commentary is "
                 "unavailable. Set GEMINI_API_KEY to enable it.]",
            facts=facts, generated_by="none",
        )

    table = "\n".join(f"  {f.id:38} {f.label:44} {f.shown}" for f in facts)
    prompt = (
        f"Portfolio: {analysis.portfolio.decision.asset_name if analysis.portfolio else 'Portfolio'}\n"
        f"Reporting currency: {analysis.reporting_currency}\n\n"
        f"Computed figures:\n{table}\n\n"
        "Write the commentary."
    )

    try:
        response = client.complete(prompt, system=SYSTEM, max_tokens=1800)
    except Exception as exc:  # noqa: BLE001
        return Narrative(text=f"[Commentary unavailable: {exc}]", facts=facts,
                         generated_by="failed")

    narrative = Narrative(text=response.text.strip(), facts=facts,
                          generated_by=f"{response.provider}/{response.model}")
    narrative.violations = verify(narrative.text, facts)
    return narrative


def verify(text: str, facts: list[Fact]) -> list[Violation]:
    """Check every number in the text against the fact table.

    Deterministic string arithmetic, no model involved -- which is the point.
    A guard that could be reasoned with would not be a guard.
    """
    allowed: set[str] = set()
    for fact in facts:
        allowed |= fact.tokens

    violations: list[Violation] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        for number, raw in _scan_numbers(sentence):
            if number in allowed:
                continue
            # The small-number allowance covers counts and years written as
            # plain integers. A decimal point means a measurement, and a
            # measurement needs a fact behind it however small it is --
            # otherwise "trailed by 1.0 percentage points", which is the model
            # doing arithmetic, passes unchallenged.
            if "." not in raw and number in _ALWAYS_ALLOWED:
                continue
            violations.append(Violation(
                number=number,
                context=sentence.strip()[:120],
            ))
    return violations


def tamper(narrative: Narrative) -> Narrative:
    """Corrupt one figure, to demonstrate the guard catching it.

    Exists for one reason: an assurance that a guard works is worth less than
    watching it refuse. This alters the text only -- the fact table is
    untouched, so the guard has every chance to notice.
    """
    for fact in narrative.facts:
        for token in sorted(fact.tokens, key=len, reverse=True):
            if token in narrative.text and len(token) > 2:
                digits = token.replace(".", "")
                bumped = str(int(digits[0]) % 9 + 1) + digits[1:]
                if "." in token:
                    position = token.index(".")
                    bumped = bumped[:position] + "." + bumped[position:]
                altered = narrative.text.replace(token, bumped, 1)
                result = Narrative(text=altered, facts=narrative.facts,
                                   generated_by=narrative.generated_by)
                result.violations = verify(altered, narrative.facts)
                if result.violations:
                    return result
    return narrative
