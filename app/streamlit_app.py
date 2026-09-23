"""Benchmark Pulse -- two-level equity benchmarking.

A portfolio is judged twice, because two different questions matter:

  Level 1   Did the book beat the market it was hired to beat? Set by the
            mandate: a developed-markets fund against developed markets, an
            emerging-markets fund against emerging markets.

  Level 2   Did each company beat its own peers? A technology holding against a
            technology index, a bank against financials.

They routinely disagree, and the disagreement is the insight: a book can beat
its mandate while most holdings lose to their sectors, which means allocation
rather than stock picking did the work.

Styled with the Preferred Square design system (see brand.py).

This file is deliberately ASCII-only. Typographic characters are written as HTML
entities, because a PowerShell `Get-Content | Set-Content` round trip on Windows
re-encodes literal UTF-8 through the ANSI codepage and corrupts them.

Run:  streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

#: Preferred Square is based in India, so the header clock reads in the
#: firm's own local time rather than the server's (UTC on Streamlit Cloud).
#: A named zone, not a fixed +5:30 offset, so it stays correct even if the
#: rule ever changes; India has no daylight-saving shift to worry about.
DISPLAY_TZ = ZoneInfo("Asia/Kolkata")
from html import escape
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from benchmark_pulse import brand, portfolio_store  # noqa: E402
from benchmark_pulse.auth import (  # noqa: E402
    MIN_PASSWORD_LENGTH, AuthError, any_accounts, authenticate, create_user,
    end_session, resume_session, start_session,
)
from benchmark_pulse.aggregate import performance_index as portfolio_index  # noqa: E402
from benchmark_pulse.analysis import PortfolioAnalysis, analyse  # noqa: E402
from benchmark_pulse.benchmarks import (  # noqa: E402
    MANDATES, MARKETS, SECTORS, UNIVERSE, holding_candidates,
    load_decisions, override, save_decisions,
)
from benchmark_pulse import news as newsfeed  # noqa: E402
from benchmark_pulse.ingest import SUPPORTED, IngestError  # noqa: E402
from benchmark_pulse.marketdata import MarketData  # noqa: E402
from benchmark_pulse.periods import WEIGHTS as TREND_WEIGHTS  # noqa: E402
from benchmark_pulse.periods import compare as period_compare  # noqa: E402
from benchmark_pulse.periods import rebased, trend_score  # noqa: E402
from benchmark_pulse.paths import describe as describe_data_root  # noqa: E402
from benchmark_pulse.reference import (  # noqa: E402
    COUNTRY_NAMES, CURRENCY_NAMES, MEASURE_NOTES, country_name, currency_name,
)
from benchmark_pulse.window_return import windows_against  # noqa: E402
from benchmark_pulse import holdings_edit, sectors  # noqa: E402
from benchmark_pulse.holdings_edit import EditError  # noqa: E402
from benchmark_pulse.portfolio import normalise_ticker  # noqa: E402

DASH = "&mdash;"
DOT = "&nbsp;&middot;&nbsp;"

SEVERITY_TONE = {"material": "bad", "warning": "warn", "info": ""}

#: The product's own first-class markets, in a fixed order -- not the much
#: longer general country list. Shared by every market donut, so a country's
#: colour is the same wherever its weight is broken out.
MARKET_UNIVERSE = list(dict.fromkeys(
    b.country for b in MARKETS.values() if b.country))

st.set_page_config(page_title="Benchmark Pulse | Preferred Square",
                   page_icon="assets/favicon.png", layout="wide")

if "dark" not in st.session_state:
    st.session_state.dark = False
st.markdown(brand.css(st.session_state.dark), unsafe_allow_html=True)


@st.cache_resource(show_spinner=False)
def market_data(offline: bool, _stamp: float) -> MarketData:
    """One data client per page load, shared by the analysis and the charts.

    Sharing it means the period charts read from the same in-memory series the
    analysis already fetched, rather than downloading a holding's prices again
    every time someone opens its detail page.
    """
    return MarketData(offline=offline, live=not offline)


@st.cache_resource(show_spinner="Fetching live prices and benchmarking...")
def run_analysis(book_path: str, offline: bool, name: str,
                 _stamp: float, _revision: float = 0.0,
                 portfolio_id: str = "",
                 reporting_currency: str = "USD") -> PortfolioAnalysis:
    """Analyse the book.

    ``_stamp`` is what makes the data live: it changes on every run of the
    script, so Streamlit's cache is bypassed and prices are refetched. The
    cache still serves reruns triggered by a widget within the same load, which
    is what stops a filter change from re-downloading the market.

    ``_revision`` is the workbook's modification time. It exists so that editing
    the holdings re-runs the analysis without re-running the market: the stamp
    is left alone, the shared MarketData keeps everything it has already
    fetched, and a share count corrected in the grid is reflected in a second
    rather than after a full refetch.
    """
    return analyse(book_path, md=market_data(offline, _stamp),
                   portfolio_name=name, portfolio_id=portfolio_id,
                   reporting_currency=reporting_currency)


@st.cache_resource(show_spinner="Reading the news...")
def company_news(asset_id: str, name: str, _stamp: float):
    """Press coverage for one holding.

    Keyed on the holding rather than the page, because a feed costs a couple of
    outbound requests and nobody needs it refetched when a filter moves. The
    module caches to disk underneath this as well, so switching back to a
    company already read is instant and an offline demo still works.
    """
    return newsfeed.articles_for(asset_id, name)


@st.cache_resource(show_spinner="Reading exchange filings...")
def company_filings(asset_id: str, name: str, country: str, _stamp: float):
    """What the company was required to disclose, as opposed to reported on."""
    return newsfeed.filings_for(asset_id, name, country)


@st.cache_resource(show_spinner="Scoring recent performance...")
def trend_scores(book_path: str, offline: bool, name: str,
                 _stamp: float, _revision: float = 0.0,
                 portfolio_id: str = "",
                 reporting_currency: str = "USD") -> dict:
    """Every holding's 3M, 6M and 12M alpha against its own benchmark.

    Cached with the analysis, because it reads the same two price series per
    holding that the analysis already fetched.
    """
    md = market_data(offline, _stamp)
    result = {}
    # Every holding, not only the scored ones. These windows are point-to-point
    # on the security, so a position opened this morning has a perfectly good
    # three-year record -- it is the *investor* who has no history, not the
    # stock. Filtering on `scored` here silently dropped a holding bought today
    # out of Winners and laggards entirely.
    for holding in run_analysis(book_path, offline, name, _stamp, _revision,
                                portfolio_id, reporting_currency).holdings:
        try:
            windows = period_compare(
                md.prices(holding.asset_id),
                md.prices(holding.decision.benchmark_ticker))
        except Exception:  # noqa: BLE001 - a missing series is not a failure
            windows = []
        result[holding.asset_id] = trend_score(windows)
    return result


@st.cache_resource(show_spinner="Measuring the last one and three years...")
def mandate_windows(book_path: str, offline: bool, name: str,
                    benchmark_ticker: str, _stamp: float,
                    _revision: float = 0.0, portfolio_id: str = "",
                    reporting_currency: str = "USD") -> list:
    """The book's one- and three-year return against its mandate benchmark.

    Keyed on the benchmark as well as the book, because changing the mandate
    from the dropdown has to move these figures; a cache keyed on the book alone
    would leave the previous comparator's numbers on screen under a new name.
    """
    md = market_data(offline, _stamp)
    analysis = run_analysis(book_path, offline, name, _stamp, _revision,
                            portfolio_id, reporting_currency)
    return windows_against([h.stream for h in analysis.holdings], md,
                           benchmark_ticker, analysis.reporting_currency)


def fmt_pct(value: float | None, dp: int = 1) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{value * 100:.{dp}f}%"


def money(value: float) -> str:
    for cutoff, suffix in ((1e9, "bn"), (1e6, "m"), (1e3, "k")):
        if abs(value) >= cutoff:
            return f"${value / cutoff:,.1f}{suffix}"
    return f"${value:,.0f}"


def section(title: str, subtitle: str = "") -> None:
    st.markdown(brand.section(title, subtitle), unsafe_allow_html=True)


# ------------------------------------------------------------------- login --

SESSION_PARAM = "s"
#: Which portfolio is open, kept in the URL so a refresh returns to it.
BOOK_PARAM = "p"


def _remember(user, token: str) -> None:
    st.session_state.user = user
    st.session_state.session_token = token
    st.query_params[SESSION_PARAM] = token


def _seed_admin_account() -> None:
    """Recreate the admin account from configured credentials if it's gone.

    Streamlit Community Cloud's disk is ephemeral -- data/users.json is
    git-ignored on purpose, since it holds password hashes, and disappears on
    every restart or redeploy. Without this, the deployed app would forget
    every account it ever created and greet an analyst who signed in
    yesterday with "create the first account" again today.

    Credentials come from Streamlit secrets in the cloud (set once in the
    app's dashboard) or matching environment variables locally; either way
    this is a no-op once any account already exists.
    """
    if any_accounts():
        return
    try:
        email = st.secrets.get("ADMIN_EMAIL") or os.getenv("BENCHMARK_PULSE_ADMIN_EMAIL")
        name = st.secrets.get("ADMIN_NAME") or os.getenv("BENCHMARK_PULSE_ADMIN_NAME")
        password = st.secrets.get("ADMIN_PASSWORD") or os.getenv("BENCHMARK_PULSE_ADMIN_PASSWORD")
    except Exception:  # noqa: BLE001 - no secrets.toml at all is not an error
        return
    if not (email and name and password):
        return
    try:
        create_user(email, name, password)
    except AuthError:
        pass


def sign_in_screen() -> None:
    """The gate. Shown instead of the app until someone is signed in."""
    logo = brand.logo_data_uri()
    left, middle, right = st.columns([1, 1.1, 1])

    with middle:
        # Held in a placeholder so a successful sign-in can clear it outright,
        # rather than leaving it on screen. Without this, the widgets already
        # sent to the browser this run stay visible -- st.rerun() jumps
        # straight into the app, which does not touch this position again, so
        # the old form sat frozen under the "fetching live prices" spinner of
        # the run that follows until that run finished.
        gate = st.empty()
        signed_in: object | None = None

        with gate.container():
            st.write("")
            st.write("")
            if logo:
                st.markdown(
                    f'<div style="background:{brand.NAVY_DEEP};border-radius:4px;'
                    f'padding:1.1rem 1.3rem;margin-bottom:1.4rem;">'
                    f'<img src="{logo}" style="width:100%;max-width:190px;'
                    f'display:block;">'
                    f'<div style="color:{brand.TEAL_LIGHT};font-size:.52rem;'
                    f'letter-spacing:.13em;text-transform:uppercase;font-weight:700;'
                    f'margin-top:.35rem;">{brand.TAGLINE}</div></div>',
                    unsafe_allow_html=True)

            first_run = not any_accounts()
            if first_run:
                st.markdown(brand.section("Create the first account"),
                            unsafe_allow_html=True)
                st.markdown(
                    brand.note(
                        "No accounts exist yet",
                        "The first account created becomes the administrator. Your "
                        "name is what appears against every benchmark you sign off, "
                        "so use the one a client would recognise.",
                    ), unsafe_allow_html=True)
            else:
                st.markdown(brand.section("Sign in"), unsafe_allow_html=True)

            with st.form("sign_in", border=False):
                email = st.text_input("Email", placeholder="you@preferredsquare.com")
                name = st.text_input("Full name",
                                     placeholder="e.g. Aditya Bhuttani") if first_run else ""
                password = st.text_input(
                    "Password", type="password",
                    help=f"At least {MIN_PASSWORD_LENGTH} characters. A short "
                         "phrase is stronger than a short password."
                    if first_run else None)
                submitted = st.form_submit_button(
                    "Create account" if first_run else "Sign in",
                    type="primary", use_container_width=True)

            if submitted:
                try:
                    signed_in = (create_user(email, name, password) if first_run
                                else authenticate(email, password))
                except AuthError as exc:
                    st.markdown(brand.note("Could not sign in", str(exc), "bad"),
                                unsafe_allow_html=True)

        if signed_in is not None:
            gate.empty()
            _remember(signed_in, start_session(signed_in))
            st.rerun()


# A refresh starts a new Streamlit session, so session_state is empty. The
# token in the URL is what carries the sign-in across that boundary; it is
# validated against the server-side record on every load, so revoking a session
# takes effect immediately rather than whenever the browser next asks.
_seed_admin_account()

if "user" not in st.session_state:
    restored = resume_session(st.query_params.get(SESSION_PARAM))
    if restored is not None:
        st.session_state.user = restored
        st.session_state.session_token = st.query_params.get(SESSION_PARAM)
    else:
        st.query_params.pop(SESSION_PARAM, None)   # stale or revoked
        sign_in_screen()
        st.stop()

user = st.session_state.user


def col(label: str) -> str:
    """A column heading carrying its definition on hover, where one exists."""
    return brand.header(label, MEASURE_NOTES.get(label, ""))


def market_cell(code: str) -> str:
    return brand.abbr(code, country_name(code))


def ccy_cell(code: str) -> str:
    """A currency code with its full name one hover away.

    Unused since the sector view was removed; kept because it is the natural
    partner to market_cell above and any table gaining a currency column will
    want it.
    """
    return brand.abbr(code, currency_name(code))


@st.cache_data(show_spinner=False)
def _trend_svg(ticker: str, _stamp: float) -> str:
    """One year of closes as a sparkline. Cached: twenty-four of these are
    drawn on every render of the holdings table."""
    try:
        series = market.prices(ticker).tail(252)
        return brand.sparkline(series.tolist())
    except Exception:  # noqa: BLE001
        return f'<span class="ps-muted">{DASH}</span>'


def trend(ticker: str) -> str:
    return _trend_svg(ticker, st.session_state.load_stamp)


def _mandate_benchmark(portfolio):
    """The mandate entry for this portfolio's benchmark.

    MANDATES first, deliberately. Several tickers appear in both MANDATES and
    MARKETS -- ACWI, NIFTYBEES.NS, KSA -- and UNIVERSE merges MARKETS last so
    that a holding falling back to its home market gets the market entry.
    Reading UNIVERSE here therefore answered "WW" for the mandate that MANDATES
    calls Global Equity.
    """
    ticker = portfolio.decision.benchmark_ticker
    return MANDATES.get(ticker) or UNIVERSE.get(ticker)


def mandate_scope(portfolio) -> str:
    """The mandate: what kind of book this is.

    Global Equity, India Equity, Banking Portfolio. Not the index -- the index
    is the answer to the mandate, and printing the ticker under a label reading
    "Mandate" puts the answer where the question belongs.
    """
    bench = _mandate_benchmark(portfolio)
    if bench is not None and bench.scope:
        return bench.scope
    return portfolio.decision.benchmark_ticker


def mandate_index(portfolio) -> str:
    """The published index that mandate is measured against, named short.

    Every mandate is named "<scope> - <index>", so the part after the dash is
    the index in the form a reader recognises: "MSCI ACWI" rather than "MSCI
    All Country World Index", which does not fit a caption.
    """
    bench = _mandate_benchmark(portfolio)
    if bench is None:
        return portfolio.decision.benchmark_ticker
    name = bench.name or bench.index or bench.ticker
    return name.split(" - ")[-1].strip()


def benchmark_cell(ticker: str) -> str:
    """A benchmark, naming the index it represents and how it was obtained."""
    bench = UNIVERSE.get(ticker)
    if bench is None:
        return ticker
    return brand.abbr(bench.name or ticker, bench.attribution)


def benchmark_picker(decision, choices: dict, key: str,
                     namespace: str = "") -> None:
    """Change the benchmark. A dropdown, and nothing else.

    This used to be a sign-off: pick, give a reason, press a button, and the
    control then replaced itself with an approval stamp. Two things were wrong
    with that. The stamp removed the only way to change the benchmark again, so
    one choice was final. And the ceremony sat in front of what an analyst
    actually wants here, which is to see the same portfolio against a different
    yardstick and compare -- an act of exploration, not of approval.

    Who changed it is still recorded, because the decision is part of the audit
    trail. It just is not a workflow any more.
    """
    tickers = list(choices)
    current_ticker = decision.benchmark_ticker
    picked = st.selectbox(
        "Benchmark", tickers,
        index=tickers.index(current_ticker) if current_ticker in tickers else 0,
        # The name already reads "<mandate> - <index>", so prefixing the ticker
        # produced "ACWI - Global Equity - MSCI ACWI". The ticker belongs at
        # the end, where it identifies rather than leads.
        format_func=lambda t: f"{choices[t].name}  ({t})",
        key=f"pick_{key}")

    if picked == current_ticker:
        return

    # Applied on selection. A separate confirm step would only ask the analyst
    # to agree with what they just clicked.
    #
    # namespace scopes the key to this portfolio for a portfolio-level
    # decision (asset_id is the fixed literal "PORTFOLIO"), which would
    # otherwise be shared -- and silently overwritten -- by every other
    # portfolio's mandate the moment either had a non-"rules" decision on
    # file. It must match analyse()'s own mandate_key exactly, or a mandate
    # picked here would be saved under a different key than the one
    # analyse() reads back on the next rerun, reverting it silently.
    dec_key = f"{namespace}:{decision.asset_id}" if namespace else decision.asset_id
    saved = load_decisions()
    current = saved.get(dec_key, decision)
    # Parentheses, not angle brackets: the rationale is rendered as HTML and a
    # <name@example.com> is swallowed as an unknown tag, which silently dropped
    # the email from the audit trail on screen.
    override(current, picked, f"{user.name} ({user.email})",
             "selected from the benchmark list")
    saved[dec_key] = current
    save_decisions(saved)
    st.cache_resource.clear()
    st.rerun()


def _upload_portfolio_form(owner: str) -> None:
    """The upload widget and its ingestion pipeline: read the file, convert
    it, fill in any missing sector or market, and open it.

    Shared by the normal Import portfolio view and the first-run screen a
    freshly created account lands on, so the two paths cannot drift apart --
    a fix to one is a fix to both.
    """
    upload = st.file_uploader(
        "Portfolio file", type=[ext.lstrip(".") for ext in SUPPORTED],
        help="Excel (.xlsx, .xlsm, .xls), CSV, PDF or PowerPoint (.pptx).",
    )
    # Suggested the moment a file is picked, rather than left blank: the
    # sidebar portfolio switcher lists only this name, and two uploads that
    # both fall back to a blank-derived "Statement" are genuinely
    # indistinguishable there. Still fully editable and non-blocking.
    suggested = Path(upload.name).stem.replace("_", " ").replace("-", " ").strip() if upload else ""
    name = st.text_input(
        "Portfolio name", value=suggested,
        placeholder="e.g. Al Faisal Global Equity - Q3 2026")

    AUTO_MANDATE = "Auto — detect from holdings"
    picked_mandate = st.selectbox(
        "Mandate", [AUTO_MANDATE] + [MANDATES[t].name for t in MANDATES],
        index=0, key="upload_mandate",
        help="The tool measures the book against its own composition "
             "automatically. Set this only if you already know the mandate "
             "and want it to stick.")
    stated_ticker = None
    if picked_mandate != AUTO_MANDATE:
        stated_ticker = next(t for t in MANDATES
                             if MANDATES[t].name == picked_mandate)

    picked_currency = st.selectbox(
        "Reporting currency", sorted(CURRENCY_NAMES), index=sorted(
            CURRENCY_NAMES).index("USD"),
        format_func=lambda c: f"{c} — {currency_name(c)}",
        key="upload_currency",
        help="What every portfolio-level figure (Cost, Market value, and "
             "so on) is rolled up in. Leave on USD unless this book is "
             "prepared for a committee that reports in something else.")

    if upload is not None and st.button("Read and analyse", type="primary"):
        with st.spinner("Reading the file..."):
            try:
                new_record = portfolio_store.add(
                    upload.name, upload.getvalue(), name.strip() or None,
                    owner=owner, reporting_currency=picked_currency,
                    stated_mandate=stated_ticker)
            except IngestError as exc:
                new_record = None
                st.markdown(brand.card("Could not read this file", str(exc), "bad"),
                            unsafe_allow_html=True)
            except Exception as exc:  # noqa: BLE001
                new_record = None
                st.markdown(brand.card("Unexpected problem", str(exc), "bad"),
                            unsafe_allow_html=True)
        if new_record is not None:
            for w in (new_record.warnings or []):
                st.markdown(brand.note("Transcription note", w, "warn"),
                            unsafe_allow_html=True)

            # Statements routinely arrive without a sector column, and every
            # holding in one is then benchmarked against its market instead of
            # its industry. Filled here, once, rather than left for the analyst
            # to notice on a later screen.
            found: list[tuple[str, str, str, str]] = []
            lost: dict[tuple[str, str], str] = {}
            try:
                grid = holdings_edit.read_holdings(new_record.workbook)
                if (holdings_edit.missing_markets(grid)
                        or holdings_edit.missing_sectors(grid)):
                    with st.spinner("Identifying markets and sectors..."):
                        grid, by_market, no_market = holdings_edit.fill_markets(
                            grid, sectors.identify)
                        grid, by_sector, no_sector = holdings_edit.fill_sectors(
                            grid, sectors.identify)
                    found = ([(t, v, how, "Country") for t, v, how in by_market]
                             + [(t, v, how, "Sector") for t, v, how in by_sector])
                    lost = {
                        **{(t, "Country"): why for t, _n, why in no_market},
                        **{(t, "Sector"): why for t, _n, why in no_sector},
                    }
                    if found:
                        portfolio_store.save_holdings(new_record, grid)
            except Exception as exc:  # noqa: BLE001 - a book still loads
                st.markdown(
                    brand.note("Markets and sectors not filled in",
                               escape(str(exc)), "warn"),
                    unsafe_allow_html=True)
            if found:
                st.markdown(
                    brand.note(
                        f"{len(found)} value{'s' if len(found) != 1 else ''} "
                        "identified",
                        "<br>".join(
                            f"<b>{escape(t)}</b> "
                            f"{holdings_edit.GAP_FIELDS[col]} is "
                            f"{escape(country_name(v) if col == 'Country' else v)}"
                            f" &mdash; {how}"
                            for t, v, how, col in found)
                        + "<br><br>Each one is an ordinary value in the "
                          "holdings table and can be typed over."),
                    unsafe_allow_html=True)
            if lost:
                # Carried to the editor, so the holding arrives there already
                # marked "not found" with the reason, rather than as a blank
                # nobody can tell from an unasked one.
                st.session_state.sector_misses = lost
                st.markdown(
                    brand.note(
                        f"{len(lost)} value{'s' if len(lost) != 1 else ''} "
                        "not found",
                        "<br>".join(
                            f"<b>{escape(t)}</b> "
                            f"{holdings_edit.GAP_FIELDS[col]} "
                            f"&mdash; {escape(why)}"
                            for (t, col), why in lost.items())
                        + "<br><br>Set them by hand under <b>Manage holdings</b>. "
                          "Until then they are measured against a broader "
                          "index than they should be.", "warn"),
                    unsafe_allow_html=True)

            st.cache_resource.clear()
            st.session_state.load_stamp = time.time()
            # Open what was just uploaded. Anything else means reading a
            # success message about a book the screens are not showing.
            st.session_state.portfolio_id = new_record.id
            st.query_params[BOOK_PARAM] = new_record.id
            # Land on Manage holdings instead of staying here: it is the one
            # editable table the tool has, and the analyst should see and be
            # able to fix a gap the moment it exists, not go find it via the
            # sidebar. Setting nav_view directly here would raise
            # StreamlitWidgetAlreadyInstantiatedError -- the sidebar radio
            # that owns that key has already rendered earlier in this same
            # run. This flag is consumed instead, right before that radio
            # re-instantiates on the fresh run st.rerun() below starts.
            st.session_state["_redirect_to_manage_holdings"] = True
            st.session_state.just_uploaded = {
                "portfolio_id": new_record.id, "at": time.time()}
            try:
                review_grid = holdings_edit.read_holdings(new_record.workbook)
                st.session_state.review_order = holdings_edit.review_order(
                    review_grid, lost)
            except Exception:  # noqa: BLE001 - ordering is a nicety, not a
                # requirement; Manage holdings falls back to file order.
                st.session_state.pop("review_order", None)
            # Excel is read directly by the tolerant loader rather than
            # counted at ingest time, so its row count is the same "not
            # counted" 0 as an empty file -- showing it here would print
            # "0 rows" on every successful Excel upload, the most common one.
            st.success(
                f"Loaded **{new_record.name}** "
                f"({f'{new_record.rows} rows from ' if new_record.rows else ''}"
                f"{new_record.source_format}).")
            st.rerun()


# ---------------------------------------------------------------- sidebar --

logo = brand.logo_data_uri()
st.sidebar.markdown(
    (f'<img src="{logo}" style="width:100%;display:block;">' if logo
     else '<div style="font-weight:800;color:#FFF;">Preferred Square</div>')
    + f'<div style="color:{brand.TEAL_LIGHT};font-size:.5rem;'
      f'letter-spacing:.105em;text-transform:uppercase;font-weight:700;'
      f'white-space:nowrap;margin-top:.3rem;">{brand.TAGLINE}</div>',
    unsafe_allow_html=True,
)

# Which book every screen is about. Held in the URL beside the session token so
# a refresh comes back to the portfolio you were reading rather than to the
# sample -- the same reason the sign-in survives one.
#
# The picker is always shown, including when only one book is loaded. Hiding it
# then looked tidier and was wrong: a control that appears only once a second
# portfolio exists cannot be found by someone looking for where to switch, and
# an analyst has no way to tell whether the tool holds one book or six. Standing
# there naming the open book, it answers both questions at once.
#
# Scoped to the signed-in account: a book uploaded under one sign-in is not
# another analyst's to browse. Only the admin's list carries the bundled
# sample, so anyone else created by the admin starts from nothing rather than
# from someone else's holdings.
records = portfolio_store.load_registry(user.email, is_admin=(user.role == "admin"))

if not records:
    st.markdown(brand.title_bar(f"Welcome, {user.name}", "No portfolio yet"),
                unsafe_allow_html=True)
    st.markdown(brand.section(
        "Import your first portfolio",
        "Nothing here is shared between accounts. What you upload is visible "
        "only under your own sign-in -- not to any other analyst, and not "
        "even to the admin who created your account."), unsafe_allow_html=True)
    _upload_portfolio_form(user.email)

    st.sidebar.markdown(
        '<div class="ps-rail-label">Signed in</div>'
        f'<div class="ps-sb-user"><div class="av">{user.initials}</div>'
        f'<div class="who"><div class="nm">{user.name}</div>'
        f'<div class="rl">{user.role.capitalize()}</div></div></div>',
        unsafe_allow_html=True,
    )
    if st.sidebar.button("Sign out", use_container_width=True):
        end_session(st.session_state.get("session_token"))
        for key in ("user", "session_token"):
            st.session_state.pop(key, None)
        st.query_params.pop(SESSION_PARAM, None)
        st.rerun()
    st.stop()

by_id = {r.id: r for r in records}

wanted = st.session_state.get("portfolio_id") or st.query_params.get(BOOK_PARAM)
if wanted not in by_id:
    wanted = records[0].id           # the sample, which is always present
st.session_state.portfolio_id = wanted

st.sidebar.markdown('<div class="ps-rail-label">Portfolio</div>',
                    unsafe_allow_html=True)
chosen_id = st.sidebar.selectbox(
    "Portfolio", [r.id for r in records],
    index=[r.id for r in records].index(wanted),
    format_func=lambda rid: by_id[rid].name,
    label_visibility="collapsed",
)
if chosen_id != wanted:
    st.session_state.portfolio_id = chosen_id
    st.query_params[BOOK_PARAM] = chosen_id
    st.rerun()

record = by_id[st.session_state.portfolio_id]
st.query_params[BOOK_PARAM] = record.id

VIEWS = ["Portfolio overview", "Portfolio vs benchmark", "Winners and laggards",
         "Holding analysis", "News", "Manage holdings",
         "Import portfolio"]

st.sidebar.markdown('<div class="ps-rail-label">Views</div>',
                    unsafe_allow_html=True)
# A widget's session-state key can only be set before that widget is
# instantiated in a given run -- setting it afterwards (e.g. from inside the
# upload handler, which runs after this radio has already rendered) raises
# StreamlitWidgetAlreadyInstantiatedError. The upload handler instead sets
# this plain flag and calls st.rerun(); consumed here, on the fresh run,
# strictly before the radio below is instantiated.
if st.session_state.pop("_redirect_to_manage_holdings", False):
    st.session_state["nav_view"] = "Manage holdings"
view = st.sidebar.radio("Views", VIEWS, key="nav_view",
                       label_visibility="collapsed")

st.sidebar.markdown('<div class="ps-rail-label">Settings</div>',
                    unsafe_allow_html=True)

offline = st.sidebar.toggle(
    "Offline mode", value=False,
    help="Use only cached market data. Turn on before presenting so a dropped "
         "connection cannot break the demo.",
)

if st.sidebar.button("Refresh prices", use_container_width=True):
    st.cache_resource.clear()
    # The button already re-fetches live prices correctly (clearing the
    # cache forces that) -- but without this line, "prices as at HH:MM"
    # keeps showing session-start time forever, because that label reads
    # load_stamp, not whether a fetch just happened. The data was fresh;
    # the clock on screen just never moved.
    st.session_state.load_stamp = time.time()
    st.rerun()

book = str(record.workbook)
if not Path(book).exists():
    st.error(
        f"Workbook not found for {record.name}.\n\n`{book}`\n\n"
        "Run `python scripts/make_demo_book.py` to generate the sample."
    )
    st.stop()

# A stamp that changes on every script run makes the analysis live: reloading
# the page refetches the market. Widget reruns within the same load reuse it,
# so changing a filter does not re-download anything.
if "load_stamp" not in st.session_state:
    st.session_state.load_stamp = time.time()

# The workbook's own modification time, so an edit to the holdings invalidates
# the analysis and nothing else. Without it a saved edit would have to clear
# every cache, which throws away the fetched prices too and turns a one-second
# correction into a full refetch.
book_rev = Path(book).stat().st_mtime
analysis = run_analysis(book, offline, record.name,
                        st.session_state.load_stamp, book_rev,
                        record.id, record.reporting_currency)
market = market_data(offline, st.session_state.load_stamp)
portfolio = analysis.portfolio
scored = analysis.scored

# A mandate stated at upload is reconciled against the book's own computed
# mandate exactly once. Agreeing, it becomes the confirmed decision with no
# further ceremony -- the same weight an analyst's own dropdown pick already
# carries. Disagreeing, the computed decision is left standing (every figure
# on screen stays measured against the book's actual composition, never an
# unconfirmed guess) and a plain note says so, wherever the mandate is shown.
if record.stated_mandate and not record.mandate_resolved and portfolio is not None:
    if record.stated_mandate == portfolio.decision.benchmark_ticker:
        override(portfolio.decision, record.stated_mandate,
                 f"{user.name} ({user.email})", "stated at upload")
        saved_decisions = load_decisions()
        saved_decisions[f"{record.id}:PORTFOLIO"] = portfolio.decision
        save_decisions(saved_decisions)
        portfolio_store.mark_mandate_resolved(record.id)
    else:
        # Shown once, on whichever screen the analyst lands on next -- not
        # gated behind a click, and not re-asked on every later visit. The
        # computed mandate already governs every figure; changing it, if the
        # stated one was right after all, is the same "Benchmark" dropdown
        # that already exists for exactly this, not new UI.
        st.session_state.mandate_conflict = {
            "stated": record.stated_mandate,
            "computed": portfolio.decision.benchmark_ticker,
        }
        portfolio_store.mark_mandate_resolved(record.id)

live = not analysis.provider.startswith("stub")

# Label and content are emitted as ONE block. Split across two st.markdown
# calls, Streamlit's wrapper collapses around block-level HTML -- the user
# block measured 14px tall while its content was 30px -- and the next element
# rides up into it, which is what put the "Signed in" rule through the avatar.
st.sidebar.markdown(
    '<div class="ps-rail-label">This portfolio</div>'
    + brand.sidebar_stat(
        "Vs mandate",
        fmt_pct(portfolio.direct_alpha) if portfolio else "n/a",
        "up" if portfolio and portfolio.beat_mandate else "down")
    + brand.sidebar_stat(
        # Out of the scored holdings, which is not always every position: one
        # bought this week has no whole-life return to judge. Printing "7 / 10"
        # in a rail beside a header reading 12 positions invites exactly the
        # question nobody can answer from the screen.
        "Beating sector",
        f"{len(analysis.outperformers)} / {len(scored)}"
        + (f'<span class="ps-muted" style="font-weight:400;"> of '
           f'{len(analysis.holdings)}</span>'
           if len(scored) != len(analysis.holdings) else ""))
    + brand.sidebar_stat(
        "Reasoning",
        f'<span class="ps-sb-engine" style="color:{brand.TEAL_LIGHT};">'
        f'{"&#9679;" if live else "&#9675;"} '
        f'{analysis.provider.split("/")[0]}</span>'),
    unsafe_allow_html=True,
)

st.sidebar.markdown(
    '<div class="ps-rail-label">Signed in</div>'
    f'<div class="ps-sb-user"><div class="av">{user.initials}</div>'
    f'<div class="who"><div class="nm">{user.name}</div>'
    f'<div class="rl">{user.role.capitalize()}</div></div></div>',
    unsafe_allow_html=True,
)

# Registration used to be self-service from the sign-in screen -- anyone with
# the URL could create their own account. Only the admin creates one now, so
# who has access to client portfolios is a decision someone makes, not a form
# anyone can fill in.
if user.role == "admin":
    with st.sidebar.expander("Add an analyst"):
        st.caption("Their portfolios start empty and stay private to their "
                   "own sign-in -- not visible here, even to you.")
        with st.form("add_analyst", border=False):
            new_email = st.text_input("Email", key="new_analyst_email")
            new_name = st.text_input("Full name", key="new_analyst_name")
            new_pass = st.text_input(
                "Password", type="password", key="new_analyst_pass",
                help=f"At least {MIN_PASSWORD_LENGTH} characters. Share it "
                     "with them directly -- there is no reset flow yet.")
            if st.form_submit_button("Create account",
                                     use_container_width=True):
                try:
                    new_user = create_user(new_email, new_name, new_pass,
                                           role="analyst")
                except AuthError as exc:
                    st.error(str(exc))
                else:
                    st.success(f"Account created for {new_user.email}.")

if st.sidebar.button("Sign out", use_container_width=True):
    # Revoke server-side first: the token in the URL must stop working, not
    # merely be forgotten by this tab.
    end_session(st.session_state.get("session_token"))
    for key in ("user", "session_token"):
        st.session_state.pop(key, None)
    st.query_params.pop(SESSION_PARAM, None)
    st.rerun()
if not live:
    st.sidebar.warning(
        "No language model configured. Every figure is still fully computed; "
        "only the written rationale is templated.",
    )

# ------------------------------------------------------------------ header --

countries = list((portfolio.exposure["by_country"] if portfolio else {}).keys())

st.markdown(
    brand.title_bar(
        record.name,
        # Every holding, not just the scored ones. A position opened today has
        # no return to score yet, and counting only the scored ones printed
        # "10 positions" over a table listing eleven.
        f"{len(analysis.holdings)} positions{DOT}"
        f"{len({h.sector for h in analysis.holdings})} sectors{DOT}"
        + ", ".join(c for c in countries[:6] if c)
        + f"{DOT}<b>{analysis.reporting_currency}</b>"
        + f"{DOT}prices as at <b>"
        # Shown in the firm's own local time (India), explicitly labelled --
        # fromtimestamp() with no tz renders in the SERVER's local clock,
        # which is UTC on Streamlit Cloud and was never actually said on
        # screen, so it silently looked like the wrong time to anyone here.
        + (f"{datetime.fromtimestamp(st.session_state.load_stamp, tz=DISPLAY_TZ):%H:%M}"
           " IST</b>")
        + (" (cached)" if offline else ""),
    ),
    unsafe_allow_html=True,
)

#: Gain on the whole book, or None when there is no cost to measure against.
gain_on_cost = (analysis.total_value / analysis.total_capital - 1
                if analysis.total_capital else None)

#: Share of holdings that beat their own benchmark -- a hit rate, one vote per
#: position regardless of its size. None when nothing could be scored, which is
#: different from zero.
win_rate = (len(analysis.outperformers) / len(scored) if scored else None)

st.markdown(
    brand.strip([
        {"label": "Cost", "value": money(analysis.total_capital)},
        # The value itself is neither good nor bad, so it stays neutral; the
        # line beneath it is a verdict and carries the colour. The word flips
        # with the sign -- "-12.3% gain" would be a nonsense.
        {"label": "Market value", "value": money(analysis.total_value),
         "note": (f"{gain_on_cost:+.1%} "
                  f"{'gain' if gain_on_cost >= 0 else 'loss'} on portfolio"
                  if gain_on_cost is not None else ""),
         "note_tone": ("" if gain_on_cost is None
                       else "up" if gain_on_cost >= 0 else "down")},
        # XIRR rather than "money-weighted": the name says the same thing to
        # anyone in the room, and it names the actual function rather than
        # describing its property. The note carries the period instead, which
        # the label cannot.
        {"label": "Portfolio XIRR",
         "value": fmt_pct(portfolio.irr) if portfolio else "n/a",
         "note": "since portfolio inception"},
        {"label": "Mandate",
         "value": mandate_scope(portfolio) if portfolio else "n/a"},
        {"label": "Benchmark",
         "value": mandate_index(portfolio) if portfolio else "n/a"},
        # The basis is stated on hover rather than left implicit, because
        # without it this reads as a market number over some unrelated window
        # sitting next to a money-weighted portfolio figure. It is not: it is
        # the IRR of these cashflows, on these dates, invested in the index
        # instead -- which is what makes it comparable to Portfolio XIRR.
        {"label": brand.abbr(
            "Benchmark XIRR",
            "Computed on the portfolio's own cashflows and dates -- the "
            "return the same capital would have earned invested in the "
            "mandate benchmark instead, so it is directly comparable to "
            "Portfolio XIRR."),
         "value": fmt_pct(portfolio.benchmark_irr) if portfolio else "n/a",
         "note": "since portfolio inception"},
        {"label": "Alpha on benchmark",
         "value": fmt_pct(portfolio.direct_alpha) if portfolio else "n/a",
         "note": "a year",
         "tone": ("up" if portfolio and portfolio.beat_mandate else "down")},
        # A hit rate, which is what "win rate" means: how often the manager was
        # right, counted per holding rather than weighted. The weighted version
        # is a different and equally fair question, so both are reported --
        # here and in full on Winners and laggards. They disagree on this book
        # because the losers are mostly small positions.
        {"label": "Portfolio win rate",
         "value": (f"{win_rate:.0%}" if win_rate is not None else "n/a"),
         # The denominator is the scored holdings, not every position. Where
         # they differ, say so on the tile -- "7 of 10" beside a header reading
         # 12 positions is the sort of gap a reviewer notices and nobody can
         # explain from the screen.
         "note": (f"{len(analysis.outperformers)} of {len(scored)} holdings"
                  + (f", {len(analysis.holdings) - len(scored)} too new"
                     if len(scored) != len(analysis.holdings) else "")),
         "tone": ("up" if win_rate is not None and win_rate >= 0.5
                  else "down")},
    ]),
    unsafe_allow_html=True,
)

# LoadReport's own docstring says warnings and skipped rows are "surfaced in
# the UI, never swallowed" -- but nothing ever rendered either one. A workbook
# uploaded without a sheet literally named "Holdings" (or any row missing a
# ticker/quantity/date) loaded as a silent, unexplained 0-position, $0 book:
# no error anywhere on this screen, just numbers that looked like a broken
# save. The real reason was one click away on Manage holdings, which is not
# where anyone reviewing an empty portfolio would think to look.
if analysis.load_report.warnings or analysis.load_report.skipped:
    lines = list(analysis.load_report.warnings)
    if analysis.load_report.skipped:
        shown = analysis.load_report.skipped[:5]
        n = len(analysis.load_report.skipped)
        lines.append(
            f"{n} row{'s' if n != 1 else ''} could not be read: "
            + "; ".join(shown) + (" ..." if n > 5 else ""))
    st.markdown(
        brand.note(
            "Some of this book did not load",
            "<br>".join(escape(line) for line in lines),
            "warn",
        ),
        unsafe_allow_html=True,
    )

# ------------------------------------------------------------ investments --

if view == "Portfolio overview":
    section("Holdings")

    f1, f2, f3 = st.columns([2, 2, 3])
    # The filter shows full country names while still filtering on the ISO
    # codes the data carries.
    pick_countries = f1.multiselect(
        "Market", analysis.countries, format_func=country_name,
        placeholder="All markets")
    pick_sectors = f2.multiselect("Sector", analysis.sectors,
                                  placeholder="All sectors")
    search = f3.text_input("Search", value="",
                           placeholder="Company name or ticker")

    shown = analysis.filtered(pick_sectors, pick_countries, search)

    if not shown:
        st.warning("No holdings match those filters.")
    else:
        # A second data strip is only worth the space when it says something
        # different from the one at the top of the page.
        is_filtered = len(shown) != len(analysis.holdings)
        if is_filtered:
            sel_value = sum(h.value_base for h in shown)
            sel_cost = sum(h.contributions_base for h in shown)
            sel_scored = [h for h in shown if h.direct_alpha is not None]
            sel_beat = [h for h in sel_scored if h.direct_alpha > 0]

            st.markdown(
                brand.strip([
                    {"label": "Selected", "value": str(len(shown)),
                     "note": f"of {len(analysis.holdings)}"},
                    {"label": "Cost", "value": money(sel_cost)},
                    {"label": "Market value", "value": money(sel_value),
                     "note": f"{sel_value / analysis.total_value:.0%} of book"
                             if analysis.total_value else ""},
                    {"label": "Gain on cost",
                     "value": fmt_pct(sel_value / sel_cost - 1) if sel_cost else "n/a",
                     "tone": "up" if sel_value >= sel_cost else "down"},
                    {"label": "Beating sector",
                     "value": f"{len(sel_beat)} / {len(sel_scored)}",
                     "tone": "up" if len(sel_beat) * 2 >= len(sel_scored) else "down"},
                ]),
                unsafe_allow_html=True,
            )

        heaviest = max((analysis.weight_of(h) for h in shown), default=1.0)
        rows = [[
            h.name,
            f'<span class="ps-muted">{h.asset_id}</span>',
            h.sector,
            market_cell(h.country),
            trend(h.asset_id),
            f"{h.stream.last_price:,.2f}" if h.stream.last_price else DASH,
            money(h.value_base),
            brand.weight_cell(analysis.weight_of(h), heaviest),
            brand.alpha_cell(h.total_return),
            fmt_pct(h.metrics.get("irr")),
        ] for h in sorted(shown, key=lambda x: -x.value_base)]

        st.markdown(
            brand.table(
                [("Holding", "ps-name"), ("Ticker", ""), ("Sector", ""),
                 ("Market", ""), ("1 year", ""), (col("Last"), "ps-num"),
                 ("Value", "ps-num"), (col("Weight"), "ps-num"),
                 (col("Total return"), "ps-alpha"), (col("IRR"), "ps-num")],
                rows,
            ),
            unsafe_allow_html=True,
        )

        st.write("")
        dark = st.session_state.dark
        # The full universe each breakdown draws from, in a fixed order -- not
        # the sectors or markets in *this* book. Colour comes from a name's
        # position here, so filtering the holdings table can shrink the pie
        # without repainting the slices that remain.
        BREAKDOWNS = (
            ("Weight by sector", lambda h: h.sector, sectors.CANONICAL,
             lambda g: g),
            ("Weight by market", lambda h: h.country, MARKET_UNIVERSE,
             country_name),
        )
        split = st.columns(2)
        for col, (title, key, universe, label_for) in zip(split, BREAKDOWNS):
            frame = pd.DataFrame([{"g": key(h), "v": h.value_base} for h in shown])
            grouped = frame.groupby("g")["v"].sum().sort_values(ascending=False)
            total = grouped.sum()
            col.markdown(brand.chart_label(title), unsafe_allow_html=True)
            if grouped.empty or not total:
                col.caption("Nothing to show for the current selection.")
                continue
            colors = [brand.categorical_slot(g, universe, dark) for g in grouped.index]
            slice_text = [f"{v / total:.0%}" for v in grouped.values]
            fig = go.Figure(go.Pie(
                labels=[label_for(g) for g in grouped.index],
                values=grouped.values,
                hole=0.58,
                sort=False,
                marker=dict(colors=colors),
                text=slice_text,
                textinfo="text",
                textposition="inside",
                insidetextorientation="radial",
                textfont=dict(color=[brand.slice_text_color(c) for c in colors],
                              size=11),
                hovertemplate="%{label}: %{value:,.0f} (%{percent})<extra></extra>",
            ))
            fig.update_layout(
                **brand.plotly_layout(dark),
                height=300,
                showlegend=True,
                legend=dict(orientation="v", x=1.03, y=0.5, yanchor="middle",
                           font=dict(size=10)),
                annotations=[dict(
                    text=f"<b>{money(total)}</b><br><span style='font-size:10px;"
                         f"letter-spacing:.01em;'>Total</span>",
                    x=0.5, y=0.5, showarrow=False, align="center",
                    font=dict(size=15, color=brand.DARK_TEXT if dark else brand.INK),
                )],
            )
            col.plotly_chart(fig, use_container_width=True)

# -------------------------------------------------------- mandate benchmark --

elif view == "Portfolio vs benchmark":
    if portfolio is None:
        st.warning("No portfolio-level result.")
    else:
        exposure = portfolio.exposure

        conflict = st.session_state.pop("mandate_conflict", None)
        if conflict:
            stated_name = MANDATES[conflict["stated"]].name
            computed_name = MANDATES[conflict["computed"]].name
            st.markdown(
                brand.note(
                    "Stated mandate doesn't match this book's composition",
                    f"You named this <b>{escape(stated_name)}</b> at upload. "
                    f"Based on what was actually uploaded, it's currently "
                    f"measured as <b>{escape(computed_name)}</b> instead -- "
                    "change it below if the stated mandate should stand.",
                    "warn"),
                unsafe_allow_html=True)

        st.markdown('<div class="ps-section">Mandate</div>',
                    unsafe_allow_html=True)
        d = portfolio.decision
        label = {"model": "proposed by model", "rules": "rules fallback",
                 "analyst": "chosen by analyst"}.get(d.source, d.source)
        # The mandate leads, because that is what this panel is headed.
        # The index and its ticker follow as the comparator chosen for it.
        st.markdown(
            f'<div style="font-family:{brand.FONT_DISPLAY};font-weight:700;'
            f'font-size:1rem;" class="ps-section">'
            f'{escape(mandate_scope(portfolio))}</div>'
            f'<div style="color:{brand.MUTED};font-size:.82rem;'
            f'margin-bottom:.5rem;">Measured against '
            f'<b>{escape(mandate_index(portfolio))}</b>'
            f'{DOT}{escape(d.benchmark_ticker)}<br>'
            f'run over this book&rsquo;s own cashflows and dates</div>'
            + brand.pill(label, "" if d.source == "model" else "navy")
            # A confidence is the model's, and only means something on a
            # proposal. Printing "confidence 100%" against a person's own
            # choice reads as a score for the analyst.
            + (brand.pill(f"confidence {d.confidence:.0%}", "navy")
               if d.source == "model" else ""),
            unsafe_allow_html=True,
        )
        # The mandate itself is sized from every holding's value, scored or
        # not -- a position bought this morning still has a price and still
        # counts here. But a reviewer who sees "5 of 10" on Winners and
        # laggards a minute later, right after reading "10 positions" on this
        # same book, needs the explanation before that gap looks like a lost
        # trade rather than a holding too new to have a whole-life return.
        if len(scored) != len(analysis.holdings):
            missing = len(analysis.holdings) - len(scored)
            st.markdown(
                brand.note(
                    "Not every holding is scored yet",
                    f"{missing} of {len(analysis.holdings)} holdings "
                    "are too recent to have a scored return yet -- bought "
                    "too recently for a whole-life return to be "
                    "calculated. They still count toward this mandate "
                    "and its exposure.",
                    "warn",
                ),
                unsafe_allow_html=True,
            )
        if d.rationale:
            # Escaped: a rationale is model output or contains an analyst's
            # email, and either can carry characters that render as markup.
            st.markdown(brand.note("Why this benchmark",
                                   escape(d.rationale)),
                        unsafe_allow_html=True)
        st.markdown(brand.section("Change the benchmark"),
                    unsafe_allow_html=True)
        benchmark_picker(d, MANDATES, "mandate", namespace=record.id)

        # -- return over a window ---------------------------------------------
        #
        # Everything above is whole-life and money-weighted, which is the right
        # measure of an investment. It is not what a committee asks, which is
        # how the book did last year and over three. That question has its own
        # arithmetic and it lives at portfolio level only -- window returns for
        # individual holdings belong on Winners and laggards, against their own
        # sectors. See window_return.py for the method.

        st.markdown(brand.section(
            "Return over one and three years",
            "Closing value, less opening value, less new cash put in, plus "
            "anything taken out &mdash; against the same benchmark, over the "
            "same dates."), unsafe_allow_html=True)

        windows = mandate_windows(book, offline, record.name,
                                  portfolio.decision.benchmark_ticker,
                                  st.session_state.load_stamp, book_rev,
                                  record.id, record.reporting_currency)
        usable = [w for w in windows if w.ok]

        if not usable:
            st.markdown(
                brand.note("Windows unavailable",
                           escape("; ".join(w.reason for w in windows)
                                  or "no data"), "warn"),
                unsafe_allow_html=True)
        else:
            def banded(value):
                return (brand.band_cell(value),
                        "ps-band " + brand.band_class(value))

            rows = []
            for w in usable:
                rows.append([
                    w.label + (", cumulative" if w.annualised else ""),
                    fmt_pct(w.portfolio), fmt_pct(w.benchmark),
                    banded(w.alpha),
                ])
                # A three-year number is quoted both ways on purpose: the
                # cumulative figure is what the account actually gained, the
                # annual one is what compares against every other rate on the
                # page.
                if w.annualised:
                    rows.append([
                        f"{w.label}, a year", fmt_pct(w.portfolio_annual),
                        fmt_pct(w.benchmark_annual), banded(w.alpha_annual),
                    ])

            st.markdown(
                brand.table(
                    [("Window", "ps-name"), ("Portfolio", "ps-alpha"),
                     (benchmark_cell(portfolio.decision.benchmark_ticker),
                      "ps-alpha"),
                     ("Difference", "ps-alpha")],
                    rows),
                unsafe_allow_html=True)

            st.markdown(brand.section(
                "Calculation detail",
                "Every input to the formula, so the figures above can be "
                "checked rather than taken."), unsafe_allow_html=True)

            def when(value):
                return value.strftime("%d %b %Y") if value else DASH

            st.markdown(
                brand.table(
                    [("", "ps-name")]
                    + [(w.label, "ps-alpha") for w in usable],
                    [["Dates",
                      *[f"{when(w.start)} to {when(w.end)}" for w in usable]],
                     ["Opening value", *[money(w.opening) for w in usable]],
                     ["Less new cash in",
                      *[money(w.injected) for w in usable]],
                     ["Plus exits and income out",
                      *[money(w.withdrawn) for w in usable]],
                     ["Closing value", *[money(w.closing) for w in usable]],
                     ["<b>Gain</b>",
                      *[f"<b>{money(w.gain)}</b>" for w in usable]],
                     ["Average capital at work",
                      *[money(w.average_capital) for w in usable]],
                     ["<b>Return</b>",
                      *[f"<b>{fmt_pct(w.portfolio)}</b>" for w in usable]]]),
                unsafe_allow_html=True)

            st.markdown(
                brand.note(
                    "How this is calculated",
                    "Gain is <b>closing value, less opening value, less new "
                    "cash put in, plus anything taken out</b>, so money paid "
                    "into the book is never counted as performance. The "
                    "percentage divides that gain by the capital actually at "
                    "work: each contribution is weighted by the share of the "
                    "window it was invested for, which is the Modified Dietz "
                    "method. Dividing by the opening value alone would "
                    "overstate any book that was added to mid-window. Prices "
                    "are dividend-adjusted on both sides, so income counts "
                    "for the portfolio and for the index alike."),
                unsafe_allow_html=True)

            caveats = []
            for w in usable:
                if w.opened_during:
                    caveats.append(
                        f"{w.opened_during} of {w.holdings} holdings were "
                        f"opened during the {w.label} window. The purchase "
                        "counts as new cash rather than as a gain, and the "
                        "capital is weighted for the part of the window it "
                        "was invested.")
                if w.unavailable:
                    caveats.append(
                        f"{w.label}: could not price "
                        + ", ".join(escape(n) for n in w.unavailable)
                        + " at the start of the window, so they are left out "
                          "of both ends of the sum.")
            if caveats:
                st.markdown(
                    brand.note("What this window does not cover",
                               " ".join(caveats), "warn"),
                    unsafe_allow_html=True)

        st.markdown(brand.section(
            "Portfolio against its benchmark",
            "Time-weighted from the first purchase, so opening a position does "
            "not read as a gain. Both rebased to 100."), unsafe_allow_html=True)

        try:
            index = portfolio_index(
                [h.stream for h in analysis.holdings], market,
                analysis.reporting_currency)
            bmk = market.prices(portfolio.decision.benchmark_ticker)
        except Exception as exc:  # noqa: BLE001
            index, bmk = None, None
            st.markdown(brand.note("Chart unavailable", str(exc), "warn"),
                        unsafe_allow_html=True)

        if index is not None and len(index) > 5:
            common = index.index.intersection(pd.to_datetime(bmk.index))
            if len(common) > 5:
                port = index.loc[common]
                bench = bmk.loc[common]
                port = port / port.iloc[0] * 100
                bench = bench / bench.iloc[0] * 100

                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=bench.index, y=bench, name=portfolio.decision.benchmark_ticker,
                    line=dict(color=brand.MUTED, width=1.3, dash="dot"),
                    hovertemplate="%{y:.1f}<extra></extra>"))
                fig.add_trace(go.Scatter(
                    x=port.index, y=port, name="Portfolio",
                    line=dict(color=brand.NAVY_DEEP, width=2),
                    fill="tonexty",
                    fillcolor=("rgba(43,185,168,.13)"
                               if port.iloc[-1] >= bench.iloc[-1]
                               else "rgba(232,24,124,.10)"),
                    hovertemplate="%{y:.1f}<extra></extra>"))
                fig.update_layout(
                    **brand.plotly_layout(st.session_state.dark), height=300,
                    yaxis_title="Rebased to 100", hovermode="x unified",
                    legend=dict(orientation="h", y=1.1, x=0,
                                bgcolor="rgba(0,0,0,0)"))
                fig.update_xaxes(gridcolor="rgba(0,0,0,0)")
                fig.update_yaxes(
                    gridcolor=brand.grid_colour(st.session_state.dark))
                st.plotly_chart(fig, use_container_width=True)

                # The chart and the headline can point opposite ways, and when
                # they do a reader has to be told why rather than left to
                # wonder which is broken.
                years = (common[-1] - common[0]).days / 365.25
                port_twr = (port.iloc[-1] / 100) ** (1 / years) - 1
                bench_twr = (bench.iloc[-1] / 100) ** (1 / years) - 1
                disagrees = ((port_twr > bench_twr)
                             != bool(portfolio.beat_mandate))

                st.markdown(
                    brand.note(
                        "Time-weighted and money-weighted disagree here"
                        if disagrees else "Reading this chart",
                        f"On the chart the portfolio compounded at "
                        f"<b>{fmt_pct(port_twr)}</b> a year against "
                        f"<b>{fmt_pct(bench_twr)}</b> for the benchmark. The "
                        f"headline alpha of <b>{fmt_pct(portfolio.direct_alpha)}"
                        f"</b> is money-weighted: it accounts for when capital "
                        "actually went in, and positions opened later carry "
                        "less of the record."
                        + (" The two point in opposite directions, which means "
                           "the timing of contributions helped the benchmark "
                           "more than it helped this book. The money-weighted "
                           "figure is what the client's capital earned; the "
                           "chart is what the strategy did."
                           if disagrees else
                           " Both agree on the direction here."),
                        "warn" if disagrees else "accent",
                    ),
                    unsafe_allow_html=True,
                )

        st.markdown(brand.section("Composition by capital"),
                    unsafe_allow_html=True)

        c = st.columns(3)
        c[0].metric("Developed markets", f"{exposure['developed_pct']:.0%}")
        c[1].metric("Emerging markets", f"{exposure['emerging_pct']:.0%}")
        c[2].metric("Unclassified", f"{exposure['unclassified_pct']:.0%}")

        st.write("")
        by_country = exposure["by_country"]
        dark = st.session_state.dark
        # Same donut treatment as the breakdowns on Portfolio overview, and
        # the same coloured identity per market -- a country's colour does
        # not change depending on which page or which of these two charts is
        # showing it.
        colors = [brand.categorical_slot(c, MARKET_UNIVERSE, dark)
                  for c in by_country]
        slice_text = [f"{w:.0%}" for w in by_country.values()]
        fig = go.Figure(go.Pie(
            labels=[country_name(c) for c in by_country],
            values=list(by_country.values()),
            hole=0.58,
            sort=False,
            marker=dict(colors=colors),
            text=slice_text,
            textinfo="text",
            textposition="inside",
            insidetextorientation="radial",
            textfont=dict(color=[brand.slice_text_color(c) for c in colors],
                          size=11),
            hovertemplate="%{label}: %{value:.1%} of capital<extra></extra>",
        ))
        fig.update_layout(
            **brand.plotly_layout(dark),
            height=320,
            showlegend=True,
            legend=dict(orientation="v", x=1.03, y=0.5, yanchor="middle",
                       font=dict(size=10)),
            annotations=[dict(
                text=f"<b>{money(analysis.total_capital)}</b><br><span "
                     f"style='font-size:10px;letter-spacing:.01em;'>"
                     f"Capital</span>",
                x=0.5, y=0.5, showarrow=False, align="center",
                font=dict(size=15, color=brand.DARK_TEXT if dark else brand.INK),
            )],
        )
        chart_col, _ = st.columns([1, 1])
        chart_col.plotly_chart(fig, use_container_width=True)

        for note in portfolio.notes:
            st.markdown(brand.card("Note", note, "warn"), unsafe_allow_html=True)

# ------------------------------------------------------ where alpha comes from --

# ------------------------------------------------------ where alpha comes from --

elif view == "Winners and laggards":
    section("Winners and laggards",
            "Every holding against its own benchmark over six months, one year "
            "and three years. Point to point on both sides, over the same "
            "trading days, so nothing here depends on when you bought, what "
            "you paid, or how much.")

    trends = trend_scores(book, offline, record.name,
                          st.session_state.load_stamp, book_rev,
                          record.id, record.reporting_currency)

    def _pct(value):
        """A banded cell: the tint carries the verdict, the figure carries the
        detail. Returned as (html, class) so the tint follows the value rather
        than the column."""
        return (brand.band_cell(value), "ps-band " + brand.band_class(value))

    # Named to avoid shadowing the module-level DOT separator for the rest of
    # this block.
    TONE_DOT = {"up": "&#9679;", "down": "&#9679;", "flat": "&#9679;"}

    def trend_of(holding):
        return trends.get(holding.asset_id)

    def status_cell(holding) -> str:
        score = trend_of(holding)
        if score is None or not score.scored:
            return f'<span class="ps-muted">not scored</span>'
        return (f'<span class="ps-{score.tone}">{TONE_DOT[score.tone]}</span> '
                f'{score.status}'
                f'<span class="ps-muted"> &middot; {score.action}</span>'
                + (" " + brand.pill("partial", "warn") if score.partial else ""))

    def weighted_of(holding):
        score = trend_of(holding)
        return score.weighted if score and score.scored else None

    def window_of(holding, code):
        """One window's PeriodReturn, or None."""
        score = trend_of(holding)
        return score.windows.get(code) if score else None

    def alpha_of(holding, code):
        window = window_of(holding, code)
        return window.relative if window else None

    CODES = list(TREND_WEIGHTS)          # 6M, 1Y, 3Y -- the scored windows
    LABELS = {"6M": "6 months", "1Y": "1 year", "3Y": "3 years"}

    # Every holding with a trend, not only those with a whole-life return.
    # Nothing on this page depends on when the position was opened, so a stock
    # bought this morning belongs here on exactly the same footing.
    graded = [h for h in analysis.holdings if weighted_of(h) is not None]
    winners = sorted((h for h in graded if weighted_of(h) > 0),
                     key=lambda h: -weighted_of(h))
    laggards = sorted((h for h in graded if weighted_of(h) <= 0),
                      key=lambda h: weighted_of(h))

    hit_rate = len(winners) / len(graded) if graded else None
    capital = analysis.total_capital or 1.0
    lift = sum(weighted_of(h) * h.contributions_base for h in winners)
    drag = sum(weighted_of(h) * h.contributions_base for h in laggards)

    st.markdown(
        brand.kpi_rail([
            # A hit rate: one vote per holding, regardless of size. The lift
            # and drag beside it are capital-weighted, so the size of the
            # positions is still on the page -- just not inside the win rate.
            {"label": "Beating benchmark",
             "value": (f"{hit_rate:.0%}" if hit_rate is not None else "n/a"),
             "note": f"{len(winners)} of {len(graded)} holdings",
             "tone": "up" if hit_rate is not None and hit_rate >= 0.5 else "down"},
            {"label": "Trailing benchmark", "value": str(len(laggards)),
             "note": f"{len(laggards) / max(len(graded), 1):.0%} of holdings",
             "tone": "down"},
            {"label": "Lift from winners", "value": fmt_pct(lift / capital),
             "note": "size-weighted", "tone": "up"},
            {"label": "Drag from laggards", "value": fmt_pct(drag / capital),
             "note": "size-weighted", "tone": "down"},
        ]),
        unsafe_allow_html=True,
    )

    def performance_table(group: list, caption: str, hint: str = "") -> None:
        st.markdown(brand.section(caption, hint), unsafe_allow_html=True)
        if not group:
            st.info("None in this portfolio.")
            return
        st.markdown(
            brand.table(
                [("Holding", "ps-name"), ("Benchmark", "")]
                + [(col(f"{c} alpha"), "") for c in CODES]
                + [(col("Weighted alpha"), ""), ("Status", ""),
                   (col("Weight"), "ps-num"), ("Caveats", "ps-tag")],
                [[h.name, benchmark_cell(h.decision.benchmark_ticker)]
                 + [_pct(alpha_of(h, c)) for c in CODES]
                 + [_pct(weighted_of(h)), status_cell(h),
                    f"{analysis.weight_of(h):.1%}",
                    # Empty for a book of listed equities, and populated the
                    # moment one carries a fund or a property. The page that
                    # collected these is gone; the annotation is not, because
                    # it is read where the holding is.
                    "".join(brand.pill(a.kind.replace("_", " "), "warn")
                            for a in h.material_caveats)
                    or f'<span class="ps-muted">{DASH}</span>']
                 for h in group],
            ),
            unsafe_allow_html=True,
        )
        st.write("")

    # Split on the security's own record against its benchmark, not on what the
    # money made. Every figure below is point to point over a fixed window: the
    # same dates for the stock and the index, whoever bought what and when.
    performance_table(
        winners, f"Outperformers ({len(winners)})",
        "Ranked by weighted alpha, best first.")
    performance_table(
        laggards, f"Underperformers ({len(laggards)})",
        "Ranked by weighted alpha, worst first.")

    if len(graded) < len(analysis.holdings):
        missing = len(analysis.holdings) - len(graded)
        st.markdown(
            brand.note(
                f"{missing} holding{'s' if missing != 1 else ''} not scored",
                ", ".join(h.name for h in analysis.holdings
                          if weighted_of(h) is None)
                + " &mdash; neither the security nor its benchmark has enough "
                  "price history to measure any of these windows.", "warn"),
            unsafe_allow_html=True)

    # -- the two sides of each gap ----------------------------------------
    #
    # -- attribution ------------------------------------------------------
    #
    # Rebuilt on the same measure as the rest of the page. The analysis layer's
    # own summaries aggregate Direct Alpha, which would put a cashflow-weighted
    # figure under a heading promising the opposite.

    st.markdown(brand.section(
        "Attribution",
        "Each group's weighted alpha, averaged across its holdings in "
        "proportion to their size in the book."), unsafe_allow_html=True)

    def grouped_alpha(key) -> list[tuple[str, int, int, float, float]]:
        buckets: dict[str, list] = {}
        for holding in graded:
            buckets.setdefault(key(holding) or "Unclassified",
                               []).append(holding)
        rows = []
        for name, members in buckets.items():
            size = sum(h.contributions_base for h in members) or 1e-9
            rows.append((
                name, len(members),
                sum(1 for h in members if weighted_of(h) > 0), size,
                sum(weighted_of(h) * h.contributions_base
                    for h in members) / size))
        return sorted(rows, key=lambda r: -r[4])

    for title, key, label, namer in (
        ("By sector", lambda h: h.sector, "Sector", str),
        ("By market", lambda h: h.country, "Market", country_name),
    ):
        rows = grouped_alpha(key)
        if not rows:
            continue
        st.markdown(f'<div class="ps-section">{title}</div>',
                    unsafe_allow_html=True)
        scale = max((abs(r[4]) for r in rows), default=1e-9) or 1e-9
        st.markdown(
            brand.table(
                [(label, "ps-name"), ("Holdings", "ps-num"),
                 ("Beat benchmark", "ps-num"),
                 (f"Capital ({analysis.reporting_currency})", "ps-num"),
                 (col("Weighted alpha"), "ps-alpha")],
                [[namer(name), str(count), str(beat), f"{size:,.0f}",
                  brand.alpha_cell(value, scale)]
                 for name, count, beat, size, value in rows],
            ),
            unsafe_allow_html=True,
        )
        st.write("")

# ----------------------------------------------------------- holding detail --

elif view == "Holding analysis":
    chosen = st.selectbox("Holding", [h.name for h in analysis.holdings])
    holding = next(h for h in analysis.holdings if h.name == chosen)

    left, right = st.columns([2, 1])

    with left:
        section(holding.name,
                f"{holding.sector}{DOT}{country_name(holding.country)}"
                f"{DOT}{currency_name(holding.stream.currency)}")

        # -- what it is trading at right now -------------------------------
        #
        # Held for a minute inside the shared market client, so moving the
        # holding dropdown does not fetch a quote on every rerun. Where the
        # market is shut or the feed will not answer, the last close is shown
        # and labelled as one -- the figure is still useful, the claim is not
        # the same.

        quote = market.quote(holding.asset_id)
        local = holding.stream.currency.upper()
        same_currency = (not quote.currency
                         or quote.currency.upper() == local)
        qty = holding.stream.quantity
        cost = holding.stream.cost_per_unit

        def money_local(value: float) -> str:
            return f"{value:,.2f}"

        def size_local(value: float) -> str:
            """A position value short enough to sit on one line. 118,214 ICICI
            shares is 158,903,259 rupees, which wrapped the tile."""
            for cutoff, suffix in ((1e9, "bn"), (1e6, "m"), (1e3, "k")):
                if abs(value) >= cutoff:
                    return f"{value / cutoff:,.1f}{suffix}"
            return f"{value:,.0f}"

        tiles = [{
            # "Live" only while the exchange is open. Outside regular hours the
            # feed's current price is the last completed session's.
            "label": ("Live price" if quote.trading
                      else "Last price" if quote.live else "Last close"),
            "value": f"{money_local(quote.price)} {quote.currency or local}",
            "note": (f"{quote.state_label}{DOT}{quote.stamp}"
                     if quote.state_label else quote.stamp),
        }]

        if quote.change_pct is not None:
            tiles.append({
                "label": "On the day",
                "value": fmt_pct(quote.change_pct, 2),
                "note": f"{quote.change:+,.2f} from {money_local(quote.previous_close)}",
                "tone": "up" if quote.change_pct > 0 else "down",
            })

        if qty and same_currency:
            tiles.append({
                "label": "Position value",
                "value": f"{size_local(qty * quote.price)} {local}",
                "note": f"{qty:,.0f} shares at {money_local(quote.price)}",
            })
            if cost:
                gain = quote.price / cost - 1.0
                tiles.append({
                    "label": "Against average cost",
                    "value": fmt_pct(gain),
                    "note": f"{money_local(quote.price)} against "
                            f"{money_local(cost)}",
                    "tone": "up" if gain > 0 else "down",
                })

        st.markdown(brand.kpi_rail(tiles), unsafe_allow_html=True)
        if quote.note:
            st.markdown(
                brand.note("Not a live quote", escape(quote.note), "warn"),
                unsafe_allow_html=True)
        if not same_currency:
            st.markdown(
                brand.note(
                    "Quoted in a different unit",
                    f"The feed quotes {escape(holding.asset_id)} in "
                    f"<b>{escape(quote.currency)}</b> while the book holds it "
                    f"in <b>{escape(local)}</b>. The position value is left "
                    "out rather than computed across two units &mdash; London "
                    "quoting in pence against a book in pounds is a factor of "
                    "a hundred.", "warn"),
                unsafe_allow_html=True)
        st.write("")

        st.markdown(brand.section(
            "Against its benchmark",
            "The security's own return against its benchmark's, measured over "
            "the same trading days. Independent of when the position was "
            "opened, what was paid, and how much &mdash; so none of it can be "
            "flattered by timing a purchase."),
            unsafe_allow_html=True)

        try:
            sec_prices = market.prices(holding.asset_id)
            bmk_prices = market.prices(holding.decision.benchmark_ticker)
            windows = period_compare(sec_prices, bmk_prices)
        except Exception as exc:  # noqa: BLE001
            windows = []
            st.markdown(brand.note("Unavailable", str(exc), "warn"),
                        unsafe_allow_html=True)

        # The headline strip: the three scored windows, each showing what the
        # stock did against what the index did, and the gap between them.
        by_code = {w.code: w for w in windows}
        score = trend_score(windows) if windows else None
        strip = []
        for code in TREND_WEIGHTS:
            w = by_code.get(code)
            label = {"6M": "6 months", "1Y": "1 year",
                     "3Y": "3 years"}.get(code, code)
            if w is None or not w.complete:
                strip.append({"label": label, "value": "n/a",
                              "note": "not enough history"})
                continue
            strip.append({
                "label": label + (", a year" if w.annualised else ""),
                "value": fmt_pct(w.relative),
                "note": f"{fmt_pct(w.security)} vs {fmt_pct(w.benchmark)}",
                "tone": "up" if w.relative > 0 else "down",
            })
        if score is not None and score.scored:
            strip.append({"label": "Weighted alpha",
                          "value": fmt_pct(score.weighted),
                          "note": score.status.lower(),
                          "tone": score.tone if score.tone != "flat" else ""})
        if strip:
            st.markdown(brand.kpi_rail(strip), unsafe_allow_html=True)
            st.write("")

        usable = [w for w in windows if w.complete]
        if usable:
            scale = max(abs(w.relative) for w in usable) or 1e-9
            st.markdown(
                brand.table(
                    [("Period", "ps-name"),
                     (holding.name[:22], "ps-num"),
                     (holding.decision.benchmark_ticker, "ps-num"),
                     ("Relative", "ps-alpha"), ("Basis", "ps-muted")],
                    [[w.label.title(),
                      fmt_pct(w.security), fmt_pct(w.benchmark),
                      brand.alpha_cell(w.relative, scale),
                      "annualised" if w.annualised else "total"]
                     if w.complete else
                     [w.label.title(), DASH, DASH, DASH,
                      '<span class="ps-muted">insufficient history</span>']
                     for w in windows],
                ),
                unsafe_allow_html=True,
            )

            st.write("")
            window = st.radio("Chart window", ["1M", "6M", "1Y", "3Y", "5Y"],
                              index=2, horizontal=True,
                              label_visibility="collapsed",
                              key=f"win_{holding.asset_id}")
            frame = rebased(sec_prices, bmk_prices, window)
            if not frame.empty:
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=frame.index, y=frame["security"], name=holding.name,
                    line=dict(color=brand.NAVY_DEEP, width=1.8),
                    hovertemplate="%{y:.1f}<extra></extra>"))
                fig.add_trace(go.Scatter(
                    x=frame.index, y=frame["benchmark"],
                    name=holding.decision.benchmark_ticker,
                    line=dict(color=brand.TEAL, width=1.4, dash="dot"),
                    hovertemplate="%{y:.1f}<extra></extra>"))
                fig.update_layout(
                    **brand.plotly_layout(st.session_state.dark), height=280,
                    yaxis_title="Rebased to 100", hovermode="x unified",
                    legend=dict(orientation="h", y=1.12, x=0,
                                bgcolor="rgba(0,0,0,0)"))
                fig.update_xaxes(gridcolor="rgba(0,0,0,0)")
                fig.update_yaxes(
                    gridcolor=brand.grid_colour(st.session_state.dark))
                st.plotly_chart(fig, use_container_width=True)

        st.write("")
        st.markdown('<div class="ps-section">Cashflows</div>',
                    unsafe_allow_html=True)
        st.markdown(
            brand.table(
                [("Date", ""), ("Type", ""), ("Amount", "ps-num")],
                [[str(f.when), f.kind.value.replace("_", " ").title(),
                  f"{f.amount:,.0f}"] for f in holding.stream.ordered()],
            ),
            unsafe_allow_html=True,
        )

    with right:
        st.markdown('<div class="ps-section">Sector benchmark</div>',
                    unsafe_allow_html=True)
        d = holding.decision
        tone = {"model": "", "rules": "warn", "analyst": "navy",
                "universe": "navy"}.get(d.source, "")
        label = {"model": "proposed by model", "rules": "rules fallback",
                 "analyst": "chosen by analyst",
                 "universe": "only benchmark for this market"}.get(d.source,
                                                                   d.source)
        st.markdown(
            f'<div style="font-family:{brand.FONT_DISPLAY};font-weight:700;'
            f'font-size:1rem;" class="ps-section">{d.benchmark_ticker}</div>'
            f'<div style="color:{brand.MUTED};font-size:.82rem;'
            f'margin-bottom:.5rem;">{d.benchmark_name}</div>'
            + brand.pill(label, tone)
            + (brand.pill(f"confidence {d.confidence:.0%}", "navy")
               if d.source == "model" else ""),
            unsafe_allow_html=True,
        )
        st.write("")
        if d.rationale:
            st.markdown(brand.card("Rationale", escape(d.rationale)),
                        unsafe_allow_html=True)
        bench = UNIVERSE.get(d.benchmark_ticker)
        if bench and bench.caveat:
            st.markdown(brand.card("Benchmark caveat", bench.caveat, "warn"),
                        unsafe_allow_html=True)
        if d.alternatives_considered:
            st.markdown(
                f'<div style="font-size:.76rem;color:{brand.MUTED};'
                f'margin:.2rem 0 .8rem 0;">Also considered: '
                + ", ".join(d.alternatives_considered) + "</div>",
                unsafe_allow_html=True,
            )

        st.markdown(brand.section("Change the benchmark"), unsafe_allow_html=True)
        # The list is the holding's own market, the same closed set the model
        # chose from. Offering every global index here would let a click
        # reintroduce exactly the cross-market comparison the engine prevents.
        choices = {b.ticker: b for b in holding_candidates(holding.stream.country)}
        benchmark_picker(d, choices, holding.asset_id)

        if holding.adjustments:
            st.markdown('<div class="ps-section">Caveats</div>',
                        unsafe_allow_html=True)
            for adj in holding.adjustments:
                st.markdown(
                    brand.card(
                        adj.kind.replace("_", " ").title() + " &mdash; "
                        + adj.headline,
                        adj.detail, SEVERITY_TONE.get(adj.severity, ""),
                    ),
                    unsafe_allow_html=True,
                )

        if holding.error:
            st.error(holding.error)

# ---------------------------------------------------------------- caveats --

# -------------------------------------------------------------------- news --

elif view == "News":
    section("Company news",
            "What is being reported about each holding, dated and sorted into "
            "the six categories. Every headline links to the article.")

    chosen = st.selectbox("Company", [h.name for h in analysis.holdings],
                          key="news_company")
    holding = next(h for h in analysis.holdings if h.name == chosen)

    feed = company_news(holding.asset_id, holding.name,
                        st.session_state.load_stamp)

    if not feed.items:
        st.markdown(brand.note("Nothing returned",
                               feed.note or "The feed returned no items.",
                               "warn"), unsafe_allow_html=True)
    else:
        grouped = feed.by_category()
        st.markdown(
            brand.strip([
                {"label": "Articles", "value": str(len(feed.items))},
                {"label": "Categories", "value": str(len(grouped))},
                {"label": "Most recent",
                 "value": max(i.when for i in feed.items)},
                {"label": "Outlets",
                 "value": str(len({i.source for i in feed.items}))},
            ]),
            unsafe_allow_html=True)

        # -- key highlights -----------------------------------------------
        #
        # The same items as below, in priority order. A reader with four
        # minutes needs the lawsuit and the order win, not ninety rows in six
        # buckets -- and every row here still links to the article it came
        # from, so nothing is summarised away.

        def headline_link(row) -> str:
            if not row.url:
                return escape(row.headline)
            return (f'<a href="{row.url}" target="_blank" '
                    f'rel="noopener noreferrer" '
                    f'style="color:{brand.NAVY_DEEP};text-decoration:underline;'
                    f'text-underline-offset:2px;">{escape(row.headline)}</a>')

        top = feed.highlights()
        if top:
            bad = sum(1 for r in top if r.tone == "bad")
            good = sum(1 for r in top if r.tone == "good")
            st.markdown(brand.section(
                "Key highlights",
                f"{len(top)} item{'s' if len(top) != 1 else ''} that would "
                f"change the investment case &mdash; {bad} negative, {good} "
                f"positive, newest first. Each is also filed under its "
                f"category below."), unsafe_allow_html=True)
            st.markdown(
                brand.table(
                    [("Date", ""), ("Signal", "ps-tag"),
                     ("Headline", "ps-name"), ("Category", ""),
                     ("Published by", "")],
                    [[
                        f'<span class="ps-muted">{r.when or DASH}</span>',
                        brand.pill(r.signal,
                                   {"bad": "bad", "good": "",
                                    "neutral": "navy"}.get(r.tone, "navy")),
                        headline_link(r),
                        f'<span class="ps-muted">{escape(r.category)}</span>',
                        f'<span class="ps-muted">{escape(r.source)}</span>',
                    ] for r in top],
                ),
                unsafe_allow_html=True)
            st.markdown(
                brand.note(
                    "How these were picked",
                    "Matched on the wording of the headline against a short "
                    "list of events that move an investment case: senior "
                    "departures and appointments, order and contract wins, "
                    "suits and penalties, rating actions, guidance changes, "
                    "deals, buybacks and regulatory outcomes. The rules are "
                    "deliberately narrow &mdash; a story missed here is still "
                    "in its category below, while a broker opinion piece "
                    "promoted to the top under a red flag would cost this "
                    "section the credibility that makes it useful. The colour "
                    "says which way the wording cuts, not what the market "
                    "did."),
                unsafe_allow_html=True)
            st.write("")
        else:
            # Said rather than left blank. A section that simply vanishes reads
            # as a feature that failed; "nothing material in fifty articles" is
            # itself a finding about a quiet quarter.
            st.markdown(brand.section("Key highlights"),
                        unsafe_allow_html=True)
            st.markdown(
                brand.note(
                    "Nothing material in this feed",
                    f"None of the {len(feed.items)} articles below matches a "
                    "material event &mdash; no senior departure, order win, "
                    "suit, rating action, guidance change, deal or buyback. "
                    "That is the common case for a large company in a quiet "
                    "month, and it is reported rather than filled with the "
                    "most eye-catching thing available."),
                unsafe_allow_html=True)
            st.write("")

        for category, rows in grouped.items():
            st.markdown(
                f'<div class="ps-section">{category}'
                f'<span class="ps-muted" style="font-weight:400;'
                f'text-transform:none;letter-spacing:0;"> &nbsp;{len(rows)}'
                f'</span></div>',
                unsafe_allow_html=True)
            st.markdown(
                brand.table(
                    [("Date", ""), ("Headline", "ps-name"), ("Published by", "")],
                    [[
                        f'<span class="ps-muted">{r.when or DASH}</span>',
                        headline_link(r)
                        + (" " + brand.pill(
                            r.signal, {"bad": "bad", "good": "",
                                       "neutral": "navy"}.get(r.tone, "navy"))
                           if r.material else ""),
                        f'<span class="ps-muted">{escape(r.source)}</span>',
                    ] for r in rows],
                ),
                unsafe_allow_html=True)

    st.markdown(
        brand.note(
            "Where these come from",
            "Google News and Yahoo Finance, merged and de-duplicated, with the "
            "outlet that wrote each piece named in its own column. Both are "
            "aggregators rather than publishers, so nothing here is marked "
            "official the way an exchange filing is &mdash; it is press "
            "coverage, and the link goes to the article. "
            "Items are kept only where the company's own name appears in the "
            "headline: a search returns a great deal that merely mentions a "
            "holding, and a market wrap is not news about one of its "
            "constituents. Categories are matched from the wording of the "
            "headline, so a story that fits none of the six is filed under "
            "Other rather than guessed at.",
        ),
        unsafe_allow_html=True)

    with st.expander("Regulatory filings for this company"):
        st.markdown(
            f'<div style="font-size:.78rem;color:{brand.MUTED};'
            f'margin-bottom:.6rem;">What the company was <i>required</i> to '
            f'disclose, as opposed to what was written about it. A filing is '
            f'the event; an article is somebody\'s account of it. Categories '
            f'here come from the regulator\'s own taxonomy &mdash; an SEC 8-K '
            f'item code, an NSE subject field &mdash; rather than from '
            f'wording.</div>',
            unsafe_allow_html=True)
        filings = company_filings(holding.asset_id, holding.name,
                                  holding.stream.country or "",
                                  st.session_state.load_stamp)
        if not filings.covered:
            st.markdown(brand.note(
                f"No primary feed for {holding.stream.country}",
                filings.note, "warn"), unsafe_allow_html=True)
        elif not filings.items:
            st.info(filings.note or "Nothing returned.")
        else:
            st.markdown(
                brand.table(
                    [("Date", ""), ("Disclosure", "ps-name"),
                     ("Exchange subject", ""), ("Source", "")],
                    [[
                        f'<span class="ps-muted">{r.when}</span>',
                        (f'<a href="{r.url}" target="_blank" '
                         f'rel="noopener noreferrer" '
                         f'style="color:{brand.NAVY_DEEP};'
                         f'text-decoration:underline;'
                         f'text-underline-offset:2px;">{escape(r.headline)}</a>'
                         if r.url else escape(r.headline)),
                        # Only a wording match is an inference. A 10-Q being
                        # financial performance is what the form is, and
                        # flagging that as inferred undersells a certainty.
                        f'<span class="ps-muted">{escape(r.subject)}</span>'
                        + (" " + brand.pill("inferred", "warn")
                           if r.classified_by == "wording" else ""),
                        f'<span class="ps-muted">{r.source}</span>'
                        + (f' <span class="ps-muted">{r.form}</span>'
                           if r.form else ""),
                    ] for r in filings.items],
                ),
                unsafe_allow_html=True)

# -------------------------------------------------------------- portfolios --

elif view == "Manage holdings":
    section("Manage holdings",
            "The book as a table. Correct a quantity, add a row for a new "
            "position, delete one you have sold &mdash; then save, and every "
            "figure in the tool recomputes from it. The uploaded file is never "
            "written over.")

    def reload_grid() -> None:
        """Force the editor to re-read the file after it changes underneath."""
        st.session_state.grid_version = st.session_state.get("grid_version", 0) + 1

    def recompute(message: str) -> None:
        """Saving changes the workbook's modification time, and that alone
        invalidates the analysis. Prices stay in the shared client, so the
        recompute is a second rather than a refetch."""
        reload_grid()
        st.session_state.grid_flash = message
        st.rerun()

    flash = st.session_state.pop("grid_flash", None)
    if flash:
        st.success(flash)

    # Set by _upload_portfolio_form the moment this exact book was uploaded,
    # and cleared the instant a different one is opened -- picking another
    # portfolio from the sidebar is an unambiguous "I've moved on" signal, so
    # no separate dismiss button is needed.
    just_uploaded = st.session_state.get("just_uploaded")
    landed_from_upload = bool(
        just_uploaded and just_uploaded.get("portfolio_id") == record.id)
    if just_uploaded and not landed_from_upload:
        st.session_state.pop("just_uploaded", None)
        st.session_state.pop("review_order", None)

    try:
        current = holdings_edit.read_holdings(record.workbook)
    except Exception as exc:  # noqa: BLE001
        current = None
        st.markdown(
            brand.card("Could not read the holdings sheet", escape(str(exc)),
                       "bad"),
            unsafe_allow_html=True)

    if current is not None:
        if landed_from_upload:
            misses = st.session_state.get("sector_misses", {})
            n_gaps = sum(1 for _, row in current.iterrows()
                        if holdings_edit.row_gaps(row, misses))
            if n_gaps:
                st.markdown(
                    brand.note(
                        f"Loaded {len(current)} position"
                        f"{'s' if len(current) != 1 else ''}",
                        f"{n_gaps} row{'s' if n_gaps != 1 else ''} below "
                        "need a look before these figures are final -- "
                        "they're sorted to the top of the table, under "
                        "“Needs attention”.", "warn"),
                    unsafe_allow_html=True)
            else:
                st.markdown(
                    brand.note(
                        f"Loaded {len(current)} position"
                        f"{'s' if len(current) != 1 else ''}",
                        "Nothing missing. Review the table below before "
                        "treating these figures as final."),
                    unsafe_allow_html=True)

        st.markdown(
            f'<div style="font-size:.78rem;color:{brand.MUTED};'
            f'margin:-.35rem 0 .5rem 0;">Editing <b>{escape(record.name)}</b>'
            f'{DOT}{len(current)} position'
            f'{"s" if len(current) != 1 else ""}'
            + (f'{DOT}working copy, edited in the app'
               if record.edited else
               f'{DOT}as uploaded') + '</div>',
            unsafe_allow_html=True)

        # -- record a trade ------------------------------------------------
        #
        # The grid alone can do everything this form does, but not the
        # arithmetic: buying more of a holding re-weights its average cost, and
        # that is the number people get wrong by hand. Doing it here also means
        # the analyst enters what actually happened -- a price and a date --
        # rather than a figure they had to derive first.

        with st.expander("Record a buy or a sale"):
            st.caption("Applies to the position and saves straight away. Any "
                       "unsaved edits in the table below are not included.")
            t = st.columns([2, 1, 1, 1, 1.4])
            held = [str(x) for x in current["Ticker"] if str(x).strip()]
            trade_ticker = t[0].text_input(
                "Ticker", key="trade_ticker",
                placeholder="AAPL, or a new one").strip().upper()
            trade_side = t[1].selectbox("Action", ["Buy", "Sell"],
                                        key="trade_side")
            trade_qty = t[2].number_input("Shares", min_value=0.0, step=1.0,
                                          value=0.0, key="trade_qty")
            trade_price = t[3].number_input(
                "Price", min_value=0.0, step=1.0, value=0.0, key="trade_price",
                help="Per share, in the currency it trades in. Ignored on a "
                     "sale: selling does not change what the remaining shares "
                     "cost.")
            trade_date = t[4].date_input("Trade date", value=datetime.today(),
                                         key="trade_date")

            known = trade_ticker in {h.upper() for h in held}
            new_name = new_country = ""
            AUTO = "Auto, from the ticker"
            if trade_ticker and not known and trade_side == "Buy":
                n = st.columns([2, 1])
                new_name = n[0].text_input(
                    "Security name", key="trade_name",
                    placeholder="Left blank, the ticker is used")
                # Auto by default. The exchange suffix already says where a
                # security is listed, and where it does not the feed does, so
                # asking is a question with a known answer.
                picked_market = n[1].selectbox(
                    "Market", [AUTO] + sorted(holdings_edit.MARKET_CURRENCY),
                    key="trade_country",
                    help="Left on Auto, the market is read from the ticker's "
                         "exchange suffix, or from the feed if it has none. "
                         "Set it yourself for a symbol neither can place.")
                new_country = "" if picked_market == AUTO else picked_market

            if st.button("Record trade", type="primary"):
                try:
                    # A position opened here has no sector until something
                    # supplies one, and without a sector it is benchmarked
                    # against the broad market rather than its own industry.
                    # Looked up only for a new holding: an existing row already
                    # has whatever the file or the analyst decided.
                    guess = None
                    market = new_country
                    if trade_ticker and trade_side == "Buy" and not known:
                        symbol = normalise_ticker(trade_ticker, new_country)
                        with st.spinner(f"Looking up {symbol}..."):
                            guess = sectors.identify(symbol, new_name,
                                                     new_country)
                        # A market the analyst chose stands. Otherwise take
                        # what the ticker and the feed say between them.
                        market = new_country or guess.market

                    updated, said = holdings_edit.apply_trade(
                        current, trade_ticker, trade_side, trade_qty,
                        trade_price, trade_date, name=new_name,
                        country=market,
                        currency=guess.currency if guess else "",
                        sector=guess.sector if guess else "")
                    if guess is not None and not new_country:
                        if guess.market_found:
                            said += (f" Market set to "
                                     f"{country_name(guess.market)} "
                                     f"({guess.market_attribution}).")
                        else:
                            said += (" The market could not be read from the "
                                     "ticker, so it is left blank &mdash; set "
                                     "it below.")
                    if guess is not None and guess.currency_note:
                        said += f" Currency left blank: {guess.currency_note}."
                    if guess is not None and guess.found:
                        said += (f" Sector set to {guess.sector} "
                                 f"({guess.attribution}).")
                    elif guess is not None:
                        # Remembered with its reason, so the panel below shows
                        # this holding as "not found" rather than as one that
                        # has simply never been asked about.
                        pending_misses = dict(
                            st.session_state.get("sector_misses", {}))
                        pending_misses[(symbol, "Sector")] = (
                            guess.note or "no source could place it")
                        if not market:
                            pending_misses[(symbol, "Country")] = (
                                "neither the ticker's suffix nor the feed "
                                "says where this is listed")
                        st.session_state.sector_misses = pending_misses
                        said += (" No sector was found, so it is measured "
                                 "against its market until one is set below.")
                    problems = holdings_edit.validate(updated)
                    flaky = []
                    if not problems:
                        fresh = {str(x).strip().upper()
                                 for x in updated["Ticker"]} - {
                                     str(x).strip().upper() for x in current["Ticker"]}
                        with st.spinner("Checking the price feed..."):
                            trouble = holdings_edit.unpriced(updated, market,
                                                             only=fresh)
                        # Only a symbol the feed says does not exist blocks the
                        # trade. A rate limit or a timeout is the plumbing, and
                        # refusing to record a real purchase because Yahoo was
                        # busy is the wrong way round: the position exists
                        # whether or not a price arrived this second.
                        for p in trouble:
                            if p.unknown:
                                problems.append(
                                    f"{p.ticker} is not a symbol the price "
                                    f"feed knows: {p.reason}")
                            else:
                                flaky.append(p)
                    if problems:
                        st.markdown(
                            brand.card("Not recorded",
                                       "<br>".join(escape(p) for p in problems),
                                       "bad"),
                            unsafe_allow_html=True)
                    else:
                        portfolio_store.save_holdings(record, updated)
                        if flaky:
                            # The feed's own words are carried through. A
                            # recurrence is then something an analyst can
                            # report precisely instead of as "it did not work".
                            said += (" Recorded, but the price feed would not "
                                     "answer for "
                                     + "; ".join(f"{p.ticker} ({p.reason})"
                                                 for p in flaky)
                                     + ". Press Refresh prices in a moment to "
                                       "value it.")
                        recompute(said)
                except EditError as exc:
                    st.markdown(brand.card("Not recorded", escape(str(exc)),
                                           "bad"), unsafe_allow_html=True)

        # -- sectors that are missing --------------------------------------
        #
        # A blank sector costs a level of the analysis: the holding falls back
        # to its broad market and its industry is never isolated. Offered as a
        # button rather than done silently on every render, because it reaches
        # the network and because an analyst should see what it decided.

        # Both columns decide a benchmark, so both are chased the same way. The
        # market is the more expensive of the two to leave blank: it decides
        # which country's indices the holding is eligible for at all, and the
        # currency along with it.
        gaps = ([(t, n, "Country") for t, n, _c
                 in holdings_edit.missing_markets(current)]
                + [(t, n, "Sector") for t, n, _c
                   in holdings_edit.missing_sectors(current)])
        misses = st.session_state.get("sector_misses", {})

        if gaps:
            kinds = {holdings_edit.GAP_FIELDS[col] for _t, _n, col in gaps}
            st.markdown(
                brand.note(
                    f"{len(gaps)} gap{'s' if len(gaps) != 1 else ''} in the "
                    "book",
                    "Until "
                    + (" and ".join(sorted(kinds)))
                    + " "
                    + ("is" if len(kinds) == 1 else "are")
                    + " set, these holdings are measured against a broader "
                      "index than they should be. The market is read from the "
                      "ticker's exchange suffix, then from Yahoo Finance; the "
                      "sector from Yahoo Finance, then from the model.",
                    "warn"),
                unsafe_allow_html=True)

            # What happened to each one, so "we tried and nothing could place
            # it" is distinguishable from "nobody has asked yet". Without that
            # distinction the only thing this screen can offer is to run the
            # same failing lookup again.
            st.markdown(
                brand.table(
                    [("Holding", "ps-name"), ("Ticker", ""), ("Missing", ""),
                     ("Status", "ps-tag"), ("What happened", "")],
                    [[escape(name or ticker),
                      f'<span class="ps-muted">{escape(ticker)}</span>',
                      holdings_edit.GAP_FIELDS[col],
                      (brand.pill("not found", "bad")
                       if (ticker, col) in misses
                       else brand.pill("not looked up", "warn")),
                      (escape(misses[(ticker, col)])
                       if (ticker, col) in misses
                       else '<span class="ps-muted">no source has been asked '
                            'for this one yet</span>')]
                     for ticker, name, col in gaps]),
                unsafe_allow_html=True)

            if st.button(f"Identify {len(gaps)} missing "
                         f"value{'s' if len(gaps) != 1 else ''}",
                         type="primary"):
                with st.spinner("Looking up..."):
                    working, filled, unresolved = holdings_edit.fill_markets(
                        current, sectors.identify)
                    working, more, also = holdings_edit.fill_sectors(
                        working, sectors.identify)
                found = ([(t, v, how, "Country") for t, v, how in filled]
                         + [(t, v, how, "Sector") for t, v, how in more])
                st.session_state.sector_misses = {
                    **{(t, "Country"): why for t, _n, why in unresolved},
                    **{(t, "Sector"): why for t, _n, why in also},
                }
                if found:
                    portfolio_store.save_holdings(record, working)
                    recompute("; ".join(
                        f"{t} {holdings_edit.GAP_FIELDS[col]} is "
                        f"{country_name(v) if col == 'Country' else v} ({how})"
                        for t, v, how, col in found) + ".")
                st.rerun()

            # Setting one by hand is not a fallback for when the lookup fails,
            # it is the authority: an analyst who knows the business overrules
            # every feed there is, and a value they state is never looked up or
            # overwritten afterwards.
            st.markdown(brand.section(
                "Set values manually",
                "Overrules every source. Choose one and it is used exactly as "
                "stated."), unsafe_allow_html=True)

            market_options = sorted(set(COUNTRY_NAMES) - {"WW"})
            chosen: dict[tuple[str, str], str] = {}
            for ticker, name, col in gaps:
                left, right = st.columns([2, 2])
                left.markdown(
                    f'<div style="padding-top:.45rem;font-size:.85rem;">'
                    f'<b>{escape(name or ticker)}</b>'
                    f'<span class="ps-muted">{DOT}{escape(ticker)}'
                    f'{DOT}{holdings_edit.GAP_FIELDS[col]}</span>'
                    f'</div>', unsafe_allow_html=True)
                if col == "Sector":
                    options_for, label = sectors.CANONICAL, "a sector"
                    shown = (lambda s: s or "Choose a sector")
                else:
                    options_for, label = market_options, "a market"
                    shown = (lambda s: country_name(s) if s
                             else "Choose a market")
                picked = right.selectbox(
                    f"{col} for {ticker}", [""] + options_for,
                    key=f"manual_{col}_{record.id}_{ticker}",
                    format_func=shown, label_visibility="collapsed")
                if picked:
                    chosen[(ticker, col)] = picked

            if st.button(f"Save {len(chosen)} value"
                         f"{'s' if len(chosen) != 1 else ''}",
                         disabled=not chosen,
                         help=None if chosen else "Choose a value above first."):
                stated = current
                for column in ("Country", "Sector"):
                    picks = {t: v for (t, c), v in chosen.items() if c == column}
                    if picks:
                        stated = holdings_edit.set_values(stated, picks, column)
                problems = holdings_edit.validate(stated)
                if problems:
                    st.markdown(
                        brand.card("Not saved",
                                   "<br>".join(escape(p) for p in problems),
                                   "bad"),
                        unsafe_allow_html=True)
                else:
                    portfolio_store.save_holdings(record, stated)
                    st.session_state.sector_misses = {
                        k: v for k, v in misses.items() if k not in chosen}
                    # Plain text: this goes to Streamlit's own status message,
                    # where an HTML entity would appear verbatim.
                    recompute("; ".join(
                        f"{t} {holdings_edit.GAP_FIELDS[c]} set to "
                        f"{country_name(v) if c == 'Country' else v}"
                        for (t, c), v in chosen.items())
                        + ". Stated by you, so no source will overwrite it.")

        # -- the grid ------------------------------------------------------

        def options(column: str, base: dict) -> list[str]:
            """Known codes, plus whatever this book already uses. A dropdown
            that silently drops an unfamiliar market is worse than a free
            text field."""
            present = {str(v).strip().upper() for v in current[column]
                       if str(v).strip()}
            return sorted(set(base) - {"WW"} | present)

        # Right after upload, gapped rows are sorted to the top so a 40-row
        # book never buries the one missing cell at row 33 -- frozen at
        # upload time (review_order), not recomputed on every keystroke,
        # which would make a row jump the moment the analyst fixes the very
        # cell they are looking at.
        order = st.session_state.get("review_order") if landed_from_upload else None
        if order:
            position = {t: i for i, t in enumerate(order)}
            current = current.iloc[sorted(
                range(len(current)),
                key=lambda i: position.get(
                    str(current.iloc[i]["Ticker"]).strip(), len(order)))
            ].reset_index(drop=True)

        # A pointer, not an editor: st.data_editor cannot conditionally colour
        # a cell or row, so what needs a look is named here in its own
        # read-only column instead, and fixed by clicking the flagged cell in
        # its own editable column alongside it. Generalised past Country/
        # Sector to every field a save would otherwise refuse as incomplete.
        misses = st.session_state.get("sector_misses", {})
        current.insert(0, "Needs attention", [
            holdings_edit.row_gaps(row, misses)
            for _, row in current.iterrows()])

        edited = st.data_editor(
            current, key=f"grid_{record.id}_{st.session_state.get('grid_version', 0)}",
            num_rows="dynamic", use_container_width=True, hide_index=True,
            column_config={
                "Needs attention": st.column_config.TextColumn(
                    "Needs attention", width="medium", disabled=True),
                "Security Name": st.column_config.TextColumn(
                    "Security", width="medium"),
                "Ticker": st.column_config.TextColumn(
                    "Ticker", width="small",
                    help="The symbol prices are fetched under. Tadawul ends "
                         ".SR, the NSE .NS, Euronext Amsterdam .AS."),
                "Ccy": st.column_config.SelectboxColumn(
                    "Ccy", width="small",
                    options=options("Ccy", CURRENCY_NAMES)),
                "Quantity": st.column_config.NumberColumn(
                    "Shares", min_value=0.0, step=1.0),
                "Avg Cost": st.column_config.NumberColumn(
                    "Avg cost", min_value=0.0, format="%.2f",
                    help="Per share, in the listing currency. Left blank, the "
                         "closing price on the purchase date is used."),
                "Purchase Date": st.column_config.DateColumn(
                    "Purchased", format="DD MMM YYYY"),
                "Country": st.column_config.SelectboxColumn(
                    "Market", width="small",
                    options=options("Country", COUNTRY_NAMES)),
                "Sector": st.column_config.TextColumn("Sector", width="medium"),
            },
        )

        pending = holdings_edit.changes(current, edited)
        if pending:
            st.markdown(
                brand.note(
                    f"{len(pending)} unsaved change"
                    f"{'s' if len(pending) != 1 else ''}",
                    "<br>".join(pending), "warn"),
                unsafe_allow_html=True)
        else:
            # Said out loud, because the alternative is a greyed Save button
            # and no explanation for it. Someone who has just typed in a cell
            # and not yet left it sees exactly this state, and needs to be told
            # that the edit has not registered rather than that the tool is
            # stuck.
            st.markdown(
                brand.note(
                    "No unsaved changes",
                    "The table matches the saved book, so there is nothing to "
                    "save. If you have just typed in a cell, press "
                    "<b>Enter</b> or click outside it &mdash; an edit is not "
                    "committed until the cell loses focus, and it will appear "
                    "here the moment it is."),
                unsafe_allow_html=True)

        actions = st.columns([1.2, 1, 1, 3])
        if actions[0].button(
                "Save and recompute", type="primary", disabled=not pending,
                help=None if pending else
                "Nothing to save yet -- the table matches the saved book."):
            clean = holdings_edit.tidy(edited)
            problems = holdings_edit.validate(clean)
            flaky = []
            if not problems:
                # Only the symbols that moved are checked. The rest were
                # priced when the book was loaded and the answer has not
                # changed since.
                fresh = {str(x).strip().upper() for x in clean["Ticker"]} - {
                    str(x).strip().upper() for x in current["Ticker"]}
                with st.spinner("Checking the price feed..."):
                    trouble = holdings_edit.unpriced(clean, market, only=fresh)
                for p in trouble:
                    if p.unknown:
                        problems.append(
                            f"{p.ticker} is not a symbol the price feed "
                            f"knows: {p.reason} The tool would otherwise drop "
                            "the holding without telling you.")
                    else:
                        flaky.append(p)
            if problems:
                st.markdown(
                    brand.card("Not saved",
                               "<br>".join(escape(p) for p in problems), "bad"),
                    unsafe_allow_html=True)
            else:
                portfolio_store.save_holdings(record, clean)
                note = ""
                if flaky:
                    note = (" The price feed would not answer for "
                            + "; ".join(f"{p.ticker} ({p.reason})"
                                        for p in flaky)
                            + ". Press Refresh prices in a moment to value "
                            + ("them." if len(flaky) != 1 else "it."))
                recompute(f"Saved {len(clean)} holdings. Every figure has been "
                          "recomputed." + note)

        if actions[1].button("Discard", disabled=not pending):
            reload_grid()
            st.rerun()

        if record.edited and actions[2].button("Revert to upload"):
            portfolio_store.clear_edit(record.id)
            recompute("Back to the file as uploaded. Every edit made in the "
                      "app has been discarded.")

        st.markdown(
            brand.note(
                "What this sheet is, and what it is not",
                "Each row is a position you <b>hold</b>: a quantity, what it "
                "cost on average, and the date the position was opened. "
                "Buying more re-weights the average cost and keeps the "
                "original date, because that is what every since-inception "
                "figure is measured from. <b>Selling in full removes the "
                "row</b>, and the record of what that holding earned goes with "
                "it &mdash; so the returns you see are always the returns on "
                "what is still owned. Keeping realised positions would need a "
                "trade ledger rather than a position sheet, which is a "
                "different file and a larger change."),
            unsafe_allow_html=True)

        st.markdown(
            f'<div style="font-size:.72rem;color:{brand.MUTED};margin-top:.4rem;">'
            f'Reading <code>{escape(str(record.workbook))}</code>'
            + (f'{DOT}original at <code>{escape(str(record.source_workbook))}</code>'
               if record.edited else "")
            + '</div>', unsafe_allow_html=True)

elif view == "Import portfolio":
    section("Import a portfolio",
            "Excel and CSV are read directly. PDF and PowerPoint are "
            "transcribed onto the same schema. An uploaded book is added to "
            "the list and opened; switch between books in the rail.")

    _upload_portfolio_form(user.email)

    st.markdown(
        brand.note(
            "Transcription, not judgement",
            "The model reads what the document states; it never invents a "
            "holding, a price or a date. The uploaded file is kept alongside "
            "the converted workbook, so every figure stays traceable. All "
            "returns are computed by the engine.",
        ),
        unsafe_allow_html=True,
    )

    st.markdown(brand.section("Portfolios on file"), unsafe_allow_html=True)
    st.markdown(
        brand.table(
            [("Portfolio", "ps-name"), ("Source", ""), ("Format", ""),
             ("Rows", "ps-num"), ("Added", ""), ("Read by", "ps-tag")],
            [[r.name
              + (" " + brand.pill("open", "") if r.id == record.id else "")
              + (" " + brand.pill("sample", "navy") if r.is_demo else ""),
              f'<span class="ps-muted">{r.source_filename}</span>',
              r.source_format, str(r.rows) if r.rows else DASH,
              r.uploaded_label or DASH,
              brand.pill("model", "warn") if r.used_model
              else brand.pill("direct", "navy")]
             for r in records],
        ),
        unsafe_allow_html=True,
    )

    removable = [r for r in records if not r.is_demo]
    if removable:
        st.write("")
        with st.expander("Remove a portfolio"):
            target = st.selectbox(
                "Portfolio to remove", [r.id for r in removable],
                format_func=lambda rid: next(r.name for r in removable
                                             if r.id == rid))
            st.caption("Deletes the uploaded file and the converted workbook. "
                       "Cannot be undone.")
            if st.button("Remove permanently"):
                if portfolio_store.remove(target, user.email):
                    st.cache_resource.clear()
                    # Removing the open book would otherwise leave every screen
                    # pointed at a workbook that no longer exists.
                    if st.session_state.get("portfolio_id") == target:
                        st.session_state.pop("portfolio_id", None)
                        st.query_params.pop(BOOK_PARAM, None)
                    st.rerun()

    st.markdown(
        f'<div style="font-size:.72rem;color:{brand.MUTED};margin-top:1rem;">'
        f'Uploads and caches are stored at <code>{describe_data_root()}</code>'
        f'</div>', unsafe_allow_html=True)

# ----------------------------------------------------------------- footer --

st.markdown(
    f'<div class="ps-footer"><div>Benchmark Pulse{DOT}Preferred Square{DOT}'
    f'{brand.TAGLINE}</div></div>',
    unsafe_allow_html=True,
)
