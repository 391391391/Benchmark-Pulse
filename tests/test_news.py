"""Tests for company news.

The claim this feature rests on is that the categories come from the exchange's
own taxonomy rather than from a model reading a headline. These pin that claim
down, and pin the filtering, because an unfiltered EDGAR feed is not news.
"""

from __future__ import annotations

import pytest

from benchmark_pulse import news
from benchmark_pulse.news import Category, NewsFeed, NewsItem


# -- the regulator's own classification ------------------------------------

@pytest.mark.parametrize("item,expected", [
    ("2.02", Category.FINANCIAL),    # Results of Operations
    ("4.02", Category.FINANCIAL),    # Non-Reliance (a restatement)
    ("5.02", Category.LEADERSHIP),   # Departure of Directors or Officers
    ("4.01", Category.LEADERSHIP),   # Change of certifying accountant
    ("1.03", Category.CREDIT),       # Bankruptcy or Receivership
    ("2.03", Category.CREDIT),       # Creation of a Direct Financial Obligation
    ("2.01", Category.CORPORATE),    # Completion of an Acquisition
    ("3.02", Category.CAPITAL),      # Unregistered Sales of Equity
    ("1.05", Category.LEGAL),        # Material Cybersecurity Incident
])
def test_8k_item_codes_map_to_the_sec_s_own_meaning(item, expected):
    """Item 2.02 IS "Results of Operations and Financial Condition". The
    regulator already classified the event; this only has to not lose it."""
    assert news._ITEM_CATEGORY[item] is expected


def test_every_mapped_item_has_a_readable_title():
    """The code is shown to an analyst, so it needs words beside it."""
    for code in news._ITEM_CATEGORY:
        assert news._ITEM_TITLE.get(code), f"item {code} has no title"


def test_auditor_resignation_is_reachable():
    """One of the six categories names it explicitly, and it is the kind of
    disclosure that matters far beyond its length."""
    assert news._ITEM_CATEGORY["4.01"] is Category.LEADERSHIP
    assert "accountant" in news._ITEM_TITLE["4.01"].lower()


# -- filtering --------------------------------------------------------------

@pytest.mark.parametrize("form", ["4", "3", "5", "144", "13F-HR", "424B2",
                                  "FWP", "SC 13G", "UPLOAD", "CORRESP"])
def test_routine_filings_are_not_news(form):
    """JPMorgan has filed 22,711 prospectus supplements and Apple 590 insider
    forms. Left in, they bury the four earnings releases someone wants."""
    assert form in news._NOISE_FORMS


@pytest.mark.parametrize("form", ["8-K", "10-K", "10-Q", "20-F", "6-K"])
def test_the_forms_that_carry_events_are_kept(form):
    assert form not in news._NOISE_FORMS


# -- wording fallback -------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Update on Lipitor Antitrust Litigation", Category.LEGAL),
    ("International Credit Ratings", Category.CREDIT),
    ("Incorporation of a Subsidiary Company", Category.CORPORATE),
    ("Allotment of 336265 shares", Category.CAPITAL),
    ("ASML reports EUR 9.3 billion total net sales", Category.FINANCIAL),
    ("Supervisory Board intends to appoint a new Chief Executive",
     Category.LEADERSHIP),
    ("Notice of Postal Ballot", Category.LEADERSHIP),
])
def test_wording_falls_back_sensibly(text, expected):
    """Used only where no exchange subject exists -- a bare Form 6-K."""
    assert news.classify(text) is expected


def test_sebi_alone_does_not_mean_a_legal_matter():
    """Every Indian disclosure cites a SEBI regulation. Matching the word filed
    routine monthly notices under Legal and regulatory."""
    assert news.classify(
        "Disclosure under Regulation 30 of SEBI LODR") is not Category.LEGAL


def test_unrecognised_wording_is_admitted_rather_than_guessed():
    assert news.classify("Intimation to the exchange") is Category.OTHER
    assert news.classify("") is Category.OTHER


# -- shape ------------------------------------------------------------------

def test_categories_are_ordered_by_priority():
    """Financial performance is the user's highest priority and reads first."""
    assert news.ORDER[0] is Category.FINANCIAL
    assert news.ORDER[-1] is Category.OTHER
    assert len(news.ORDER) == len(Category)


def test_grouping_sorts_newest_first_within_a_category():
    feed = NewsFeed(asset_id="X", name="X", items=[
        NewsItem(when="2026-01-05", headline="older",
                 category=Category.FINANCIAL.value, subject="", source="s",
                 url=""),
        NewsItem(when="2026-06-01", headline="newer",
                 category=Category.FINANCIAL.value, subject="", source="s",
                 url=""),
    ])
    rows = feed.by_category()[Category.FINANCIAL.value]
    assert [r.headline for r in rows] == ["newer", "older"]


def test_a_market_without_a_feed_says_so_instead_of_looking_empty():
    """An empty list reads as "nothing happened", which is a different claim
    from "we cannot see this market"."""
    feed = news.feed_for("2222.SR", "Saudi Aramco", "SA")
    assert feed.covered is False
    assert feed.items == []
    assert "403" in feed.note


def test_nse_boilerplate_is_stripped_from_headlines():
    """Every NSE announcement opens with the same clause, which eats the
    column and sits in front of the words the classifier needs."""
    tidied = news._tidy(
        "Sun Pharmaceutical Industries Limited has informed the Exchange "
        "about Incorporation of a Subsidiary Company")
    assert tidied == "Incorporation of a Subsidiary Company"


@pytest.mark.parametrize("raw,expected", [
    ("11-Sep-2026 19:57:45", "2026-09-11"),
    ("25-Aug-2026", "2026-08-25"),
    ("nonsense", ""),
])
def test_nse_dates_are_normalised(raw, expected):
    assert news._nse_date(raw) == expected


# -- press articles: relevance ----------------------------------------------
#
# The hard part of a news search is not fetching it, it is throwing away what
# merely mentions the company.

@pytest.mark.parametrize("name,headline", [
    ("Apple Inc.", "Opinion: iPhone 18 Pro Demand Is a Problem for Apple"),
    ("Sun Pharmaceutical Inds", "Sun Pharma falls after USFDA observation"),
    ("Saudi Aramco", "Aramco suspends Yanbu loadings"),
    ("Al Rajhi Bank", "Al Rajhi Bank's stock rises after cash dividends"),
    ("ICICI Bank Ltd", "ICICI Bank Q1 earnings call highlights"),
    ("ASML Holding NV", "ASML reports transactions under its buyback"),
    ("ING Groep NV", "ING Groep stock holds gains as Q2 earnings beat"),
])
def test_a_headline_about_the_company_is_kept(name, headline):
    assert news._about(headline, news._stems(name))


@pytest.mark.parametrize("name,headline", [
    ("ASML Holding NV", "European Indexes Rise as Banks, AI Stocks Recover"),
    ("Apple Inc.", "Fed holds rates steady as inflation cools"),
    ("ICICI Bank Ltd", "Sensex ends flat in choppy trade"),
])
def test_a_headline_that_merely_moved_with_the_market_is_dropped(name, headline):
    """A market wrap is not news about one of its constituents, even when the
    constituent is why it moved."""
    assert not news._about(headline, news._stems(name))


def test_short_and_generic_words_are_not_matched_on():
    """"Sun" or "ING" alone would match half the internet, so words under four
    characters never become match keys."""
    assert "sun" not in news._stems("Sun Pharmaceutical Inds")
    assert "ing" not in news._stems("ING Groep NV")
    assert "groep" in news._stems("ING Groep NV")


def test_legal_wrappers_are_not_match_keys():
    """Matching on "limited" or "holding" would pull in every other company."""
    stems = news._stems("ASML Holding NV")
    assert "holdi" not in stems
    assert "asml" in stems


def test_machine_written_price_tickers_are_not_news():
    """"XOM|Exxon Mobil Corp|Price:169.320|Chg%:+4.240" is a quote."""
    assert news._MACHINE_NOISE.search("XOM|Exxon Mobil Corp|Price:169.320")
    assert news._MACHINE_NOISE.search(
        "ICICI Bank Ltd Stock (IBN) Opened Down by 3.08% on Sep 15")
    assert not news._MACHINE_NOISE.search(
        "Al Rajhi Bank stock falls despite revenue beat on costs")


# -- press articles: classification -----------------------------------------

@pytest.mark.parametrize("headline,expected", [
    ("Al Rajhi Bank posts strong H1 2026 profit", Category.FINANCIAL),
    ("ASML reports transactions under its share buyback program",
     Category.CAPITAL),
    ("Apple names new CEO as Cook steps down", Category.LEADERSHIP),
    ("Moody's cuts outlook on ICICI Bank debt", Category.CREDIT),
    ("Exxon sued over refinery emissions", Category.LEGAL),
    ("ING Groep completes acquisition of rival", Category.CORPORATE),
])
def test_press_headlines_are_categorised(headline, expected):
    assert news.classify(headline) is expected


def test_a_broker_opinion_piece_is_not_a_corporate_action():
    """"Best Stocks to Buy" was being filed as an acquisition -- a wrong label
    on a row that looks confident."""
    for headline in ("ASML Among the Best Stocks to Buy For High Returns",
                     "Is Apple Inc. (AAPL) A Good Stock To Buy Now?"):
        assert news.classify(headline) is not Category.CORPORATE


def test_an_analyst_rating_change_is_not_a_credit_event():
    """An upgrade in a headline is almost always a broker changing a
    recommendation, which says nothing about the company's creditworthiness."""
    assert news.classify(
        "Bernstein reaffirms their Buy rating on ASML") is not Category.CREDIT


# -- press articles: merging ------------------------------------------------

def test_the_same_story_from_two_feeds_appears_once():
    a = NewsItem(when="2026-09-16", headline="ASML reports record bookings",
                 category="", subject="", source="Reuters", url="direct")
    b = NewsItem(when="2026-09-16", headline="ASML Reports Record Bookings!",
                 category="", subject="", source="Google News", url="redirect")
    merged = news._merge([a], [b])
    assert len(merged) == 1
    assert merged[0].url == "direct", "the first feed's link should win"


def test_merged_items_are_newest_first():
    older = NewsItem(when="2026-01-02", headline="one", category="",
                     subject="", source="s", url="")
    newer = NewsItem(when="2026-09-02", headline="two", category="",
                     subject="", source="s", url="")
    assert [i.headline for i in news._merge([older, newer])] == ["two", "one"]


def test_press_items_are_not_marked_official():
    """An aggregator is not a regulator, and the app should not imply it is."""
    item = NewsItem(when="2026-09-16", headline="h", category="",
                    subject="Press coverage", source="Reuters", url="u",
                    official=False)
    assert item.official is False


@pytest.mark.parametrize("raw,expected", [
    ("Tue, 15 Sep 2026 14:18:00 GMT", "2026-09-15"),   # RSS, as Google sends
    ("2026-09-16T09:42:01Z", "2026-09-16"),            # ISO, as Yahoo sends
    ("", ""),
])
def test_article_dates_are_normalised(raw, expected):
    """Two feeds, two date formats, one column."""
    assert news._iso(raw) == expected


def test_an_epoch_timestamp_is_read_as_utc():
    """Yahoo sometimes sends seconds since the epoch instead of a string."""
    from datetime import datetime, timezone
    when = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
    assert news._iso(when.timestamp()) == "2026-09-16"
