# Demo script — Benchmark Pulse

Six minutes. Four beats. Every number below is real output from the sample
book; re-check them on the morning, because prices refresh on every load.

---

## Before you start

1. **Sign in before you share your screen.** The session token sits in the
   address bar once you are in, and a refresh keeps you signed in for 12 hours,
   so you will never hit the login screen mid-demo. Do not open on it either —
   beat 1 should start on **My investments**.
2. Load the app once **on the network you will present from**. This warms the
   price cache and the model-response cache.
3. Turn **Offline mode** on in the sidebar. The demo then runs entirely from
   cache and a dropped connection cannot break it.
4. Have the **Commentary** view generated already — it takes a few seconds the
   first time.
5. Keep the browser at full width. The holdings table is designed to show the
   whole book without scrolling.

---

## Beat 1 — the problem, in one screen (60s)

Open on **My investments**.

> "Ten listed holdings across the United States, India, Saudi Arabia and the
> Netherlands. Four sectors, three currencies. Small enough that you can check
> every number on this screen yourself — which is the point. The question a
> client asks is simple — *am I doing well?* — and it is surprisingly hard to
> answer, because it is really two questions."

Filter to **Financials** to show the book slicing live, then clear it. Four of
the ten are banks; that matters in beat 3.

---

## Beat 2 — level one: the mandate (90s)

Go to **Portfolio vs mandate**.

> "First question: did the book beat the market it was hired to beat? That
> depends on the mandate, and the mandate is not what the portfolio is called —
> it is what the portfolio holds."

Point at the composition: **US 31%, SA 27%, IN 26%, NL 17%** by market, and
**Financials 39%** as the largest sector.

> "The first question is what kind of book this is, because the type decides the
> benchmark. A sector above 60% makes it a sector mandate — REIT, banking,
> technology. A market above 70% makes it single-market — US, India, the Gulf.
> Neither threshold is met here, so it is a diversified global book.
>
> That reasoning is on screen, with a confidence, and the analyst can overrule
> it."

Use the **Change the benchmark** dropdown. Pick **QQQ — Technology Portfolio**,
let the page redraw, then put it back to **ACWI**.

> "And this is not locked in. Every figure recomputes against whichever
> yardstick you choose. Against MSCI ACWI this book is **+1.2%**. Against the
> Nasdaq-100 it is **−4.0%**. Same portfolio, same dates, five points apart.
>
> That is the argument for getting the mandate right, made by just showing it."

> "Result: the book returned **19.0%**, the mandate benchmark **17.5%**. It beat
> its mandate by about a point and a half."

Hold that number. It is about to be contradicted by the next screen, and the
contradiction is the finding.

---

## Beat 3 — level two, and the moment that justifies the design (2 min)

Go to **Each stock vs its sector**.

> "Second question: did each company beat its own peers? And peers means the
> same sector **in the same market**."

**Start with the four banks.** This is the strongest thing on the screen.

> "Four of these ten are banks. JPMorgan, ICICI, Al Rajhi, ING. Look at what
> they are measured against: **US financials, the Nifty Bank index, the Saudi
> market, the Dutch market.** Four banks, four different benchmarks.
>
> A tool with one benchmark measures all four against the same index and calls
> the difference skill. It isn't skill. It is which country the allocator chose,
> and that decision was already judged on the previous screen."

Then **ICICI Bank** specifically.

> "Against global financials, ICICI shows **−2.7%** — a laggard. Against Indian
> banks, **+3.5%** — a winner. Same stock, same period, opposite verdict, and
> six percentage points between them.
>
> A holding never chose its country. The allocator did. Charging the stock for
> that decision measures the allocator twice and the stock not at all."

**Al Rajhi Bank is the same story, larger**: −12.7% against global financials,
**+6.6%** against its own market. Nineteen points.

Then **Apple**, for the sector half of the argument.

> "Apple returned 21.5%. Against the S&P 500 that is comfortably ahead — it
> looks like good stock picking. Against US technology, which is what Apple
> should be measured against, it is **behind**. The sector did the work."

If there is time, **Sun Pharmaceutical** flips the other way:

> "Sun returned 18.7%. Global healthcare returned about 8%, so against a global
> index it looks excellent — **+10.1%**. But Indian pharma returned about 20%.
> Sun actually **trailed** its real peers. The global benchmark was paying it
> for being in the right country."

Then go to **Winners and laggards** — open on the **Trend scorecard**.

> "One window can say anything, so we show three and weight them: three months
> at 20%, six at 30%, twelve at 50%. A year-long trend says more about a company
> than a quarter, which is as often sentiment as substance."

Point at **ASML**, top of the table.

> "Minus ten over three months. Plus sixty over twelve. Score **+29%** — a
> strong outperformer having a bad quarter. A three-month screen would have
> flagged it for review."

Then the bottom of the table, **Infosys**.

> "Negative in all three windows. Score **−10.9%**, which is the severe band.
> That is not a bad month, it is a trend, and the status says *exit review*
> rather than leaving someone to eyeball three columns."

**If asked why this disagrees with the alpha on the other screens:** it is a
different question. Direct Alpha is what *your money* made from the day you
bought. This is what the *security* has done lately, whenever you bought. Apple
is Direct Alpha **−4.3%** and trend **+5.3%** — bought at a bad moment,
recovering since. Four of the ten disagree, and each disagreement is worth a
conversation.

Scroll to **Direct Alpha, since you bought**.

> "Six of ten beat their sector. Four trail. And remember the previous screen:
> the book **beat** its mandate by 1.5 points. Both are true. The book was in
> the right markets and picked the wrong names in four of them — allocation
> carried it, selection dragged. That gap is the finding, and it is invisible to
> a tool with one benchmark."

**If asked "why is Saudi Aramco measured against the whole Saudi market?"**
Saudi Arabia publishes no free total-return sector index. The tool uses the
Saudi market and says so rather than reaching for a global energy index, which
would charge Aramco for the Gulf-versus-world trade. The app labels that
decision *only benchmark for this market* — it is a determined choice, not a
model's guess, so it is not sent for review. The same is true of the two Dutch
holdings.

**If asked why level one and level two disagree:** different benchmarks, and
deliberately so. Level one compares the whole book to a world index, because the
book chose its countries. Level two compares each holding to its own market and
sector, because a holding did not. The two answering differently is the tool
working, not a bug — and reconciling them is the analyst's judgement, which is
exactly the part worth an analyst's time.

---

## Beat 3b — what moved it, from the filings (45s, optional)

Go to **News**, pick **Sun Pharmaceutical**.

> "A benchmark tells you a holding trailed. The next question is always *why*,
> and the honest answer starts with what the company actually disclosed.
>
> These are not press articles. They are filings — what the company was legally
> required to tell an exchange, and on what date. Click one and it opens the
> filed document."

Point at the categories.

> "Six categories, and they are not a model's opinion. An SEC 8-K carries item
> codes: item 2.02 **is** 'results of operations', item 4.01 **is** a change of
> auditor. Every NSE announcement carries a subject field. The regulator already
> classified it; we just don't lose it."

Then pick **Saudi Aramco**.

> "And where we cannot see, we say so. The Saudi Exchange refuses programmatic
> access, so this is blank with a reason rather than filled with press coverage.
> A licensed Tadawul feed is one adapter away."

**If asked about volume:** JPMorgan has filed 22,711 prospectus supplements.
Unfiltered, they bury the four earnings releases you want. The filter is half
the feature.

---

## Beat 4 — why you can trust the numbers (90s)

Go to **Commentary**.

> "This is written by a language model. The first thing anyone asks is: what if
> it invents a figure?"

Point at the fact table on the right.

> "The engine computes every figure and records it here. The model receives only
> this table. It never calculates, and it never sees the portfolio."

Click **Tamper with a figure**.

> "Now I have altered one number in the text — the way a model might if it had
> been allowed to do arithmetic."

The banner turns red. The blocked figure is listed with its sentence, and
**Publish is disabled**.

> "A guard reads the finished text, pulls out every number, and checks each one
> against the table. Anything it cannot trace blocks publication. That guard is
> string arithmetic — there is no model in it — so it cannot be talked out of a
> refusal."

Click **Regenerate** to return to a verified draft.

---

## Closing (30s)

> "Portfolio Analysis and Monitoring appears in six of Preferred Square's eight
> client segments. It is the firm's most repeated billable service, and it is
> assembled by hand every cycle. This does the assembly in about ten seconds and
> leaves the analyst the part that is actually second-order thinking.
>
> Currency comes from the European Central Bank. Prices come from a free feed
> today and are one adapter away from the firm's own data. The model chooses
> benchmarks and writes prose; it never produces a number."

---

## Questions to expect

**"Why Yahoo Finance?"**
No free source is authoritative for global equity prices. It is the only one
covering all three of your markets, and we say so rather than implying
otherwise. Currency uses the ECB, where an official source does exist. One
module knows where prices come from.

**"Why an ETF and not the index?"**
The real S&P sector indices are available, and they are **price** indices.
Holdings are measured on a total-return basis, so comparing them to a price
index overstates alpha by about 1.9 points a year — measured on the S&P 500.
The index is named; the fund is how a total-return level is obtained.

**"What if the model picks a bad benchmark?"**
It chooses from a closed list of verified tickers, so it cannot invent one — and
that list is filtered to the holding's own market *before the model sees it*, so
it cannot reach a global index for an Indian bank even if it wants to. The
country rule is structural, not a line in a prompt. It states a confidence and
its reasoning, and the analyst can change it from a dropdown — every figure
recomputes. Each change is recorded with who and when. Those records are the
most valuable data the system produces: each one is an expert disagreeing with
the model, which is exactly what a later review of benchmark policy needs.

**"Why not benchmark everything globally? It's one portfolio."**
Because the portfolio and the holding answer different questions. The portfolio
*did* make a country decision, so it is measured globally — against MSCI ACWI.
A holding did not. Measuring both globally would judge the country bet twice and
tell you nothing about the stock.

**"Does this handle private assets?"**
Not in this build; it is deliberately public equity only. The engine underneath
reduces every holding to cashflows, so private funds and direct property are a
natural extension rather than a rewrite.

**"How current is the data?"**
Prices refresh on every page load — the timestamp is in the header. It is
running from cache right now so the demo cannot break.
