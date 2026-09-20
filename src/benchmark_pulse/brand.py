"""Preferred Square design system.

Palette from the firm's brand swatches; typography from the live site, which
loads exactly Manrope and Work Sans.

The look is modelled on professional investment software rather than on a
dashboard template, and four decisions follow from that:

  The interface does not explain itself.  An analyst does not need a paragraph
  under each heading saying what a benchmark is. Prose in the chrome is the
  clearest tell of a tool built for a demo rather than for daily use, so
  headings are bare and the explanation lives in the memo.

  Rules, not cards.  Boxes with radii and shadows fragment a page into objects.
  Hairline rules separate without adding weight, which is why every terminal
  from Bloomberg down uses them.

  Numbers are the interface.  Tabular figures, right-aligned, tight leading,
  and a large size ratio between a figure and its label so the figure reads
  first.

  Colour carries meaning only.  The page is navy, grey and white. Teal marks a
  beat, magenta a miss, and nothing else is coloured. A page where six colours
  compete is decoration; a page where two colours mean something is an
  instrument.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

from .paths import DATA_ROOT

# -- Brand palette ----------------------------------------------------------

NAVY_DEEP = "#13294B"
NAVY = "#14304F"
NAVY_SOFT = "#1D3E63"

TEAL_DARK = "#27706E"
TEAL = "#2BB9A8"
TEAL_LIGHT = "#6FDCCB"

MAGENTA = "#E8187C"
CORAL = "#F58BA0"
YELLOW_PALE = "#FAF0A5"
BLUE_PALE = "#C9DCF0"

CYAN = TEAL
MINT = TEAL_LIGHT
NAVY_MID = NAVY_SOFT

INK = "#13294B"
MUTED = "#75839A"
HAIRLINE = "#E3E8EF"
CANVAS = "#FFFFFF"
CANVAS_ALT = "#FAFBFD"

POSITIVE = "#1E9E8C"
POSITIVE_TEXT = "#1B7A6C"
NEGATIVE = "#E8187C"
NEGATIVE_TEXT = "#C0135F"
NEUTRAL = MUTED
WARN = "#B8801A"

DARK_CANVAS = "#0C1A2E"
DARK_SURFACE = "#14263E"
DARK_TEXT = "#E4EBF5"
DARK_MUTED = "#8A9BB3"

# A deliberate exception to "colour carries meaning only" above: a sector or
# market breakdown has no beat/miss to report, only identity, and teal-only
# shading of same-size slices is not something a reader can actually tell
# apart. The firm's own four-colour swatch, used as-is at the owner's request
# even though two of the four -- the light grey and the pale mint -- read
# close to identical to each other (normal-vision contrast 4.7, versus the 15
# a categorical pair needs) and close to invisible against a light surface
# (contrast ~1.1:1, versus the 3:1 a fill needs). `marker.line` on every slice
# in `categorical_slot`'s caller carries the weight those two colours can't:
# a strong outline is what makes a near-white slice read as a shape at all.
CHART_CATEGORICAL = {
    "light": ["#44546A", "#E7E6E6", "#D0F7F0", "#29BDAD"],
    "dark": ["#44546A", "#E7E6E6", "#D0F7F0", "#29BDAD"],
}

#: Where a category falls outside the four identities above -- the fifth
#: sector, the fifth market. A shared, deliberately unsaturated bucket rather
#: than a generated fifth hue, which would be indistinguishable from an
#: existing one under colour-blindness.
CHART_OTHER = {"light": "#C7CCD6", "dark": "#3A4A63"}


def categorical_slot(key: str, universe: list[str], dark: bool = False) -> str:
    """A colour for ``key`` that depends only on its identity, never on what
    else is on screen.

    Position comes from ``universe`` -- the full set of possible values, in a
    fixed order -- not from the category's rank in the current chart. Rank-based
    colour looks fine until someone filters: the moment a category drops out,
    every colour after it shifts, and a reader who learned "Financials is
    orange" is misled. A key past the fourth slot, or one ``universe`` does not
    recognise, gets the shared "other" bucket rather than a new hue.
    """
    palette = CHART_CATEGORICAL["dark" if dark else "light"]
    try:
        i = universe.index(key)
    except ValueError:
        i = len(palette)
    return palette[i] if i < len(palette) else CHART_OTHER["dark" if dark else "light"]


def _linear_channel(value: float) -> float:
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_color: str) -> float:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return (0.2126 * _linear_channel(r) + 0.7152 * _linear_channel(g)
            + 0.0722 * _linear_channel(b))


def _contrast(hex_a: str, hex_b: str) -> float:
    la, lb = _relative_luminance(hex_a), _relative_luminance(hex_b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def slice_text_color(fill_hex: str) -> str:
    """White or the brand's own ink, whichever actually reads on ``fill_hex``.

    A fixed "always white" label -- the usual choice for a mark's inside text
    -- goes unreadable on the pale grey and mint slots above (contrast ~1.2:1).
    Computed per fill rather than assumed, so a future palette swap can't
    silently reintroduce invisible labels.
    """
    return "#FFFFFF" if _contrast(fill_hex, "#FFFFFF") >= _contrast(fill_hex, INK) else INK


# -- Typography -------------------------------------------------------------

# Weight RANGES, not a list of weights. A list makes Google return one static
# instance per weight -- eight files, 394 KB once inlined. A range returns the
# variable font, which is one file per family covering every weight in between,
# and cuts that to about a quarter.
FONT_IMPORT = (
    "https://fonts.googleapis.com/css2?"
    "family=Manrope:wght@500..800&family=Work+Sans:wght@400..700"
    "&display=swap"
)
FONT_DISPLAY = "'Manrope', 'Segoe UI', system-ui, sans-serif"
FONT_BODY = "'Work Sans', 'Segoe UI', system-ui, sans-serif"
FONT_MONO = "'SF Mono', ui-monospace, 'Cascadia Mono', Consolas, monospace"

TAGLINE = "Every Decision Better Informed"

LOGO_PATH = Path(__file__).resolve().parents[2] / "app" / "assets" / "ps-logo-white.png"

# -- Typefaces, held locally ------------------------------------------------
#
# Loading the brand faces from Google means the look of the tool depends on an
# outbound request succeeding. It fails in exactly the situations that matter:
# the demo runs with Offline mode on, and the firm's network already blocks
# FRED, so there is every reason to think it may block fonts.googleapis.com too.
#
# The failure is quiet, which is what makes it worth removing. Nothing errors --
# the page simply renders in Segoe UI, whose metrics differ enough to move the
# alignment that was measured against Manrope.
#
# So the faces are cached once into the writable data root, the same place the
# price cache lives, and served as data URIs. build_font_cache() is the one-off
# that fills it; if the cache is missing the @import is used as before, so a
# fresh clone still looks right on a machine with a network.

FONT_CACHE = DATA_ROOT / "fonts.css"


def font_faces() -> str:
    """The cached @font-face block, or "" if it has not been built."""
    try:
        return FONT_CACHE.read_text(encoding="utf-8")
    except OSError:
        return ""


def build_font_cache(timeout: int = 30) -> Path:
    """Fetch the brand faces once and inline them as data URIs.

    Run from a machine that can reach Google Fonts; after that the app needs no
    network for typography. Returns the path written.
    """
    import urllib.request

    # A modern browser UA, or Google serves the ttf stylesheet instead of woff2.
    headers = {"User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")}

    request = urllib.request.Request(FONT_IMPORT, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        sheet = response.read().decode("utf-8")

    # Latin only. The full response carries Cyrillic, Greek and Vietnamese
    # subsets this tool never renders, and latin-ext only adds accented forms
    # the app cannot produce -- it is ASCII-only by construction. Keeping just
    # latin took the cache from 662 KB to roughly half that, and the block is
    # re-sent on every rerun, so the size is worth caring about.
    blocks = re.split(r"(?=/\*)", sheet)
    kept = []
    for block in blocks:
        if "@font-face" not in block:
            continue
        if "/* latin */" not in block:
            continue
        match = re.search(r"url\((https://[^)]+\.woff2)\)", block)
        if not match:
            continue
        url = match.group(1)
        with urllib.request.urlopen(
                urllib.request.Request(url, headers=headers),
                timeout=timeout) as font:
            payload = base64.b64encode(font.read()).decode("ascii")
        kept.append(block.replace(
            url, f"data:font/woff2;base64,{payload}").strip())

    if not kept:
        raise RuntimeError("no woff2 faces found in the Google Fonts response")

    FONT_CACHE.parent.mkdir(parents=True, exist_ok=True)
    FONT_CACHE.write_text("\n".join(kept), encoding="utf-8")
    return FONT_CACHE


def css(dark: bool = False) -> str:
    canvas = DARK_CANVAS if dark else CANVAS
    surface = DARK_SURFACE if dark else CANVAS_ALT
    text = DARK_TEXT if dark else INK
    muted = DARK_MUTED if dark else MUTED
    hairline = "rgba(255,255,255,.10)" if dark else HAIRLINE
    heading = DARK_TEXT if dark else NAVY_DEEP
    up = TEAL_LIGHT if dark else POSITIVE_TEXT
    down = CORAL if dark else NEGATIVE_TEXT

    # Cached faces where they exist, the remote sheet where they do not.
    faces = font_faces()
    font_block = faces if faces else f"@import url('{FONT_IMPORT}');"

    return f"""
<style>
{font_block}

:root {{
    --ps-canvas: {canvas};
    --ps-surface: {surface};
    --ps-text: {text};
    --ps-heading: {heading};
    --ps-muted: {muted};
    --ps-hairline: {hairline};
    --ps-teal: {TEAL};
    --ps-magenta: {MAGENTA};
    --ps-up: {up};
    --ps-down: {down};
    --ps-r: 3px;
}}

html, body, .stApp, [class*="css"] {{
    font-family: {FONT_BODY};
    font-feature-settings: "tnum" 1;
    -webkit-font-smoothing: antialiased;
}}
.stApp {{ background: var(--ps-canvas); color: var(--ps-text); }}
.stApp p, .stApp li, .stApp span, .stApp label,
[data-testid="stMarkdownContainer"] {{ color: var(--ps-text); }}
/* Same problem one level down: Streamlit sets its own family on the markdown
   container, which outranks the rule on .stApp, so every custom table inherited
   Source Sans. Set on the container only -- elements that name a family
   (headings, figures, the mono meta line) still win for themselves. */
[data-testid="stMarkdownContainer"] {{ font-family: {FONT_BODY}; }}
[data-testid="stHeader"] {{ background: transparent; height: 0; }}

.stApp h1 span, .stApp h2 span, .stApp h3 span, .stApp h4 span {{
    color: inherit !important;
}}
.stApp h1 a, .stApp h2 a, .stApp h3 a, .stApp h4 a {{ display: none !important; }}
/* Scoped under .stApp as well as bare, because Streamlit's own heading rule is
   more specific than an element selector and was quietly winning: headings
   rendered in Source Sans while anything naming the family explicitly got
   Manrope, so the page ran two typefaces that look similar enough to miss. */
.stApp h1, .stApp h2, .stApp h3, .stApp h4,
h1, h2, h3, h4 {{
    font-family: {FONT_DISPLAY} !important;
    letter-spacing: -.02em; color: var(--ps-heading);
}}

.block-container {{
    padding: 1.6rem 2.2rem 4rem 2.2rem !important; max-width: 1640px;
}}
[data-testid="stVerticalBlock"] {{ gap: .55rem; }}
hr {{ border-color: var(--ps-hairline); margin: 1rem 0; }}

/* ---------------------------------------------------------------- sidebar */
/* The firm's mark is white on transparent, so the rail stays navy in both
   themes -- on a white ground it would disappear. */
section[data-testid="stSidebar"] {{
    background: {NAVY_DEEP};
    border-right: 1px solid rgba(255,255,255,.06);
    width: 232px !important;
}}
section[data-testid="stSidebar"] > div {{ padding-top: 0; }}
[data-testid="stSidebarHeader"] {{
    position: absolute !important; top: 0; right: 0; left: auto;
    width: auto; height: auto !important; min-height: 0 !important;
    padding: .3rem .35rem 0 0 !important; margin: 0 !important;
    z-index: 6; pointer-events: none;
}}
[data-testid="stSidebarHeader"] button {{ pointer-events: auto; }}
[data-testid="stLogoSpacer"] {{ display: none !important; }}
/* ---- One left edge -------------------------------------------------------
   Everything in the rail aligns to a single line. Streamlit gives each widget
   its own padding, so without this the logo, the group labels, the nav text
   and the toggles each start at a different indent and the edge reads ragged.
   The gutter is declared once and every element is pulled back to it. */
section[data-testid="stSidebar"] {{ --ps-gutter: 1rem; }}
/* Streamlit puts its own padding on the content wrapper, which stacked on top
   of the gutter below to give a 34px inset on a 232px rail -- a seventh of the
   width gone, and the cause of the empty band down the left. Zeroed so the
   gutter is declared in one place. */
[data-testid="stSidebarContent"] {{
    padding-left: 0 !important; padding-right: 0 !important;
}}
[data-testid="stSidebarUserContent"] {{
    padding: .75rem var(--ps-gutter) 1.4rem var(--ps-gutter) !important;
}}
section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {{ gap: .3rem; }}
section[data-testid="stSidebar"] [data-testid="stElementContainer"] {{
    margin: 0 !important;
}}
/* Streamlit trims the trailing paragraph margin of ordinary markdown with
   margin-bottom:-1rem on the container. Our rail blocks are raw divs carrying
   no such margin, so nothing cancels it: the container measured 16px shorter
   than its own contents and the next element rode up into it, which is what
   laid the Sign out button across the signed-in name. */
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] {{
    margin-bottom: 0 !important;
}}
/* A button after a rail block is a separate action, not part of it. */
section[data-testid="stSidebar"] .stButton {{ margin-top: .55rem !important; }}
/* Buttons keep the light theme's near-white fill on the navy rail while the
   rail's own rule paints their label #D5DEEC -- about 1.3:1, which is why they
   read as blank white boxes. Made rail-native instead. */
section[data-testid="stSidebar"] .stButton button {{
    background: rgba(255,255,255,.07) !important;
    border: 1px solid rgba(255,255,255,.16) !important;
    color: #E8EEF7 !important;
}}
section[data-testid="stSidebar"] .stButton button p {{
    color: #E8EEF7 !important; font-weight: 600;
}}
section[data-testid="stSidebar"] .stButton button:hover {{
    background: rgba(255,255,255,.13) !important;
    border-color: {TEAL} !important;
}}
section[data-testid="stSidebar"] .stButton button:hover p {{
    color: #FFF !important;
}}
/* Inputs in the rail keep the light theme's white fill while the rail's blanket
   rule paints their text #D5DEEC -- about 1.3:1, so the selected portfolio was
   barely readable. Same defect as the buttons had. The fill is white, so the
   text and the chevron are navy. */
section[data-testid="stSidebar"] [data-baseweb="select"],
section[data-testid="stSidebar"] [data-baseweb="select"] *,
section[data-testid="stSidebar"] [data-testid="stSelectbox"] input,
section[data-testid="stSidebar"] .stTextInput input {{
    color: {NAVY_DEEP} !important;
}}
section[data-testid="stSidebar"] [data-baseweb="select"] input::placeholder {{
    color: {NAVY_DEEP} !important; opacity: 1;
}}
section[data-testid="stSidebar"] [data-baseweb="select"] svg,
section[data-testid="stSidebar"] [data-testid="stSelectbox"] svg {{
    fill: {NAVY_DEEP} !important;
}}
section[data-testid="stSidebar"] [data-testid="stSidebarNav"] {{ display: none; }}
section[data-testid="stSidebar"] * {{ color: #D5DEEC; }}
section[data-testid="stSidebar"] hr {{
    border-color: rgba(255,255,255,.09); margin: .85rem 0 .7rem 0;
}}
section[data-testid="stSidebar"] label[data-testid="stWidgetLabel"] {{
    margin-bottom: .15rem;
}}
section[data-testid="stSidebar"] label[data-testid="stWidgetLabel"] p {{
    color: rgba(213,222,236,.78) !important;
    font-size: .78rem !important; font-weight: 500 !important;
    text-transform: none !important; letter-spacing: 0 !important;
}}
section[data-testid="stSidebar"] input {{
    background: rgba(255,255,255,.05) !important;
    color: #D5DEEC !important;
    border: 1px solid rgba(255,255,255,.12) !important;
    font-size: .78rem !important; border-radius: var(--ps-r) !important;
}}
section[data-testid="stSidebar"] svg {{ fill: rgba(213,222,236,.55); }}

/* Toggle rows: the control and its label on the same baseline, flush left. */
section[data-testid="stSidebar"] [data-testid="stCheckbox"] label {{
    align-items: center; gap: .5rem; padding: .18rem 0;
}}
section[data-testid="stSidebar"] [data-testid="stCheckbox"]
    [data-testid="stWidgetLabel"] {{ margin-bottom: 0; }}

/* Group headings. The rule beneath separates groups without a margin big
   enough to leave the rail looking gappy. */
/* Group heading. display:block with a defined line-height so the element
   cannot collapse; a collapsed heading lets the next block ride up through
   its rule. */
.ps-rail-label {{
    display: block; line-height: 1.2;
    font-size: .64rem; font-weight: 700; letter-spacing: .02em;
    color: rgba(213,222,236,.42);
    margin: 1.05rem 0 .5rem 0; padding-bottom: .32rem;
    border-bottom: 1px solid rgba(255,255,255,.07);
}}
.ps-rail-label:first-child {{ margin-top: .7rem; }}

/* Navigation: a radio group restyled as rows. Selectors read from the DOM --
   the dot sits three levels down, and the active state is an attribute. */
section[data-testid="stSidebar"] div[role="radiogroup"] {{ gap: 0; }}
/* The row's tint runs wider than the gutter while its text stays on the common
   left edge: the negative margin pushes the background out, the matching
   padding brings the label back. Indenting the text instead is what made the
   nav look inset from everything above it. */
section[data-testid="stSidebar"] label[data-testid="stRadioOption"] {{
    width: calc(100% + .9rem); margin: 0 -.45rem;
    padding: .33rem .45rem; border-radius: var(--ps-r);
    cursor: pointer; transition: background .1s ease;
    box-shadow: inset 2px 0 0 transparent;
}}
section[data-testid="stSidebar"] label[data-testid="stRadioOption"]
    > div > div > div:first-child {{ display: none; }}
section[data-testid="stSidebar"] label[data-testid="stRadioOption"]
    div[data-testid="stMarkdownContainer"] p {{
    color: rgba(213,222,236,.66) !important;
    font-size: .815rem !important; font-weight: 500 !important;
    text-transform: none !important; letter-spacing: 0 !important; margin: 0 !important;
}}
section[data-testid="stSidebar"] label[data-testid="stRadioOption"]:hover {{
    background: rgba(255,255,255,.05);
}}
section[data-testid="stSidebar"] label[data-testid="stRadioOption"][data-selected="true"] {{
    background: rgba(255,255,255,.07); box-shadow: inset 2px 0 0 var(--ps-teal);
}}
section[data-testid="stSidebar"] label[data-testid="stRadioOption"][data-selected="true"]
    div[data-testid="stMarkdownContainer"] p {{
    color: #FFFFFF !important; font-weight: 600 !important;
}}

/* Toggles. Streamlit's default track is a dark grey at 20% opacity, which is
   invisible on navy; lifted here and teal when on. */
section[data-testid="stSidebar"] [data-testid="stCheckbox"] label > div:not([data-testid]) {{
    background: rgba(255,255,255,.18) !important;
}}
section[data-testid="stSidebar"] [data-testid="stCheckbox"] label:has(input:checked)
    > div:not([data-testid]) {{ background: var(--ps-teal) !important; }}
section[data-testid="stSidebar"] [data-testid="stCheckbox"] label > div:not([data-testid]) > div {{
    background: #FFFFFF !important;
}}

.ps-sb-stat {{
    display: flex; justify-content: space-between; align-items: baseline;
    gap: .5rem; padding: .26rem 0;
    border-bottom: 1px solid rgba(255,255,255,.06);
}}
.ps-sb-stat:last-of-type {{ border-bottom: none; }}
.ps-sb-stat .k {{
    font-size: .7rem; color: rgba(213,222,236,.5); line-height: 1.3;
}}
.ps-sb-stat .v {{
    font-family: {FONT_DISPLAY}; font-weight: 700; font-size: .9rem;
    color: #FFF; white-space: nowrap;
}}
.ps-sb-stat .v.up {{ color: {TEAL_LIGHT}; }}
.ps-sb-stat .v.down {{ color: {CORAL}; }}

/* Signed-in user, on the same left edge as everything above it. min-height
   matches the avatar so the row reserves its own space rather than collapsing
   to the text's line box. */
.ps-sb-user {{
    display: flex; align-items: center; gap: .55rem;
    padding: .15rem 0; min-height: 30px;
}}
.ps-sb-user .av {{
    width: 26px; height: 26px; flex: 0 0 26px; border-radius: 50%;
    background: {TEAL}; color: {NAVY_DEEP}; display: flex;
    align-items: center; justify-content: center;
    font-weight: 800; font-size: .66rem;
}}
.ps-sb-user .who {{ min-width: 0; line-height: 1.25; }}
.ps-sb-user .nm {{
    font-size: .76rem; font-weight: 600; color: #FFF;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
.ps-sb-user .rl {{
    font-size: .66rem; color: rgba(213,222,236,.46);
    letter-spacing: .01em;
}}
.ps-sb-engine {{ font-size: .68rem; line-height: 1.45; word-break: break-all; }}

/* ------------------------------------------------------------- title bar */
.ps-bar {{
    display: flex; align-items: baseline; justify-content: space-between;
    gap: 1rem; flex-wrap: wrap;
    padding-bottom: .5rem; border-bottom: 1.5px solid var(--ps-heading);
    margin-bottom: 0;
}}
.ps-bar h1 {{
    font-size: 1.32rem; font-weight: 800; margin: 0; letter-spacing: -.015em;
    color: var(--ps-heading); text-transform: none;
}}
.ps-bar .meta {{
    font-size: .74rem; color: var(--ps-muted); font-family: {FONT_MONO};
    letter-spacing: -.01em;
}}
.ps-bar .meta b {{ color: var(--ps-heading); font-weight: 600; }}
.ps-tag-demo {{
    font-size: .58rem; font-weight: 700; letter-spacing: .13em;
    text-transform: uppercase; color: var(--ps-muted);
    border: 1px solid var(--ps-hairline); border-radius: 2px;
    padding: .08rem .35rem; margin-left: .5rem; vertical-align: middle;
}}

/* Data strip: figures divided by hairlines, no boxes. */
.ps-strip {{
    display: flex; flex-wrap: wrap;
    border-bottom: 1px solid var(--ps-hairline); margin-bottom: 1.1rem;
}}
.ps-strip .cell {{
    flex: 1 1 140px; padding: .6rem 1.1rem .65rem 0;
    border-right: 1px solid var(--ps-hairline);
}}
.ps-strip .cell:last-child {{ border-right: none; }}
.ps-strip .cell + .cell {{ padding-left: 1.1rem; }}
.ps-strip .k {{
    font-size: .68rem; font-weight: 700; letter-spacing: .01em;
    color: var(--ps-muted); white-space: nowrap;
}}
.ps-strip .v {{
    font-family: {FONT_DISPLAY}; font-weight: 800; font-size: 1.28rem;
    line-height: 1.25; margin-top: .1rem; color: var(--ps-heading);
    font-variant-numeric: tabular-nums;
}}
.ps-strip .v.up {{ color: var(--ps-up); }}
.ps-strip .v.down {{ color: var(--ps-down); }}
.ps-strip .n {{
    font-size: .68rem; color: var(--ps-muted); margin-top: .05rem;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
/* A note that carries a verdict rather than a caption. Slightly heavier than
   the muted default, because it is now saying something rather than labelling
   the figure above it. */
.ps-strip .n.up {{ color: var(--ps-up); font-weight: 600; }}
.ps-strip .n.down {{ color: var(--ps-down); font-weight: 600; }}

/* --------------------------------------------------------------- section */
.ps-section {{
    font-family: {FONT_DISPLAY}; font-weight: 700; font-size: .86rem;
    letter-spacing: 0; color: var(--ps-muted);
    margin: 1.5rem 0 .5rem 0; padding-bottom: .3rem;
    border-bottom: 1px solid var(--ps-hairline);
}}
.ps-section-sub {{
    color: var(--ps-muted); font-size: .78rem; line-height: 1.5;
    margin: -.2rem 0 .6rem 0; max-width: 84ch;
}}

/* ------------------------------------------------------------------ tabs */
.stTabs [data-baseweb="tab-list"] {{
    gap: 0; border-bottom: 1px solid var(--ps-hairline);
}}
.stTabs [data-baseweb="tab"] {{
    font-weight: 600; font-size: .8rem; color: var(--ps-muted);
    padding: .5rem .9rem;
}}
.stTabs [aria-selected="true"] {{ color: var(--ps-heading) !important; }}
.stTabs [data-baseweb="tab-highlight"] {{ background: var(--ps-teal); height: 2px; }}

/* ---------------------------------------------------------------- tables */
/* Rendered as HTML: Streamlit's grid draws to a canvas, so neither brand
   colour, a theme change, nor an inline bar can reach inside it. */
.ps-tablewrap {{ overflow-x: auto; margin-bottom: .3rem; }}
/* Streamlit puts margin-bottom:-1rem on every markdown container to swallow a
   trailing paragraph's margin. A table has no such margin, so nothing cancels
   it: the element box measured 16px shorter than the table's own ink and the
   next block rode up underneath it. Scoped with :has() so ordinary prose keeps
   the spacing Streamlit intends. */
[data-testid="stMarkdownContainer"]:has(> .ps-tablewrap) {{
    margin-bottom: 0 !important;
}}

/* A heading above a chart. Plotly's own title is drawn inside the SVG's top
   margin and overflows the container when that margin is smaller than the
   type -- measured 9px above the chart's own box, straight into the table
   above it. A real element cannot escape the layout it is in. */
.ps-chart-label {{
    font-family: {FONT_DISPLAY}; font-weight: 700; font-size: .72rem;
    letter-spacing: .01em; color: var(--ps-muted);
    margin: .3rem 0 .15rem 0;
}}
.ps-table {{
    width: 100%; border-collapse: collapse; font-size: .785rem;
    line-height: 1.35;
}}
.ps-table thead th {{
    position: sticky; top: 0; z-index: 2; background: var(--ps-canvas);
    text-align: left; font-weight: 700; font-size: .68rem;
    letter-spacing: .01em; color: var(--ps-muted);
    padding: .3rem .7rem .4rem .7rem; white-space: nowrap;
    border-bottom: 1.5px solid var(--ps-heading);
}}
.ps-table tbody td {{
    padding: .34rem .7rem; border-bottom: 1px solid var(--ps-hairline);
    color: var(--ps-text); vertical-align: middle; white-space: nowrap;
}}
.ps-table tbody tr:hover td {{ background: var(--ps-surface); }}
.ps-table .ps-num, .ps-table .ps-alpha {{
    text-align: right; font-variant-numeric: tabular-nums;
}}
.ps-table .ps-name {{ font-weight: 600; color: var(--ps-heading); }}
.ps-table .ps-alpha {{ font-weight: 700; }}
.ps-table .ps-muted {{ color: var(--ps-muted); }}
/* The second reading of a return, where the holding and its benchmark are
   quoted in different currencies. Set below the figure and smaller: it is the
   same return seen another way, not a competing number. */
.ps-table .ps-fx {{
    font-size: .64rem; color: var(--ps-muted); font-weight: 400;
    line-height: 1.3; margin-top: .05rem; white-space: nowrap;
}}
.ps-table .ps-wrap {{ white-space: normal; }}
.ps-up {{ color: var(--ps-up); }}
.ps-down {{ color: var(--ps-down); }}

/* Banded cells: the tint IS the verdict.
   A column of signed percentages has to be read a figure at a time. Tinted to
   the same five bands the status column uses, the shape of a book is visible
   before any number is: a row of green with one red cell finds the holding
   worth talking about without reading ten numbers.
   Kept pale on purpose. These sit behind figures that must stay the thing you
   read, and a saturated fill turns a report into a spreadsheet. The text
   carries the darker tone so the meaning survives if the background does not
   print, and so it is not colour alone doing the work. */
.ps-table td.ps-band {{
    text-align: right; font-variant-numeric: tabular-nums; font-weight: 700;
}}
.ps-b-strong-up {{ background: rgba(43,185,168,.22); color: {TEAL_DARK}; }}
.ps-b-up       {{ background: rgba(43,185,168,.11); color: {TEAL_DARK}; }}
.ps-b-flat     {{ background: rgba(184,128,26,.10); color: {WARN}; }}
.ps-b-down     {{ background: rgba(232,24,124,.10); color: var(--ps-down); }}
.ps-b-strong-down {{ background: rgba(232,24,124,.20); color: var(--ps-down); }}
.ps-b-none     {{ color: var(--ps-muted); font-weight: 400; }}

/* Diverging bar: turns a column of percentages into a shape readable at a
   glance without reading any single number. */
.ps-bar-cell {{
    position: relative; display: inline-block; width: 44px; height: 10px;
    vertical-align: middle; margin-left: .45rem;
}}
.ps-bar-cell::before {{
    content: ''; position: absolute; left: 50%; top: 0; bottom: 0;
    width: 1px; background: var(--ps-hairline);
}}
.ps-bar-cell i {{ position: absolute; top: 3px; height: 4px; }}
.ps-bar-cell i.pos {{ left: 50%; background: var(--ps-teal); }}
.ps-bar-cell i.neg {{ right: 50%; background: var(--ps-magenta); }}

.ps-spark {{ display: block; opacity: .85; }}
.ps-table tbody tr:hover .ps-spark {{ opacity: 1; }}

/* A weight bar behind the figure, so position size reads as a shape down the
   column without spending a column on it. */
.ps-weight {{ position: relative; display: block; padding: .05rem .1rem; }}
.ps-weight span {{ position: relative; z-index: 1; }}
.ps-weight i {{
    position: absolute; right: 0; top: 2px; bottom: 2px;
    background: rgba(19,41,75,.09); border-radius: 1px;
}}

/* ------------------------------------------------------------- side note */
/* Replaces the card. A rule and indented text, which is how a printed note
   appears in a research document. */
.ps-note {{
    border-left: 2px solid var(--ps-hairline);
    padding: .1rem 0 .1rem .75rem; margin: .5rem 0 .9rem 0;
}}
.ps-note.accent {{ border-left-color: var(--ps-teal); }}
.ps-note.warn {{ border-left-color: {WARN}; }}
.ps-note.bad {{ border-left-color: var(--ps-magenta); }}
.ps-note .t {{
    font-size: .72rem; font-weight: 700; letter-spacing: .04em;
    color: var(--ps-heading); margin-bottom: .1rem;
}}
.ps-note .b {{ font-size: .78rem; color: var(--ps-muted); line-height: 1.55; }}

/* ---------------------------------------------------- abbreviation marker */
/* A small circled i beside a code, carrying the full form on hover.
   The tooltip is the browser's own `title` rather than a styled pseudo-element
   on purpose: the table scrolls inside an overflow container, and a CSS
   tooltip positioned inside one is clipped at its edge. A native tooltip is
   plainer but it always appears, which matters more on a screen someone is
   presenting from. */
.ps-abbr {{ white-space: nowrap; cursor: help; }}
.ps-abbr > i {{
    display: inline-flex; align-items: center; justify-content: center;
    width: 11px; height: 11px; margin-left: .22rem;
    border: 1px solid var(--ps-hairline); border-radius: 50%;
    font-family: {FONT_BODY}; font-style: normal; font-weight: 700;
    font-size: .52rem; line-height: 1; color: var(--ps-muted);
    vertical-align: .08em; transition: border-color .1s, color .1s;
}}
.ps-abbr:hover > i {{ border-color: var(--ps-teal); color: var(--ps-teal); }}
.ps-table thead th .ps-abbr > i {{ width: 10px; height: 10px; }}

.ps-pill {{
    display: inline-block; padding: .05rem .38rem; border-radius: 2px;
    font-size: .6rem; font-weight: 700; letter-spacing: .07em;
    text-transform: uppercase; margin-right: .25rem;
    border: 1px solid var(--ps-hairline); color: var(--ps-muted);
}}
.ps-pill.ps-pill-navy {{ color: var(--ps-heading); }}
.ps-pill.ps-pill-warn {{ color: {WARN}; border-color: {WARN}44; }}
.ps-pill.ps-pill-bad {{ color: var(--ps-down); border-color: {MAGENTA}44; }}

/* -------------------------------------------------------------- controls */
div[data-testid="stMetric"] {{
    background: transparent; border: none; border-right: 1px solid var(--ps-hairline);
    border-radius: 0; padding: .1rem 1rem .1rem 0;
}}
div[data-testid="stMetricValue"] {{
    font-family: {FONT_DISPLAY}; font-weight: 800; color: var(--ps-heading);
    font-size: 1.28rem;
}}
div[data-testid="stMetricLabel"] * {{
    text-transform: uppercase; letter-spacing: .14em;
    font-size: .58rem; color: var(--ps-muted); font-weight: 700;
}}
.stButton button {{
    font-weight: 600; border-radius: var(--ps-r); font-size: .8rem;
    border: 1px solid var(--ps-hairline); padding: .3rem .9rem;
}}
.stButton button[kind="primary"] {{
    background: var(--ps-heading); border-color: var(--ps-heading); color: #FFF;
}}
/* Streamlit wraps a button's label in a <p>, which then takes the page's
   paragraph colour -- navy -- and lands navy-on-navy: contrast 1.0, so the
   primary button rendered as a blank rectangle. "Sign off" is the button the
   demo actually presses, and it was invisible. */
.stButton button[kind="primary"] p,
.stButton button[kind="primary"] span,
.stButton button[kind="primary"] div {{ color: #FFF !important; }}
.stButton button[kind="primary"]:hover {{
    background: {NAVY_SOFT}; border-color: {NAVY_SOFT};
}}
.stButton button:hover {{ border-color: var(--ps-teal); }}
/* A disabled button has to look disabled. The primary rule above forces its
   label white with !important, which outranks Streamlit's own greying -- so a
   disabled Save rendered as a solid navy button with white text, identical to
   a live one. Pressing it did nothing, which reads as the tool being stuck
   rather than as there being nothing to save.

   Every selector here is a descendant, not a child. Streamlit wraps any button
   carrying a `help` tooltip in a .stTooltipHoverTarget, and `.stButton > button`
   then matches nothing: the button loses its brand styling entirely and the
   navy-on-navy invisibility above comes straight back. */
.stButton button:disabled,
.stButton button:disabled:hover {{
    background: var(--ps-surface) !important;
    border-color: var(--ps-hairline) !important;
    cursor: not-allowed !important;
    opacity: 1 !important;
}}
.stButton button:disabled p,
.stButton button:disabled span,
.stButton button:disabled div {{
    color: var(--ps-muted) !important; font-weight: 500 !important;
}}

/* --------------------------------------------------------------- loading */
/* Streamlit's default is to fade the whole page to half opacity while a rerun
   is in flight -- 44 elements pick up data-stale="true" on an ordinary click.
   On a page that is mostly numbers a fade reads as a fault: the figures are
   still there, still legible, and now look switched off. Worse, it fades the
   very figures someone is mid-sentence about.
   Held at full opacity, with the progress moved to the top edge where it
   belongs. */
.stApp [data-stale="true"] {{
    opacity: 1 !important;
    transition: none !important;
}}

/* An indeterminate bar travelling the top edge, in the brand gradient. Driven
   by the presence of Streamlit's own status widget, which is in the DOM only
   while the script is running -- so this needs no JavaScript and cannot fall
   out of step with the app's actual state. */
.stApp:has([data-testid="stStatusWidget"])::before {{
    content: ""; position: fixed; top: 0; left: 0; right: 0; height: 3px;
    background: rgba(43,185,168,.14); z-index: 99998; pointer-events: none;
}}
.stApp:has([data-testid="stStatusWidget"])::after {{
    content: ""; position: fixed; top: 0; height: 3px; width: 34%; left: -34%;
    z-index: 99999; pointer-events: none;
    background: linear-gradient(90deg,
        rgba(43,185,168,0) 0%, {TEAL} 35%, {MAGENTA} 75%, rgba(232,24,124,0) 100%);
    animation: ps-travel 1.15s cubic-bezier(.65,.02,.34,1) infinite;
}}
@keyframes ps-travel {{ to {{ left: 100%; }} }}

/* The spinner shown during a full refresh, which is the longest wait in the
   app and therefore the one worth dressing. Streamlit's own mark is restyled
   in place rather than hidden and replaced: adding a second ring beside the
   first is how a page ends up with two spinners, which is what happened the
   first time this was written. */
[data-testid="stSpinner"] {{
    font-size: .82rem; color: var(--ps-muted);
}}
[data-testid="stSpinnerIcon"] {{
    display: inline-block !important;
    width: 20px !important; height: 20px !important;
    border-radius: 50% !important;
    border: 2px solid var(--ps-hairline) !important;
    border-top-color: {TEAL} !important;
    border-right-color: {MAGENTA} !important;
    background: none !important;
    animation: ps-spin .72s linear infinite !important;
}}
@keyframes ps-spin {{ to {{ transform: rotate(360deg); }} }}

/* Respect a reader who has asked for less motion: the bar still shows that
   something is happening, it just pulses instead of travelling. */
@media (prefers-reduced-motion: reduce) {{
    .stApp:has([data-testid="stStatusWidget"])::after {{
        left: 0; width: 100%; animation: ps-pulse 1.4s ease-in-out infinite;
    }}
    [data-testid="stSpinner"]::before {{ animation-duration: 2.4s; }}
    @keyframes ps-pulse {{ 50% {{ opacity: .35; }} }}
}}
.stSelectbox div[data-baseweb="select"] > div,
.stMultiSelect div[data-baseweb="select"] > div,
.stTextInput input {{
    border-color: var(--ps-hairline) !important; border-radius: var(--ps-r) !important;
    font-size: .8rem !important; min-height: 32px;
}}
[data-baseweb="tag"] {{
    background: var(--ps-heading) !important; border-radius: 2px !important;
    font-size: .7rem !important;
}}
.stExpander {{ border: 1px solid var(--ps-hairline); border-radius: var(--ps-r); }}

/* --------------------------------------------------------------- footer */
.ps-footer {{
    margin-top: 2.4rem; padding-top: .6rem;
    border-top: 1px solid var(--ps-hairline);
    color: var(--ps-muted); font-size: .68rem;
    display: flex; justify-content: space-between; flex-wrap: wrap; gap: .5rem;
}}

footer {{ visibility: hidden; }}
</style>
"""


# -- components -------------------------------------------------------------

def logo_data_uri() -> str | None:
    if not LOGO_PATH.exists():
        return None
    import base64
    return ("data:image/png;base64,"
            + base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii"))


def title_bar(title: str, meta: str, badge: str = "") -> str:
    """Compact identity line. No hero panel: the space one occupies is space
    the analyst wanted for holdings."""
    tag = f'<span class="ps-tag-demo">{badge}</span>' if badge else ""
    return (f'<div class="ps-bar"><h1>{title}{tag}</h1>'
            f'<div class="meta">{meta}</div></div>')


def strip(items: list[dict]) -> str:
    """A row of figures divided by hairlines.

    Each item: {label, value, note, tone, note_tone}, all but label and value
    optional. ``tone`` colours the figure; ``note_tone`` colours the line
    beneath it. They are separate because the two are often different things:
    a market value is neither good nor bad, while the gain underneath it is.
    """
    cells = "".join(
        f'<div class="cell"><div class="k">{i["label"]}</div>'
        f'<div class="v {i.get("tone", "")}">{i["value"]}</div>'
        + (f'<div class="n {i.get("note_tone", "")}">{i["note"]}</div>'
           if i.get("note") else "")
        + "</div>"
        for i in items
    )
    return f'<div class="ps-strip">{cells}</div>'


def sidebar_stat(label: str, value: str, tone: str = "") -> str:
    """A label-and-figure row in the rail, both on the shared left edge."""
    return (f'<div class="ps-sb-stat"><span class="k">{label}</span>'
            f'<span class="v {tone}">{value}</span></div>')


def section(title: str, subtitle: str = "") -> str:
    out = f'<div class="ps-section">{title}</div>'
    if subtitle:
        out += f'<div class="ps-section-sub">{subtitle}</div>'
    return out


def chart_label(text: str) -> str:
    """Heading for a chart, set as an element rather than a Plotly title."""
    return f'<div class="ps-chart-label">{text}</div>'


def note(title: str, body: str, tone: str = "accent") -> str:
    """A margin note, as it would appear in a research document."""
    return (f'<div class="ps-note {tone}"><div class="t">{title}</div>'
            f'<div class="b">{body}</div></div>')


#: Kept so existing call sites keep working; renders as a margin note.
def card(title: str, body: str, tone: str = "") -> str:
    return note(title, body, tone or "accent")


def abbr(short: str, full: str, marker: bool = True) -> str:
    """A short code with its full form one hover away.

    ``marker`` draws the circled i. Turn it off where a column is already
    dense with them and the hint would become noise.
    """
    if not full or full == short:
        return short
    safe = full.replace('"', "&quot;")
    dot = "<i>i</i>" if marker else ""
    return f'<span class="ps-abbr" title="{safe}">{short}{dot}</span>'


def header(label: str, explanation: str = "") -> str:
    """A column heading, optionally carrying a definition on hover."""
    return abbr(label, explanation) if explanation else label


def pill(text: str, tone: str = "") -> str:
    css_class = {"navy": "ps-pill ps-pill-navy", "warn": "ps-pill ps-pill-warn",
                 "bad": "ps-pill ps-pill-bad"}.get(tone, "ps-pill")
    return f'<span class="{css_class}">{text}</span>'


def table(headers: list[tuple[str, str]], rows: list[list[str]]) -> str:
    """A table. Column classes come from the header.

    A cell may also be given as ``(html, extra_class)`` where one row needs a
    class the column cannot supply -- a tint that depends on the value rather
    than on which column it sits in.
    """
    head = "".join(f'<th class="{cls}">{label}</th>' for label, cls in headers)
    parts = []
    for row in rows:
        cells = []
        for cell, (_, cls) in zip(row, headers):
            extra = ""
            if isinstance(cell, tuple):
                cell, extra = cell
            classes = f"{cls} {extra}".strip()
            cells.append(f'<td class="{classes}">{cell}</td>')
        parts.append("<tr>" + "".join(cells) + "</tr>")
    body = "".join(parts)
    return (f'<div class="ps-tablewrap"><table class="ps-table">'
            f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody>"
            f"</table></div>")


def weight_cell(share: float, scale: float = 1.0) -> str:
    """A weight, with a bar behind it showing size relative to the largest."""
    width = min(share / scale, 1.0) * 100 if scale > 0 else 0
    return (f'<span class="ps-weight"><i style="width:{width:.0f}%"></i>'
            f'<span>{share:.1%}</span></span>')


def sparkline(values, positive: bool | None = None, width: int = 66,
              height: int = 18) -> str:
    """A one-year price trace, small enough to sit inside a table row.

    A column of sparklines turns a holdings table into something you read as a
    shape before you read it as numbers -- which name has been falling all year,
    which turned in the spring. No axis, no label: at this size the outline is
    the information, and anything else is clutter.
    """
    series = [float(v) for v in values if v is not None]
    if len(series) < 3:
        return '<span class="ps-muted">&mdash;</span>'

    # Thin to roughly one point per horizontal pixel; more is invisible detail
    # that only makes the SVG larger.
    if len(series) > width:
        step = len(series) / width
        series = [series[int(i * step)] for i in range(width)]

    low, high = min(series), max(series)
    span = (high - low) or 1.0
    pad = 2
    inner = height - pad * 2
    points = " ".join(
        f"{i / (len(series) - 1) * width:.1f},"
        f"{pad + (1 - (v - low) / span) * inner:.1f}"
        for i, v in enumerate(series)
    )

    rising = series[-1] >= series[0] if positive is None else positive
    colour = POSITIVE if rising else NEGATIVE
    last_x = width
    last_y = pad + (1 - (series[-1] - low) / span) * inner

    return (
        f'<svg class="ps-spark" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" preserveAspectRatio="none">'
        f'<polyline points="{points}" fill="none" stroke="{colour}" '
        f'stroke-width="1.2" stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{last_x - 1}" cy="{last_y:.1f}" r="1.6" fill="{colour}"/>'
        f"</svg>"
    )


#: Lower bound of each band, with the class that paints it. The thresholds are
#: the scorecard's own, so a cell's colour and its status always agree -- two
#: separate ladders would eventually drift and put an amber cell on a green row.
_BAND_CLASS: list[tuple[float, str]] = [
    (0.10, "ps-b-strong-up"),
    (0.03, "ps-b-up"),
    (-0.03, "ps-b-flat"),
    (-0.10, "ps-b-down"),
    (float("-inf"), "ps-b-strong-down"),
]


def band_class(value: float | None) -> str:
    """The tint for a relative-performance figure."""
    if value is None:
        return "ps-b-none"
    for floor, name in _BAND_CLASS:
        if value >= floor:
            return name
    return _BAND_CLASS[-1][1]


def band_cell(value: float | None, dash: str = "&mdash;") -> str:
    """A percentage on a background that says what it means."""
    if value is None:
        return dash
    return f"{value * 100:+.1f}%"


def alpha_cell(value: float | None, scale: float | None = None) -> str:
    """A figure coloured teal for a beat, magenta for a miss, with an optional
    diverging bar scaled against the page's largest absolute value."""
    if value is None:
        return '<span class="ps-muted">n/a</span>'
    cls = "ps-up" if value > 0 else "ps-down"
    label = f'<span class="{cls}">{value * 100:+.1f}</span>'
    if not scale or scale <= 0:
        return label
    width = min(abs(value) / scale, 1.0) * 50.0
    side = "pos" if value > 0 else "neg"
    return (f'{label}<span class="ps-bar-cell">'
            f'<i class="{side}" style="width:{width:.1f}%"></i></span>')


# -- charts -----------------------------------------------------------------

def plotly_layout(dark: bool = False) -> dict:
    return dict(
        font=dict(family="Work Sans, Segoe UI, sans-serif", size=10,
                  color=DARK_MUTED if dark else MUTED),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=8, t=8, b=0),
        hoverlabel=dict(font=dict(family="Work Sans, sans-serif", size=11,
                                  color="#FFFFFF"),
                        bgcolor=NAVY_DEEP, bordercolor=NAVY_DEEP),
    )


PLOTLY_LAYOUT = plotly_layout(False)


def grid_colour(dark: bool = False) -> str:
    return "rgba(255,255,255,.07)" if dark else HAIRLINE


def axis_colour(dark: bool = False) -> str:
    return "rgba(255,255,255,.35)" if dark else NAVY_DEEP


# Legacy aliases used by earlier call sites.
page_header = title_bar
kpi_rail = strip
