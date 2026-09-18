"""Tests for the material events lifted to the top of the news page.

Six categories tell a reader what a story is about. They do not tell them which
three of ninety items would move a valuation. This pass does, and the rule it
lives or dies by is precision: a story missed here is still in its category one
click below, while a broker opinion piece promoted to the top under a red flag
costs the section the credibility that makes it worth having.

So most of what follows is about what must NOT be flagged. The headlines in the
negative cases are the shapes this feed actually returns.
"""

from __future__ import annotations

import pytest

from benchmark_pulse.news import (
    Category, NewsFeed, NewsItem, highlight,
)


def item(headline: str, when: str = "2026-09-18", category=Category.OTHER,
         url: str = "https://example.com/a") -> NewsItem:
    return NewsItem(when=when, headline=headline, category=category.value,
                    subject=headline, source="Reuters", url=url,
                    official=False, classified_by="wording")


# -- the events the user asked for -------------------------------------------

@pytest.mark.parametrize("headline,label,tone", [
    # A chief executive leaving. Neutral: material whichever way it reads.
    ("Infosys CEO resigns after six years at the helm",
     "Leadership change", "neutral"),
    ("Reliance appoints new CEO for retail arm",
     "Leadership change", "neutral"),
    ("Apple CFO steps down, successor named", "Leadership change", "neutral"),
    # An order book won.
    ("L&T bags order worth Rs 5,000 crore from NHAI", "Order win", "good"),
    ("Infosys wins contract with European bank", "Order win", "good"),
    ("Company reports record order book for the quarter", "Order win", "good"),
    # A suit filed.
    ("Shareholders file class action against the company",
     "Legal action", "bad"),
    ("Regulator sues bank over mis-selling", "Legal action", "bad"),
    ("SEBI imposes penalty of Rs 25 crore on the promoter", "Penalty", "bad"),
])
def test_the_events_that_change_an_investment_case_are_flagged(
        headline, label, tone):
    assert highlight(headline) == (label, tone)


@pytest.mark.parametrize("headline,label,tone", [
    ("Moody's cuts the group's senior unsecured rating", "Credit event", "bad"),
    ("Fitch upgrades the issuer to BBB+", "Credit upgrade", "good"),
    ("Borrower defaults on $400m of notes", "Credit event", "bad"),
    ("Company cuts guidance for the full year", "Earnings miss", "bad"),
    ("Quarterly profit jumps 40% on strong demand", "Earnings beat", "good"),
    ("Firm to acquire rival for $2bn", "Deal", "neutral"),
    ("Board approves share buyback of Rs 10,000 crore",
     "Capital return", "good"),
    ("Drugmaker issues product recall across three states",
     "Regulatory setback", "bad"),
    ("Company receives approval for its lead candidate",
     "Regulatory approval", "good"),
])
def test_the_other_material_events_are_flagged(headline, label, tone):
    assert highlight(headline) == (label, tone)


# -- what must never reach the top of the page -------------------------------

@pytest.mark.parametrize("headline", [
    # The false positive that already bit the category classifier once.
    "3 Best Stocks to Buy Now in September",
    "Is Apple a Good Stock To Buy Now?",
    # Analyst noise. An "upgrade" in a press headline is a recommendation,
    # not a rating action, which is why the credit rules name agencies.
    "Analyst upgrades Apple to overweight, raises target price",
    "Brokerage downgrades the stock to hold",
    # Real headlines the live feed returned. Both are opinion pieces whose
    # authors call their own verdict a "Rating Downgrade".
    "ING Groep: Executing Well, But No Longer Undervalued (Rating Downgrade)",
    "Infosys: Next Fiscal Year Is Another Low-Growth Year (Rating Downgrade)",
    # Routine disclosure. "appoints" alone is not a leadership change.
    "Company appoints statutory auditors for FY27",
    "Notice of the 31st annual general meeting",
    "Disclosure under Regulation 30 of SEBI LODR",
    # Ordinary reporting of a scheduled event.
    "Company to announce quarterly results on 24 October",
    "Shares close higher in a firm market",
    # Machine-written price lines.
    "XOM|Exxon Mobil Corp|Price:169.320|Chg%:+4.240",
])
def test_ordinary_coverage_is_left_where_it_is(headline):
    assert highlight(headline) == ("", "")


def test_an_empty_headline_is_not_material():
    assert highlight("") == ("", "")
    assert highlight(None) == ("", "")


# -- the substring trap ------------------------------------------------------

@pytest.mark.parametrize("headline,expected", [
    # "sues" sits inside "issues". This one was caught by this test file
    # before the section ever reached a screen.
    ("Drugmaker issues product recall across three states",
     "Regulatory setback"),
    ("Company issues statement on the media report", ""),
    ("Board issues bonus shares to existing holders", ""),
    # "fined" sits inside "refined", which every energy headline carries.
    ("Refined product margins widen on strong cracks", ""),
    ("Well-defined strategy pays off, says chief", ""),
    # "sued" sits inside "pursued".
    ("Group pursued a different strategy last year", ""),
])
def test_a_short_phrase_only_matches_a_whole_word(headline, expected):
    """These phrases are short enough that substring matching mislabels real
    headlines. The rules match whole words."""
    assert highlight(headline)[0] == expected


# -- ordering ----------------------------------------------------------------

def feed(*items) -> NewsFeed:
    return NewsFeed(asset_id="X", name="X Ltd", items=list(items))


def test_highlights_are_newest_first():
    f = feed(item("CEO resigns", when="2026-09-01"),
             item("Firm wins order worth $1bn", when="2026-09-17"))
    assert [i.when for i in f.highlights()] == ["2026-09-17", "2026-09-01"]


def test_bad_news_wins_a_tie_on_the_same_day():
    """A reader skimming five rows should meet the lawsuit before the press
    release."""
    f = feed(item("Board approves share buyback", when="2026-09-18"),
             item("Shareholders file class action", when="2026-09-18"),
             item("Firm to acquire rival", when="2026-09-18"))
    assert [i.tone for i in f.highlights()] == ["bad", "neutral", "good"]


def test_only_material_items_are_lifted():
    f = feed(item("CEO resigns"), item("Shares close higher"),
             item("3 Best Stocks to Buy Now"))
    assert [i.signal for i in f.highlights()] == ["Leadership change"]


def test_the_list_is_capped():
    f = feed(*[item(f"Firm wins order worth ${n}m for project {n}",
                    when=f"2026-09-{n:02d}") for n in range(1, 13)],
             *[item(f"Board approves share buyback tranche {n}",
                    when=f"2026-08-{n:02d}") for n in range(1, 6)],
             *[item(f"Shareholders file class action number {n}",
                    when=f"2026-07-{n:02d}") for n in range(1, 6)])
    assert len(f.highlights()) == 6
    assert len(f.highlights(limit=3)) == 3


def test_one_event_repeated_does_not_crowd_out_the_page():
    """A running buyback generates a story a day. Six of ING's ten most recent
    items were the same programme, which is one highlight, not six."""
    f = feed(*[item(f"Buyback passes {n}0 percent mark",
                    when=f"2026-09-{n:02d}") for n in range(1, 8)],
             item("Shareholders file class action", when="2026-08-01"),
             item("Firm wins order worth $500m", when="2026-07-01"))

    tops = f.highlights()
    assert sum(1 for i in tops if i.signal == "Capital return") == 2
    assert {i.signal for i in tops} == {
        "Capital return", "Legal action", "Order win"}


# -- coverage about the company, not by the company --------------------------

@pytest.mark.parametrize("headline", [
    # The flood in any US feed: aggregators turn every quarterly 13F into a
    # headline. Somebody bought the stock; the company did nothing.
    "EP Wealth Advisors LLC Acquires 66,424 Shares of JPMorgan Chase & Co.",
    "Varma Mutual Pension Insurance Co Acquires 10,900 Shares of JPMorgan",
    "Vanguard Group Inc. Sells 1,204,331 Shares of Exxon Mobil",
    "Jane Street Group LLC Takes Position in Apple Inc.",
    "Fund Boosts Stake in ICICI Bank Ltd",
    "Shares of Infosys acquired by Northern Trust Corp",
    "Hedge fund has $42.1 million position in ASML Holding",
    # Opinion packaging. Commentary about a company, not an event at one.
    "Apple Inc. (AAPL): One of the 20 Stocks with the Biggest Share Buybacks",
    "ExxonMobil vs. ConocoPhillips: Which Oil Major's Buybacks Win?",
    "5 Best Dividend Stocks to Buy in September",
    "Should you buy JPMorgan after the dividend hike?",
])
def test_coverage_about_the_shareholders_is_not_a_company_event(headline):
    assert highlight(headline) == ("", "")


def test_the_companys_own_buyback_still_counts():
    """The exclusions must not swallow the real thing they resemble."""
    assert highlight(
        "Board approves share buyback of Rs 10,000 crore")[0] == "Capital return"
    assert highlight(
        "ING Groep completes 69% of EUR 1 billion share buyback"
    )[0] == "Capital return"
    assert highlight("Firm to acquire rival for $2bn")[0] == "Deal"


def test_a_feed_with_nothing_material_returns_nothing():
    assert feed(item("Shares close higher")).highlights() == []
    assert feed().highlights() == []


# -- the link is the point ---------------------------------------------------

def test_a_highlight_keeps_the_article_link_it_came_with():
    """The section is the same coverage in priority order, not a summary of
    it. Losing the link would make it an assertion instead of a citation."""
    row = item("CEO resigns", url="https://reuters.com/story-42")
    lifted = feed(row).highlights()[0]
    assert lifted.url == "https://reuters.com/story-42"
    assert lifted is row, "the same object, not a copy that could drift"


def test_a_highlight_keeps_its_category():
    row = item("Shareholders file class action", category=Category.LEGAL)
    assert feed(row).highlights()[0].category == Category.LEGAL.value


def test_materiality_is_derived_not_stored():
    """Computed from the headline, so a feed already sitting in the cache
    gains highlights without being refetched and without a migration."""
    row = NewsItem(when="2026-09-18", headline="CEO resigns after six years",
                   category=Category.OTHER.value, subject="", source="X",
                   url="")
    assert row.material and row.signal == "Leadership change"
