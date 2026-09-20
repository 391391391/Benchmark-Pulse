# Benchmark Pulse

Two-level equity benchmarking for Preferred Square.

A portfolio is judged twice, because two different questions matter:

**Level 1 — did the book beat the market it was hired to beat?** The comparator
comes from the mandate, and the mandate is a portfolio *type*. Measuring a
technology book against a broad world index would report an allocation bet as
skill.

| Portfolio type | Benchmark | Read from |
| --- | --- | --- |
| Global Equity | MSCI ACWI | iShares MSCI ACWI ETF |
| US Focused | S&P 500 Total Return | the index itself |
| GCC Equity | S&P GCC Composite | *see caveat* — iShares MSCI Saudi Arabia |
| India Equity | Nifty 500 | the index itself (**price-only**) |
| India Equity | Nifty 50 | Nippon India Nifty 50 BeES (total return) |
| REIT Portfolio | FTSE EPRA Nareit Global REITS | iShares Global REIT ETF |
| Banking Portfolio | MSCI Banks | *see caveat* — iShares Global Financials |
| Technology Portfolio | Nasdaq-100 | Invesco QQQ Trust |

### Every holding in its own currency

A holding is priced in the currency of the market it trades in — **INR** for
Indian listings, **USD** for American, **EUR** for Dutch, **SAR** for Saudi —
and measured against a benchmark quoted in that same currency. Reading a Dutch
company in dollars mixes a currency view into what is meant to be a view of the
business.

That is why the Dutch holdings are the Amsterdam lines (`ASML.AS`, `INGA.AS`)
rather than the New York ADRs, and why they benchmark to the euro-denominated
AEX tracker rather than the dollar-quoted `EWN`.

Where a holding's currency and its benchmark's are the same, there is no
translation inside the comparison and only one return is reported.

**Where they differ, both returns are shown.** A return is a percentage, so a
rupee holding can be compared to a dollar index without converting anything,
and the alpha is computed that way — in the holding's own currency, which is
what isolates the business from the exchange rate. But the investor's own
outcome is a different number, so the table carries the local return with the
translated one beneath it and the annual currency move that separates them.
Neither is *the* return. On this book the rupee cost about **4.8% a year** and
the euro added about **2–3%**, which is the difference between Infosys at
−6.1% and −10.6% depending on whose question you are answering.

Where a currency is pegged the two are the same number and the line says so
rather than reporting a few basis points of noise as a currency view.

The Saudi holdings are the one place where the quote currency differs, and the
benchmark is still a Saudi one — **MSCI Saudi Arabia**, Saudi companies on a
total-return basis. It is quoted in dollars, which costs nothing: the riyal has
been pegged at 3.75 to the dollar since 1986, so a Saudi return in riyals and
the same return in dollars are the same number. Tadawul's riyal-quoted TASI is
not used because its free feed is unreliable — it returned five years of
history and then a single observation within the same day — and because it is
published price-only, which would understate the benchmark by the Saudi
market's dividend yield.

---

The type is read from the book, not its name: a **sector** above 60% of capital
makes it a sector mandate, otherwise a **market** above 70% makes it a
single-market one, otherwise it is Global Equity. Sector is tested first because
it is the more specific claim — a book that is mostly banks is a banking mandate
whichever countries those banks are in.

Three of the eight have no exact free tracker and say so on screen rather than
passing quietly as the index they stand in for. No tracker follows the S&P GCC
Composite — GULF and MES are both delisted — so Saudi Arabia stands in for the
region and understates Kuwait, Qatar and the UAE. No free tracker follows an
MSCI Banks index, so global financials stands in and is broader than banking.
And NSE publishes the Nifty 500 price-only, which understates the benchmark by
its dividend yield, so the total-return Nifty 50 sits beside it as the
narrower-but-honest alternative.

The benchmark is a dropdown on the **Portfolio vs mandate** view. Changing it
recomputes every figure on the page, which is the fastest way to see how much
the choice is worth: the sample book reads **+1.2%** against MSCI ACWI and
**−4.0%** against the Nasdaq-100.

**Level 2 — did each company beat its own peers?** Peers means the same sector
**in the same market**. ICICI Bank is measured against the Nifty Bank index, not
a global financials index. Apple is measured against US technology, not world
technology.

The reason is that a holding never chose its country — the allocator did.
Charging a stock for its market's performance measures the allocator twice and
the stock not at all. The country bet belongs at level one, where someone is
accountable for it.

This is not a small correction. **Four of the ten holdings change verdict**
entirely when measured at home rather than globally:

| Holding | vs global sector | vs its own market |
| --- | --- | --- |
| ICICI Bank | −2.7% *laggard* | **+3.5% winner** |
| Al Rajhi Bank | −12.7% *laggard* | **+6.6% winner** |
| ASML | −2.2% *laggard* | **+9.9% winner** |
| Sun Pharmaceutical | +10.1% *winner* | **−1.0% laggard** |

Sun Pharmaceutical is the instructive one. Indian pharma returned about 20% over
the period while global healthcare returned about 8%, so an 18.7% return that
looked strong against the world was in fact behind its actual peers. The global
benchmark had been paying it for being in the right country.

The sample book holds **four banks in four markets** — JPMorgan, ICICI, Al Rajhi
and ING — and they take four different benchmarks. A tool with one benchmark
measures all four against the same index and calls the difference skill.

The two levels routinely disagree, and the disagreement is the finding. A book
can beat its mandate while most holdings trail their sectors, which means
allocation rather than stock selection did the work.

### One year and three, on the mandate page

Direct Alpha is whole-life and money-weighted, which is the right measure of an
investment and the wrong answer to "how did we do last year". That question gets
its own arithmetic, at portfolio level only:

```
gain = closing value - opening value - new cash injected + anything taken out
```

Subtracting injections is the whole point — without it a book that simply
received money would read as having made it. That identity is the numerator of
the **Modified Dietz** return, and the denominator follows from it: each
contribution is weighted by the share of the window it was actually invested
for, so a purchase made in the final month is not counted as a full year of
capital. Every input is printed under **The working**, so the figure can be
checked rather than taken.

The sample book is **+6.9% over one year** and **−0.5% a year over three**
against MSCI ACWI, while Direct Alpha since inception is **+1.2% a year**. Three
different signs, all correct: one year is the current trend, three years is the
longer record, and Direct Alpha is what this client's capital actually earned
given when it went in. The page says so rather than leaving a reader to decide
which number is broken.

---

## Running it

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

python scripts\make_demo_book.py      # builds the sample portfolio (needs network)
streamlit run app\streamlit_app.py --server.address 127.0.0.1
```

Then open <http://localhost:8501>.

Streamlit binds to every interface by default, which puts the sign-in page on
the local network with no HTTPS in front of it. `--server.address 127.0.0.1`
keeps it on this machine, which is where a pilot holding real positions belongs.

### Signing in

The first launch asks you to create an account; that first account becomes the
admin. Sign-in exists so a benchmark change records an authenticated person
rather than whatever someone typed into a box.

It is a pilot-grade local gate, not enterprise identity: passwords are scrypt
hashes in `users.json` under `%LOCALAPPDATA%\BenchmarkPulse`, there is no reset
and no multi-factor, and the session token travels in the page URL. A session
survives a refresh and lasts 12 hours from last use; signing out revokes it
server-side. For firmwide use `auth.py` is replaced by Microsoft Entra ID and
nothing else moves.

### A language model is optional

Every figure is computed by the engine and works with no model at all. A model
is used for judgement calls only: choosing benchmarks and explaining those
choices, classifying a news item, and naming a sector the price feed has no
answer for. Without a key the app falls back to deterministic rules and says so
on screen.

To enable it, create `.env` beside this file:

```
GEMINI_API_KEY=your-key-here
```

A free key comes from <https://aistudio.google.com/apikey>. Groq, Anthropic and
Azure OpenAI are also supported — see `.env.example`. The adapter in
`src/benchmark_pulse/llm.py` is the only file that knows which provider is in
use.

---

## Keeping the book current

A portfolio is not a document, it is a position that changes. Re-uploading a
spreadsheet after every trade is how a monitoring tool stops being used by the
second week, so **Edit holdings** puts the book on screen as a table: correct a
quantity, add a row for a new position, delete one that has been sold, save, and
every figure recomputes.

**Record a buy or a sale** does the arithmetic that people get wrong by hand. A
buy re-weights the average cost across the old and new shares — 1,000 at 150
plus 500 at 300 is 1,500 at 200 — and keeps the original purchase date, because
that is what every since-inception figure is measured from. A sale leaves the
average cost alone, since selling does not change what the remaining shares
cost.

Three things make it safe to use on a client book:

- **The upload is never written over.** Edits go to a working copy under the app's
  data root and the registry points at it, so the file the client sent stays
  exactly as they sent it. **Revert to upload** discards every edit in one press.
- **What will change is shown before it is saved**, row by row, and a save is
  refused outright on anything that would load and lie: a zero quantity, two rows
  claiming one ticker, a purchase dated next month, or a symbol the price feed
  says does not exist.
- **An outage is not a spelling mistake.** A rate limit and a bad ticker fail
  identically at the feed, so the two are told apart by what it says: only "no
  such symbol" blocks the trade. Anything else — a 429, a timeout — records the
  position and says it is not yet valued, carrying the feed's own words so a
  recurrence can be reported precisely. Refusing to record a real purchase
  because Yahoo was busy is the wrong way round; the position exists either way.
- **Other sheets survive.** A book carrying fund commitments or property beside the
  equities does not lose them the first time someone corrects a share count.

### The market fills itself in too

The market matters more than the sector: it decides which country's indices a
holding is eligible for at all — the whole point of measuring an Indian bank
against Bank Nifty rather than global financials — and it decides the currency.

It is read from the **exchange suffix** first, which is deterministic, offline
and instant: `.SR` is Tadawul, `.NS` is the NSE, `.AS` is Amsterdam. A bare
symbol goes to Yahoo, and the currency comes back with it. Recording a buy no
longer asks for the market at all — the dropdown says *Auto, from the ticker*
and you override it only for a symbol nothing can place.

Two traps, both guarded:

- **Listing venue, never head office.** Yahoo's `country` field is the company's
  address. The Infosys ADR comes back as India and Alibaba as China, when both
  are New York listings trading in dollars. The listing country is read from
  Yahoo's `market` field instead, so `INFY` is a US listing and `INFY.NS` an
  Indian one — the same business, correctly benchmarked two different ways.
- **Pence are not pounds.** London quotes in pence and Yahoo marks it `GBp`.
  Writing `GBP` against a price series denominated in pence overstates the
  position a hundredfold, so a fractional-unit code is refused and reported
  rather than accepted.

An unrecognised symbol stays blank rather than defaulting to a US listing in
dollars — which is how a Tadawul code typed without `.SR` would have ended up
measured against the S&P.

### Sectors fill themselves in

A blank sector is not a cosmetic gap — it costs a level of the analysis. With
one, an Indian bank is measured against the Nifty Bank index; without one it
falls back to the broad market and its industry is never isolated. Client
statements routinely arrive with no sector column at all.

So on upload, and when a buy opens a new position, the sector is looked up.
**Yahoo Finance** answers first, because it is the same feed the prices come
from and the sector then agrees with the price about which company this is.
Probed against this book it resolved US, Indian, Dutch, Danish and — the market
that defeats most sources — Saudi listings. Anything it holds no sector for goes
to **the model**, which is the only input in the tool carrying no citation, so
it is marked wherever it appears and never overrides a source that answered. A
sector stated in the client's own file outranks both and is never looked up.

Worked examples from the live feed:

| Ticker | Answer | Source |
| --- | --- | --- |
| `RELIANCE.NS` | Energy | Yahoo Finance |
| `2010.SR` (SABIC) | Materials | Yahoo Finance |
| `7203.T` (Toyota) | Consumer Discretionary | Yahoo Finance |
| `TATAMOTORS.NS` | Consumer Discretionary | model — Yahoo 404s the symbol |
| `ZZZZNOTREAL` | *nothing* | both declined, and it says so |

That last row is the point: an unrecognised company returns no sector rather
than a plausible one, because inventing a sector silently picks a benchmark.
Every answer is cached, editable in the grid, and listed with its source under
**Data sources**.

When nothing can place a company, **Edit holdings** says so rather than leaving
a blank cell. Each holding without a sector is listed with what happened to it —
`not found`, with the reason each source gave, or `not looked up` if none has
been asked yet — and beside it a dropdown of the eleven sectors to **set one by
hand**. That is not a fallback for when the lookup fails: a sector an analyst
states outranks every feed, is written as stated, and is never looked up or
overwritten afterwards.

The **SEC's SIC code** was tested as a primary source and rejected. It is the
most official option available, it covers only US filers — three of this book's
eleven holdings — and it returns a 1987 taxonomy ("Electronic Computers" for
Apple) that needs its own crosswalk before it says anything about a sector.
Tadawul and the NSE both block automated requests, and **GICS itself**, the
taxonomy these benchmarks are built on, is licensed by S&P and MSCI with no free
API.

The one limitation worth stating plainly: this is a **position sheet**, not a
trade ledger. Selling in full removes the row, and the record of what that
holding earned goes with it — so every return in the tool is the return on what
is still owned. Keeping realised positions would need a second file and a larger
change.

---

## Where the data comes from

| Data | Source | Basis |
| --- | --- | --- |
| Exchange rates | **European Central Bank** reference rates | Official |
| Share prices, index levels | Yahoo Finance | Third party |
| Sectors | Yahoo Finance, then the model for gaps | Third party / inferred |
| Live quotes | Yahoo Finance, held 60s, last close on failure | Third party |
| Listing market, currency | The ticker's exchange suffix, then Yahoo Finance | Deterministic / third party |

Currency comes from the institution that publishes the reference rate. Prices
come from Yahoo Finance, which is the only free feed covering US, Indian and
Saudi listings together — stated plainly rather than implied to be official.
`marketdata.py` is the only module that knows where prices come from, so a firm
data licence is a single adapter away.

FRED and Stooq were both tested and rejected: FRED is unreachable from the
firm's network, and Stooq serves a JavaScript challenge instead of CSV.

Benchmarks name the index they represent, its publisher, and how the level was
obtained. Where an index is published price-only, the level is read from the
fund that tracks it, because holdings are measured on a total-return basis and
comparing them to a price index overstates alpha by roughly **1.9 percentage
points a year** on the S&P 500 (measured, not estimated).

That rule is why the Indian sector comparators are the Nifty sector ETFs rather
than the Nifty sector indices themselves: `^NSEBANK`, `^CNXIT` and the rest all
carry history, but NSE publishes them price-only.

Where a market publishes no total-return index for a sector, the holding falls
back to that market's broad index rather than borrowing a foreign sector one.
Saudi Arabia and the Netherlands publish no free sector index, so their holdings
are measured against the Saudi and Dutch markets respectively, and the app
labels that *only benchmark for this market* — a determined choice rather than a
model's guess. A fair comparison with a stated limitation beats a
precise-looking comparison against the wrong peer group.

Prices refresh on every page load. Fifty-odd series are pulled in one batched
request, so a full refresh takes about ten seconds rather than a minute.

---

## The live price, and what "live" is allowed to mean

**Holding detail** opens with what the security is trading at now: the price,
the move on the day against the previous close, the position at that price, and
the gain against average cost. All in the listing currency.

Three rules keep the claim honest, because a book across four timezones will
always have some exchanges trading and some shut hours ago:

- **"Live price" only while the exchange is open.** Outside regular hours the
  feed's "current price" is the last completed session's, so the tile says
  **Last price** and the caption says *pre-market*, *after hours* or *market
  closed* with the timestamp in the exchange's own zone — `18 Sep 14:42 IST`,
  not the reader's clock.
- **It never fails.** A refusal, a timeout or offline mode falls back to the
  last cached close, relabels the tile **Last close**, and prints the reason.
  A holding page that cannot reach the market should say so, not raise.
- **It never mixes units.** If the feed quotes in a different currency from the
  book — London in pence against a book in pounds — the position value is left
  out rather than computed across two units, and the page explains why.

Quotes are held for sixty seconds inside the shared market client. That endpoint
is the one Yahoo rate-limits hardest and Streamlit re-runs the script on every
widget touch, so without the hold, moving the holding dropdown would earn a 429
within the minute.

---

## Judging a holding without its cashflows

**Winners and laggards** and **Holding detail** measure every holding against
its benchmark over **six months, one year and three years** — point to point on
both sides, across the same trading days.

Nothing on those two pages depends on when a position was opened, what was paid
for it, or how much went in. That is deliberate, and it is the reason they
carry no IRR and no Direct Alpha: a money-weighted figure credits the timing of
purchases, and the person being judged is the person who chose the timing. Both
pages print the two sides of every gap rather than only the difference, because
+5% built from +30 against +25 is a different fact from +5% built from −10
against −15, and only the pair tells them apart.

The money-weighted view is still in the tool, on **My investments** and
**Portfolio vs mandate**, where it answers the other fair question — what the
client's capital actually earned. It is labelled as such wherever it appears.

### The trend score

One window can say anything. A holding can be ahead over six months and behind
over three years, and reporting whichever flatters the answer is how a scorecard
stops being useful. So all three are shown, then combined:

```
Weighted alpha = 6M alpha x 20%  +  1Y alpha x 30%  +  3Y alpha x 50%
```

where alpha is the security's return minus its own benchmark's over the same
window. The three-year number decides half the score on its own: a full cycle
says more about a company than two quarters, which are as often sentiment as
substance. Windows longer than a year are annualised on both sides.

The windows were 3M / 6M / 12M when the scorecard was first specified and were
widened to 6M / 1Y / 3Y later. The arithmetic is untouched — three windows at
20/30/50, longest heaviest — so the original worked examples still reconcile
exactly, and the tests say so.

| Weighted alpha | Status | Action |
| --- | --- | --- |
| above +10% | Strong outperformer | Hold |
| +3% to +10% | Outperformer | Hold |
| −3% to +3% | Neutral | Hold |
| −3% to −10% | Underperformer | Watchlist |
| below −10% | Severe underperformer | Exit review |

The weighting earns its keep in both directions on the sample book. **ASML** is
+10.2% over six months and +72.8% over one year but +21.2% a year over three,
and scores **+34.5%**. **Apple** is +4.3% over a year and −6.1% a year over
three, and scores **−2.0%** — a good twelve months inside a three-year record
that trails its sector, which one window alone would have hidden either way.

Where a window has no data — a recent listing, a benchmark that does not reach
back — its weight is shared across the windows that do, and the row is marked
**partial**. Counting it as zero would quietly drag every young holding towards
Neutral and make a real signal look like an average one. A holding with no
window at all is listed as **not scored** rather than ranked from no evidence.

---

## Company news

The **News** view lists what each company was legally required to tell an
exchange, and when. These are filings, not press coverage: the filing is the
event and an article is somebody's account of it. Clicking a headline opens the
filed document itself, which is the source.

| Market | Feed | Holdings covered |
| --- | --- | --- |
| US, and any foreign issuer with a US listing | **SEC EDGAR** | 7 of 10 |
| India | **NSE corporate announcements** | 3 of 10 (Sun Pharma only there) |
| Saudi Arabia | none — Tadawul returns HTTP 403 | 0 of 2 |

**The six categories are not a model's opinion.** Both exchanges publish their
own subject taxonomy and the mapping uses it. An SEC 8-K carries item codes:
item 2.02 *is* "Results of operations and financial condition", item 4.01 *is* a
change of certifying accountant — the auditor-resignation case. Every NSE
announcement carries a subject field. The regulator already did the
classification; this only has to not lose it.

Rows marked **inferred** are the exception. A bare Form 6-K has no subject — its
cover page is boilerplate — so the category comes from wording in the company's
own exhibit title. Marking them is the point: a reader can tell a regulator's
classification from ours.

**Filtering is half the feature.** JPMorgan has filed 22,711 prospectus
supplements for structured notes and Apple 590 insider-transaction forms. An
unfiltered EDGAR feed buries the four earnings releases an analyst wants under
thousands of routine documents about somebody else.

The two Saudi holdings show a stated reason rather than an empty list. Filling
that gap with press coverage would put a different class of source behind the
same heading, and a reader could not tell which rows were regulated disclosure.

---

## How a figure is verified

Numbers never come from the model. Every figure on every screen is computed by
the engine — `metrics.py`, `directalpha.py`, `window_return.py` — and the model
is used only where the answer is a judgement rather than a number: which
benchmark a holding belongs against, which sector it trades in, which category a
news item falls into. Those are labelled on screen with their source, and none
of them is arithmetic.

The one place the model was allowed near prose is `narrative.py`, which writes
client commentary over a fact table it cannot add to:

1. The engine computes every figure and records each as a fact with an
   identifier, a label and the exact string it should appear as.
2. The model receives only that fact table and writes prose over it. It never
   calculates and never sees the portfolio.
3. A guard reads the finished text, extracts every number, and checks each
   against the table. Anything unaccounted for blocks publication.

The guard is string arithmetic with no model involved, so it cannot be reasoned
out of a refusal. **The Commentary view was removed from the app**, so this path
is no longer reachable from the interface; the module and its tests remain, and
the tamper test (`tests/test_narrative.py`) still proves the guard refuses a
figure that is not in the table.

---

## Layout

```
src/benchmark_pulse/
  cashflows.py      every holding reduced to one shape: money out, money in, value today
  metrics.py        XIRR, TVPI, DPI, TWR  -- deterministic, no model, no network
  directalpha.py    KS-PME and Direct Alpha, the common measure
  aggregate.py      holdings rolled into one portfolio-level cashflow stream
  benchmarks.py     the verified benchmark universe and both levels of selection
  adjustments.py    leverage, J-curve, staleness and currency caveats
                    (no page of its own; surfaced beside the holding)
  analysis.py       orchestration: one code path for the app, CLI and commentary
  narrative.py      grounded commentary and the numeric guard (not wired to the app)
  marketdata.py     prices, live quotes, FX, caching, offline mode
  sources.py        ECB access and provenance records
  portfolio.py      tolerant reader for client workbooks
  holdings_edit.py  the editable grid: read, validate, apply a trade, write back
  sectors.py        what a ticker is: sector, listing market and trading currency
  ingest.py         Excel, CSV, PDF and PowerPoint onto one schema
  periods.py        1D to 5Y windows, and the weighted-alpha trend scorecard
  window_return.py  the portfolio's 1Y and 3Y return, net of contributions
  news.py           exchange filings, categorised by the exchange's own taxonomy
  auth.py           sign-in and sessions -- the only module Entra ID replaces
  brand.py          Preferred Square design system
app/streamlit_app.py
scripts/            make_demo_book.py, run_benchmark.py, show_rationales.py
tests/              443 tests
```

Run the tests with `pytest`. The engine tests use hand-checked golden numbers;
the guard tests are the ones the pitch rests on.

---

## Two things to know about this machine

**The project folder rejects new files.** OneDrive Files On-Demand or an
endpoint agent blocks file *creation* in the synced folder — reads and edits
work, creates fail with a misleading `FileNotFoundError`. Everything written at
runtime therefore goes to `%LOCALAPPDATA%\BenchmarkPulse`: the market-data
cache, uploaded portfolios, benchmark decisions, **and the generated sample
workbook**. `paths.py` resolves this by attempting an actual file creation
rather than trusting permission bits, and `BENCHMARK_PULSE_DATA` overrides it.

That is why `make_demo_book.py` must be run before the first launch — the
sample is generated, not shipped.

**On Streamlit Community Cloud the account file does not survive a redeploy.**
`data/users.json` is git-ignored and lives on the app's own ephemeral disk, so
a restart forgets every account and shows "create the first account" again.
Set `ADMIN_EMAIL`, `ADMIN_NAME` and `ADMIN_PASSWORD` in the app's Streamlit
secrets (Settings -> Secrets in the Cloud dashboard) and that account is
recreated automatically on startup whenever none exists, so sign-in keeps
working across redeploys. Locally, the equivalent environment variables
(`BENCHMARK_PULSE_ADMIN_EMAIL`, `_ADMIN_NAME`, `_ADMIN_PASSWORD`) do the same.

**Offline mode** is in the sidebar. Turn it on before presenting and the app
uses only cached data, so a dropped connection cannot break a demo. Load the
app once on the network first to warm the cache.

**The typefaces are held locally too.** Loading Manrope and Work Sans from
Google would make the look of the tool depend on an outbound request, and it
fails quietly: nothing errors, the page simply renders in Segoe UI, whose
metrics differ enough to move alignment that was measured against Manrope. That
matters here because the demo runs with Offline mode on, and the firm's network
already blocks FRED. So the two faces are cached once as data URIs alongside the
price cache:

```bash
python -c "import sys; sys.path.insert(0,'src'); from benchmark_pulse import brand; print(brand.build_font_cache())"
```

Run it once from a machine with a network. If the cache is absent the app falls
back to the Google import, so a fresh clone still looks right. The cache is
99 KB because the request asks for weight *ranges* rather than a list of
weights, which returns one variable font per family instead of eight static
instances -- a list costs 662 KB for the same result.
