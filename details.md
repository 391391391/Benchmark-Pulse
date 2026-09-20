# Benchmark Pulse — working context

Written for whoever picks this up next, human or agent. It is not a changelog.
It is the set of things that are true about this codebase, the decisions that
look wrong until you know why, and the traps that were found by probing rather
than by reasoning. Read it before changing anything; several of the obvious
"improvements" here have already been tried and reverted for stated reasons.

Owner: Aditya, Preferred Square. Built for an internal AI Innovation Challenge.

---

## 1. What it does

Every investment in a portfolio, benchmarked twice:

- **Level one — the portfolio against its mandate.** A book is classified by
  what it actually holds (Global Equity, India Equity, Banking Portfolio, …)
  and measured against the index for that mandate.
- **Level two — each holding against its own sector, in its own market.** ICICI
  Bank is measured against the Nifty Bank index, not a global financials index.
  Apple against US technology, not world technology.

The two routinely disagree, and the disagreement is the finding: a book can
beat its mandate while most holdings trail their sectors, which means allocation
rather than stock selection did the work.

The pitch line is *"every investment decision, benchmarked"*. The commercial
frame is that **Portfolio Analysis & Monitoring** appears in 6 of the 8 client
segments on preferredsquare.com — it is the firm's most-repeated billable
service, and this industrialises it.

---

## 2. Environment — read this first

| Thing | Value |
|---|---|
| Python | 3.14.7 |
| venv | `C:\dev\bp-venv` — **outside** the project, deliberately |
| Run | `C:\dev\bp-venv\Scripts\python.exe -m streamlit run app/streamlit_app.py --server.address 127.0.0.1` |
| Tests | `C:\dev\bp-venv\Scripts\python.exe -m pytest tests/ -q` — see §9 for current pass count |
| Project | `C:\Users\Aditya\Documents\Projects - IC\benchmark-pulse` — the canonical, git-tracked copy, pushed to `https://github.com/391391391/Benchmark-Pulse.git` (public — a deliberate choice, not an oversight; see §8) |
| Data root | resolved at import by `paths.py`; in this location it's simply the project's own `data/` folder, since nothing here blocks a new file |

### History: the OneDrive copy is retired

The project used to live at
`…\OneDrive - Preferred Square Analytics Pvt Ltd\Documents\Projects - IC\benchmark-pulse`,
a **OneDrive Files On-Demand tree** where every file was a reparse point and
sandboxed tooling couldn't create new files there (`touch`, `Set-Content`,
Python `write_text` and `git init` all failed with a misleading *"Could not
find a part of the path"*). That is why `paths.py` resolves a writable data
root at import time instead of assuming the project folder is writable — the
logic is still there and still correct, it just resolves to somewhere less
surprising now that the project isn't inside OneDrive.

On 18 Sep the whole tree was copied out to the path above and turned into a
real git repo, pushed to GitHub, and that copy is now canonical. **The OneDrive
folder is stale as of that copy and should no longer be edited** — it is
missing everything committed here since, including the removal of the
two-level summary card and the sign-in-screen fixes from 20 Sep.

---

## 3. Module map

```
src/benchmark_pulse/
  cashflows.py      every holding reduced to one shape: money out, money in, value today
  metrics.py        XIRR, TVPI, DPI, TWR — deterministic, no model, no network
  directalpha.py    KS-PME and Direct Alpha, the common measure
  aggregate.py      holdings rolled into one portfolio-level cashflow stream
  window_return.py  the portfolio's 1Y and 3Y return, net of contributions
  periods.py        1D–5Y point-to-point windows, and the weighted trend score
  benchmarks.py     the verified benchmark universe and both levels of selection
  sectors.py        what a ticker is: sector, listing market, trading currency
  adjustments.py    leverage, J-curve, staleness, currency caveats (no page now)
  analysis.py       orchestration: one code path for the app, CLI and commentary
  marketdata.py     prices, live quotes, FX, caching, offline mode
  sources.py        ECB access and provenance records
  portfolio.py      tolerant reader for client workbooks
  holdings_edit.py  the editable grid: read, validate, apply a trade, write back
  portfolio_store.py the registry of books, and the edited working copies
  ingest.py         Excel, CSV, PDF and PowerPoint onto one schema
  news.py           press coverage and filings, categorised, with materiality
  reference.py      display names and column definitions (no behaviour)
  auth.py           sign-in and sessions — the only module Entra ID replaces
  brand.py          Preferred Square design system
  narrative.py      grounded commentary + numeric guard — NOT wired to the app
  llm.py            provider adapter (gemini | groq | anthropic | azure | stub)
  ipo_sources.py    dead — IPO module was cut
  ipo_score.py      dead — IPO module was cut
app/streamlit_app.py   ~2,600 lines, eight views
```

---

## 4. The three measures, and which screen uses which

This is the single most important section. Three different return measures are
in use, deliberately, and mixing them up will produce numbers that look wrong.

### Direct Alpha — money-weighted, whole-life

What *your money* earned against the benchmark, from the day you bought,
weighted by when you put it in. Computed via KS-PME: each cashflow is discounted
by the index's own growth. `benchmark_irr` is derived from the identity
`(1 + asset_irr) = (1 + benchmark_irr)(1 + direct_alpha)`.

Verified independently: simulating "buy ACWI with the same 11 cashflows on the
same dates" gives 17.6992% against the tool's 17.6897% — **0.95 bp apart**.

**Used on:** the KPI strip (Portfolio XIRR, Mandate, Benchmark, Benchmark
return, Alpha on benchmark), the
Portfolio vs mandate verdict card, the win rate.

### Trend score — time-weighted, fixed windows

What the *security* did against its benchmark over **6M / 1Y / 3Y**, weighted
**20 / 30 / 50**, point to point on the same trading days. Independent of when
the investor bought. Banded: >+10% strong outperformer, +3–10% outperformer,
±3% neutral, −3 to −10% underperformer, <−10% severe.

**Used on:** Winners and laggards (entirely), Holding analysis's headline strip.

> The user asked for this explicitly on 18 Sep: *"I don't want to see those
> returns as they can be manipulated by the manager."* Money-weighted returns
> credit timing, and the manager chooses the timing. That is why those two pages
> carry **no IRR and no Direct Alpha**.
>
> The windows were 3M/6M/12M when first specified and were widened to 6M/1Y/3Y
> on instruction. The 20/30/50 shape is unchanged, so the original worked
> examples still reconcile — `tests/test_trend_score.py` pins them.

### Modified Dietz — the portfolio over a window

`gain = closing − opening − cash injected + anything taken out`, divided by
time-weighted average capital. Specified by the user; the numerator is exactly
what they wrote, and the Modified Dietz denominator follows from it.

**Used on:** Portfolio vs mandate, "Return over one and three years".

---

## 5. Decisions that look wrong until you know why

**Do not change these without reading the reason.**

1. **`UNIVERSE` merges MARKETS last.** `ACWI`, `NIFTYBEES.NS` and `KSA` appear in
   both `MANDATES` and `MARKETS`. The order is deliberate so a holding falling
   back to its home market gets the market entry. It also means
   `UNIVERSE["ACWI"].scope` is `"WW"`, not `"Global Equity"` — anything wanting
   the *mandate* must read `MANDATES` first. `mandate_scope()` in the app does.

2. **Holding-level benchmarks are filtered by market.** `holding_candidates()`
   offers a holding only its own market's sector indices plus its market index.
   This is what stops ICICI Bank being measured against global financials, and
   it flipped 4 of 6 verdicts when introduced.

3. **Indian sector benchmarks are read through ETFs, not raw indices.** The NSE
   publishes `^NSEBANK`, `^CNXIT` etc. price-only. Using them would understate
   the benchmark by the dividend yield and flatter every Indian holding — worth
   ~1.9pp/yr on the S&P for comparison. `BANKBEES.NS`, `ITBEES.NS`, etc. are
   total-return.

4. **`^TASI.SR` is not used.** It returned 1,184 rows one morning and 1 row the
   same afternoon. `KSA` (iShares MSCI Saudi Arabia) stands in, and the
   substitution is stated on screen.

5. **Dividends are not added as cashflows.** yfinance `auto_adjust=True` already
   back-adjusts for them, so the price series is total-return. Adding dividends
   again inflates IRR by the yield — on a 7%-yielding Saudi telco that is the
   difference between 13% and a fictitious 24%.

6. **`xirr` refuses a zero-elapsed-time stream.** With no time passed every flow
   discounts by `(1+r)^0 = 1`, the NPV is flat, and the solver returned
   **−99.99%** for a position bought at today's price. It now raises, and the
   holding is reported as unscored. Do not remove this guard.

7. **A holding bought today is unscored, and every screen says so.** It has no
   whole-life IRR. It *does* have a trend, because the security has years of
   history whichever day the investor turned up — so Winners and laggards grades
   from `analysis.holdings`, never from `analysis.scored`.
   `tests/test_trade_flows_through.py` reads the app source and fails if anyone
   reintroduces the filter.

8. **News materiality matches whole words only.** `"sues"` is inside `"issues"`,
   `"fined"` inside `"refined"`, `"sued"` inside `"pursued"`. Boundaries are on
   the **front only**, because the phrases are stems and the press writes
   resigns/resigned/resignation for one event.

9. **Credit signals require a named agency.** A bare "(Rating Downgrade)" in a
   headline is almost always a blogger or a broker. Seeking Alpha files exactly
   that. The rules name Moody's, Fitch, S&P, CRISIL, ICRA, CARE.

10. **`GBp` is refused as a currency.** London quotes in pence. Writing `GBP`
    against a pence-denominated series overstates a position 100×.

11. **Listing market comes from Yahoo's `market` field, never `country`.**
    `country` is the head office: Alibaba returns China and the Infosys ADR
    returns India, but both are NYSE listings trading in dollars.

12. **`.SA` is São Paulo. Saudi Arabia is `.SR`.** One keystroke apart.

13. **CSS selectors are descendant, not child.** Streamlit wraps any button with
    a `help` tooltip in a `.stTooltipHoverTarget`, so `.stButton > button`
    silently stops matching and the button loses all brand styling — which
    previously produced navy-on-navy invisible buttons.

14. **Portfolios are private to the account that uploaded them (21 Sep).**
    `portfolio_store.load_registry(owner, is_admin)` filters by the signed-in
    email; only the admin's list carries the bundled sample, so anyone else
    the admin creates starts from nothing. A record written before this field
    existed (blank `owner`) is treated as the admin's rather than made
    invisible to everyone. `add`/`remove`/`rename` read `_load_all()` — every
    account's records, unfiltered — before rewriting the registry; the
    filtered `load_registry` must never be the thing that gets saved back, or
    saving one account's upload would erase every other account's.

---

## 6. The eight views

| View | Measure | Notes |
|---|---|---|
| Portfolio overview | IRR, total return | holdings table, sector/market weights (was "My investments") |
| Portfolio vs mandate | Direct Alpha + Modified Dietz | mandate rationale, benchmark dropdown, 1Y/3Y window returns |
| Winners and laggards | trend only | split on weighted alpha, attribution by sector/market |
| Holding analysis | live quote + trend | price, day move, position value, windows, chart, cashflows (was "Holding detail") |
| News | — | Key highlights, six categories, filings in an expander |
| Manage holdings | — | editable grid, record a trade, fill gaps, revert (was "Edit holdings") |
| Import portfolio | — | upload, registry, remove (was "Load portfolio") |

Renamed 21 Sep for a more professional-reading nav — the `view ==` checks in
`streamlit_app.py` use these new labels; grep for the old ones if a stale
reference turns up.

**Data sources removed 21 Sep** (was: provenance table, sector attribution,
rows excluded from analysis) at the owner's request -- `analysis.provenance`,
`analysis.source_notes` and the sector-attribution path in `sectors.py` are
still computed and cached, just no longer surfaced on any screen. Restoring
the view is a UI-only job if this is asked for again.

**Removed deliberately, do not restore without asking:** an interactive
dashboard ("not looking good"), Commentary (the numeric-guard demo), Fairness
caveats, Each stock vs its sector, and (as of 18 Sep) the two-column summary
card on Portfolio vs mandate that restated the KPI strip — Mandate now fills
the full width there. `narrative.py` and `adjustments.py` still exist and are
still tested; they have no screen.

**Casualties worth knowing:** holding-level Direct Alpha and KS-PME are no
longer displayed anywhere, and the two-currency split (`adjustments.currency_split`,
a feature the user specifically asked for on 17 Sep) has no screen. Both are
small jobs to restore if asked.

> **Open regression from the 18 Sep card removal:** that same card carried the
> sentence qualifying the win-rate count when it's below the holdings count
> ("N of the M positions are too recent to have a whole-life return…").
> `tests/test_trade_flows_through.py::test_a_count_over_scored_holdings_states_its_denominator`
> still asserts that sentence exists somewhere on screen and currently fails —
> see §9. Either restore an equivalent qualifier elsewhere on Portfolio vs
> mandate, or update the test to match the new UI; don't just delete the
> assertion without deciding which.

---

## 7. Editing the book

`holdings_edit.py` + the Manage holdings view. The grid is a **position sheet**,
one row per holding, matching what custodian statements contain.

- The uploaded file is **never** written over. Edits go to
  `<data root>/portfolios/<id>/edited.xlsx` and `edits.json` points at it.
  "Revert to upload" deletes the working copy.
- A save is refused on anything that would load and lie: zero quantity, two rows
  with one ticker, a future purchase date, a symbol the feed says does not exist.
- **A feed outage is not a spelling mistake.** Only "no such symbol" blocks a
  trade; a 429 or timeout records the position and says it is not yet valued.
  `unpriced()` retries with a pause and returns the feed's own words.
- Sector and market fill themselves in: exchange suffix → Yahoo → (sector only)
  the model. A value stated by the analyst outranks every source and is never
  looked up again.
- **Selling in full removes the row**, and the record of what it earned goes
  with it. This is a position sheet, not a trade ledger. Stated on screen.

---

## 8. Security and version control

- **Registration is admin-only (21 Sep).** The public "Add another analyst"
  expander on the sign-in screen is gone; `create_user` is now only called
  from an "Add an analyst" expander in the sidebar, gated on
  `user.role == "admin"`. There is exactly one admin — whoever the first
  account or `_seed_admin_account()` made one — and every account it creates
  defaults to `role="analyst"`. No self-service signup path exists any more.
- **`.env` holds a live Gemini API key.** It is in `.gitignore` and has never
  been committed — verified against the full history, not just the working
  tree. It must never be committed and must be deleted from any copy shared
  with anyone. `.env.example` is the file that belongs in the repo.
- `.gitignore` also excludes `data/portfolios/` (uploaded client statements)
  and `data/*.json` (account credentials and sessions — see the note on
  `_seed_admin_account()` in `auth.py`'s caller for why that matters on a
  redeploy). Verified: no file under either path has ever been committed.
- The repo is **public**, on a personal GitHub account (`391391391`), not a
  private company-org remote. The original guidance for this project called
  for the opposite. This was raised and the owner confirmed it's intentional
  for now — leave repo visibility alone unless asked to change it.

---

## 9. Testing

`pytest tests/ -q` → **511 passing, 1 failing** as of the last check (20 Sep),
~10s, no network needed — every external source is stubbed. The one failure is
the regression noted at the end of §6:
`test_a_count_over_scored_holdings_states_its_denominator` expects wording
from the summary card that was deliberately removed on 18 Sep and hasn't been
reconciled since. It is not caused by anything unrelated — don't chase it as a
new bug without first reading that note.

The tests that carry the most weight:

- `test_metrics.py` — golden numbers, hand-checked in Excel.
- `test_two_level.py` — the market-filtered benchmark selection.
- `test_trend_score.py` — the user's own worked examples, including a deliberate
  −14.2 where the spec said −13.6 (the spec had an arithmetic slip; the
  docstring explains why it is not "fixed").
- `test_trade_flows_through.py` — the regression that a trade reaches every
  view. Two of its tests read `app/streamlit_app.py` as text.
- `test_highlights.py` — mostly asserts what must **not** be flagged, using real
  headlines the feed returned.

---

## 10. Known open items

- The denominator-qualifier regression in §6/§9 — needs a decision, not just a
  fix.
- Dead files never deleted: `ipo_sources.py`, `ipo_score.py`,
  `scripts/run_ipo.py`, `scripts/run_league_table.py`, `tests/test_ipo_score.py`,
  `sql/` (empty), `data/demo/meridian_family_office.xlsx`. They were left
  because the old OneDrive folder blocked programmatic deletion; that
  restriction doesn't apply here any more, so this is just cleanup nobody has
  done yet.
- The sample book may still carry two test positions added during
  development — `E2E.NS` (100,000 @ 600) and `BAJFINANCE.NS`. `E2E.NS` scores
  +150% weighted alpha and dominates Winners and laggards if present. Check
  Manage holdings and delete both if the sample is being shown to anyone.
- Cold-start test never run: fresh venv, README only, reach a working app.
- LAN sharing: IT need `http://PSA-LPT-063:8501` (hostname — the DHCP address
  has already changed twice). A firewall rule needs an Administrator PowerShell.
