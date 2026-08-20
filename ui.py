"""Browser UI over the local NEPSE archive — candles, floorsheet, broker flow.

    streamlit run ui.py

Everything is read straight from Master_data/*.txt with the standard library; Streamlit and
Plotly only draw. No JavaScript is written here — the charting is the library's own.
"""

import re
import subprocess
import sys
import threading
import time
from collections import defaultdict
from datetime import date as date_cls, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import live_1d
import market_hours
import naasa
import supply_demand
import swing_pro
import swp
import trade_setup
from prices import bars as adjusted_bars, ex_dates as _ex_dates
from indicators import alma, atr, bollinger, ema, macd, pivots, rsi, sma, structure, trade_levels

MASTER = Path(__file__).parent / "Master_data"
# Mutual funds and debentures are excluded everywhere — they trade on NAV and coupons, not
# on the structure this indicator reads. NEPSE publishes no sector map, so they are
# identified by ticker shape: D+digits / B+2 digits are debentures; a trailing F, MF, S2,
# SY or a numeric scheme name is a fund.
DEBENTURE = re.compile(r"D\d|B\d{2}$")
FUND = re.compile(r"(F\d*|MF\d*|S\d|SY\d*)$|^H\d{4}$|^LSH\d+$")


def file_stamp(path):
    """mtime of a file, 0 when absent — the invalidation key for anything read from it."""
    return path.stat().st_mtime if path.exists() else 0.0


def instrument_names():
    """{symbol: company name} from NAASA's instrument list, for the page header."""
    path = MASTER / "instruments.txt"
    return read_instrument_names(file_stamp(path))


@st.cache_data(show_spinner=False)
def read_instrument_names(mtime):
    path = MASTER / "instruments.txt"
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        parts = line.split("	")
        if len(parts) >= 3:
            out[parts[0]] = parts[2]
    return out


def instrument_types():
    """NAASA's own classification, if naasa.py has been run — it beats guessing."""
    return read_instrument_types(file_stamp(MASTER / "instruments.txt"))


@st.cache_data(show_spinner=False)
def read_instrument_types(mtime):
    return naasa.load_types()


def tradeable(symbol):
    """Stocks only. Uses the exchange's instrument type when available, ticker shape otherwise."""
    kind = instrument_types().get(symbol.replace("/", "-"))
    if kind:
        return kind == "Stock"
    return not DEBENTURE.search(symbol) and not FUND.search(symbol)

FLOORSHEET = MASTER / "floorsheet"

st.set_page_config(page_title="NEPSE archive", layout="wide")

# Everything must fit the viewport: no page scrollbar, ever. Chart heights are expressed
# in vh so they shrink with the window instead of pushing the page taller.
st.markdown("""
<style>
  /* Light fintech look: soft grey page, white rounded cards, coral accent.
     Everything must fit the viewport - no page scrollbar, ever. */
  html, body, [data-testid="stAppViewContainer"] { overflow: hidden !important; }
  [data-testid="stAppViewContainer"] { background: #0F1115 !important; }
  [data-testid="stAppViewContainer"] > .main { overflow-y: auto; }
  [data-testid="stHeader"] { height: 0 !important; background: transparent !important; }
  .block-container { padding: 1.1rem 1.4rem 0.4rem 1.4rem !important; max-width: 100% !important; }

  /* sidebar as a white panel */
  [data-testid="stSidebar"] { background: #141721 !important; border-right: 1px solid #242833; }
  [data-testid="stSidebar"] .block-container { padding-top: 1.2rem !important; }

  h1 { font-size: 1.45rem !important; color: #E6E8EC !important; margin-bottom: .1rem !important;
       font-weight: 700 !important; }
  h2, h3 { color: #E6E8EC !important; font-weight: 600 !important; }

  /* metrics as cards */
  [data-testid="stMetric"] { background: #171A21; border: 1px solid #242833; border-radius: 14px;
                             padding: .7rem .9rem; box-shadow: 0 1px 2px rgba(0,0,0,.35); }
  [data-testid="stMetricLabel"] { font-size: .7rem !important; color: #98A2B3 !important; }
  [data-testid="stMetricValue"] { font-size: 1.25rem !important; color: #F2F4F7 !important;
                                  font-weight: 700 !important; }

  /* charts and tables in the same card shell */
  .stPlotlyChart, [data-testid="stDataFrame"], iframe {
      background: #171A21; border: 1px solid #242833; border-radius: 14px;
      box-shadow: 0 1px 2px rgba(0,0,0,.35); max-height: 78vh !important; }
  .stPlotlyChart { padding: .3rem; }

  /* tabs: quiet until active, then coral */
  .stTabs [data-baseweb="tab-list"] { gap: 1.4rem; border-bottom: 1px solid #242833; }
  .stTabs [data-baseweb="tab"] { color: #98A2B3; font-weight: 600; }
  .stTabs [aria-selected="true"] { color: #FF6B5B !important; }

  /* coral pill buttons */
  .stButton > button, .stFormSubmitButton > button {
      background: #FF6B5B; color: #FFFFFF; border: 0; border-radius: 999px;
      padding: .35rem 1.1rem; font-weight: 600; }
  .stButton > button:hover, .stFormSubmitButton > button:hover { background: #F1543F; color: #FFF; }

  [data-testid="stExpander"] { background: #171A21; border: 1px solid #242833;
                               border-radius: 12px; }
  .page-title { font-size: 1.35rem; font-weight: 700; color: #F2F4F7; letter-spacing: -.01em; }
  .page-title .muted { font-size: .8rem; font-weight: 500; color: #98A2B3; margin-left: .4rem; }
  .page-price { font-size: 1.9rem; font-weight: 700; color: #F2F4F7; line-height: 1.1; }
  .page-price .unit { font-size: .9rem; color: #FF6B5B; font-weight: 600; }
  .pill { display: inline-block; float: right; padding: .35rem .9rem; border-radius: 999px;
          font-weight: 700; font-size: .95rem; }
  .pill.up { background: rgba(18,184,134,.12); color: #12B886; }
  .pill.down { background: rgba(255,107,91,.12); color: #FF6B5B; }
  .section { font-size: .95rem; font-weight: 700; color: #F2F4F7; margin: .7rem 0 .35rem; }
  .jump { font-size: .78rem; font-weight: 600; color: #6EA8FE; text-decoration: none;
          margin-left: .5rem; white-space: nowrap; }
  .jump:hover { text-decoration: underline; }
  /* sidebar nav: the menu radio reads as a list of pages, active one in coral */
  [data-testid="stSidebar"] [role="radiogroup"] { gap: .15rem; }
  [data-testid="stSidebar"] [role="radiogroup"] label {
      padding: .45rem .7rem; border-radius: 10px; width: 100%; color: #9AA4B2;
      font-weight: 600; font-size: .92rem; }
  [data-testid="stSidebar"] [role="radiogroup"] label:hover { background: #1C202B; }
  [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {
      background: rgba(255,107,91,.12); color: #FF6B5B; }
  [data-testid="stSidebar"] [role="radiogroup"] label > div:first-child { display: none; }
  /* chart header, TradingView style */
  .tv-head { display: flex; align-items: baseline; gap: .45rem; flex-wrap: wrap;
             margin: .1rem 0 0 .1rem; }
  .tv-sym { font-weight: 700; color: #F2F4F7; font-size: .95rem; }
  .tv-meta { color: #8A93A5; font-size: .85rem; }
  .tv-ohlc { font-size: .85rem; margin-left: .5rem; font-variant-numeric: tabular-nums; }
  .tv-ohlc b { font-weight: 600; }
  .tv-vol { color: #8A93A5; font-size: .8rem; margin: .05rem 0 .3rem .1rem; }


  /* Streamlit's height="stretch" only stretches into an ancestor that has a definite
     height. <section data-testid="stMain"> is full-height flex, but stMainBlockContainer
     under it is display:block and content-sized, which breaks the chain — so a stretched
     chart collapses to nothing. Re-link it, and min-height:0 lets the flex children shrink
     instead of overflowing. */
  section[data-testid="stMain"] > div[data-testid="stMainBlockContainer"] {
      flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; }
  section[data-testid="stMain"] div[data-testid="stVerticalBlock"] { min-height: 0; }
  /* basis 0, not auto: with auto the chart is sized from its own content and the
     column gaps above it are never subtracted, so it overshoots the fold. */
  .st-key-mainchart { flex: 1 1 0 !important;
      border: none !important; background: transparent !important; padding: 0 !important; }
  /* FULL FIT, no page scroll — using VIEWPORT units, not flex percentages.
     Four earlier attempts each traded one artifact for another: flex:1 1 0 gave Plotly a box it
     could not measure (450px chart in a 191px slot, painted over the table); height:100% against
     a flex item resolved to 0 and the map vanished; and container-only resizing left Plotly
     laying out at its old size with 247 of 297 tiles clipped, because Plotly re-lays-out on
     WINDOW resize, not container resize. A vh height is definite at first paint, so Plotly sizes
     correctly once and never needs a nudge. */
  .stMain .block-container { display: flex !important; flex-direction: column !important;
      height: 100dvh !important; max-height: 100dvh !important; overflow: hidden !important;
      padding-bottom: .3rem !important; }
  /* the box's height is NOT written here — it is emitted from HM_H just below this block, so
     the figure and its container cannot drift. They already had: this rule said 366px against
     an HM_H of 360 while a comment asked a human to keep the two in step. */
  .st-key-mainchart { flex: 0 0 auto !important; overflow: hidden !important; }
  /* the index table scrolls inside itself: the page is pinned to 100dvh with overflow hidden,
     so without this the last rows are clipped away with no scrollbar to reach them */
  .idxtbl { overflow: auto; max-height: calc(100dvh - 500px); min-height: 120px; }
  .st-key-mainchart [data-testid="stVerticalBlock"],
  .st-key-mainchart [data-testid="stElementContainer"] { min-height: 0 !important; }
  /* whatever the map leaves goes to the table, and only the TABLE scrolls if a screen is short */
  .stMain .block-container > div:last-child { flex: 1 1 auto !important; min-height: 0 !important;
      overflow-y: auto !important; }

  /* Value-change flash. A permanent square is noise on 15 rows; an animation fires only on the
     render where the number actually moved, then fades. The whole table is re-rendered each
     poll, so an unchanged cell simply never carries the class and never animates. */
  @keyframes tickUp   { from { background: rgba(63,185,80,.45); } to { background: transparent; } }
  @keyframes tickDown { from { background: rgba(248,81,73,.45); } to { background: transparent; } }
  td.tick-up { animation: tickUp 1s ease-out 1; }
  td.tick-dn { animation: tickDown 1s ease-out 1; }

  /* Cron "job running" animation — an indeterminate sliding bar + pulsing label */
  .cron-run { position: relative; height: 7px; border-radius: 4px; overflow: hidden;
      background: #232833; margin: 6px 0 2px; }
  .cron-run::before { content: ''; position: absolute; top: 0; left: -45%; width: 45%; height: 100%;
      border-radius: 4px; background: linear-gradient(90deg, transparent, #ff6b5b 55%, #ffd0c8, transparent);
      animation: cronSlide 1.05s cubic-bezier(.4,0,.2,1) infinite; }
  @keyframes cronSlide { 0% { left: -45%; } 100% { left: 100%; } }
  .cron-run-lbl { color: #ff8f80; font-weight: 600; font-size: .9rem; }
  .cron-run-lbl::after { content: '…'; animation: cronDots 1.2s steps(4,end) infinite; }
  @keyframes cronDots { 0%,20%{content:'';} 40%{content:'.';} 60%{content:'..';} 80%,100%{content:'...';} }

  /* Cron schedule = a centered vertical flowchart: title → ↓ → time box, nodes joined by arrows */
  .cron-job-title { font-weight: 700; font-size: 1.02rem; color: #F2F4F7; text-align: center; }
  .cron-job-sub { color: #8A93A5; font-size: .74rem; text-align: center; margin-bottom: 3px;
      font-variant-numeric: tabular-nums; }
  .cron-arrow { text-align: center; font-size: 1.7rem; line-height: 1; color: #ff8f80;
      font-weight: 700; margin: 3px 0; }
  .cron-timebox-lbl { color: #E6E8EC; font-weight: 600; font-size: .82rem; margin: 2px 0;
      text-align: center; }
  .cron-flow-foot { text-align: center; color: #8A93A5; font-size: .76rem; margin-top: 4px; }
  /* center the chip/add/run buttons' labels inside the narrow flow column */
  .st-key-cronflow div[data-testid="stButton"] button,
  .st-key-cronflow_hist div[data-testid="stButton"] button { justify-content: center; }
  /* pipeline node status highlight + running pulse */
  .cron-node { border-radius: 10px; padding: 12px 10px; text-align: center; border: 1px solid #0d1117; }
  .cron-node b { color: #fff; font-size: .98rem; }
  .cron-node span { color: #e6e8ec; font-size: .72rem; opacity: .85; }
  .cron-node-running { animation: cronPulse 1s ease-in-out infinite; }

  /* Trade ticket — the clicked supply/demand row costed two ways, side by side */
  .ticket { border: 1px solid #242833; border-radius: 10px; padding: 10px 13px; background: #12161f; }
  .ticket .ohlc { color: #8A93A5; font-size: .82rem; margin-bottom: 7px; }
  .ticket .ohlc b { color: #E6E8EC; font-variant-numeric: tabular-nums; }
  .ticket table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
  .ticket th { text-align: right; font-size: .68rem; color: #8A93A5; font-weight: 700;
               padding: 2px 9px; text-transform: uppercase; letter-spacing: .05em; }
  .ticket th.k, .ticket td.k { text-align: left; }
  .ticket td { text-align: right; padding: 3px 9px; font-size: .92rem; color: #E6E8EC; }
  .ticket td.k { color: #8A93A5; font-size: .8rem; }
  .ticket tr.sep td { border-top: 1px solid #242833; padding-top: 6px; }
  .ticket .warn { color: #F0A202; font-size: .78rem; margin-top: 7px; }
  @keyframes cronPulse { 0%,100% { opacity: 1; } 50% { opacity: .62; } }

  /* resolution toolbar — flat TradingView pills, not Streamlit's chunky buttons */
  div[data-testid="stSegmentedControl"] button { background: transparent; border: none;
      color: #8A93A5; font-size: .78rem; font-weight: 600; padding: .1rem .5rem; }
  div[data-testid="stSegmentedControl"] button[aria-checked="true"] {
      background: #232833; color: #E6E8EC; border-radius: 5px; }
  ::-webkit-scrollbar { width: 0 !important; height: 0 !important; }
  * { scrollbar-width: none !important; }
</style>
""", unsafe_allow_html=True)

# The treemap's height, in one place. It is set on the FIGURE (so Plotly lays out at exactly this
# size — it measures once at init and never re-lays-out when CSS moves the box afterwards) AND on
# the container, which is why it has to be a single constant rather than a number typed into both.
# It was typed into both: the stylesheet said 366px against an HM_H of 360, with a comment asking
# whoever came next to keep them in step. The box is deliberately a few px taller than the figure
# so a 1px rounding difference cannot clip the bottom row of tiles.
HM_H = 360
st.markdown(f"<style>.st-key-mainchart{{height:{HM_H + 6}px !important}}</style>",
            unsafe_allow_html=True)


# ---------------------------------------------------------------- data access

def universe():
    # both lists are rewritten by fetch_symbols.py, so both mtimes are part of the key
    return read_universe(file_stamp(MASTER / "symbols.txt"), file_stamp(MASTER / "indices.txt"))


@st.cache_data(show_spinner=False)
def read_universe(sym_mtime, idx_mtime):
    out = {}
    for kind in ("symbols", "indices"):
        for name in (MASTER / f"{kind}.txt").read_text(encoding="utf-8").split():
            out[name] = kind
    return out


def archive_stamp():
    """Newest mtime across the daily archive — the cache key for anything derived from it.

    ~730 stat calls, single-digit milliseconds. Cheaper than the alternative, which is a
    scanner that keeps reporting yesterday until someone restarts Streamlit.
    """
    return max((f.stat().st_mtime for kind in ("symbols", "indices")
                for f in (MASTER / kind).glob("*/1D.txt")), default=0.0)


def dir_stamp(path):
    """mtime of a directory — changes when a file is added to or removed from it."""
    return path.stat().st_mtime if path.exists() else 0.0


def bars(symbol, timeframe, limit):
    """OHLCV rows, newest last. Returns column lists ready for plotting.

    The cache is keyed on the file's mtime, so a fetch that rewrites the archive shows up on
    the next rerun without anyone clearing anything — a plain @cache_data would pin the
    chart to whatever was on disk the first time it was drawn.
    """
    safe = symbol.replace("/", "-")
    for kind in ("symbols", "indices"):
        path = MASTER / kind / safe / f"{timeframe}.txt"
        if path.exists():
            return read_bars(str(path), path.stat().st_mtime, limit)
    return None


@st.cache_data(show_spinner=False)
def read_bars(path, mtime, limit):        # NB: no leading underscore — Streamlit does not
                                          # hash _-prefixed params, which would void the key
    rows = Path(path).read_text(encoding="utf-8").splitlines()[1:]
    rows = rows[-limit:] if limit else rows
    cols = {k: [] for k in ("when", "open", "high", "low", "close", "change", "pct", "volume", "amount")}
    for line in rows:
        f = line.split("\t")
        for key, value in zip(cols, f):
            cols[key].append(value)
    for k in ("open", "high", "low", "close", "change", "pct", "volume", "amount"):
        cols[k] = [float(v) if v not in ("", "None") else None for v in cols[k]]

    # Corporate-action adjust DAILY SYMBOL bars here, so every page that already calls bars()
    # gets the same series prices.bars() hands the analysis modules. Without it the Chart, the
    # Scanner and the signal replay drew a -50% ex-date gap that never traded, while swing_pro
    # and backtest had spliced it out — the same symbol, two different histories.
    # Indices carry no corporate actions and intraday files are same-day, so both stay raw.
    p = Path(path)
    if p.name == "1D.txt" and p.parent.parent.name == "symbols" and cols["close"]:
        if all(x is not None for x in cols["open"]) and all(x is not None for x in cols["close"]):
            # Adjusting the TRUNCATED window is correct: an ex-date outside it scales nothing
            # visible, and one inside it scales exactly the bars before it, as on the full series.
            for i, fac in reversed(_ex_dates(cols["when"], cols["close"], cols["open"])):
                for k in ("open", "high", "low", "close"):
                    for j in range(i):
                        if cols[k][j] is not None:
                            cols[k][j] *= fac
    return cols


def with_live_bar(cols, symbol):
    """Lay today's bar from the socket over a loaded daily series.

    live_1d already writes exactly this row into the archive, but only every LIVE_1D_FLUSH
    seconds — so without this the newest candle is up to that stale, and before the day's first
    flush it is missing altogether. Same numbers either way; the only thing that changes is
    latency. Daily series only: the intraday files are their own timeframe.

    Returns a copy — mutating `cols` would poison the mtime-keyed read_bars cache it came from,
    and every other page reading that symbol would inherit a provisional bar.
    """
    if not cols or not cols["when"]:
        return cols
    try:
        feed = naasa_feed()
        with feed["lock"]:
            quote = dict(feed["snap"].get(live_1d.feed_name(symbol), {}))
    except Exception:
        return cols
    day = live_1d.today()
    line = live_1d.row(quote, day)
    if line is None:                        # has not traded today — leave the archive alone
        return cols
    fields = line.split("\t")
    values = [fields[0]] + [float(v) if v else None for v in fields[1:]]
    out = {k: list(v) for k, v in cols.items()}
    keys = ("when", "open", "high", "low", "close", "change", "pct", "volume", "amount")
    replace = out["when"][-1] == day        # the flush already wrote today; refresh it in place
    for key, value in zip(keys, values):
        if key in out:
            if replace:
                out[key][-1] = value
            else:
                out[key].append(value)
    return out


def last_line(path, tail=4096):
    """Final line of a file without reading the whole thing — these archives are large."""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - tail))
            rows = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return ""
    return rows[-1] if rows else ""


@st.cache_data(show_spinner=False)
def archive_state(_stamp=0.0):
    """Newest stamp in every store, and how many symbols actually reach it.

    'Behind' is the number that stop short of the newest date anyone has — a symbol that
    simply did not trade counts as behind, so treat it as a prompt to look, not an error.
    """
    out = {}
    for label, fname in (("Daily bars", "1D.txt"), ("Minute bars", "1minutes.txt")):
        stamps = []
        for kind in ("symbols", "indices"):
            for d in (MASTER / kind).glob("*"):
                line = last_line(d / fname)
                if line:
                    stamps.append(line.split("	")[0])
        newest = max(stamps, default="")
        out[label] = (newest, sum(1 for s in stamps if s != newest), len(stamps))

    floor = MASTER / "floorsheet"
    days = []
    if floor.exists():
        for d in floor.iterdir():
            if d.is_dir():
                files = sorted(f.stem for f in d.glob("*.txt"))
                if files:
                    days.append(files[-1])
    newest = max(days, default="")
    out["Floorsheet"] = (newest, sum(1 for d in days if d != newest), len(days))

    flow = MASTER / "broker_flow"
    stamps = [last_line(f).split("	")[0] for f in flow.glob("*.txt")] if flow.exists() else []
    stamps = [x for x in stamps if x]
    newest = max(stamps, default="")
    out["Broker flow"] = (newest, sum(1 for s in stamps if s != newest), len(stamps))

    def by_date(path, col):
        """(newest date, rows not at it, total). MAX, not the last line: these two files are
        sorted by rank, not by date, so the physical last row is routinely an older session."""
        if not path.exists():
            return "", 0, 0
        ds = [f[col] for f in (l.split("	") for l in
                               path.read_text(encoding="utf-8").splitlines()[1:])
              if len(f) > col and f[col]]
        newest = max(ds, default="")
        return newest, sum(1 for d in ds if d != newest), len(ds)

    out["Volume spike"] = by_date(MASTER / "volume_spike.txt", 2)
    out["Signal scan"] = by_date(MASTER / "scan.txt", 1)
    return out


def operator_scan():
    path = MASTER / "operator_scan.txt"
    if not path.exists():
        return []
    return read_operator_scan(str(path), path.stat().st_mtime)


def warn_if_behind_archive(session, what, button):
    """Say so when a board's .txt was computed BEFORE the bars now on disk.

    Every board here reads a pre-computed table, so its numbers are only as fresh as the last
    time its script ran. That is fine until the archive moves past it, and then the page shows
    yesterday's analysis with today's confidence: swing_pro.txt was built at 07:36 holding 299
    rows dated 2026-08-19, while 339 of 364 symbols already had an 08-20 bar. The sidebar's
    freshness line knew; this page did not, and quietly presented the stale read as current.

    Returns True when a warning was shown, so a caller can adjust what it says next.
    """
    newest = archive_state().get("Daily bars", ("", 0, 0))[0]
    if not (newest and session) or session >= newest:
        return False
    st.warning(
        f"These {what} were computed on **{session}**, but the daily archive already reaches "
        f"**{newest}**. Every number below is from the earlier session — press **{button}** to "
        f"recompute. (Before the 15:00 NPT close the newest bar is a PARTIAL session, so "
        f"rebuilding mid-day scores an unfinished candle.)")
    return True


def swing_pro_board():
    """Rows of Master_data/swing_pro.txt, or [] when swing_pro.py has not run."""
    path = MASTER / "swing_pro.txt"
    if not path.exists():
        return []
    return read_operator_scan(str(path), path.stat().st_mtime)     # same tab-separated shape


@st.cache_data(show_spinner=False, ttl=300)
def swing_market_calendar():
    """The market's own session calendar, which swing_pro's Section 14 needs.

    Without it analyse() cannot tell "did not trade" from "did not exist", so trade_freq comes
    back None, the trades-infrequently flag never fires, and the liquidity cap never applies.
    Passing it here is what keeps the clicked report identical to the row it was clicked from —
    they disagreed on 22 of 307 symbols, and on one the score itself differed by 3 points.
    Cheap (~1s over a 60-symbol sample) and only recomputed every 5 minutes.
    """
    names = (MASTER / "symbols.txt").read_text(encoding="utf-8").split()
    return swing_pro.market_calendar(names)


def supply_demand_board():
    """Rows of Master_data/supply_demand.txt, or [] when supply_demand.py has not run."""
    path = MASTER / "supply_demand.txt"
    if not path.exists():
        return []
    return read_operator_scan(str(path), path.stat().st_mtime)     # same tab-separated shape


@st.cache_data(show_spinner=False)
def read_operator_scan(path, mtime):
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    if len(lines) < 2:
        return []
    cols = lines[0].split("	")
    return [dict(zip(cols, l.split("	"))) for l in lines[1:] if l.strip()]


def broker_ledger(symbol, broker):
    """One broker's daily bought/sold/net in a stock — for the day-by-day operator breakdown."""
    path = MASTER / "broker_flow" / f"{symbol.replace('/', '-')}.txt"
    if not path.exists():
        return []
    return read_broker_ledger(str(path), path.stat().st_mtime, broker)


@st.cache_data(show_spinner=False)
def read_broker_ledger(path, mtime, broker):
    out = []
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    for line in lines[max(1, len(lines) - 12000):]:
        f = line.split("	")
        if len(f) >= 5 and f[1] == broker:
            out.append((f[0], float(f[2]), float(f[3]), float(f[4])))   # date, bought, sold, net
    return out          # oldest .. newest


@st.cache_data(show_spinner=False)
def _read_trade_setup(path, mtime, symbol):
    return trade_setup.setup(symbol)


def get_trade_setup(symbol):
    """Technical A+ setup (score, signal, entry/T1/T2/SL) for a symbol, cached on its 1D mtime."""
    p = MASTER / "symbols" / symbol.replace("/", "-") / "1D.txt"
    if not p.exists():
        return None
    return _read_trade_setup(str(p), p.stat().st_mtime, symbol)


@st.cache_data(ttl=1, show_spinner=False)
def live_snapshot(symbol):
    """NAASA's public quote for one symbol (no login), cached 1s so redraws don't refetch.

    The TTL and the fragments' run_every are deliberately equal: a cache longer than the poll
    makes the poll redraw the same numbers, and a cache shorter than it just refetches for
    nothing. Keep them in step if either changes.
    """
    try:
        return naasa.quotes([symbol]).get(symbol.replace("/", "-")) or {}
    except Exception:
        return {}


def live_price(sym):
    """{ltp, chp, bid, ask, vol, src} for one symbol, socket first then the public REST quote.

    The boards are computed on the last CLOSED session, so during a live session their entry /
    stop / target are yesterday's plan against today's tape. This is what lets a page say where
    price actually is right now without the trader leaving for the NAASA panel.
    """
    try:
        with naasa_feed()["lock"]:
            sk = dict(naasa_feed()["snap"].get(sym, {}))
    except Exception:
        sk = {}
    if sk.get("LTP"):
        return {"ltp": float(sk["LTP"]), "bid": sk.get("BidPrice"), "ask": sk.get("OfferPrice"),
                "vol": sk.get("TTQ"), "chp": None, "src": f"socket · {sk.get('_t', '')}"}
    q = live_snapshot(sym)
    if q.get("lp"):
        return {"ltp": float(q["lp"]), "bid": q.get("bid"), "ask": q.get("ask"),
                "vol": q.get("volume"), "chp": q.get("chp"), "src": "public quote (15s)"}
    return {}


def live_vs_plan(sym, entry, stop, target, key):
    """Where the live price sits against a plan the board computed on the last closed session."""
    try:                                   # point the socket at whatever the trader is reading
        feed = naasa_feed()
        if feed["depth"] != (sym,):
            feed["depth"] = (sym,)
    except Exception:
        pass

    @st.fragment(run_every=1)
    def _strip():
        q = live_price(sym)
        if not q:
            st.caption("No live quote for this symbol right now — the plan below is from the "
                       "last closed session.")
            return
        ltp = q["ltp"]
        risk = abs(entry - stop) or 1
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("LIVE", f"{ltp:,.2f}",
                  None if q["chp"] is None else f"{float(q['chp']):+.2f}%")
        c2.metric("vs entry", f"{(ltp - entry) / entry * 100:+.2f}%",
                  f"{ltp - entry:+,.2f}", delta_color="off")
        c3.metric("R from entry", f"{(ltp - entry) / risk:+.2f}R", delta_color="off")
        hit = ("STOP HIT" if ltp <= stop else "TARGET HIT" if ltp >= target else "in trade range")
        c4.metric("status", hit)
        st.caption(f"🔴 {q['src']} · plan entry {entry:,.2f} · stop {stop:,.2f} · "
                   f"target {target:,.2f} (computed on the last closed session)")
    _strip()


@st.cache_resource(show_spinner=False)
def naasa_feed():
    """Server-wide NAASA live socket. A daemon thread that logs in, holds the socket, and keeps
    {symbol: {field: value}} fresh — ALWAYS ON (want=True) so live data is always available; it
    idles only when no login is saved. Survives Streamlit reruns. ONE NAASA session per account —
    this holds it, so a browser tab logged in as the same account gets evicted (use a dedicated
    feed account to avoid that). `subs` is updated to the symbol currently being viewed."""
    # `subs` is now the WHOLE market and stays constant, so the socket is not torn down every
    # time somebody looks at a different scrip. Only `depth` follows the viewed symbol.
    state = {"snap": {}, "want": True, "subs": tuple(live_1d.names()), "depth": ("NEPSE",),
             "status": "off", "err": "", "lock": threading.Lock(),
             "saved": 0, "saved_at": "", "saved_err": ""}

    def on_tick(rec):
        with state["lock"]:
            state["status"] = "live"        # only a delivered tick proves the feed is live
            d = state["snap"].setdefault(rec["symbol"], {})
            d.update(rec["fields"]); d["_t"] = rec.get("time", "")

    def loop():
        while True:
            if not (state["want"] and market_hours.feed_on()):
                state["status"] = "off"; time.sleep(0.4); continue
            em, pw = naasa.load_credentials()
            if not (em and pw):
                state["status"] = "no login saved"; state["want"] = False; continue
            subs, depth = state["subs"], state["depth"]
            try:
                state["status"] = "connecting"; state["err"] = ""
                naasa.stream_ticks(em, pw, list(subs), on_tick, depth=list(depth),
                                   stop=lambda: (not state["want"]) or state["subs"] != subs
                                   or state["depth"] != depth or not market_hours.feed_on())
            except Exception as e:
                state["err"] = str(e)[:110]; state["status"] = "reconnecting"; time.sleep(4)

    def archiver():
        """Push today's live bar into every instrument's 1D.txt while the session is running.

        Only while the market is LIVE: a snapshot left over from a finished session would keep
        rewriting today's row on top of the official bar the daily fetch writes after close.
        """
        while True:
            time.sleep(LIVE_1D_FLUSH)
            try:
                if market_status()[0] != "LIVE":
                    continue
                with state["lock"]:
                    snap = {k: dict(v) for k, v in state["snap"].items()}
                written, _ = live_1d.flush(snap)
                state["saved_err"] = ""
                if written:
                    state["saved"] = written
                    state["saved_at"] = datetime.now(NPT).strftime("%H:%M:%S")
            except Exception as e:                 # a bad bar must never kill the archiver
                state["saved_err"] = str(e)[:110]

    threading.Thread(target=loop, daemon=True).start()
    threading.Thread(target=archiver, daemon=True).start()
    return state


# Public quotes poll at 1s; these do NOT. Three of these panels are open at once (orders,
# holdings, collateral), so a 1s clock is three AUTHENTICATED calls per second against a live
# brokerage account — and NAASA allows only one session at a time, so this competes with the
# socket feed and with any browser tab signed in as the same user. None of these change
# meaningfully inside a second: an order book moves when you place or fill something, holdings
# and collateral move slower still. 3s is indistinguishable to a human watching and a third of
# the load. Raise it if the broker ever complains; do not lower it.
ACCT_POLL = 3     # order book / holdings / collateral — authed report calls
LIVE_1D_FLUSH = 20   # how often today's live bar is written to the archive, in seconds
CANDLE_POLL = 5      # how often the chart redraws when Live candle is on
IDX_POLL = 5     # index table — one batched quote call for every index
HM_POLL = 10     # sector heatmap — a batched quote for ~282 scrips, the heaviest call we make

# These are a POLL BUDGET, not cosmetics. Every one of them is a network round trip against the
# single NAASA session the live socket also depends on, so polling them hard does not just make
# the page heavy — it starves the feed (see naasa._X_RELOGIN_GAP). Anything reading the socket
# snapshot instead is free and stays at 1s.


@st.cache_resource(show_spinner=False)
def acct_state():
    """Background poller for the NAASA account reports — order book, holdings, collateral.

    These used to be fetched inside the panels' own fragments. A fragment runs on the SESSION's
    script thread and Streamlit serialises those runs, so one slow report call stalled every
    other fragment in the session — including the 1s live ladder. That is why the page went
    still until the tab was refreshed: the socket kept streaming into memory the whole time,
    nothing was left to draw it. The network now lives on its own thread and the panels read
    memory, so a slow or failing report can no longer freeze anything on screen.
    """
    state = {"orders": [], "holdings": [], "collateral": {}, "err": "", "at": "",
             "lock": threading.Lock()}

    def loop():
        while True:
            em, pw = naasa.load_credentials()
            if em and pw:
                try:
                    fresh = (naasa.x_orderbook(em, pw), naasa.x_holdings(em, pw),
                             naasa.x_collateral(em, pw))
                    with state["lock"]:
                        state["orders"], state["holdings"], state["collateral"] = fresh
                        state["err"] = ""
                        state["at"] = datetime.now(NPT).strftime("%H:%M:%S")
                except Exception as e:
                    state["err"] = str(e)[:140]   # keep the last good data on screen beside it
            time.sleep(ACCT_POLL)

    threading.Thread(target=loop, daemon=True).start()
    return state


def acct_call(kind):
    """The latest account report from the poller. A pure memory read — never touches the network,
    because this runs inside the render path."""
    acct = acct_state()
    with acct["lock"]:
        data = {"orders": list(acct["orders"]), "holdings": list(acct["holdings"])}.get(
            kind, dict(acct["collateral"]))
        err = acct["err"]
    if err and not data:
        raise RuntimeError(err)      # nothing to show AND a live error — say so, do not show []
    return data


def _broker_reply(resp):
    """(ErrorCode, Message) out of a NAASA order reply, which answers in either casing. Returns
    code None when the shape is unrecognised — for a money call that is "unknown", NOT "failed",
    and the caller must say so rather than invite a retry that could double the order."""
    if not isinstance(resp, dict):
        return None, str(resp)[:200]
    code = resp.get("ErrorCode", resp.get("errorCode"))
    return code, str(resp.get("Message") or resp.get("message") or "")


def render_socket_depth(symbol, feed):
    """Live quote + best bid/ask for `symbol` straight off NAASA's socket snapshot (sub-second)."""
    with feed["lock"]:
        q = dict(feed["snap"].get(symbol, {}))
    status, scol = market_status()
    def n(x):
        try: return f"{float(x):,.2f}"
        except (TypeError, ValueError): return "–"
    def n0(x):
        try: return f"{float(x):,.0f}"
        except (TypeError, ValueError): return "–"
    lp, pc = q.get("LTP"), q.get("Close")
    try: chp = (float(lp) - float(pc)) / float(pc) * 100
    except (TypeError, ValueError, ZeroDivisionError): chp = 0.0
    col = "#3fb950" if chp >= 0 else "#f85149"
    st.markdown("<hr style='margin:16px 0 8px;border:none;border-top:1px solid #2b3240'>", unsafe_allow_html=True)
    st.markdown(
        "<div style='display:flex;align-items:center;gap:12px;flex-wrap:wrap'>"
        "<span style='font-weight:700;font-size:1.03em'>🔴 Live socket feed</span>"
        f"<span style='background:{scol};color:#0d1117;font-weight:700;padding:1px 9px;"
        f"border-radius:5px;font-size:0.82em'>{status}</span>"
        f"<span style='color:#8a93a5'>{symbol} · LTP <b style='color:{col}'>{n(lp)}</b> "
        f"({chp:+.2f}%) · feed {feed['status']}</span></div>", unsafe_allow_html=True)
    if not q:
        err = feed["err"]
        evicted = feed["status"] in ("reconnecting", "off") or "time" in err.lower()
        st.warning(
            f"No live ticks yet — feed status: **{feed['status']}**"
            + (f" · {err}" if err else "")
            + (".\n\nMost likely your NAASA session is being held by a **browser tab on the same "
               "account**. NAASA allows one live session per account, so the server socket and your "
               "browser keep evicting each other. Fix: either **close your NAASA browser tabs**, or "
               "sign the server feed in with a **dedicated feed account** (a different login than the "
               "one you trade from)." if evicted else "."))
        return

    def f(x):
        try: return float(x)
        except (TypeError, ValueError): return None
    levels = q.get("depth", [])

    def ladder(title, bg, order_first):
        cols = ("Order", "Qty", "Price") if order_first else ("Price", "Qty", "Order")
        head = "".join(f"<th style='padding:3px 8px;text-align:right;color:#8a93a5'>{c}</th>" for c in cols)
        body, tot = "", 0.0
        for i in range(5):
            if i < len(levels):
                l = levels[i]
                if order_first:
                    cells = (n0(l.get("BuyOrders")), n0(l.get("BuyQty")), n(l.get("BuyRate")))
                    tot += f(l.get("BuyQty")) or 0
                else:
                    cells = (n(l.get("SellRate")), n0(l.get("SellQty")), n0(l.get("SellOrders")))
                    tot += f(l.get("SellQty")) or 0
            else:
                cells = ("–", "–", "–")
            body += "<tr>" + "".join(f"<td style='padding:3px 8px;text-align:right'>{c}</td>" for c in cells) + "</tr>"
        foot = (f"<td style='padding:3px 8px;color:#8a93a5'>Total</td>"
                f"<td style='padding:3px 8px;text-align:right;font-weight:700'>{n0(tot)}</td><td></td>")
        return (f"<div style='border:1px solid #2b3240;border-radius:6px;overflow:hidden'>"
                f"<div style='background:{bg};color:#fff;text-align:center;font-weight:700;padding:4px'>{title}</div>"
                f"<table style='width:100%;border-collapse:collapse;font-size:0.86em'><tr>{head}</tr>{body}"
                f"<tr style='border-top:1px solid #2b3240'>{foot}</tr></table></div>")

    vol, avg = f(q.get("TTQ")), f(q.get("WeightedAverage"))
    turnover = vol * avg if (vol is not None and avg is not None) else None   # VWAP × volume
    info = ("<div style='border:1px solid #2b3240;border-radius:6px;overflow:hidden'>"
            "<div style='background:#123;color:#7fd1e0;text-align:center;font-weight:700;padding:4px'>"
            "Stock Information</div><table style='width:100%;border-collapse:collapse;font-size:0.86em'>")
    for k, v in (("Avg Price", n(avg)), ("D.High", n(q.get("High"))), ("D.Low", n(q.get("Low"))),
                 ("P.Close", n(q.get("Close"))), ("Volume", n0(vol)), ("Turnover", n0(turnover))):
        info += (f"<tr><td style='padding:3px 8px;color:#8a93a5'>{k}</td>"
                 f"<td style='padding:3px 8px;text-align:right;font-weight:600'>{v}</td></tr>")
    info += "</table></div>"

    c1, c2, c3 = st.columns(3)
    c1.markdown(ladder("TOP 5 BUY", "#0d3b2e", True), unsafe_allow_html=True)
    c2.markdown(ladder("TOP 5 SELL", "#3b1d0d", False), unsafe_allow_html=True)
    c3.markdown(info, unsafe_allow_html=True)
    # Two clocks on purpose: "last tick" is when THIS scrip last traded, "drawn" is when the
    # panel last re-rendered. Without the second one a quiet stock and a frozen page look
    # identical — which is exactly the confusion that hid a real freeze.
    st.caption(f"🔴 Real-time from NAASA's WebSocket (x:8006), sub-second · last tick "
               f"{q.get('_t','—')} · drawn {datetime.now(NPT):%H:%M:%S}")


NPT = timezone(timedelta(hours=5, minutes=45))


def market_status():
    """NEPSE session right now in Nepal time — PRE-OPEN 10:30–10:45 (orders can be entered),
    PRE-OPEN CLOSE 10:46–10:59 (matching, no new entry), LIVE 11:00–15:00, else CLOSED — and
    CLOSED all day Fri/Sat, when NEPSE is shut."""
    now = datetime.now(NPT)
    mins = now.hour * 60 + now.minute
    if not market_hours.is_trading_day(now):        # per the Market Open Hours switch
        return "CLOSED", "#8a93a5"
    if 630 <= mins < 646:                # 10:30–10:45
        return "PRE-OPEN", "#e3a008"
    if 646 <= mins < 660:                # 10:46–10:59
        return "PRE-OPEN CLOSE", "#d2691e"
    if 660 <= mins < 900:                # 11:00–15:00
        return "LIVE", "#3fb950"
    return "CLOSED", "#8a93a5"


def render_market_depth(symbol):
    """Live market snapshot — NAASA's public quote (no login). The full Top-5 ladder streams
    only over their signed-in socket, so over REST we show best bid/ask + the day's stock stats.
    One quote, cached 1s, so a page full of cards stays cheap."""
    q = live_snapshot(symbol)
    status, scol = market_status()
    def n(x): return f"{x:,.2f}" if isinstance(x, (int, float)) else "–"
    def n0(x): return f"{x:,.0f}" if isinstance(x, (int, float)) else "–"
    lp, chp = q.get("lp"), q.get("chp") or 0
    col = "#3fb950" if (chp or 0) >= 0 else "#f85149"
    st.markdown("<hr style='margin:16px 0 8px;border:none;border-top:1px solid #2b3240'>",
                unsafe_allow_html=True)
    st.markdown(
        "<div style='display:flex;align-items:center;gap:12px;flex-wrap:wrap'>"
        "<span style='font-weight:700;font-size:1.03em'>📊 Live market depth</span>"
        f"<span style='background:{scol};color:#0d1117;font-weight:700;padding:1px 9px;"
        f"border-radius:5px;font-size:0.82em'>{status}</span>"
        f"<span style='color:#8a93a5'>{symbol} · LTP <b style='color:{col}'>{n(lp)}</b> "
        f"({chp:+.2f}%)</span></div>", unsafe_allow_html=True)

    def ladder(title, head_bg, price, side):
        cols = ("Order", "Qty", "Price") if side == "buy" else ("Price", "Qty", "Order")
        head = "".join(f"<th style='padding:3px 8px;text-align:right;color:#8a93a5'>{c}</th>"
                       for c in cols)
        body = ""
        for i in range(5):
            cells = ["–", "–", "–"]
            if i == 0:
                cells = ["–", "–", n(price)] if side == "buy" else [n(price), "–", "–"]
            body += "<tr>" + "".join(f"<td style='padding:3px 8px;text-align:right'>{c}</td>"
                                     for c in cells) + "</tr>"
        return (f"<div style='border:1px solid #2b3240;border-radius:6px;overflow:hidden'>"
                f"<div style='background:{head_bg};color:#fff;text-align:center;font-weight:700;"
                f"padding:4px'>{title}</div><table style='width:100%;border-collapse:collapse;"
                f"font-size:0.86em'><tr>{head}</tr>{body}</table></div>")

    info = ("<div style='border:1px solid #2b3240;border-radius:6px;overflow:hidden'>"
            "<div style='background:#123;color:#7fd1e0;text-align:center;font-weight:700;"
            "padding:4px'>Stock Information</div><table style='width:100%;"
            "border-collapse:collapse;font-size:0.86em'>")
    for k, v in (("Open", n(q.get("open"))), ("D.High", n(q.get("high"))),
                 ("D.Low", n(q.get("low"))), ("P.Close", n(q.get("prev_close"))),
                 ("Volume", n0(q.get("volume")))):
        info += (f"<tr><td style='padding:3px 8px;color:#8a93a5'>{k}</td>"
                 f"<td style='padding:3px 8px;text-align:right;font-weight:600'>{v}</td></tr>")
    info += "</table></div>"

    c1, c2, c3 = st.columns(3)
    c1.markdown(ladder("TOP 5 BUY", "#0d3b2e", q.get("bid"), "buy"), unsafe_allow_html=True)
    c2.markdown(ladder("TOP 5 SELL", "#3b1d0d", q.get("ask"), "sell"), unsafe_allow_html=True)
    c3.markdown(info, unsafe_allow_html=True)
    st.caption("Best bid/ask + day stats from NAASA's public feed (cached 1s). The full Top-5 "
               "ladder, average price and turnover stream only over NAASA's signed-in socket. "
               "NEPSE pre-open is 10:30–10:45 — you can queue orders before the open.")


def render_live_depth(symbol):
    """Full live Top-5 ladder + stock stats from NAASA's authed feed (GetMDepth +
    GetSpecifiedQuote) — the exact numbers on NAASA's order screen. Falls back to the public
    best bid/ask (render_market_depth) when signed out or the feed can't be reached."""
    if not st.session_state.get("naasa_who"):
        render_market_depth(symbol)
        return
    try:
        depth = naasa_authed(lambda c: naasa.market_depth(c, symbol))
        quote = naasa_authed(lambda c: naasa.market_quote(c, symbol))
    except Exception:
        # The full Top-5 feed needs NAASA's in-app socket session, which a server can't hold;
        # the public best bid/ask + day stats still work, so fall back to them quietly.
        render_market_depth(symbol)
        return

    status, scol = market_status()
    def n(x): return f"{x:,.2f}" if isinstance(x, (int, float)) else "–"
    def n0(x): return f"{x:,.0f}" if isinstance(x, (int, float)) else "–"
    lp, pc = quote.get("LTP"), quote.get("PreviousClose")
    chp = (lp - pc) / pc * 100 if isinstance(lp, (int, float)) and isinstance(pc, (int, float)) and pc else 0
    col = "#3fb950" if chp >= 0 else "#f85149"
    st.markdown("<hr style='margin:16px 0 8px;border:none;border-top:1px solid #2b3240'>",
                unsafe_allow_html=True)
    st.markdown(
        "<div style='display:flex;align-items:center;gap:12px;flex-wrap:wrap'>"
        "<span style='font-weight:700;font-size:1.03em'>📊 Live market depth</span>"
        f"<span style='background:{scol};color:#0d1117;font-weight:700;padding:1px 9px;"
        f"border-radius:5px;font-size:0.82em'>{status}</span>"
        f"<span style='color:#8a93a5'>{symbol} · LTP <b style='color:{col}'>{n(lp)}</b> "
        f"({chp:+.2f}%)</span></div>", unsafe_allow_html=True)

    levels = depth.get("levels", [])
    def ladder(title, bg, order_first):
        cols = ("Order", "Qty", "Price") if order_first else ("Price", "Qty", "Order")
        head = "".join(f"<th style='padding:3px 8px;text-align:right;color:#8a93a5'>{c}</th>" for c in cols)
        body, tot = "", 0
        for i in range(5):
            if i < len(levels):
                l = levels[i]
                if order_first:
                    cells, tot = (n0(l["bid_orders"]), n0(l["bid_qty"]), n(l["bid_price"])), tot + (l["bid_qty"] or 0)
                else:
                    cells, tot = (n(l["ask_price"]), n0(l["ask_qty"]), n0(l["ask_orders"])), tot + (l["ask_qty"] or 0)
            else:
                cells = ("–", "–", "–")
            body += "<tr>" + "".join(f"<td style='padding:3px 8px;text-align:right'>{c}</td>" for c in cells) + "</tr>"
        foot = (f"<td style='padding:3px 8px;color:#8a93a5'>Total</td>"
                f"<td style='padding:3px 8px;text-align:right;font-weight:700'>{n0(tot)}</td><td></td>")
        return (f"<div style='border:1px solid #2b3240;border-radius:6px;overflow:hidden'>"
                f"<div style='background:{bg};color:#fff;text-align:center;font-weight:700;padding:4px'>{title}</div>"
                f"<table style='width:100%;border-collapse:collapse;font-size:0.86em'><tr>{head}</tr>{body}"
                f"<tr style='border-top:1px solid #2b3240'>{foot}</tr></table></div>")

    info = ("<div style='border:1px solid #2b3240;border-radius:6px;overflow:hidden'>"
            "<div style='background:#123;color:#7fd1e0;text-align:center;font-weight:700;padding:4px'>"
            "Stock Information</div><table style='width:100%;border-collapse:collapse;font-size:0.86em'>")
    for k, v in (("Avg Price", n(quote.get("WeightedAverage"))), ("D.High", n(quote.get("High"))),
                 ("D.Low", n(quote.get("Low"))), ("P.Close", n(quote.get("PreviousClose"))),
                 ("Volume", n0(quote.get("Volume"))), ("Turnover", n0(quote.get("Turnover")))):
        info += (f"<tr><td style='padding:3px 8px;color:#8a93a5'>{k}</td>"
                 f"<td style='padding:3px 8px;text-align:right;font-weight:600'>{v}</td></tr>")
    info += "</table></div>"

    c1, c2, c3 = st.columns(3)
    c1.markdown(ladder("TOP 5 BUY", "#0d3b2e", True), unsafe_allow_html=True)
    c2.markdown(ladder("TOP 5 SELL", "#3b1d0d", False), unsafe_allow_html=True)
    c3.markdown(info, unsafe_allow_html=True)
    st.caption(f"Exact live feed from NAASA (GetMDepth + GetSpecifiedQuote) · as of {depth.get('time','—')}.")


@st.cache_data(ttl=IDX_POLL, show_spinner=False)
def indices_snapshot():
    """NEPSE index quotes from NAASA (batched SpecifiedQuote) — works live AND while closed.
    Cached 1s so the sidebar + heatmap page share one fetch. [] when signed out / unreachable."""
    em, pw = naasa.load_credentials()
    if not (em and pw):
        return []
    try:
        return naasa.x_indices(em, pw)
    except Exception:
        return []


def _idx_change(r):
    """(%change, ltp, close) for an index row; %Change is blank in the payload so compute it."""
    try:
        lp, cl = float(r.get("LTP")), float(r.get("Close"))
        return ((lp - cl) / cl * 100 if cl else 0.0), lp, cl
    except (TypeError, ValueError):
        return 0.0, None, None


@st.cache_resource(show_spinner=False)
def _tick_memory():
    """{ticker: last LTP drawn}. Survives reruns, so each 1s poll can mark which rows moved."""
    return {}


def tick_class(key, value):
    """CSS class for a cell whose value moved on THIS render: '' / 'tick-up' / 'tick-dn'.

    A class, not a coloured div. The div sat there permanently on all 15 rows and read as
    clutter; and because the table is re-rendered every poll, the browser replays the animation
    only for cells that carry the class on that render — so the flash means "this number just
    moved", which is precisely the signal wanted.
    """
    mem = _tick_memory()
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    last = mem.get(key)
    mem[key] = v
    if last is None or v == last:
        return ""                                      # no move on this render, no animation
    return "tick-up" if v > last else "tick-dn"


def render_index_table(rows, compact=False):
    """NEPSE index watchlist table — SYMBOL, LTP, High, Low, Open, Close, %Change."""
    if not rows:
        st.caption("Sign in under 🔐 **NAASA account** (with Remember me) to load NEPSE indices.")
        return
    def n(x):
        try: return f"{float(x):,.2f}"
        except (TypeError, ValueError): return "–"
    pad = "2px 6px" if compact else "4px 10px"
    fs = "0.78em" if compact else "0.92em"
    head = ("<tr style='color:#8a93a5;text-align:right'>"
            f"<th style='text-align:left;padding:{pad}'>SYMBOL</th><th style='padding:{pad}'>LTP</th>"
            f"<th style='padding:{pad}'>HIGH</th><th style='padding:{pad}'>LOW</th>"
            f"<th style='padding:{pad}'>OPEN</th><th style='padding:{pad}'>CLOSE</th>"
            f"<th style='padding:{pad}'>%CHG</th></tr>")
    body = []
    for r in rows:
        chg, lp, cl = _idx_change(r)
        col = "#3fb950" if chg >= 0 else "#f85149"
        body.append(
            f"<tr style='text-align:right;border-top:1px solid #20262f'>"
            f"<td style='text-align:left;padding:{pad};color:{col};font-weight:600'>{r.get('ticker')}</td>"
            f"<td class='{tick_class('idx:' + str(r.get('ticker')), r.get('LTP'))}' "
            f"style='padding:{pad}'>{n(r.get('LTP'))}</td>"
            f"<td style='padding:{pad}'>{n(r.get('High'))}</td>"
            f"<td style='padding:{pad}'>{n(r.get('Low'))}</td><td style='padding:{pad}'>{n(r.get('Open'))}</td>"
            f"<td style='padding:{pad}'>{n(r.get('Close'))}</td>"
            f"<td style='padding:{pad};color:{col};font-weight:600'>{chg:+.2f}%</td></tr>")
    st.markdown(f"<div class='idxtbl'><table style='width:100%;border-collapse:collapse;"
                f"font-size:{fs};white-space:nowrap'>{head}{''.join(body)}</table></div>",
                unsafe_allow_html=True)


def render_index_heatmap(rows):
    """Our own NEPSE index heatmap — one tile per index, coloured by %change (diverging red→green),
    labelled with symbol, LTP and %change. Built from the same NAASA snapshot as the table."""
    if not rows:
        em, _pw = naasa.load_credentials()
        st.info("Sign in under 🔐 NAASA account (with Remember me) to load the heatmap."
                if not em else
                "Signed in, but NAASA returned no index quotes just now — the feed is down or "
                "the session expired. Re-enter the password under 🔐 NAASA account, or retry "
                "after the 10:30 NPT pre-open.")
        return
    labels, changes, ltps = [], [], []
    for r in rows:
        chg, lp, cl = _idx_change(r)
        labels.append(f"<b>{r.get('ticker')}</b><br>{lp:,.2f}<br>{chg:+.2f}%" if lp is not None
                      else f"<b>{r.get('ticker')}</b>")
        changes.append(chg)
        ltps.append(lp or 0)
    fig = go.Figure(go.Treemap(
        labels=labels, parents=[""] * len(labels), values=[1] * len(labels),
        marker=dict(colors=[_hm_color(c) for c in changes], cornerradius=6,
                    line=dict(width=2, color="#0d1117")),
        text=labels, textinfo="text",
        textfont=dict(size=15, color=[_hm_ink(c) for c in changes]),
        textposition="middle center", hovertemplate="%{label}<extra></extra>", tiling=dict(pad=3)))
    fig.update_layout(margin=dict(t=0, l=0, r=0, b=0), uirevision="idx-heatmap", height=HM_H,
                      transition=dict(duration=350, easing="cubic-in-out"),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    # key= keeps ONE component across reruns: Plotly then animates tiles to their new
    # sizes and colours instead of the container going black while a fresh chart mounts.
    st.plotly_chart(fig, key="hm_index", width="stretch",
                    config={"displayModeBar": False, "responsive": True})


# --- NEPSE stock heatmap (sector-grouped, like nepsetrading) -----------------------------------
# Non-equity buckets NAASA's SectorStock lumps in — kept out of the equity heatmap.
_HM_EXCLUDE = {"", "Promotor Share", "Corporate Debenture", "Government Bond", "Mutual Fund"}
_SECTOR_NAME = {"Development Bank Limited": "Development Banks"}


@st.cache_resource(show_spinner=False)
def _sector_cache():
    return {"rows": [], "who": "", "t": 0.0}


def _sector_map():
    """Stock->sector membership (static-ish), cached an hour — but NEVER cache a failure.

    This used to be a plain @st.cache_data(ttl=3600). One call before sign-in, or one API blip,
    pinned an empty list for an hour; signing in does not invalidate a TTL cache, so the heatmap
    told a signed-in user to sign in while the Indices tab beside it streamed live. Now only a
    non-empty result is stored, it is keyed on WHO is signed in, and a later failure falls back
    to the last good list instead of erasing it.
    """
    em, pw = naasa.load_credentials()
    if not (em and pw):
        return []
    c = _sector_cache()
    if c["rows"] and c["who"] == em and time.time() - c["t"] < 3600:
        return c["rows"]
    try:
        rows = naasa.heatmap_sectors(em, pw)
    except Exception:
        return c["rows"]                       # keep serving the last good map
    if rows:
        c.update(rows=rows, who=em, t=time.time())
    return rows or c["rows"]


@st.cache_data(ttl=HM_POLL, show_spinner=False)
def stock_heatmap_rows():
    """Every equity + its sector + live quote, for the sector-grouped heatmap. Batched quotes
    (works while closed). [{stock, sector, ltp, chg, turnover}], only rows with a price."""
    em, pw = naasa.load_credentials()
    if not (em and pw):
        return []
    pairs = [(r["stock"], _SECTOR_NAME.get(r["sector"], r["sector"]))
             for r in _sector_map()
             if r.get("stock") and r.get("sector") and r["sector"] not in _HM_EXCLUDE]
    sector = dict(pairs)
    tickers = list(sector)
    quotes = {}
    for i in range(0, len(tickers), 150):                 # NAASA batches ~150/call fine
        try:
            for q in naasa.x_quotes(em, pw, tickers[i:i + 150]):
                quotes[q.get("ticker")] = q
        except Exception:
            pass
    out = []
    for s in tickers:
        q = quotes.get(s)
        if not q or not q.get("LTP"):
            continue
        try:
            lp, cl = float(q["LTP"]), float(q.get("Close") or 0)
        except (TypeError, ValueError):
            continue
        out.append({"stock": s, "sector": sector[s], "ltp": lp,
                    "chg": (lp - cl) / cl * 100 if cl else 0.0,
                    "turnover": float(q.get("Volume") or 0)})
    return out


_HM_BUCKETS = [("≤ -2%", "#8b1a1a"), ("-1%", "#c0392b"), ("-0%", "#e0736a"), ("0%", "#5b6472"),
               ("+0%", "#5bbf7a"), ("+1%", "#27ae60"), ("≥ +2%", "#1c8b45")]


def _hm_ink(chg):
    """Readable ink for a tile: white fails on the pale buckets (2.29:1), dark ink passes."""
    return "#0d1117" if -1 < chg < 2 and not (-0.05 <= chg < 0.05) else "#ffffff"


def _hm_color(chg):
    """Discrete red→gray→green bucket for a %change, matching the nepsetrading legend."""
    if chg <= -2:   return "#8b1a1a"
    if chg <= -1:   return "#c0392b"
    if chg < -0.05: return "#e0736a"
    if chg <= 0.05: return "#5b6472"
    if chg < 1:     return "#5bbf7a"
    if chg < 2:     return "#27ae60"
    return "#1c8b45"


def heatmap_legend():
    """Compact horizontal colour-bucket legend (shown on every heatmap view)."""
    chips = "".join(
        f"<span style='display:inline-flex;align-items:center;gap:4px;margin-right:9px'>"
        f"<span style='width:13px;height:13px;border-radius:4px;background:{c};display:inline-block'></span>"
        f"<span style='color:#8a93a5;font-size:0.74em'>{lab}</span></span>" for lab, c in _HM_BUCKETS)
    st.markdown(f"<div style='display:flex;flex-wrap:wrap;align-items:center;gap:2px'>{chips}</div>",
                unsafe_allow_html=True)


def render_index_ribbon(rows):
    """One-line NEPSE + sector-index pills — the 'indices below' strip under the heatmap."""
    if not rows:
        return
    pills = []
    for r in rows:
        chg, _, _ = _idx_change(r)
        col = "#3fb950" if chg >= 0 else "#f85149"
        pills.append(
            "<span style='background:#161b22;border:1px solid #2b3240;border-radius:14px;"
            "padding:2px 10px;font-size:0.78em;white-space:nowrap'>"
            f"<b>{r.get('ticker')}</b> <span style='color:{col}'>{chg:+.2f}%</span></span>")
    st.markdown(f"<div style='display:flex;flex-wrap:wrap;gap:5px;padding-top:6px'>{''.join(pills)}</div>",
                unsafe_allow_html=True)


# Sector → NEPSE sector-index ticker, so each sector tile is coloured by its official index move.
_SECTOR_INDEX = {
    "Hydro Power": "HYDPOWIND", "Commercial Banks": "BANKSUBIND",
    "Manufacturing And Processing": "MANPROCIND", "Microfinance": "MICRFININD",
    "Investment": "INVIDX", "Life Insurance": "LIFINSIND", "Non-Life Insurance": "NONLIFIND",
    "Hotels And Tourism": "HOTELIND", "Others": "OTHERSIND", "Development Banks": "DEVBANKIND",
    "Finance": "FININD", "Tradings": "TRADIND",
}


def render_stock_heatmap(rows):
    """Sector-grouped NEPSE heatmap that DRILLS: the first view is NEPSE + the sector tiles
    (coloured by each sector's index move); click a sector to reveal its symbols; symbols are
    leaves (no further click). Symbol tiles are sized by turnover, coloured by today's %change."""
    if not rows:
        em, _pw = naasa.load_credentials()
        st.info("Sign in under 🔐 NAASA account (with Remember me) to load the heatmap."
                if not em else
                "Signed in, but NAASA returned no sector quotes just now. The feed may be "
                "between updates — this retries every 20s. Indices below are unaffected.")
        return
    idx_chg = {r.get("ticker"): _idx_change(r)[0] for r in indices_snapshot()}
    sectors = sorted({r["sector"] for r in rows})
    sec_size = {s: 0.0 for s in sectors}
    for r in rows:
        r["_size"] = max(r["turnover"], 1e5)              # floor so untraded scrips stay visible
        sec_size[r["sector"]] += r["_size"]

    def sector_chg(s):
        tkr = _SECTOR_INDEX.get(s)
        if tkr and tkr in idx_chg:
            return idx_chg[tkr]                           # official sector-index move
        tot = sec_size[s] or 1                            # else turnover-weighted average
        return sum(r["chg"] * r["_size"] for r in rows if r["sector"] == s) / tot
    sec_chg = {s: sector_chg(s) for s in sectors}
    nepse_chg = idx_chg.get("NEPSE", 0.0)
    # Compress sector areas with a square root so a heavy sector (Hydro Power's 111 scrips) can't
    # dwarf the rest — every sector stays clearly visible. Stocks stay proportional to turnover
    # WITHIN their sector (value = size / √sector_total → each sector sums to √sector_total).
    sec_val = {s: sec_size[s] ** 0.5 for s in sectors}
    for r in rows:
        r["_val"] = r["_size"] / (sec_size[r["sector"]] ** 0.5)
    nepse_size = sorted(sec_val.values())[len(sec_val) // 2] if sec_val else 1.0  # a median-sized tile

    ids = ["idx::NEPSE"] + ["sec::" + s for s in sectors] + ["stk::" + r["stock"] for r in rows]
    labels = ["NEPSE"] + list(sectors) + [r["stock"] for r in rows]
    parents = [""] + [""] * len(sectors) + ["sec::" + r["sector"] for r in rows]
    values = [nepse_size] + [sec_val[s] for s in sectors] + [r["_val"] for r in rows]
    colors = ([_hm_color(nepse_chg)] + [_hm_color(sec_chg[s]) for s in sectors]
              + [_hm_color(r["chg"]) for r in rows])
    text = ([f"<b>NEPSE</b><br>{nepse_chg:+.2f}%"]
            + [f"<b>{s}</b><br>{sec_chg[s]:+.2f}%" for s in sectors]
            + [f"{r['stock']}<br>{r['chg']:+.2f}%" for r in rows])

    fig = go.Figure(go.Treemap(
        ids=ids, labels=labels, parents=parents, values=values, text=text, textinfo="text",
        marker=dict(colors=colors, cornerradius=6, line=dict(width=2, color="#0d1117")),
        branchvalues="total", maxdepth=1, tiling=dict(pad=3), sort=True,
        pathbar=dict(visible=False),          # no empty breadcrumb strip; click a sector's header to go back
        hovertemplate="%{label}<extra></extra>",
        textfont=dict(size=13, color=([_hm_ink(nepse_chg)]
                                      + [_hm_ink(sec_chg[s]) for s in sectors]
                                      + [_hm_ink(r["chg"]) for r in rows])),
        textposition="middle center"))
    fig.update_layout(margin=dict(t=0, l=0, r=0, b=0), uirevision="stock-heatmap", height=HM_H,
                      transition=dict(duration=350, easing="cubic-in-out"),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    # key= keeps ONE component across reruns: Plotly then animates tiles to their new
    # sizes and colours instead of the container going black while a fresh chart mounts.
    st.plotly_chart(fig, key="hm_stock", width="stretch",
                    config={"displayModeBar": False, "responsive": True})


def _first_table(payload):
    """The first list-of-dicts found anywhere in a JSON response — the rows to display. The
    report endpoint's exact envelope is not documented, so pull the rows out wherever they sit."""
    if isinstance(payload, list):
        return payload if payload and isinstance(payload[0], dict) else []
    if isinstance(payload, dict):
        for v in payload.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
        for v in payload.values():
            found = _first_table(v)
            if found:
                return found
    return []


def naasa_authed(call):
    """Run call(cookie) against NAASA; on a superseded-session 400 ('Missing sessionNo'),
    re-login once when the password is remembered and retry, so our login reclaims the active
    session. Raises if it cannot recover."""
    cookie = st.session_state.get("naasa_cookie")
    try:
        return call(cookie)
    except Exception as e:
        em, pw = naasa.load_credentials()
        if em and pw and ("sessionNo" in str(e) or str(e).startswith("400")):
            cookie = naasa.login_password(em, pw)          # our login becomes the active session
            naasa.save_session(cookie)
            st.session_state["naasa_cookie"] = cookie
            return call(cookie)
        raise


def _dash_fields(payload):
    """Flatten dashboard-details `Data` (a list of one-row groups) into one {field: value},
    so any metric can be looked up by name regardless of which group it sits in."""
    out = {}
    for group in (payload.get("Data") or []):
        row = group[0] if isinstance(group, list) and group else (group if isinstance(group, dict) else None)
        if isinstance(row, dict):
            out.update(row)
    return out


def spike_screen():
    """Rows of Master_data/volume_spike.txt, or [] when volume_spike.py has not run."""
    path = MASTER / "volume_spike.txt"
    if not path.exists():
        return [], ""
    return read_spike_screen(str(path), path.stat().st_mtime)


@st.cache_data(show_spinner=False)
def read_spike_screen(path, mtime):
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    if len(lines) < 2:
        return [], ""
    cols = lines[0].split("\t")
    rows = [dict(zip(cols, l.split("\t"))) for l in lines[1:] if l.strip()]
    return rows, max((r["date"] for r in rows), default="")


def run_job(label, script, *args):
    """Run one fetch script and show its tail. Blocking on purpose — these are manual. While the
    subprocess runs, a sliding-bar animation + pulsing label show the job is working (the CSS
    animates client-side even though the Streamlit script is blocked here)."""
    with st.status(f"{label} — running", expanded=True) as box:
        anim = st.empty()
        anim.markdown(f"<div class='cron-run-lbl'>⏳ Running <b>{label}</b></div>"
                      "<div class='cron-run'></div>", unsafe_allow_html=True)
        started = time.time()
        try:
            p = subprocess.run([sys.executable, script, *args], cwd=Path(__file__).parent,
                               capture_output=True, text=True, timeout=7200)
        except (OSError, subprocess.TimeoutExpired) as e:
            anim.empty()
            box.update(label=f"{label} — failed: {type(e).__name__}", state="error")
            return
        anim.empty()                   # stop the animation once the job returns
        tail = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()[-12:]
        st.code(chr(10).join(tail) or "(no output)")
        ok = p.returncode == 0
        box.update(label=f"{label} — {'done' if ok else 'FAILED'} in {time.time() - started:,.0f}s",
                   state="complete" if ok else "error")
    st.cache_data.clear()          # the scanner and floorsheet readers cache by argument only


def update_service_running():
    """Is the scheduled systemd job (chukul-update.service) fetching right now? `systemctl is-active`
    needs no sudo. Returns (running, state); False anywhere systemctl is absent (e.g. local dev)."""
    try:
        r = subprocess.run(["systemctl", "is-active", "chukul-update.service"],
                           capture_output=True, text=True, timeout=5)
        state = r.stdout.strip()
        return state in ("active", "activating", "reloading"), state
    except Exception:
        return False, ""


# ---- in-app cron: three schedulable jobs, times editable in the UI, auto-run at NPT ------------
# Daily pipeline = only the INCREMENTAL work. fetch_last_session.py already does today's session
# fully (daily bars + floorsheet + minutes via fetch_minutes_today + broker-flow upsert), so the
# full-history backfills (fetch_ohlc/fetch_intraday/build_broker_flow) are NOT here — they re-walk
# the whole archive (~26 min) for no daily benefit and live in the Historical-backfills expander.
CRON_JOBS = {
    "symbols": {"label": "Last Traded Data (symbols → today: bars · floorsheet · minutes · broker · signals)",
                # fetch_symbols first: pull the live NEPSE symbol+index list so newly-listed stocks
                # are picked up — fetch_last_session then reads that list and upsert_daily makes each
                # new symbol's folder automatically (no manual paste). If nothing new, the list is
                # just rewritten unchanged and it moves on.
                "scripts": ["fetch_symbols.py", "fetch_last_session.py", "scan.py",
                            "volume_spike.py"]},
    "swp":     {"label": "SmartWealthPro → Operator",   # runs last, so operator has fresh inputs
                "scripts": ["fetch_swp.py", "operator_scan.py"]},
    # Its own node rather than a fifth script on the symbols job: disk-only and fast, but it
    # reads the session that job just fetched AND trade_setup's trend verdict, so it has to come
    # after both — and a separate node means a failure here is visible instead of buried.
    "supply":  {"label": "Supply Demand Dashboard (zones · confirmed entries)",
                "scripts": ["supply_demand.py"]},
    # after fetch_swp, because the fundamental-quality part of its rubric reads fundamentals.txt
    "swing":   {"label": "Swing Trader Pro (22-section 1D framework)",
                "scripts": ["swing_pro.py"]},
}

# Full-archive backfills — a SEPARATE section, run on demand (each re-walks ALL history, slow). NOT
# in the daily pipeline (fetch_last_session already keeps today current).
HIST_JOBS = {
    "chart_hist": {"label": "Chart history — all daily + minute bars",
                   "scripts": ["fetch_ohlc.py", "fetch_intraday.py"]},
    "floor_hist": {"label": "Floorsheet history — all dates + broker flow",
                   "scripts": ["fetch_floorsheet_merolagani.py", "build_broker_flow.py"]},
}
_DEFAULT_MASTER = ["15:15"]                     # one master time fires the WHOLE pipeline
CRON_FILE = MASTER / "cron_schedule.txt"
CRON_RAN_FILE = MASTER / "cron_ran.txt"        # today's fired keys, so restarts don't re-fire
CRON_CATCHUP_MIN = 30                           # fire at the time, or catch up within this many min


def load_master_times():
    """Master run times (HH:MM NPT), each firing the whole pipeline. Plain .txt, `master<TAB>HH:MM`
    (bare `HH:MM` lines also accepted). Falls back to the default the first time."""
    if not CRON_FILE.exists():
        return list(_DEFAULT_MASTER)
    out = []
    for line in CRON_FILE.read_text(encoding="utf-8").splitlines():
        p = line.strip().split("\t")
        t = p[1] if len(p) == 2 and p[0] == "master" else line.strip()
        if re.match(r"^\d{2}:\d{2}$", t):
            out.append(t)
    return out


def save_master_times(times):
    CRON_FILE.parent.mkdir(parents=True, exist_ok=True)
    CRON_FILE.write_text("\n".join(f"master\t{t}" for t in sorted(set(times))) + "\n", encoding="utf-8")


def to_12h(hhmm):
    """'15:15' → '3:15PM' (stored 24h, shown 12h)."""
    return datetime.strptime(hhmm, "%H:%M").strftime("%I:%M%p").lstrip("0")


@st.cache_resource(show_spinner=False)
def cron_scheduler():
    """Server-wide scheduler thread + a SEQUENTIAL pipeline. One master time (or the ▶ Run-all
    button) fires the whole pipeline, which runs the jobs strictly one-by-one — a job's next script
    starts only after the previous finished, and the next JOB starts only after the previous job is
    done. Per-job status (idle/pending/running/done/failed) drives the colour highlights. Fired keys
    persist to a .txt so a redeploy doesn't re-fire. Skips Fri/Sat. Survives Streamlit reruns."""
    def load_ran():
        try:
            return set(CRON_RAN_FILE.read_text(encoding="utf-8").split())
        except Exception:
            return set()
    state = {"running": None, "status": {j: "idle" for j in CRON_JOBS}, "last": None,
             "log": {}, "ran": load_ran(), "lock": threading.Lock(),
             "hist_status": {j: "idle" for j in HIST_JOBS}, "hist_last": {}}

    def mark_ran(key, today):
        state["ran"].add(key)
        try:                                       # keep only today's keys, so the file stays tiny
            CRON_RAN_FILE.write_text(
                "\n".join(sorted(k for k in state["ran"] if k.endswith(today))) + "\n", "utf-8")
        except Exception:
            pass

    def run_pipeline():
        with state["lock"]:                        # one pipeline at a time
            if state["running"] is not None:
                return
            state["running"] = "start"
            for j in CRON_JOBS:
                state["status"][j] = "pending"
        for job, cfg in CRON_JOBS.items():         # strict one-by-one
            state["running"] = job
            state["status"][job] = "running"
            outs, ok = [], True
            for script in cfg["scripts"]:          # each script fully finishes before the next
                try:
                    p = subprocess.run([sys.executable, script], cwd=Path(__file__).parent,
                                       capture_output=True, text=True, timeout=7200)
                    ok = ok and (p.returncode == 0)
                    outs.append(f"{script} {'ok' if p.returncode == 0 else 'FAIL'}")
                except Exception as e:
                    ok = False
                    outs.append(f"{script} {type(e).__name__}")
            state["status"][job] = "done" if ok else "failed"
            state["log"][job] = " · ".join(outs)
        state["running"] = None
        state["last"] = datetime.now(NPT).strftime("%Y-%m-%d %H:%M")

    state["run"] = run_pipeline                    # exposed so the ▶ Run-all button can start it

    def run_hist(job):                             # one full-archive backfill, in the background
        if state["hist_status"].get(job) == "running":
            return
        state["hist_status"][job] = "running"
        ok = True
        for script in HIST_JOBS[job]["scripts"]:
            try:
                p = subprocess.run([sys.executable, script], cwd=Path(__file__).parent,
                                   capture_output=True, text=True, timeout=36000)
                ok = ok and (p.returncode == 0)
            except Exception:
                ok = False
        state["hist_status"][job] = "done" if ok else "failed"
        state["hist_last"][job] = datetime.now(NPT).strftime("%Y-%m-%d %H:%M")

    state["run_hist"] = run_hist

    def loop():
        while True:
            now = datetime.now(NPT)
            today = now.strftime("%Y-%m-%d")
            weekend = not market_hours.is_trading_day(now)   # "closed today", per the switch
            now_min = now.hour * 60 + now.minute
            for t in load_master_times():
                key = f"master@{t}@{today}"
                try:
                    due = now_min - (int(t[:2]) * 60 + int(t[3:]))
                except ValueError:
                    continue
                if (not weekend and 0 <= due <= CRON_CATCHUP_MIN and key not in state["ran"]
                        and state["running"] is None):
                    mark_ran(key, today)
                    run_pipeline()
            time.sleep(15)

    threading.Thread(target=loop, daemon=True).start()
    return state


def floorsheet_dates(symbol):
    d = FLOORSHEET / symbol.replace("/", "-")
    return read_floorsheet_dates(str(d), dir_stamp(d))


@st.cache_data(show_spinner=False)
def read_floorsheet_dates(path, mtime):
    d = Path(path)
    return sorted((p.stem for p in d.glob("*.txt")), reverse=True) if d.exists() else []


def trades(symbol, date):
    """[(buyer, seller, qty, rate, amount, txn)] for one session."""
    path = FLOORSHEET / symbol.replace("/", "-") / f"{date}.txt"
    if not path.exists():
        return []
    return read_trades(str(path), path.stat().st_mtime)


@st.cache_data(show_spinner=False)
def read_trades(path, mtime):
    path = Path(path)
    out = []
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        b, s, q, r, a, t = line.split("\t")
        out.append((b, s, float(q), float(r), float(a), t))
    return out


def flow_table(symbol):
    """Precomputed broker-days from build_broker_flow.py: {date: {broker: (bought, sold)}}."""
    path = MASTER / "broker_flow" / f"{symbol.replace('/', '-')}.txt"
    if not path.exists():
        return {}
    return read_flow_table(str(path), path.stat().st_mtime)


@st.cache_data(show_spinner=False)
def read_flow_table(path, mtime):
    path = Path(path)
    out = defaultdict(dict)
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        date, broker, bought, sold, *_ = line.split("\t")
        out[date][broker] = (float(bought), float(sold))
    return dict(out)


def broker_flow(symbol, dates):
    """Net quantity per broker across the given sessions: bought - sold."""
    table = flow_table(symbol)
    net, bought, sold = defaultdict(float), defaultdict(float), defaultdict(float)
    for date in dates:
        for broker, (b, s) in table.get(date, {}).items():
            bought[broker] += b
            sold[broker] += s
            net[broker] += b - s
    if not net:  # table not built yet — fall back to the raw trades
        for date in dates:
            for b, s, q, *_ in trades(symbol, date):
                bought[b] += q
                sold[s] += q
                net[b] += q
                net[s] -= q
    return net, bought, sold


# ---------------------------------------------------------------- table colours

GREEN, RED, GREY, GOLD = "#12B886", "#FF6B5B", "#98A2B3", "#F0A202"


def paint(rows, columns=None):
    """Colour a table by meaning: side, outcome, and whether a number is profit or loss."""
    frame = pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns or [])
    if frame.empty:
        return frame

    def side(v):
        return f"color:{GREEN};font-weight:600" if v == "BUY" else (
            f"color:{RED};font-weight:600" if v == "SELL" else "")

    def outcome(v):
        return {"TARGET 2": f"background-color:rgba(18,184,134,.30);color:{GREEN};font-weight:600",
                "TARGET 1": "background-color:rgba(18,184,134,.14)",
                "STOP": f"background-color:rgba(255,107,91,.28);color:{RED};font-weight:600",
                "OPEN": f"color:{GREY}"}.get(v, "")

    def pnl(v):
        try:
            x = float(v)
        except (TypeError, ValueError):
            return ""
        if x > 0:
            return f"color:{GREEN};font-weight:600"
        return f"color:{RED};font-weight:600" if x < 0 else f"color:{GREY}"

    # a Styler prints raw floats (1460.000000) unless told otherwise
    whole = {"volume", "bars_ago", "swing_age", "buyer_net", "seller_net", "bought", "sold",
             "net", "vs_prev", "age", "visits"}
    fmt = {c: ("{:,.0f}" if c in whole else "{:,.2f}")
           for c in frame.columns if pd.api.types.is_numeric_dtype(frame[c])}
    styler = frame.style.format(fmt)
    cell = getattr(styler, "map", None) or styler.applymap  # pandas renamed applymap -> map
    def tint(fn, col):
        nonlocal styler
        styler = (styler.map(fn, subset=[col]) if hasattr(styler, "map")
                  else styler.applymap(fn, subset=[col]))

    def churn(v):
        try:
            x = float(v)
        except (TypeError, ValueError):
            return ""
        # low net_churn is the finding, so it is the one that gets shouted at
        return f"color:{GOLD};font-weight:600" if x <= 0.25 else (f"color:{GREY}" if x >= 0.5 else "")

    def kind(v):
        return {"markup": f"color:{GREEN};font-weight:600",
                "markdown": f"color:{RED};font-weight:600",
                "churn": f"color:{GOLD};font-weight:600"}.get(v, "")

    def flags(v):
        if not isinstance(v, str) or v == "-":
            return f"color:{GREY}"
        return f"color:{GOLD};font-weight:600" if "CHURN" in v else "font-weight:600"

    for col in frame.columns:
        if col == "net_churn":
            tint(churn, col)
        elif col == "kind":
            tint(kind, col)
        elif col == "flags":
            tint(flags, col)
        elif col == "signal":
            tint(side, col)
        elif col == "result":
            tint(outcome, col)
        elif col in ("return_pct", "open_pct", "change_pct", "progress", "net",
                     "vs_prev", "vs_prev_pct", "AD_trend", "price_chg"):
            tint(pnl, col)
        elif col in ("floor", "chart", "position", "in_zone", "confirmed"):
            tint(lambda v: f"color:{GREEN};font-weight:600" if v == "yes"
                 else f"color:{RED}", col)
        elif col in ("target1", "target2", "tp"):
            tint(lambda v: f"color:{GREEN}", col)
        elif col in ("stop", "sl"):
            tint(lambda v: f"color:{RED}", col)
        elif col == "direction":                      # supply/demand board: Bullish vs Bearish
            tint(lambda v: f"color:{GREEN};font-weight:600" if v == "Bullish"
                 else (f"color:{RED};font-weight:600" if v == "Bearish" else ""), col)
        elif col == "grade":
            # NB: match the whole grade, never a prefix — "AVOID" also starts with "A", and for a
            # while it rendered in the green success chip, identical to "A+ ELITE".
            tint(lambda v: (f"background-color:rgba(18,184,134,.22);color:{GREEN};font-weight:700"
                            if str(v).startswith(("A+", "A ")) else
                            f"color:{GOLD};font-weight:600" if str(v).startswith("B") else
                            f"color:{RED};font-weight:600" if str(v) == "AVOID" else
                            f"color:{GREY}"), col)
        elif col == "decision":
            tint(lambda v: f"color:{GREEN};font-weight:700" if v == "BUY"
                 else (f"color:{GOLD};font-weight:600" if str(v).startswith("BUY")
                       else (f"color:{RED};font-weight:600" if v == "AVOID" else f"color:{GREY}")), col),
        elif col == "order":
            tint(lambda v: f"color:{GREEN};font-weight:600" if v == "AT ENTRY"
                 else f"color:{GREY}", col)
        elif col == "zone_history":                   # a fresh zone is the one worth having
            tint(lambda v: {"untested": f"color:{GOLD};font-weight:600",
                            "strong": f"color:{GREEN};font-weight:600",
                            "weak": f"color:{GREY}"}.get(v, ""), col)
        elif col == "strong":
            tint(lambda v: f"background-color:rgba(240,162,2,.18);color:{GOLD};font-weight:600"
                 if v == "yes" else "", col)
    return styler


# ---------------------------------------------------------------- sidebar

names = universe()
st.sidebar.title("NEPSE archive")
PAGES = ["Chart", "Floorsheet", "Broker flow", "Scanner",
         "Volume spike", "Operator radar", "Master operator", "Swing master", "Master signal",
         "Backtest", "NAASA", "Heatmap", "Swing Trader Pro", "Supply Demand", "Cron"]
# Persist the current page in the URL (?page=…) so a refresh / hard refresh / redeploy keeps you
# where you are — a browser reload starts a fresh Streamlit session, so session_state alone resets.
_qp_page = st.query_params.get("page")
page = st.sidebar.radio("Menu", PAGES, index=PAGES.index(_qp_page) if _qp_page in PAGES else 0,
                        label_visibility="collapsed", key="nav")

# One freshness line for the WHOLE app. `_stale` only compares the stores to EACH OTHER, and one
# pipeline fills all of them — so a whole-pipeline stall freezes them in lockstep and that check
# stays empty forever. It printed a green "archive current" over two-day-old prices. State the
# AGE against the NPT calendar instead: a fact the trader reads, not a verdict the app gets wrong.
# NPT, not the system clock — the VPS runs UTC and is a day out for ~6h daily.
_state = archive_state(file_stamp(MASTER / "scan.txt"))
_session = max((v[0][:10] for v in _state.values() if v[0]), default="")
_stale = [k for k, (stamp, _b, _t) in _state.items() if stamp and stamp[:10] != _session]
def _missed_sessions(newest):
    """NEPSE trades Sun-Thu, so calendar age lies: Thursday data read on Sunday is 3 days old and
    perfectly current, while Tuesday data read on Thursday has missed a real session. Count the
    trading days that have passed since `newest` instead. Holidays only ever inflate this, which
    is why 1 missed session is still treated as fine — today's 15:15 job may simply not have run."""
    try:
        d = date_cls.fromisoformat(newest)
    except ValueError:
        return None
    today, n = datetime.now(NPT).date(), 0
    while d < today:
        d += timedelta(days=1)
        if market_hours.is_trading_day(d):    # only days the market actually opens count
            n += 1
    return n


_missed = _missed_sessions(_session) if len(_session) == 10 else None
if _session:
    _ok = _missed is not None and _missed <= 1 and not _stale
    st.sidebar.caption(
        (f"🟢 archive · **{_session}**" if _ok else f"🟠 archive · **{_session}**")
        + ("" if not _missed else
           f" · {_missed} NEPSE session{'s' if _missed > 1 else ''} behind")
        + (f" · stores behind: {', '.join(_stale)}" if _stale else ""))
if st.query_params.get("page") != page:
    st.query_params["page"] = page
st.sidebar.divider()

# Indicators panel removed — Personal indicators drive the chart. These stay as the
# defaults the drawing code reads.
overlays = []
oscillator = "None"

with st.sidebar.expander("🧠  Personal indicators (chart + Scanner)", expanded=False):
    st.caption("SMC — ported from Pine, computed in Python. The sensitivities below feed the "
               "Scanner and Backtest too, not just the chart drawing.")

    # The sliders render unconditionally on purpose. They used to be `... if <checkbox> else
    # <default>`, and a widget Streamlit does not render has its state DELETED — so unticking a
    # box silently reset the sensitivity that scan.py and the Backtest equity curve are computed
    # from, changing which symbols came back. The checkboxes still gate the DRAWING only.
    smc_wave = st.checkbox("Trend wave (ALMA)", value=False)
    wave_len = st.slider("Wave period", 5, 100, 21, key="wave_len")

    smc_struct = st.checkbox("Structure BOS / CHoCH", value=False)
    smc_sens = st.slider("Structure sensitivity", 2, 30, 7, key="smc_sens")

    smc_badges = st.checkbox("Swing BUY / SELL badges", value=False)
    sig_sens = st.slider("Swing sensitivity", 3, 50, 10, key="sig_sens")
    st.caption("⚠ a swing is only confirmed this many bars later — historical marks, not live calls")

    smc_sd = st.checkbox("−2.5 SD target", value=False)
    sd_len = st.slider("SD period", 5, 100, 20, key="sd_len")

st.sidebar.caption(f"{len(names)} instruments · {sum(1 for v in names.values() if v == 'indices')} indices")

# ------------------------------------------------- resolution bar, above the chart
# TradingView keeps the timeframe on a toolbar over the chart rather than in a panel.
TF_LABEL = {"1D": "1D", "1minutes": "1m"}
WINDOW_LABEL = {"3d": "3 days", "2w": "2 weeks", "1m": "1 month"}
tf_choices, tf_default = ["1D", "1minutes"], "1D"
if st.session_state.get("tf") not in tf_choices:
    st.session_state["tf"] = tf_default
# Only the pages that are ABOUT one instrument get the picker and the price header. The
# whole-market pages (Scanner, Volume spike, Cron) used to inherit them, which read as though
# their tables were showing that one symbol.
PER_SYMBOL = {"Chart", "Floorsheet", "Broker flow"}
symbol, timeframe, data = None, "1D", None

if page in PER_SYMBOL:
    if page == "Chart":
        bar_sym, bar_tf = st.columns([1, 5], vertical_alignment="center")
        timeframe = bar_tf.segmented_control("Timeframe", tf_choices, key="tf",
                                             label_visibility="collapsed",
                                             format_func=lambda r: TF_LABEL.get(r, r)) or tf_default
    else:
        bar_sym = st.columns([1, 5])[0]
    # Options stay bare tickers on purpose: Streamlit filters the box with a *fuzzy
    # subsequence* match, so putting company names in the label made "nabil" match "Nepal SBI
    # Bank Limited" and half the Laghubittas. The name is on the header line right below.
    opts = sorted(names)
    _qp_sym = st.query_params.get("sym")
    _seed = _qp_sym if _qp_sym in opts else ("NABIL" if "NABIL" in opts else opts[0])
    symbol = bar_sym.selectbox("Symbol", opts, label_visibility="collapsed",
                               placeholder="Search symbol", index=opts.index(_seed))
    if st.query_params.get("sym") != symbol:      # mirror it, same as ?page= above
        st.query_params["sym"] = symbol

    data = bars(symbol, timeframe, None)
    if timeframe == "1D":
        data = with_live_bar(data, symbol)       # today's candle at socket speed
    if not data:
        st.error(f"No {timeframe} file for {symbol} — run fetch_ohlc.py / fetch_intraday.py first.")
        st.stop()

    last, prev = data["close"][-1], (data["close"][-2] if len(data["close"]) > 1 else data["close"][-1])
    change = last - prev
    name = instrument_names().get(symbol.replace("/", "-"), "")

    head, right = st.columns([3, 2])
    head.markdown(f"<div class='page-title'>{symbol} <span class='muted'>{name}</span></div>"
                  f"<div class='page-price'>{last:,.2f} <span class='unit'>NPR</span></div>",
                  unsafe_allow_html=True)
    right.markdown(f"<div class='pill {'up' if change >= 0 else 'down'}'>"
                   f"{change:+,.2f} ({(change / prev * 100 if prev else 0):+.2f}%)</div>",
                   unsafe_allow_html=True)



# ---------------------------------------------------------------- NAASA account
#
# Permanent login. The app API authorizes with a NextAuth session cookie (the Keycloak bearer
# token is rejected there). There is no password API — the realm disables direct grant — so
# naasa.login_password() drives the same browser OAuth flow a human does, minting that cookie
# from an email + password. The cookie is saved to naasa_session.txt (~30-day life); when the
# user opts to remember the password it also goes to naasa_login.txt (plain text, gitignored,
# local only), which lets startup re-authenticate silently once the cookie lapses. Both files
# live under Master_data/ (already gitignored). Only read endpoints are called; no order placed.
def _naasa_auto_login():
    """Populate session_state['naasa_who'] once per session — STICKY. As long as the password is
    remembered, the user stays "signed in" (identity from the live session, else the saved email)
    and the UI never drops back to the login form on its own — only an explicit Sign out does that.
    The real session is refreshed lazily by naasa_authed() when data is actually fetched."""
    st.session_state["naasa_who"] = None
    st.session_state["naasa_cookie"] = naasa.load_session()
    if st.session_state["naasa_cookie"]:
        try:
            st.session_state["naasa_who"] = naasa.session_info(st.session_state["naasa_cookie"])["user"]
        except Exception:
            st.session_state["naasa_who"] = None
    if not st.session_state["naasa_who"]:                      # cookie missing/stale
        em, _ = naasa.load_credentials()
        if em:                                                 # remembered → stay signed in
            st.session_state["naasa_who"] = {"email": em}

if "naasa_who" not in st.session_state:
    _naasa_auto_login()

# -- Trading days ------------------------------------------------------------------------------
# The single switch for "is the market open". Which weekdays trade used to be hardcoded as
# `weekday() in (4, 5)` in three separate places, so a schedule change meant finding all three.
# Everything reads market_hours now: the session banner, the cron scheduler's is-anything-due
# check, the archive-freshness counter and the live socket. It lives in the sidebar rather than
# on one page because it governs all of them — and it can only live in ONE place, since a second
# copy would collide on the widget keys.
with st.sidebar.expander("📅  Trading days", expanded=False):
    _open_now = market_hours.open_days()
    _pills = st.columns(7)
    for _i, (_wd, _lbl) in enumerate(market_hours.WEEK):
        _is_open = _wd in _open_now
        if _pills[_i].button(_lbl[0], key=f"mh_day_{_wd}", width="stretch",
                             type="primary" if _is_open else "secondary",
                             help=f"{_lbl} — " + ("open, click to close" if _is_open
                                                  else "closed, click to open")):
            market_hours.toggle_day(_wd)
            st.rerun()
    _n_open, _n_tot, _feed_armed = market_hours.summary()
    if _n_open:
        st.caption(f"🟢 **{_n_open} / {_n_tot} days open** — honoured by every cron, by the "
                   "session banner and by the live feed.")
    else:
        st.caption("🔴 **Every day closed** — no cron will fire, the banner reads CLOSED and the "
                   "live feed stays off until a day is open.")

    _want_feed = st.toggle(
        "NAASA live feed (WSS)", value=_feed_armed, key="mh_feed",
        help="Master switch for the websocket. Off stops the live ladder, the live prices on the "
             "account panels, and the live 1D archive updater — the archive then only moves when "
             "the daily fetch runs.")
    if _want_feed != _feed_armed:
        market_hours.set_feed_on(_want_feed)
        st.rerun()
    st.caption("🟢 Feed armed — runs while the market is open on the days above." if _want_feed
               else "⚪ Feed off — no socket, no live prices, today's 1D bar stops updating.")


with st.sidebar.expander("🔐  NAASA account", expanded=False):
    who = st.session_state.get("naasa_who")
    if who:
        st.success(f"Signed in as {who.get('name') or who.get('email') or 'NAASA user'}")
        remembered = naasa.CREDS_FILE.exists()
        st.caption(f"Session in `{naasa.SESSION_FILE.name}`"
                   + (f" · password remembered in `{naasa.CREDS_FILE.name}` (auto re-login)"
                      if remembered else " · stays logged in across restarts."))
        tc, sc = st.columns(2)
        if tc.button("🔍 Test connection", key="naasa_test"):
            em, pw = naasa.load_credentials()
            if not (em and pw):
                st.error("✗ No saved password — sign in with “Remember me” so the live API can authenticate.")
            else:
                try:
                    n = len(naasa.x_orderbook(em, pw))
                    st.success(f"✓ NAASA live API works — order book returned {n} order(s).")
                except Exception as e:
                    st.error(f"✗ NAASA live API failed — {str(e)[:110]}")
        if sc.button("Sign out", key="naasa_out"):
            naasa.clear_session()
            naasa.clear_credentials()
            naasa_feed()["want"] = False          # stop the socket on explicit logout
            for k in ("naasa_cookie", "naasa_who"):
                st.session_state.pop(k, None)
            st.rerun()
    else:
        st.caption("Sign in with your NAASA email and password. Only read endpoints are used — "
                   "this dashboard never places an order.")
        email = st.text_input("NAASA email", key="naasa_email", placeholder="you@example.com")
        pw = st.text_input("Password", type="password", key="naasa_pw")
        remember = st.checkbox("Remember me on this PC (auto-login)", value=True, key="naasa_remember",
                               help="Saves your password in plain text in Master_data/naasa_login.txt "
                                    "(gitignored, local only) so the app can re-login on its own.")
        if st.button("Log in", key="naasa_login") and email and pw:
            try:
                with st.spinner("Signing in to NAASA…"):
                    cookie = naasa.login_password(email.strip(), pw)
                    who = naasa.session_info(cookie)["user"]
                naasa.save_session(cookie)
                if remember:
                    naasa.save_credentials(email.strip(), pw)
                    naasa_feed()["want"] = True      # re-arm the always-on socket
                else:
                    naasa.clear_credentials()
                st.session_state["naasa_cookie"] = cookie
                st.session_state["naasa_who"] = who
                st.rerun()
            except Exception as e:
                st.error(f"Login failed: {str(e)[:200]}")

# ---------------------------------------------------------------- SmartWealthPro account
# Same permanent-login model as NAASA: the app POSTs the user's own email+password to SWP's
# ASP.NET login (anti-forgery token handled), keeps the HttpOnly auth cookie in a Python cookie
# jar (fine — HttpOnly only blocks browser JS), saves it to swp_session.txt and (opt-in) the
# password to swp_login.txt for silent re-login. Gives the app SWP's corporate-action / auction /
# lock-in JSON API.
if "swp_who" not in st.session_state:
    _ck = swp.load_session()
    _who = None
    if _ck:
        try:
            _who = swp.session_info(_ck)["companies"]
        except Exception:
            _who = None
    if _who is None:                                   # cookie missing/stale → try remembered creds
        _em, _pw = swp.load_credentials()
        if _em and _pw:
            try:
                _ck = swp.login_password(_em, _pw)
                swp.save_session(_ck)
                _who = swp.session_info(_ck)["companies"]
            except Exception:
                _who = None
    st.session_state["swp_cookie"] = _ck
    st.session_state["swp_who"] = _who

with st.sidebar.expander("💼  SmartWealthPro", expanded=False):
    swp_who = st.session_state.get("swp_who")
    swp_cookie = st.session_state.get("swp_cookie")
    if swp_who:
        st.success(f"✓ Connected — {swp_who} companies indexed")
        st.caption(f"Session in `{swp.SESSION_FILE.name}`"
                   + (" · password remembered (auto re-login)" if swp.CREDS_FILE.exists() else "."))
        tc, sc = st.columns(2)
        if tc.button("🔍 Test", key="swp_test"):
            try:
                d = swp.dividends(swp_cookie, items=5)
                st.success(f"✓ API works — {len(d)} recent corporate action(s)")
            except Exception as e:
                st.error(f"✗ {str(e)[:80]}")
        if sc.button("Sign out", key="swp_out"):
            swp.clear_session()
            swp.clear_credentials()
            for k in ("swp_cookie", "swp_who"):
                st.session_state.pop(k, None)
            st.rerun()
        # a small peek at the data — the nearest book-closures
        try:
            rows = swp.dividends(swp_cookie, items=6)
            if rows:
                items = "".join(
                    f"<tr><td style='padding:1px 4px;color:#e6e8ec'>{r.get('symbol') or r.get('stockSymbol') or ''}</td>"
                    f"<td style='padding:1px 4px;text-align:right;color:#8a93a5'>{r.get('bookClosureDateAD') or ''}</td></tr>"
                    for r in rows[:6])
                st.markdown("<div style='font-size:.72rem;font-weight:600;color:#8a93a5;margin-top:4px'>"
                            "Recent corporate actions</div>"
                            f"<table style='width:100%;font-size:.72rem'>{items}</table>",
                            unsafe_allow_html=True)
        except Exception:
            pass
    else:
        st.caption("Sign in with your SmartWealthPro email and password for corporate actions, "
                   "auctions and lock-in data. Only read endpoints are used.")
        em = st.text_input("SWP email", key="swp_email", placeholder="you@example.com")
        pw = st.text_input("Password", type="password", key="swp_pw")
        remember = st.checkbox("Remember me on this PC (auto-login)", value=True, key="swp_remember")
        if st.button("Log in", key="swp_login") and em and pw:
            try:
                with st.spinner("Signing in to SmartWealthPro…"):
                    cookie = swp.login_password(em.strip(), pw)
                    who = swp.session_info(cookie)["companies"]
                swp.save_session(cookie)
                if remember:
                    swp.save_credentials(em.strip(), pw)
                else:
                    swp.clear_credentials()
                st.session_state["swp_cookie"] = cookie
                st.session_state["swp_who"] = who
                st.rerun()
            except Exception as e:
                st.error(f"Login failed: {str(e)[:200]}")

# ---------------------------------------------------------------- live quote strip (always on)
#
# No toggle — always show a live price strip on the per-symbol pages. Prefer the NAASA socket
# feed (sub-second) when it's holding this symbol; otherwise the public REST quote.
if page in PER_SYMBOL and symbol:
    @st.fragment(run_every=1)
    def live_quote(sym=symbol):
        def n(x):
            try:
                return f"{float(x):,.2f}"
            except (TypeError, ValueError):
                return "–"
        with naasa_feed()["lock"]:
            sk = dict(naasa_feed()["snap"].get(sym, {}))
        if sk.get("LTP"):                                   # live off the socket
            a, b, c, d, e = st.columns(5)
            a.metric("LTP", n(sk.get("LTP")))
            b.metric("Bid", n(sk.get("BidPrice")))
            c.metric("Ask", n(sk.get("OfferPrice")))
            d.metric("Volume", n(sk.get("TTQ")).split(".")[0])
            e.metric("Avg price", n(sk.get("WeightedAverage")))
            st.caption(f"🔴 Live from NAASA socket · last tick {sk.get('_t', '')}")
            return
        q = live_snapshot(sym)                              # public REST fallback
        if q.get("lp"):
            a, b, c, d, e = st.columns(5)
            a.metric("LTP", f"{q['lp']:,.2f}", f"{q.get('chp') or 0:+.2f}%")
            b.metric("Bid", f"{q.get('bid') or 0:,.2f}")
            c.metric("Ask", f"{q.get('ask') or 0:,.2f}")
            d.metric("Volume", f"{q.get('volume') or 0:,.0f}")
            e.metric("Prev close", f"{q.get('prev_close') or 0:,.2f}")
            st.caption("Public quote (cached 1s) — go live on the NAASA page for the socket feed.")
    live_quote()



# ---------------------------------------------------------------- TradingView
#
# NAASA runs a licensed TradingView Advanced Charting Library over the same public NEPSE
# datafeed we use, and serves it without X-Frame-Options or a frame-ancestors CSP — so it
# can be embedded directly. This is the real charting library, drawing tools and all;
# copying their library files instead would be using someone else's licence.

# ---------------------------------------------------------------- chart

VIEW_BARS = 120      # candles visible when the chart first opens
SCAN_BARS = 500      # history each scanner pass reads per symbol


def hidden_periods(stamps, intraday):
    """Rangebreaks that hide the time the market was shut, so the series has no gaps.

    Everything between the first and last bar that is NOT in the data is a break: holidays,
    weekends, and — intraday — the hours outside the 11:00-15:00 session.
    """
    days = sorted({s[:10] for s in stamps})
    start, end = date_cls.fromisoformat(days[0]), date_cls.fromisoformat(days[-1])
    traded, missing, d = set(days), [], start
    while d <= end:
        if d.isoformat() not in traded:
            missing.append(d.isoformat())
        d += timedelta(days=1)
    breaks = [dict(values=missing)] if missing else []
    if intraday:
        breaks.append(dict(bounds=[15, 10.75], pattern="hour"))  # NEPSE trades 10:45-15:00 NPT
    return breaks


def build_chart(data, title, timeframe, lock_price=False, chart_px=None):
    """The price/volume/oscillator figure, shared by the Chart tab and the Scanner preview."""
    x, close = data["when"], data["close"]
    intraday = timeframe not in ("1D", "D", "W", "M")
    breaks = hidden_periods(x, intraday)
    show_osc = oscillator != "None"
    # Volume sits *inside* the price pane on its own hidden axis, the way TradingView
    # draws it — the bars touch the candles instead of living in a block of their own.
    rows = 2 if show_osc else 1
    heights = [0.76, 0.24] if show_osc else [1.0]
    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.05,
                        row_heights=heights,
                        specs=[[{"secondary_y": True}]] + [[{"secondary_y": False}]] * (rows - 1))

    fig.add_trace(go.Candlestick(
        x=x, open=data["open"], high=data["high"], low=data["low"], close=close, name=title,
        showlegend=False,
        increasing_line_color="#12B886", decreasing_line_color="#FF6B5B",
        increasing_fillcolor="#12B886", decreasing_fillcolor="#FF6B5B",
    ), row=1, col=1)

    line = dict(EMA9=("EMA 9", ema, 9, "#3B82F6"), EMA21=("EMA 21", ema, 21, "#ff9800"),
                SMA50=("SMA 50", sma, 50, "#8B5CF6"), SMA200=("SMA 200", sma, 200, "#98A2B3"))
    for key, (label, fn, n, color) in line.items():
        if label in overlays:
            fig.add_trace(go.Scatter(x=x, y=fn(close, n), name=label, line=dict(color=color, width=1.4)), row=1, col=1)
    if "Bollinger 20" in overlays:
        mid, up, lo = bollinger(close)
        for series, nm, dash in ((up, "BB upper", "dot"), (mid, "BB basis", "solid"), (lo, "BB lower", "dot")):
            fig.add_trace(go.Scatter(x=x, y=series, name=nm, line=dict(color="#93A4C1", width=1, dash=dash)),
                          row=1, col=1)

    # --- SMC block, ported from the Pine indicator ---------------------------------
    if smc_wave:
        wave = alma(close, wave_len)
        up = [w if w is not None and c >= w else None for w, c in zip(wave, close)]
        dn = [w if w is not None and c < w else None for w, c in zip(wave, close)]
        fig.add_trace(go.Scatter(x=x, y=close, line=dict(width=0), showlegend=False,
                                 hoverinfo="skip"), row=1, col=1)
        fig.add_trace(go.Scatter(x=x, y=up, name="Wave ↑", line=dict(color="#12B886", width=2),
                                 fill="tonexty", fillcolor="rgba(0,230,118,.08)"), row=1, col=1)
        fig.add_trace(go.Scatter(x=x, y=close, line=dict(width=0), showlegend=False,
                                 hoverinfo="skip"), row=1, col=1)
        fig.add_trace(go.Scatter(x=x, y=dn, name="Wave ↓", line=dict(color="#FF6B5B", width=2),
                                 fill="tonexty", fillcolor="rgba(255,23,68,.08)"), row=1, col=1)

    if smc_struct or smc_badges:
        ph, pl = pivots(data["high"], data["low"], smc_sens, smc_sens)
        pb, plb = pivots(data["high"], data["low"], sig_sens, sig_sens)

    if smc_struct:
        for i, level, kind, way in structure(close, ph, pl):
            color = "#8B5CF6" if kind == "CHoCH" else "#3B82F6"
            fig.add_shape(type="line", x0=x[max(i - 5, 0)], x1=x[i], y0=level, y1=level,
                          line=dict(color=color, width=1, dash="dash"), row=1, col=1)
            fig.add_annotation(x=x[i], y=level, text=kind, showarrow=False, font=dict(color=color, size=10),
                               yshift=12 if way == "up" else -12, row=1, col=1)

    if smc_badges:
        span = (max(data["high"]) - min(data["low"])) * 0.012
        for i, level in enumerate(pb):
            if level is not None:
                fig.add_annotation(x=x[i], y=level + span, text="SELL", showarrow=False,
                                   font=dict(color="#ffffff", size=9), bgcolor="#FF6B5B", row=1, col=1)
        for i, level in enumerate(plb):
            if level is not None:
                fig.add_annotation(x=x[i], y=level - span, text="BUY", showarrow=False,
                                   font=dict(color="#ffffff", size=9), bgcolor="#12B886", row=1, col=1)

    if smc_badges:
        # The badges only print at pivots, so the newest one can be far behind the last
        # candle. State it explicitly on the right edge — this is the same value the
        # Scanner reports, so chart and list always agree.
        active, since_i = "", None
        for i in range(len(close) - 1, -1, -1):
            if pb[i] is not None or plb[i] is not None:
                active, since_i = ("SELL" if pb[i] is not None else "BUY"), i
                break
        if active:
            fig.add_annotation(
                x=x[-1], y=close[-1], text=f"  {active} since {x[since_i][:10]}  ",
                showarrow=False, xanchor="left", font=dict(color="#ffffff", size=11),
                bgcolor="#12B886" if active == "BUY" else "#FF6B5B", row=1, col=1)

        if active and since_i is not None:
            pivot_px = pb[since_i] if pb[since_i] is not None else plb[since_i]
            atr_now = next((v for v in reversed(atr(data["high"], data["low"], close)) if v is not None), None)
            stop, t1, t2, _risk = trade_levels(active, close[-1], pivot_px, atr_now)
            for level, label, colour in ((stop, "Stop", "#FF6B5B"), (t1, "Target 1", "#12B886"),
                                         (t2, "Target 2", "#12B886")):
                if level:
                    fig.add_hline(y=level, line=dict(color=colour, width=1, dash="dot"), row=1, col=1,
                                  annotation_text=f"{label} {level:,.2f}", annotation_position="left",
                                  annotation_font=dict(color=colour, size=10))

    if smc_sd:
        basis = sma(close, sd_len)
        if basis[-1] is not None:
            window = close[-sd_len:]
            mean = sum(window) / sd_len
            sd = (sum((v - mean) ** 2 for v in window) / sd_len) ** 0.5
            target = mean - 2.5 * sd
            fig.add_hline(y=target, line=dict(color="#ff9100", width=2, dash="dash"), row=1, col=1,
                          annotation_text=f"−2.5 SD target {target:,.2f}", annotation_position="right")

    fig.add_trace(go.Bar(
        x=x, y=data["volume"], name="Volume", showlegend=False,
        marker_color=["rgba(18,184,134,.55)" if c >= o else "rgba(255,107,91,.55)" for o, c in zip(data["open"], close)],
        marker_line_width=0, opacity=0.55,
    ), row=1, col=1, secondary_y=True)

    if oscillator == "RSI 14":
        fig.add_trace(go.Scatter(x=x, y=rsi(close), name="RSI 14", line=dict(color="#8B5CF6", width=1.5)),
                      row=2, col=1)
        for level, color in ((70, "#FF6B5B"), (30, "#12B886")):
            fig.add_hline(y=level, line=dict(color=color, width=1, dash="dot"), row=2, col=1)
    elif oscillator == "MACD":
        line_, sig, hist = macd(close)
        fig.add_trace(go.Bar(x=x, y=hist, name="MACD hist", marker_line_width=0,
                             marker_color=["#12B886" if (h or 0) >= 0 else "#FF6B5B" for h in hist]), row=2, col=1)
        fig.add_trace(go.Scatter(x=x, y=line_, name="MACD", line=dict(color="#3B82F6", width=1.3)), row=2, col=1)
        fig.add_trace(go.Scatter(x=x, y=sig, name="signal", line=dict(color="#ff9800", width=1.3)), row=2, col=1)

    # last price and last volume as axis tags, like the boxed labels on TradingView
    def compact(v):
        for cut, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
            if abs(v) >= cut:
                return f"{v / cut:,.2f}{suffix}"
        return f"{v:,.0f}"

    last_px = close[-1]
    tag = GREEN if last_px >= (close[-2] if len(close) > 1 else last_px) else RED
    fig.add_hline(y=last_px, line=dict(color=tag, width=1, dash="dot"), row=1, col=1,
                  annotation_text=f" {last_px:,.2f} ", annotation_position="right",
                  annotation_bgcolor=tag, annotation_font=dict(color="#0F1115", size=11))
    last_vol = next((v for v in reversed(data["volume"]) if v), 0)
    if last_vol:
        # yref by hand: add_hline puts its annotation on the primary axis whatever
        # secondary_y says, which drags the price scale up to volume numbers
        fig.add_annotation(x=1, xref="paper", xanchor="right", y=last_vol, yref="y2",
                           text=f" {compact(last_vol)} ", showarrow=False,
                           bgcolor="#2A3040", font=dict(color="#C9D1DB", size=10))

    fig.update_layout(
        height=chart_px, margin=dict(l=0, r=0, t=8, b=28), template="plotly_dark",
        paper_bgcolor="#171A21", plot_bgcolor="#171A21", bargap=0.22,
        hovermode="x unified", dragmode="pan", xaxis_rangeslider_visible=False,
        hoverlabel=dict(bgcolor="#20242F", bordercolor="#394050", font=dict(color="#E6E8EC", size=11)),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0, bgcolor="rgba(0,0,0,0)"),
        transition_duration=0,
    )
    # A real time axis zooms smoothly; a category axis jumps in discrete steps and is what
    # made pinch-zoom stutter. Non-trading time is hidden with rangebreaks instead, so the
    # series still has no holiday gaps.
    fig.update_xaxes(rangebreaks=breaks, showspikes=True, spikemode="across", spikethickness=1,
                     spikecolor="#787b86", spikedash="dot", gridcolor="rgba(120,123,134,.15)")
    if len(x) > VIEW_BARS:
        # daily gets a few blank days on the right so the newest candle is not against the
        # axis; intraday would only have that space swallowed by the session rangebreak
        end = x[-1] if intraday else (date_cls.fromisoformat(x[-1][:10]) + timedelta(days=6)).isoformat()
        fig.update_xaxes(range=[x[-VIEW_BARS], end])
        # ... and price scales to what is on screen, or the window looks flat
        hi = max(v for v in data["high"][-VIEW_BARS:] if v is not None)
        lo = min(v for v in data["low"][-VIEW_BARS:] if v is not None)
        pad = (hi - lo) * 0.12 or hi * 0.01
        fig.update_yaxes(range=[lo - pad, hi + pad], secondary_y=False, row=1, col=1)
    fig.update_yaxes(side="right", showspikes=True, spikethickness=1, spikecolor="#98A2B3",
                     spikedash="dot", gridcolor="rgba(154,164,178,.13)", fixedrange=lock_price)
    vol_top = max((v for v in data["volume"][-VIEW_BARS:] if v), default=1) * 4.5
    fig.update_yaxes(range=[0, vol_top], showgrid=False, showticklabels=False, showspikes=False,
                     fixedrange=True, secondary_y=True, row=1, col=1)
    return fig



if page == "Chart":
    # TradingView's chart header: symbol, timeframe, then the last bar's OHLC and change
    o, h, l, c = (data[k][-1] for k in ("open", "high", "low", "close"))
    prev_c = data["close"][-2] if len(data["close"]) > 1 else c
    diff = c - prev_c
    tone = GREEN if diff >= 0 else RED
    vol_last = data["volume"][-1] or 0
    unit = {"1D": "1D", "1minutes": "1m"}.get(timeframe, timeframe)
    st.markdown(
        f"<div class='tv-head'>"
        f"<span class='tv-sym'>{symbol}</span>"
        f"<span class='tv-meta'>· {unit} · NEPSE · {str(data['when'][-1])[:16]}</span>"
        f"<span class='tv-ohlc' style='color:{tone}'>"
        f"O<b>{o:,.2f}</b> H<b>{h:,.2f}</b> L<b>{l:,.2f}</b> C<b>{c:,.2f}</b>"
        f"&nbsp;{diff:+,.2f} ({(diff / prev_c * 100 if prev_c else 0):+.2f}%)</span>"
        f"</div>"
        f"<div class='tv-vol'>Volume <span style='color:{tone}'>{vol_last:,.0f}</span></div>",
        unsafe_allow_html=True)

    # height="stretch" all the way down: the container claims the space left under the
    # header and the chart fills it. A CSS height here instead is racy — Plotly settles at
    # its own 450px default before the stylesheet lands, and the chart renders short.
    # Streamlit re-mounts the Plotly component on every fragment run — key= and uirevision do
    # not prevent it (same finding as the heatmap), so a live redraw costs the zoom and pan
    # state. That is a real trade, so it is a switch rather than a default nobody asked for.
    live_candle = timeframe == "1D" and st.checkbox(
        "🔴 Live candle", value=True, key="live_candle",
        help="Redraw every few seconds so today's candle moves with the market. The chart "
             "re-mounts on each redraw, so zoom and pan reset — untick to inspect in detail.")

    def _draw_chart():
        d = with_live_bar(bars(symbol, timeframe, None), symbol) if live_candle else data
        with st.container(key="mainchart"):
            fig = build_chart(d, symbol, timeframe)
            st.plotly_chart(fig, height="stretch", config={
                "scrollZoom": True,        # wheel / pinch zooms about the pointer
                "doubleClick": "reset",
                "displaylogo": False,
                "displayModeBar": "hover",
                "responsive": True,        # follow the CSS-sized container, not chart_px
                "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d",
                                           "toggleSpikelines"],
                "scrollZoomSpeed": 0.6,
            })

    (st.fragment(run_every=CANDLE_POLL)(_draw_chart) if live_candle else _draw_chart)()


# ---------------------------------------------------------------- floorsheet

if page == "Floorsheet":
    dates = floorsheet_dates(symbol)
    if not dates:
        st.info(f"No floorsheet for {symbol}. Run fetch_floorsheet_merolagani.py.")
    else:
        session_date = st.selectbox("Session", dates, key="fs_date")
        rows = trades(symbol, session_date)
        qty = sum(r[2] for r in rows)
        turnover = sum(r[4] for r in rows)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Trades", f"{len(rows):,}")
        m2.metric("Shares", f"{qty:,.0f}")
        m3.metric("Turnover", f"Rs {turnover:,.0f}")
        m4.metric("Avg trade", f"{qty / len(rows):,.0f} sh" if rows else "—")

        net, bought, sold = broker_flow(symbol, (session_date,))
        top = sorted(net.items(), key=lambda kv: -abs(kv[1]))[:15]
        flow = go.Figure(
            go.Bar(x=[b for b, _ in top], y=[v for _, v in top],
                   marker_color=["#12B886" if v > 0 else "#FF6B5B" for _, v in top])
        )
        flow.update_layout(height=300, margin=dict(l=0, r=0, t=10, b=0), template="plotly_dark",
                           xaxis_title="broker", yaxis_title="net shares", showlegend=False)
        st.plotly_chart(flow, width="stretch")
        st.caption("Positive = net buyer that session. Broker codes are NEPSE member numbers.")

        st.dataframe(
            [{"buyer": b, "seller": s, "quantity": q, "rate": r, "amount": a, "transaction": t}
             for b, s, q, r, a, t in rows],
            width="stretch", height=260,
        )

# ---------------------------------------------------------------- broker flow

if page == "Broker flow":
    dates = floorsheet_dates(symbol)
    if not dates:
        st.info(f"No floorsheet for {symbol}.")
    else:
        if len(dates) < 6:                       # a slider needs a real range to mean anything
            window = len(dates)
            st.caption(f"Only {window} floorsheet session(s) on file for {symbol} — showing all.")
        else:
            window = st.slider("Sessions", 5, min(120, len(dates)), min(20, len(dates)))
        picked = dates[:window]
        net, bought, sold = broker_flow(symbol, tuple(picked))
        ranked = sorted(net.items(), key=lambda kv: -kv[1])
        accum, distrib = ranked[:10], ranked[-10:][::-1]

        st.subheader(f"Net broker flow · last {window} sessions ({picked[-1]} → {picked[0]})")
        left, right = st.columns(2)
        left.markdown("**Accumulating**")
        # keep these numeric — as f-strings pandas infers object dtype and Streamlit's
        # header-click sort compares them as TEXT, ranking "9,000" above "80,000"
        _fmt = {c: st.column_config.NumberColumn(format="%,d")
                for c in ("net shares", "bought", "sold")}
        left.dataframe([{"broker": b, "net shares": v, "bought": bought[b], "sold": sold[b]}
                        for b, v in accum if v > 0],
                       width="stretch", hide_index=True, column_config=_fmt)
        right.markdown("**Distributing**")
        right.dataframe([{"broker": b, "net shares": v, "bought": bought[b], "sold": sold[b]}
                         for b, v in distrib if v < 0],
                        width="stretch", hide_index=True, column_config=_fmt)

        # concentration: how much of the buying sits with the top 5 brokers
        total_buy = sum(bought.values()) or 1
        top5 = sum(v for _, v in sorted(bought.items(), key=lambda kv: -kv[1])[:5])
        st.metric("Top-5 broker share of buying", f"{top5 / total_buy * 100:.1f}%")
        st.caption("High concentration with few buyers against many sellers is the classic accumulation footprint.")

# ---------------------------------------------------------------- scanner


@st.cache_data(show_spinner=False)
def live_scan(wave_len, smc_sens, sig_sens, bars_back, stamp):
    """Run the Personal-indicator rules over every symbol, at the settings now in the sidebar.

    Cached on the settings themselves, so moving a slider recomputes once and every later
    view of that combination is instant.
    """
    rows = []
    for name in sorted(universe()):
        if not tradeable(name):
            continue
        d = bars(name, "1D", bars_back)
        if not d or len(d["close"]) < wave_len + 2 * max(smc_sens, sig_sens) + 2:
            continue
        close, high, low = d["close"], d["high"], d["low"]

        wave = alma(close, wave_len)
        trend = "up" if wave[-1] is not None and close[-1] >= wave[-1] else "down"

        ph, pl = pivots(high, low, smc_sens, smc_sens)
        events = structure(close, ph, pl)
        struct = f"{events[-1][2]}-{events[-1][3]}" if events else "none"
        struct_dir = events[-1][3] if events else ""

        # The signal IS the indicator's last badge — the same BUY/SELL the chart drew.
        # A pivot low prints BUY, a pivot high prints SELL, and it stands until the next
        # one replaces it, so every symbol is on one side or the other.
        sh, sl = pivots(high, low, sig_sens, sig_sens)
        signal, age, since, pivot_px = "", "", "", None
        for i in range(len(close) - 1, -1, -1):
            if sh[i] is not None or sl[i] is not None:
                signal = "SELL" if sh[i] is not None else "BUY"
                pivot_px = sh[i] if sh[i] is not None else sl[i]
                age = len(close) - 1 - i
                since = d["when"][i]
                break
        if not signal:  # no pivot in the window yet — fall back to which side of the wave
            signal, age, since = ("BUY" if trend == "up" else "SELL"), "", ""

        atr_now = next((v for v in reversed(atr(high, low, close)) if v is not None), None)
        stop, t1, t2, risk_pct = trade_levels(signal, close[-1], pivot_px, atr_now)
        prev = close[-2] if len(close) > 1 else close[-1]
        rows.append({
            "symbol": name, "date": d["when"][-1], "close": round(close[-1], 2),
            "change_pct": round((close[-1] - prev) / prev * 100, 2) if prev else 0.0,
            "volume": int(d["volume"][-1] or 0),
            "signal": signal, "stop": stop, "target1": t1, "target2": t2, "risk_pct": risk_pct,
            "since": since, "bars_ago": age, "trend": trend, "structure": struct,
        })
    order = {"BUY": 0, "SELL": 1}
    rows.sort(key=lambda r: (order[r["signal"]], -r["change_pct"]))
    return rows


if page == "Scanner":
    st.caption(f"Scanning every stock with your current Personal-indicator settings — "
               f"ALMA {wave_len} · structure {smc_sens} · swing {sig_sens}. "
               "Change a slider in the sidebar and this recomputes. "
               "Mutual funds and debentures are excluded.")
    table = live_scan(wave_len, smc_sens, sig_sens, SCAN_BARS, archive_stamp())
    if not table:
        st.info("No symbols have enough history for these settings — lower the sensitivities.")
        st.stop()

    counts = {s: sum(1 for r in table if r["signal"] == s) for s in ("BUY", "SELL")}
    a, b, c = st.columns(3)
    a.metric("Scanned", len(table))
    b.metric("BUY", counts["BUY"])
    c.metric("SELL", counts["SELL"])
    st.caption(f"Last traded session · {table[0]['date']}")

    pick = st.radio("Show", ["BUY", "SELL", "All"], horizontal=True, index=0, key="scan_pick")
    shown = table if pick == "All" else [r for r in table if r["signal"] == pick]

    chosen = st.dataframe(paint(shown), width="stretch", hide_index=True, height=300,
                          on_select="rerun", selection_mode="single-row", key="scan_rows")
    st.caption(f"{len(shown)} symbols · click a row to chart it")

    picked_rows = chosen.selection.rows if hasattr(chosen, "selection") else []
    if picked_rows:
        row = shown[picked_rows[0]]
        st.subheader(f"{row['symbol']} · {row['close']:,.2f}  ({row['change_pct']:+.2f}%)  → {row['signal']}")
        sub = bars(row["symbol"], "1D", SCAN_BARS)
        if sub:
            fig = build_chart(sub, row["symbol"], "1D", lock_price=True, chart_px=420)
            st.plotly_chart(fig, width="stretch", key="scan_chart",
                            config={"scrollZoom": True, "displaylogo": False, "doubleClick": "reset"})
            st.caption(f"{row['signal']} since {row['since'] or '—'} ({row['bars_ago']} bars ago) · "
                       f"trend {row['trend']} · structure {row['structure']}")

    # ------------------------------------------------------------ conviction lists
    st.divider()
    st.subheader("Strong signals")

    fresh_max = st.slider("Only signals no older than (bars)", 3, 40, 15, key="fresh_max")
    st.caption(
        "A signal is **strong** when all three parts of the indicator agree — the BUY/SELL badge, "
        "price on the right side of the ALMA wave, and the last structure break pointing the same "
        "way — and it is still fresh, with a stop far enough away to clear costs (risk 1.5–20%)."
    )

    def conviction(rows, side):
        """Strong = every part of the indicator agrees, the signal is fresh, and the stop
        is far enough away to be worth taking. Indicator only — no other data feeds this."""
        want = "up" if side == "BUY" else "down"
        picked = [
            r for r in rows
            if r["signal"] == side
            and r["trend"] == want
            and r["structure"].endswith(f"-{want}")
            and isinstance(r["bars_ago"], int) and r["bars_ago"] <= fresh_max
            and r["risk_pct"] and 1.5 <= r["risk_pct"] <= 20
        ]
        picked.sort(key=lambda r: (r["bars_ago"], -abs(r["change_pct"])))  # freshest first
        return picked

    cols = ["symbol", "close", "change_pct", "stop", "target1", "target2", "risk_pct",
            "bars_ago", "since", "structure"]
    buy_side, sell_side = conviction(table, "BUY"), conviction(table, "SELL")

    left, right = st.columns(2)
    left.markdown(f"### 🟢 Strong BUY · {len(buy_side)}")
    # every cell a plain number or string — a None in a numeric column collapses the layout
    left.dataframe(paint([{c: (r.get(c) if r.get(c) is not None else 0) for c in cols} for r in buy_side]),
                   width="stretch", hide_index=True, height=250)
    right.markdown(f"### 🔴 Strong SELL · {len(sell_side)}")
    right.dataframe(paint([{c: (r.get(c) if r.get(c) is not None else 0) for c in cols} for r in sell_side]),
                    width="stretch", hide_index=True, height=250)
    st.caption("Freshest signals first. Everything here comes from the indicator itself — "
               "badge, ALMA trend and structure break — and nothing else.")

    # ------------------------------------------------------------ signal history
    st.divider()
    st.subheader("Signal history · did the targets get hit?")

    @st.cache_data(show_spinner="Replaying every past signal ...")
    def signal_history(wave_len, smc_sens, sig_sens, bars_back, stamp):
        """Every past signal, with what happened to it afterwards.

        A pivot at bar i is only *confirmable* at bar i+sig_sens, so entry is that bar's
        close — never the pivot bar itself, which would be hindsight. From the next bar on,
        each session is checked for the stop and the targets; if a bar could have hit both,
        the stop is taken first, so the record never flatters itself.
        """
        out = []
        for name in sorted(universe()):
            if not tradeable(name):
                continue
            d = bars(name, "1D", bars_back)
            if not d or len(d["close"]) < wave_len + 2 * sig_sens + 5:
                continue
            close, high, low, when = d["close"], d["high"], d["low"], d["when"]
            wave = alma(close, wave_len)
            atr_series = atr(high, low, close)
            ph, pl = pivots(high, low, smc_sens, smc_sens)
            events = structure(close, ph, pl)
            sh, sl = pivots(high, low, sig_sens, sig_sens)

            for i in range(len(close)):
                if sh[i] is None and sl[i] is None:
                    continue
                side = "SELL" if sh[i] is not None else "BUY"
                pivot_px = sh[i] if sh[i] is not None else sl[i]
                j = i + sig_sens                     # the bar the signal became knowable
                if j > len(close) - 1:
                    continue                         # confirmable only in the future
                entry = close[j]
                stop, t1, t2, risk_pct = trade_levels(side, entry, pivot_px, atr_series[j])
                if stop is None:
                    continue

                trend_ok = wave[j] is not None and ((close[j] >= wave[j]) == (side == "BUY"))
                last = [e for e in events if e[0] <= j]
                struct_ok = bool(last) and last[-1][3] == ("up" if side == "BUY" else "down")

                result, exit_px, took = "OPEN", None, ""
                for k in range(j + 1, len(close)):
                    hit_stop = low[k] <= stop if side == "BUY" else high[k] >= stop
                    hit_t2 = high[k] >= t2 if side == "BUY" else low[k] <= t2
                    hit_t1 = high[k] >= t1 if side == "BUY" else low[k] <= t1
                    if hit_stop:                      # pessimistic when a bar spans both
                        result, exit_px, took = "STOP", stop, when[k]
                        break
                    if hit_t2:
                        result, exit_px, took = "TARGET 2", t2, when[k]
                        break
                    if hit_t1:
                        result, exit_px, took = "TARGET 1", t1, when[k]
                        break

                ret = None
                if exit_px is not None:
                    ret = (exit_px - entry) / entry * 100 * (1 if side == "BUY" else -1)
                out.append({
                    "date": when[j], "symbol": name, "signal": side,
                    "strong": "yes" if (trend_ok and struct_ok) else "",
                    "entry": round(entry, 2), "stop": stop, "target1": t1, "target2": t2,
                    "risk_pct": risk_pct, "result": result, "closed_on": took,
                    "return_pct": round(ret, 2) if ret is not None else 0.0,
                })
        out.sort(key=lambda r: r["date"], reverse=True)
        return out

    @st.cache_data(show_spinner=False, ttl=60)
    def latest_prices(bars_back, stamp):
        """Last traded price per symbol — NAASA's live quote where it answers, archive close
        otherwise, so the column is still populated when the feed is unreachable."""
        out = {}
        for name in sorted(universe()):
            if not tradeable(name):
                continue
            d = bars(name, "1D", bars_back)
            if d and d["close"]:
                out[name] = d["close"][-1]
        try:
            for symbol, q in naasa.quotes(list(out)).items():
                if q.get("lp"):
                    out[symbol] = q["lp"]
        except Exception:
            pass  # market data is a bonus here, never a hard dependency
        return out

    history = signal_history(wave_len, smc_sens, sig_sens, SCAN_BARS, archive_stamp())
    ltp_by_symbol = latest_prices(SCAN_BARS, archive_stamp())
    if not history:
        st.info("No completed signals in this window.")
    else:
        dates = sorted({r["date"] for r in history}, reverse=True)
        sessions = sorted({r["date"] for r in table} | set(dates), reverse=True)
        newest = date_cls.fromisoformat(sessions[0])      # the last traded session
        oldest = date_cls.fromisoformat(sessions[-1])
        c1, c2, c3, c4 = st.columns([1.3, 1.2, 1, 1])
        picked_day = c1.date_input("Date", value=newest, min_value=oldest, max_value=newest,
                                   format="YYYY-MM-DD", key="hist_day")
        mode = c2.radio("Range", ["On this date", "From this date"], key="hist_mode")
        side_pick = c3.radio("Side", ["All", "BUY", "SELL"], key="hist_side")
        strong_only = c4.checkbox("Strong only", value=False, key="hist_strong")

        chosen = picked_day.isoformat()
        rows = [dict(r) for r in history          # copy: the source list is cached
                if (r["date"] == chosen if mode == "On this date" else r["date"] >= chosen)
                and (side_pick == "All" or r["signal"] == side_pick)
                and (not strong_only or r["strong"] == "yes")]

        # mark every row against the market as it stands now
        for r in rows:
            ltp = ltp_by_symbol.get(r["symbol"])
            r["ltp"] = round(ltp, 2) if ltp else 0.0
            if ltp and r["result"] == "OPEN":
                move = (ltp - r["entry"]) / r["entry"] * 100 * (1 if r["signal"] == "BUY" else -1)
                r["open_pct"] = round(move, 2)
                span = abs(r["target1"] - r["entry"]) or 1
                r["to_target1_pct"] = round(abs(r["target1"] - ltp) / ltp * 100, 2)
                r["progress"] = round(max(min((ltp - r["entry"]) / (r["target1"] - r["entry"]), 1.5), -1.5), 2)
            else:
                r["open_pct"] = r["return_pct"]
                r["to_target1_pct"] = 0.0
                r["progress"] = 0.0

        order = ["date", "symbol", "signal", "strong", "entry", "ltp", "open_pct", "stop",
                 "target1", "target2", "to_target1_pct", "progress", "risk_pct", "result",
                 "closed_on", "return_pct"]
        rows = [{k: r[k] for k in order} for r in rows]
        if mode == "On this date" and not rows:
            st.info(f"No signals confirmed on {chosen} — it may not have been a trading day, "
                    "or no swing was confirmed. Pick another date or switch to “From this date”.")

        done = [r for r in rows if r["result"] != "OPEN"]
        wins = [r for r in done if r["result"].startswith("TARGET")]
        t2s = [r for r in done if r["result"] == "TARGET 2"]
        avg = sum(r["return_pct"] for r in done) / len(done) if done else 0.0

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Signals", len(rows))
        m2.metric("Resolved", len(done))
        m3.metric("Hit a target", f"{len(wins) / len(done) * 100:.0f}%" if done else "—")
        m4.metric("Reached target 2", f"{len(t2s) / len(done) * 100:.0f}%" if done else "—")
        m5.metric("Avg return", f"{avg:+.2f}%")

        st.dataframe(paint(rows), width="stretch", hide_index=True, height=300)
        st.caption("ltp is the latest close; open_pct is the unrealised move in the signal's "
                   "direction, progress is 1.0 when price has reached target 1. "
                   "Entry is the close of the bar the signal became confirmable, never the pivot "
                   "itself. When one bar could have hit both the stop and a target, the stop is "
                   "counted — so these numbers are the pessimistic reading, before costs.")


# ---------------------------------------------------------------- NAASA
#
# Mirror of NAASA's order screen — for MONITORING only. Everything here is read: the public
# quote (Top-5 best bid/ask + stock stats) and, when signed in, your own order book. The New
# Order form is deliberately display-only: order placement is a live trade and is done in
# NAASA itself, never from this dashboard.

if page == "NAASA":
    status, scol = market_status()
    st.markdown(
        "<div style='display:flex;align-items:center;gap:12px'>"
        "<span class='section' style='margin:0'>NAASA order panel</span>"
        f"<span style='background:{scol};color:#0d1117;font-weight:700;padding:2px 11px;"
        f"border-radius:5px'>{status}</span></div>", unsafe_allow_html=True)
    st.caption("PRE-OPEN 10:30–10:45 (queue orders), PRE-OPEN CLOSE 10:46–10:59, "
               "LIVE 11:00–15:00, CLOSED otherwise. Orders go under **Place order** below.")

    # `symbol` is always None here (this page is outside PER_SYMBOL), so seed from ?sym=
    _nsym = st.query_params.get("sym") or "NABIL"
    sym = st.text_input("Scrip", value=_nsym, key="naasa_sym").strip().upper() or "NABIL"
    if st.query_params.get("sym") != sym:      # mirror it, same as ?page= and the per-symbol bar
        st.query_params["sym"] = sym           # without this a refresh re-seeds from a stale ?sym=
    # Live feed is always on (NAASA WSS) — no toggle, NO cache. Show the socket snapshot only, the
    # instant it arrives. No public/REST fallback here: this panel is live-or-nothing on purpose.
    feed = naasa_feed()
    if feed["depth"] != (sym,):
        feed["depth"] = (sym,)
    @st.fragment(run_every=1)
    def _depth():
        render_socket_depth(sym, feed)
    _depth()

    @st.fragment(run_every=LIVE_1D_FLUSH)
    def _archive_status():
        """What the whole-market archiver is doing — otherwise it is invisible until you diff
        a 1D.txt, and a silently dead updater is exactly the failure this project keeps hitting."""
        n_live = len(feed["snap"])
        if feed.get("saved_err"):
            st.caption(f"⚠ Archive updater: {feed['saved_err']}")
        elif feed.get("saved_at"):
            st.caption(f"💾 Today's 1D bar written for {feed['saved']} instrument(s) at "
                       f"{feed['saved_at']} NPT · {n_live} instruments live in the feed · "
                       f"flushing every {LIVE_1D_FLUSH}s while the market is open.")
        else:
            st.caption(f"💾 Archive updater armed · {n_live} instruments live in the feed · "
                       f"writes today's 1D bar every {LIVE_1D_FLUSH}s while the market is open.")
    _archive_status()

    # Order Book / Holdings / Collateral all come from the SAME X-app session the live feed holds
    # (naasa.x_*), so they no longer fight the socket for NAASA's one-session-per-account. They need
    # the saved password (the report API is authed), so gate on credentials, not just the cookie.
    em, pw = naasa.load_credentials()
    who = st.session_state.get("naasa_who")

    def _num(x):
        try: return f"{float(x):,.0f}"
        except (TypeError, ValueError): return "–"
    def _money(x):
        try: return f"Rs {float(x):,.2f}"
        except (TypeError, ValueError): return "—"

    # -- Order ticket — REAL money ---------------------------------------------------------------
    # Deliberately OUTSIDE every run_every fragment. The panels here re-run on a 1s timer, and a
    # timer must never be able to re-enter a path that spends money; st.fragment reruns are
    # fragment-scoped, so keeping the ticket in the main body means only a click can reach it.
    # Sending is two-step: the form STAGES a validated draft, a separate button sends it, and the
    # draft is popped BEFORE the call so no rerun or refresh can replay an order.
    # test_ops.py enforces the "not on a timer" half of this.
    st.markdown("<div class='section'>Place order</div>", unsafe_allow_html=True)
    if not (em and pw):
        st.caption("Sign in under 🔐 **NAASA account** in the sidebar (with Remember me) to place orders.")
    else:
        # Keys, not `value=`, so the Modify buttons below can prefill the ticket by writing
        # session_state before it renders — passing both makes Streamlit ignore one of them.
        mod_row = st.session_state.get("mod_row")
        st.session_state.setdefault("ord_scrip", sym)
        st.session_state.setdefault("ord_side", "BUY")
        st.session_state.setdefault("ord_qty", 10)
        st.session_state.setdefault("ord_price", 0.0)
        if mod_row:
            m1, m2 = st.columns([5, 1])
            m1.info(f"**Modifying order #{mod_row.get('BrokerTranID') or mod_row.get('LatestOrderID')}** "
                    f"— {mod_row.get('Scrip')} {mod_row.get('BuySellType') or mod_row.get('B/S')}. "
                    "Change the quantity or price and send; symbol and side are fixed.")
            if m2.button("Stop modifying", width="stretch"):
                st.session_state.pop("mod_row", None)
                st.session_state.pop("order_draft", None)
                st.rerun()
        with st.form("order_ticket"):
            q1, q2, q3, q4, q5 = st.columns([2, 1, 1, 1.4, 1])
            t_scrip = q1.text_input("Symbol", key="ord_scrip", disabled=bool(mod_row))
            t_side = q2.selectbox("Side", ("BUY", "SELL"), key="ord_side", disabled=bool(mod_row))
            t_qty = q3.number_input("Qty", min_value=1, max_value=99999999, step=1, key="ord_qty")
            t_price = q4.number_input("Limit price (0 = market)", min_value=0.0, step=0.10,
                                      format="%.2f", key="ord_price")
            # GTD is the one term needing a date, and the ticket carries no date field — so it
            # is not offered here rather than left as an option that can only fail validation.
            # naasa.ORDER_TERMS still lists it for API callers that supply term_validity.
            t_terms = q5.selectbox("Validity", [t for t in naasa.ORDER_TERMS if t != "GTD"])
            review = st.form_submit_button(
                "Review change →" if mod_row else "Review order →", width="stretch")
        if review:
            try:                       # validate now so the confirm step shows a real order
                st.session_state["order_draft"] = naasa.order_body(
                    t_scrip, t_side, t_qty, t_price, t_terms)
            except ValueError as e:
                st.session_state.pop("order_draft", None)
                st.error(f"Not a valid order — {e}")

        draft = st.session_state.get("order_draft")
        if draft:
            is_mkt = draft["Market"] == "1"
            d_px = 0.0 if is_mkt else float(draft["Price"])
            d_qty = int(draft["Quantity"])
            kind = "MARKET order" if is_mkt else f"LIMIT @ Rs {d_px:,.2f}"
            worth = "filled at whatever the market offers" if is_mkt else f"Rs {d_qty * d_px:,.2f}"
            st.warning(f"**{draft['BuySellType'].upper()} {d_qty:,} × {draft['Scrip']}** — {kind}, "
                       f"{draft['OrderTerms']}. Order value: {worth}.\n\n"
                       "Sending this places a **real order on NEPSE** through your NAASA account.")
            ltp = (live_price(draft["Scrip"]) or {}).get("ltp")   # live_price returns a dict
            if ltp and not is_mkt and abs(d_px - ltp) / ltp > 0.05:
                st.info(f"Heads up: your limit is {abs(d_px - ltp) / ltp * 100:.1f}% away from the "
                        f"last traded price of Rs {ltp:,.2f}.")
            g1, g2 = st.columns(2)
            _send_lbl = ("✅ Send this change" if mod_row
                         else f"✅ Send this {draft['BuySellType'].upper()} order")
            if g1.button(_send_lbl, type="primary", width="stretch"):
                st.session_state.pop("order_draft", None)    # consume FIRST — no replay on rerun
                _row = st.session_state.pop("mod_row", None)
                try:
                    if _row:
                        resp = naasa.x_modify_order(em, pw, _row, draft["Quantity"], d_px,
                                                    draft["OrderTerms"], draft["TermValidity"])
                    else:
                        resp = naasa.x_place_order(em, pw, draft["Scrip"], draft["BuySellType"],
                                                   draft["Quantity"], d_px, draft["OrderTerms"],
                                                   draft["TermValidity"])
                    code, msg = _broker_reply(resp)
                    if code == 0:
                        st.success(("Change accepted by the broker. " if _row
                                    else "Order accepted by the broker. ") + msg)
                    elif code is None:
                        st.warning("The broker replied in an unexpected shape — check the order "
                                   f"book below before you retry, so you do not double up. {msg}")
                    else:
                        st.error(f"Broker REJECTED the order (code {code}) — {msg}")
                except Exception as e:
                    st.error(f"Order was NOT sent — {e}")
            if g2.button("Discard", width="stretch"):
                st.session_state.pop("order_draft", None)
                st.rerun()

        # Being able to place without being able to pull is a trap, so cancel lives right here.
        try:
            open_rows = [r for r in acct_call("orders")
                         if isinstance(r, dict) and (r.get("BrokerTranID") or r.get("LatestOrderID"))
                         and naasa.order_is_working(r)]        # a filled order cannot be cancelled
        except Exception:
            open_rows = []
        if open_rows:
            def _order_label(r):
                return (f"{r.get('Scrip')} · {r.get('BuySellType') or r.get('B/S')} "
                        f"{r.get('RemainingQty')} @ {r.get('Price')} · {r.get('OrderStatus')} "
                        f"· #{r.get('BrokerTranID') or r.get('LatestOrderID')}")
            pick = st.selectbox("Cancel an open order", range(len(open_rows)),
                                format_func=lambda i: _order_label(open_rows[i]),
                                index=None, placeholder="Choose an open order…")
            if pick is not None and st.button("🚫 Cancel this order", width="stretch"):
                try:
                    code, msg = _broker_reply(naasa.x_cancel_order(em, pw, open_rows[pick]))
                    if code == 0:
                        st.success(f"Cancellation accepted. {msg}".strip())
                    else:
                        st.error(f"Cancellation rejected (code {code}) — {msg}")
                except Exception as e:
                    st.error(f"Cancellation was NOT sent — {e}")

            # Per-row actions. Modify loads the order into the ticket above rather than opening a
            # second form: one place to check qty/price before anything is sent.
            st.caption("Working orders — Modify loads the order into the ticket above.")
            for _i, _r in enumerate(open_rows):
                a1, a2, a3 = st.columns([5, 1.1, 1.1])
                a1.markdown(
                    f"<div style='padding-top:6px'><b>{_r.get('Scrip')}</b> · "
                    f"{_r.get('BuySellType') or _r.get('B/S')} {_r.get('RemainingQty')} @ "
                    f"{_r.get('Price')} · {_r.get('OrderStatus')} · "
                    f"#{_r.get('BrokerTranID') or _r.get('LatestOrderID')}</div>",
                    unsafe_allow_html=True)
                if a2.button("✏ Modify", key=f"ob_mod_{_i}", width="stretch"):
                    _side = str(_r.get("BuySellType") or _r.get("B/S") or "B").upper()
                    st.session_state["mod_row"] = _r
                    st.session_state["ord_scrip"] = _r.get("Scrip")
                    st.session_state["ord_side"] = "BUY" if _side.startswith("B") else "SELL"
                    try:
                        st.session_state["ord_qty"] = int(float(_r.get("RemainingQty")))
                        st.session_state["ord_price"] = float(_r.get("Price"))
                    except (TypeError, ValueError):
                        pass
                    st.session_state.pop("order_draft", None)
                    st.rerun()
                if a3.button("🚫 Cancel", key=f"ob_can_{_i}", width="stretch"):
                    try:
                        code, msg = _broker_reply(naasa.x_cancel_order(em, pw, _r))
                        if code == 0:
                            st.success(f"Cancellation accepted. {msg}".strip())
                        else:
                            st.error(f"Cancellation rejected (code {code}) — {msg}")
                    except Exception as e:
                        st.error(f"Cancellation was NOT sent — {e}")

    st.markdown("<div class='section'>Order Book</div>", unsafe_allow_html=True)
    if not (em and pw):
        st.caption("Sign in under 🔐 **NAASA account** in the sidebar (with Remember me) to load your live order book.")
    else:
        @st.fragment(run_every=ACCT_POLL)
        def _orderbook_panel():
            try:
                rows = acct_call("orders")
                if rows:
                    df = pd.DataFrame(rows)
                    # (display name, first matching source column) — one source per name, no dupes
                    wanted = [("Symbol", ("Scrip",)), ("Side", ("BuySellType", "BuySellText", "B/S")),
                              ("Qty", ("Quantity", "RemainingQty")), ("Price", ("Price",)),
                              ("Status", ("OrderStatus",)), ("Validity", ("OrderTerms",)),
                              ("Time", ("BrokerOrderTime",)), ("Order No", ("ExchangeOrderNo",))]
                    view = pd.DataFrame({name: df[next(s for s in srcs if s in df.columns)]
                                         for name, srcs in wanted
                                         if any(s in df.columns for s in srcs)})
                    st.dataframe(view, width="stretch", hide_index=True, height=240)
                    # The book is today's orders, not just the live ones — count them apart, or a
                    # filled trade reads as an order still working in the market.
                    n_open = sum(1 for r in rows if naasa.order_is_working(r))
                    done = len(rows) - n_open
                    st.caption(f"🔴 {n_open} working order(s)"
                               + (f" · {done} completed today" if done else "")
                               + f" · live from NAASA (MarketOrder/OrderBook) · polled "
                                 f"{ACCT_POLL}s · read-only.")
                else:
                    st.info("No open orders in your NAASA order book right now.")
            except Exception as e:
                st.caption(f"⚠ Could not load the order book — {str(e)[:120]}")
        _orderbook_panel()

    # -- Holdings --
    st.markdown("<div class='section'>My Holdings</div>", unsafe_allow_html=True)
    if not (em and pw):
        st.caption("Sign in to load your holdings.")
    else:
        @st.fragment(run_every=ACCT_POLL)
        def _holdings_panel():
            try:
                hold = acct_call("holdings")
                if hold:
                    df = pd.DataFrame(hold)
                    cols = [c for c in ("NEPSECode", "AvailableQty", "CDSFreeBalance",
                                        "LastTradedPrice", "ClosePrice", "ValueAsOfLTP",
                                        "DayGainLoss") if c in df.columns]
                    view = df[cols].rename(columns={
                        "NEPSECode": "Symbol", "AvailableQty": "Qty", "CDSFreeBalance": "Free",
                        "LastTradedPrice": "LTP", "ClosePrice": "Prev close",
                        "ValueAsOfLTP": "Market value", "DayGainLoss": "Day P/L"})
                    st.dataframe(view, width="stretch", hide_index=True)
                    st.caption(f"🔴 {len(hold)} holding(s) · live from NAASA "
                               f"(TradeBook/HoldingDataReport) · polled {ACCT_POLL}s · read-only.")
                else:
                    st.info("No holdings in your NAASA account.")
            except Exception as e:
                st.caption(f"⚠ Could not load holdings — {str(e)[:120]}")
        _holdings_panel()

    # -- Collateral & account summary --
    st.markdown("<div class='section'>Collateral & account summary</div>", unsafe_allow_html=True)
    if not (em and pw):
        st.caption("Sign in to load your collateral and trade summary.")
    else:
        @st.fragment(run_every=ACCT_POLL)
        def _collateral_panel():
            try:
                f = _dash_fields(acct_call("collateral"))    # [2]=holdings [3]=collateral, [4] PII
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Available collateral", _money(f.get("GrossAvalibleExposure")))
                c2.metric("Used collateral", _money(f.get("GrossUsedExposure")))
                c3.metric("Holdings value", _money(f.get("TotalHoldingAmount")))
                c4.metric("Holdings", f"{_num(f.get('HoldingStockCount'))} scrip(s)")
                d1, d2, d3, d4 = st.columns(4)
                d1.metric("Orders today", _num(f.get("OrderCount")))
                d2.metric("Trades today", _num(f.get("TradeCount")))
                d3.metric("Traded qty", _num(f.get("TotalTradedQty")))
                d4.metric("Traded value", _money(f.get("TotalTradedValue")))
                st.caption(f"🔴 Live from NAASA (Home/DashboardDetails) · polled {ACCT_POLL}s · read-only.")
            except Exception as e:
                st.caption(f"⚠ Could not load collateral — {str(e)[:120]}")
        _collateral_panel()


# ---------------------------------------------------------------- master signal

def _tsv(path):
    """[(header, rows)] from one of our tab-separated .txt files, or (None, []) if absent."""
    p = MASTER / path
    if not p.exists():
        return None, []
    lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return (lines[0].split("\t"), [ln.split("\t") for ln in lines[1:]]) if lines else (None, [])


if page == "Master operator":
    st.markdown("<div class='section'>Master operator — floorsheet only, four windows</div>",
                unsafe_allow_html=True)

    vhdr, vrows = _tsv("operator_verdict.txt")
    if vrows:
        vdf = pd.DataFrame(vrows, columns=vhdr)
        for c in ("passes", "share_in", "share_out", "ratio", "clip", "clip_mkt",
                  "block_pct", "persist_pct", "counterparties", "net_shares", "value_rs"):
            if c in vdf.columns:
                vdf[c] = pd.to_numeric(vdf[c], errors="coerce")
        eng = vdf[vdf["verdict"] == "OPERATOR ENGAGED"]
        st.markdown("### ⚑ Operator engaged — all four tests passed")
        st.caption("Four independent tests, each with an innocent explanation on its own; passing "
                   "**all four at once** does not have one. **1 Concentration** — its share of this "
                   "stock vs its own share of every other stock (its own client base is the control "
                   "group, so \"it is a big broker\" cannot explain it). **2 Size** — clips vs the "
                   "rest of the tape. **3 Persistence** — % of sessions it was actually on that "
                   "side. **4 Breadth** — distinct counterparties, so a block/cross is excluded.")
        c1, c2, c3 = st.columns(3)
        c1.metric("OPERATOR ENGAGED", int((vdf["verdict"] == "OPERATOR ENGAGED").sum()))
        c2.metric("Likely (3 of 4)", int((vdf["verdict"] == "LIKELY").sum()))
        c3.metric("Liquid symbols tested", len(vdf))
        cols = [c for c in ("symbol", "side", "broker", "share_in", "share_out", "ratio",
                            "persist_pct", "counterparties", "value_rs") if c in eng.columns]
        b, sll = eng[eng["side"] == "BUY"], eng[eng["side"] == "SELL"]
        st.markdown("**🟢 Accumulating**")
        st.dataframe(b[cols], width="stretch", hide_index=True, height=260)
        st.markdown("**🔴 Distributing**")
        st.dataframe(sll[cols], width="stretch", hide_index=True, height=220)
        st.caption("`ratio` = how many times more of this stock the broker handles than it handles "
                   "of everything else. CKHL broker 82 runs 0.52% of NEPSE generally and 53% of "
                   "that one name — 101×. Watch for one broker across several rows: 82 holds CKHL "
                   "and MKHL, 92 holds NTC/EBL/CHCL/SCB, 38 is distributing MAKAR and SGHC.")
        st.error("**What the verdict means:** one decision-maker is behind the flow — hundreds of "
                 "independent retail clients cannot produce a 35× concentration in a single broker "
                 "while trading many times the market's block rate. **What it does not mean:** "
                 "anything about motive or direction. A manipulator, a fund building a position "
                 "and a prop desk are identical here, and none of this predicts price — every "
                 "predictive version was tested and died (table below).")
        st.divider()
    if not vrows:
        st.info("No verdict yet — press **↻ Re-run the operator test** below to build it.")
    if st.button("↻ Re-run the operator test", key="ov_run"):
        run_job("Operator verdict", "operator_verdict.py")
        st.rerun()

    ghdr, grows = _tsv("operator_now.txt")
    if grows:
        gdf = pd.DataFrame(grows, columns=ghdr)
        for c in ("score", "regular", "grip20", "grip15", "grip7", "grip3", "tighten",
                  "persist", "counterparties"):
            if c in gdf.columns:
                gdf[c] = pd.to_numeric(gdf[c], errors="coerce")

        st.caption("**Read `regular` first — it answers *are they buying every day, or was it one "
                   "block?*** It is the % of the last 20 sessions that broker was actually net on "
                   "that side. **90% = a daily campaign. 25% = one big trade** with a quiet "
                   "fortnight around it. `grip20→grip3` is their share of volume over the four "
                   "windows, so rising left-to-right means they are taking more lately.")

        cfg = {
            "regular": st.column_config.NumberColumn(
                "regular %", help="% of the last 20 sessions this broker was net on this side. "
                                  "High = a persistent daily campaign, low = one-off blocks."),
            "score": st.column_config.NumberColumn(
                "score", help="cross-sectional z of grip + tightening + persistence, scaled by "
                              "regularity — ranked against every other stock the SAME day"),
            "tighten": st.column_config.NumberColumn(
                "tighten", help="3d grip minus 20d grip; positive = tightening recently"),
            "persist": st.column_config.NumberColumn(
                "persist", help="distinct leaders across the 4 windows; 1 = same broker throughout"),
            "counterparties": st.column_config.NumberColumn(
                "ctp", help="distinct brokers on the other side; under 5 is a block and filtered"),
        }
        show = ["symbol", "score", "broker", "regular", "grip20", "grip15", "grip7", "grip3",
                "tighten", "persist", "counterparties"]

        buy = gdf[gdf["side"] == "BUY"].head(20)
        sell = gdf[gdf["side"] == "SELL"].head(20)

        st.markdown("### 🟢 Accumulating — one broker is the dominant **net BUYER**")
        st.caption("One hand is taking supply off a crowd of sellers. High `regular` + `persist=1` "
                   "+ many counterparties is the cleanest version of that picture.")
        st.dataframe(buy[[c for c in show if c in buy.columns]], width="stretch",
                     hide_index=True, height=330, column_config=cfg)

        st.markdown("### 🔴 Distributing — one broker is the dominant **net SELLER**")
        st.caption("The mirror image: one hand feeding stock out to many buyers. Worth knowing "
                   "before buying a name — and note a symbol can appear on **both** lists, which "
                   "just means two large desks are on opposite sides of it.")
        st.dataframe(sell[[c for c in show if c in sell.columns]], width="stretch",
                     hide_index=True, height=330, column_config=cfg)

        st.caption("Each side is z-scored against **its own** cross-section on the same date — a "
                   "40% buy-grip and a 40% sell-grip are not comparable, so they get separate "
                   "baselines. Two artifacts are filtered out: stale names, and leaders facing "
                   "fewer than 5 counterparties (promoter shares and debentures trade as single "
                   "negotiated blocks, which otherwise read as 100% grip).")
        with st.expander("🔬 Is this really an operator? — the proof, and its hard limit",
                         expanded=False):
            st.markdown(
                "**The obvious confound: some brokers are just big.** If a broker handles 8% of "
                "all NEPSE volume, it leading a stock is arithmetic, not evidence. So each flag "
                "is tested against **that same broker's share of every *other* symbol** over the "
                "same sessions (`operator_proof.py`).\n\n"
                "| symbol | broker | in-stock | elsewhere | ratio |\n"
                "|---|---|---|---|---|\n"
                "| NLICL | 20 | 25.9% | **0.27%** | **97×** |\n"
                "| TRH | 16 | 42.3% | 1.15% | 36.7× |\n"
                "| NTC | 92 | 47.7% | 1.30% | 36.6× |\n"
                "| EBL | 92 | 44.1% | 1.24% | 35.7× |\n"
                "| CGH | 44 | 42.0% | 2.30% | 18.3× |\n\n"
                "**9 of 9 flags are genuinely concentrated** — 18× to 97× the broker's own norm. "
                "And the market's actual giants (broker 58 at 8.2%, 34 at 3.5%, 49 at 3.3%) do "
                "**not** appear in the flag list at all. The big-broker confound is ruled out.")
            st.error(
                "**But this is NOT proof of an operator, and the gap is not closeable with this "
                "data.** The floorsheet publishes **broker IDs, never client IDs**. A broker is a "
                "*pipe*: 44% of EBL through broker 92 could be one operator, or four hundred "
                "unrelated retail clients who happen to share a broker. No calculation on this "
                "dataset can separate those two — it is a missing column, not a weak signal.\n\n"
                "So the strongest honest claim is **\"unusually concentrated order flow\"**, "
                "not **\"an operator is accumulating\"**.")
            st.caption("Statistical note, since the numbers look overwhelming: the z-scores run "
                       "into the thousands because the test counts each *share* as an independent "
                       "observation, which they are not — trades cluster. Treat the **ratio** as "
                       "the meaningful figure and ignore the p-value's magnitude.")
        st.warning("**Unusual ≠ profitable.** This ranks how abnormal today's concentration is and "
                   "attaches no expected return, deliberately — every predictive version of this "
                   "was tested and died (verdict table below).")
        st.divider()
    if not grows:
        st.info("No grip scan yet — press **↻ Rescan the tape** below to build it.")
    if st.button("↻ Rescan the tape", key="on_run"):
        run_job("Operator grip scan", "operator_now.py")
        st.rerun()
    st.divider()
    st.markdown("#### Why the *predictive* version does not exist")
    st.caption("Built from the **per-trade floorsheet alone** (1,056 sessions, 2022→2026) — no "
               "OHLC indicators, no fundamentals, none of the earlier signal logic. Every measure "
               "is expressed across the four lookbacks you asked for: **1 month · 15d · 7d · 3d**. "
               "Even the price series is the floorsheet's own daily VWAP.")
    hdr, rows = _tsv("operator_master.txt")
    if rows:
        st.dataframe(pd.DataFrame(rows, columns=hdr), width="stretch", hide_index=True)
        st.caption(f"{len(rows)} candidate(s) · rebuilt by `operator_master.py`.")
        if st.button("↻ Recompute now", key="mo_run"):
            run_job("Master operator", "operator_master.py")
            st.rerun()
    else:
        st.error("**Result: no operator signal found.** Six independent floorsheet-only families "
                 "were built, measured, split-half tested, then attacked by adversarial rebuilds. "
                 "All six are dead. No `operator_master.py` was written, because building one "
                 "would have meant inventing a signal the data does not support.")
        st.dataframe(pd.DataFrame([
            {"family": "cascade", "idea": "accumulation accelerating 3d>7d>15d>20d",
             "monotonic": "no", "OOS vs baseline": "+0.07pp", "verdict": "DEAD"},
            {"family": "breadth", "idea": "how many distinct sellers absorbed",
             "monotonic": "no", "OOS vs baseline": "±0.22pp, sign flips", "verdict": "DEAD"},
            {"family": "clipsize", "idea": "accumulator clip vs market median clip",
             "monotonic": "yes (raw)", "OOS vs baseline": "+0.01pp after price control",
             "verdict": "DEAD"},
            {"family": "aggression", "idea": "price paid vs VWAP; buys late vs early",
             "monotonic": "no (U-shape)", "OOS vs baseline": "+0.41pp, decaying yearly",
             "verdict": "DEAD"},
            {"family": "exhaustion", "idea": "seller supply drying up under a buyer",
             "monotonic": "no", "OOS vs baseline": "+0.16pp best anywhere", "verdict": "DEAD"},
            {"family": "rotation / wash", "idea": "one persistent broker; circular volume",
             "monotonic": "yes (obs-weighted only)",
             "OOS vs baseline": "holdout +0.148pp, t=+0.53", "verdict": "REFUTED"},
        ]), width="stretch", hide_index=True)
        st.markdown(
            "#### Why the premise itself fails\n"
            "> Across **135,626 liquid symbol-days**, the most-accumulating broker's 20-session net "
            "was positive on **100% of them — zero exceptions.**\n\n"
            "With ~50 brokers and a zero-sum book, *somebody* is always the biggest net buyer. "
            "Every family began by selecting that broker — so every family began by selecting "
            "**nothing**. \"An operator is accumulating this stock\" is an **identity, not a "
            "condition**. It compounds the known limit that the floorsheet carries **broker IDs, "
            "not client IDs**: one \"accumulating broker\" is hundreds of unrelated clients "
            "sharing a pipe.\n\n"
            "The four-window structure added nothing either — cascade tested the 20/15/7/3 "
            "ordering head-on and went **+1.63pp in-sample → +0.07pp out**. Four correlated views "
            "of one number is not four pieces of evidence.")
        with st.expander("The two that came closest, and why they still died"):
            st.markdown(
                "- **clipsize** is the cleanest cautionary tale: a beautifully monotone "
                "−1.04% → +1.95% factor that is just the **low-price effect**. "
                "`spearman(price, relative clip) = −0.446`, because clip size measured in *shares* "
                "mechanically rises as price falls. Denominate in rupees and it dies.\n"
                "- **wash** reproduced its claimed number exactly (+1.95% vs +1.02% baseline) but "
                "**failed in its own discovery sample** (per-date t=+0.86) and is non-monotonic "
                "once date-demeaned. The gap between the obs-weighted view (−3.01pp) and the "
                "per-date view (−0.28pp) *was* the entire signal — a date-mix artifact.")
        st.info("A bug worth keeping: old floorsheet files use CRLF and write quantity as `50.0`, "
                "so a bare `int(parts[2])` throws and **silently drops the whole day** — NABIL "
                "parsed 227 of 1,056 sessions. Parse with `int(float(x))`. On that truncated "
                "remnant one dead family looked monotone at +2.67pp. This repo's existing "
                "`operator_scan.py` and `build_broker_flow.py` use `float()` and are unaffected.")


if page == "Swing master":
    st.markdown("<div class='section'>Swing master — screen, size, and the exit plan</div>",
                unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1, 1, 2])
    cap = c1.number_input("Book size (Rs)", 10_000, 100_000_000, 100_000, step=10_000, key="sw_cap")
    rsk = c2.number_input("Risk per trade (%)", 0.25, 5.0, 1.0, step=0.25, key="sw_risk")
    if c3.button("↻ Recompute for this book size", key="sw_run", width="stretch"):
        run_job("Swing master", "swing_master.py", "--capital", str(cap), "--risk", str(rsk))
        st.rerun()

    hdr, rows = _tsv("swing_master.txt")
    st.caption("Horizon is **~20 trading days**, because that is the horizon every number here was "
               "measured over. Quantity is sized so a stop-out costs the same rupees on every "
               "trade — with a 44% base win rate, fixed loss is the only reliable control.")
    if not rows:
        st.info("No swing_master.txt yet — press Recompute. An empty list is a valid answer: "
                "the rule only fires on volume confirmation, and cash is a position.")
    else:
        df = pd.DataFrame(rows, columns=hdr)
        buys = df[df["verdict"] == "BUY"]
        a, b, c = st.columns(3)
        a.metric("Actionable BUY", len(buys))
        b.metric("On the list", len(df))
        try:
            a_risk = sum(int(r[9]) for r in rows if r[1] == "BUY")
            c.metric("Risk if all taken", f"Rs {a_risk:,}")
        except (ValueError, IndexError):
            pass
        st.dataframe(df, width="stretch", hide_index=True)
        st.caption("**WAIT** = already extended past the trigger the backtest actually entered on. "
                   "That is a different, unmeasured trade — wait for a pullback to the 20-EMA or "
                   "skip it. **hold_bars 20** is a real exit: the median NEPSE 20-day return is "
                   "negative, so time in a trade is a cost, not a neutral.")
    st.warning("Base rates so these read against something real: a random NEPSE 20-day hold wins "
               "**44%** of the time with a **negative median** — the average is carried by a thin "
               "tail. This rule won ~58% out of sample, and still lost money in 7 of the last 13 "
               "years, 2025 and 2026 included. A screen, not advice.")


if page == "Master signal":
    st.markdown("<div class='section'>Master signal — the one rule that survived testing</div>",
                unsafe_allow_html=True)
    hdr, rows = _tsv("master_signal.txt")
    st.caption("Candidates are filtered to **volume-confirmed entries on a confirmed uptrend** — "
               "the only ingredient that held its edge out of sample across 7,630 replayed trades. "
               "`edge_oos` is the measured historical average for that volume band over ~20 bars; "
               "it is a backward-looking average **with losing years inside it**, not a forecast.")
    if not rows:
        st.info("No master_signal.txt yet — run `python master_signal.py`, or press the button "
                "below. An empty list is also a normal result: the rule only fires on volume.")
    else:
        df = pd.DataFrame(rows, columns=hdr)
        st.dataframe(df, width="stretch", hide_index=True)
        buys = sum(1 for r in rows if r[1] == "BUY")
        st.caption(f"{len(rows)} candidate(s) · {buys} fresh **BUY**, {len(rows)-buys} "
                   "**WAIT-PULLBACK** (already extended past the trigger the backtest entered on — "
                   "a different, worse trade).")
    if st.button("↻ Recompute now", key="ms_run"):
        run_job("Master signal", "master_signal.py")
        st.rerun()
    st.warning("This is a screen, not advice. The same rule lost money in 7 of the last 13 years, "
               "2025 and 2026 included. Size positions on that, not on the green numbers.")


# ---------------------------------------------------------------- backtest

if page == "Backtest":
    st.markdown("<div class='section'>Backtest — what actually survives out of sample</div>",
                unsafe_allow_html=True)
    hdr, rows = _tsv("backtest.txt")
    if not rows:
        st.info("No backtest.txt yet — press **↻ Run the backtest** below. It replays ~7,600 "
                "trades and takes a few minutes.")
    # the button sits OUTSIDE the if/else on purpose: this was the one empty state in the app
    # that named a script no control could run, so it was unreachable from the phone or the VPS
    if st.button("↻ Run the backtest", key="bt_run"):
        run_job("Backtest", "backtest.py")
        st.rerun()
    if rows:
        m = rows[0]
        st.caption(f"**{m[7]} trades** · {m[9]} → {m[10]} · split at {m[8]} · "
                   f"{m[11]}% round-trip cost · max hold {m[12]} bars · entry at the **next** bar's "
                   "open · stop wins ties · prices bonus/rights adjusted.")
        tab = pd.DataFrame([{
            "variant": r[0],
            "in-sample n": int(r[1]), "in-sample win%": float(r[2]), "in-sample avg%": float(r[3]),
            "OUT-of-sample n": int(r[4]), "OUT win%": float(r[5]), "OUT avg%": float(r[6]),
            "degradation": round(float(r[3]) - float(r[6]), 2),
        } for r in rows]).sort_values("OUT avg%", ascending=False)
        st.dataframe(
            tab, width="stretch", hide_index=True,
            # the variant name auto-sizes to ~300px and eats a phone screen on its own
            column_order=["variant", "OUT avg%", "degradation", "OUT win%", "OUT-of-sample n",
                          "in-sample avg%", "in-sample win%", "in-sample n"],
            column_config={
                "variant": st.column_config.TextColumn("variant", width="medium"),
                "OUT avg%": st.column_config.NumberColumn(
                    "OUT avg%", width="small",
                    help="The only column with any claim on the future"),
                "degradation": st.column_config.NumberColumn(
                    "degradation", width="small",
                    help="in-sample avg% minus OUT avg% — big means fitted to the past")})
        st.caption("Read the **OUT avg%** column and ignore the rest. A big `degradation` means "
                   "the rule was fitted to the past — ADX and breakout both look best in-sample "
                   "and collapse out of it.")
    with st.expander("Why this page exists — the traps it caught", expanded=False):
        st.markdown(
            "- **Look-ahead via pivots.** A 5/5 pivot low isn't confirmed until 5 bars later; "
            "using it at the signal bar is peeking. Fixed — it moved every number.\n"
            "- **Unadjusted prices.** 116 drops worse than the −10% circuit (GUFL −80%) are "
            "bonus/rights resets, not trades. They were firing stops that never happened.\n"
            "- **Selection bias.** A deliberately-inverted control variant *tops* the leaderboard "
            "on one lucky year. With ~20 variants ranked on one metric, the best row is usually "
            "the luckiest — which is why a control is always included.\n"
            "- **Stacking made it worse.** A 3-condition 'master' scored +1.28% out of sample "
            "against +2.44% for plain `volume>3x`. More conditions bought overfit, not edge.")


# ---------------------------------------------------------------- heatmap

if page == "Heatmap":
    hc1, hc2, hc3, hc4, hc5 = st.columns([2, 3, 1, 1, 1])
    view = hc1.segmented_control("Heatmap", ["Stocks by sector", "Indices"],
                                 default="Stocks by sector", key="hm_view",
                                 label_visibility="collapsed") or "Stocks by sector"
    with hc2:
        heatmap_legend()
    # This page had NO auto-refresh at all: it rendered once and then held whatever it had until
    # the browser tab was reloaded by hand, which is what made a live feed look frozen. The toggle
    # exists because re-rendering a Plotly treemap resets a sector drill-down — pause it to
    # explore, leave it on to watch.
    live = hc3.toggle("Live", value=True, key="hm_live",
                      help="Table ticks every 1s. The treemap redraws on the slower clock in "
                           "the box beside this — Streamlit re-mounts a Plotly chart on every "
                           "redraw, and that teardown is the flash, so redrawing it once a "
                           "second is all cost and no information.")
    st.session_state.setdefault("hm_secs", 20)
    hc4.selectbox("Map redraw", [10, 20, 30, 60], key="hm_secs",
                  format_func=lambda v: f"map {v}s", label_visibility="collapsed")
    status, scol = market_status()
    hc5.markdown(f"<div style='text-align:right'><span style='background:{scol};color:#0d1117;"
                 f"font-weight:700;padding:2px 10px;border-radius:5px;font-size:0.82em'>{status}"
                 f"</span></div>", unsafe_allow_html=True)

    # Full-fit: the heatmap fills whatever height is left after the index table below it, so the
    # controls + heatmap + table exactly fill the screen — no gap, no scroll (flex chain does it).
    # The fragment goes INSIDE the container, not around it. Wrapping st.container(height=
    # "stretch") in a fragment inserts a DOM level that breaks the `.st-key-mainchart{flex:1 1 0}`
    # chain, and the container resolves to 0px — the treemap renders into the DOM and is invisible.
    # The treemap redraws on a SLOWER clock than the table, deliberately. Streamlit re-mounts a
    # Plotly component every time its fragment re-runs — key= and uirevision do not prevent it
    # (verified: a data-attribute tagged on the chart node is gone after one poll) — and that
    # teardown is the black flash. Sector aggregates move slowly, so a 1s redraw bought nothing
    # and cost a blink every second. The table beside it still ticks at 1s where it matters.
    hm_every = st.session_state.get("hm_secs", 20)
    with st.container(key="mainchart"):
        @st.fragment(run_every=hm_every if live else None)
        def _hm_chart():
            if view == "Stocks by sector":
                render_stock_heatmap(stock_heatmap_rows())
            else:
                render_index_heatmap(indices_snapshot())
        _hm_chart()

    @st.fragment(run_every=IDX_POLL if live else None)
    def _hm_table():
        render_index_table(indices_snapshot())
        if live:
            st.caption(f"🔴 polling every {IDX_POLL}s · last draw {datetime.now(NPT):%H:%M:%S} NPT")
    _hm_table()


# ---------------------------------------------------------------- cron

# Zone colours, matching the vendor's own chart: demand green, supply red, a broken (turncoat)
# zone purple — the same purple their MTF screenshot uses for flipped zones.
SD_FILL = {("demand", "strong"): ("rgba(18,184,134,.20)", "rgba(18,184,134,.55)"),
           ("demand", "untested"): ("rgba(18,184,134,.12)", "rgba(18,184,134,.40)"),
           ("demand", "weak"): ("rgba(152,162,179,.14)", "rgba(152,162,179,.40)"),
           ("demand", "turncoat"): ("rgba(139,92,246,.14)", "rgba(139,92,246,.40)"),
           ("supply", "strong"): ("rgba(255,107,91,.20)", "rgba(255,107,91,.55)"),
           ("supply", "untested"): ("rgba(255,107,91,.12)", "rgba(255,107,91,.40)"),
           ("supply", "weak"): ("rgba(152,162,179,.14)", "rgba(152,162,179,.40)"),
           ("supply", "turncoat"): ("rgba(139,92,246,.14)", "rgba(139,92,246,.40)")}


def sd_ticket(symbol, r):
    """The clicked row costed two ways: entry at the zone edge, and entry at the last close.

    Stop and target are price LEVELS — they do not move because you paid more — so taking the
    close instead of the zone changes only what you risk, and therefore what the trade is worth.
    That is the number the board's own `entry` column hides once price has run past the zone.
    """
    b = adjusted_bars(symbol)
    if not b:
        return ""
    when, o, h, l, c = b[0][-1], b[1][-1], b[2][-1], b[3][-1], b[4][-1]
    buy = r["direction"] == "Bullish"
    stop, target, zone = float(r["sl"]), float(r["tp"]), float(r["entry"])

    def leg(entry):
        risk = (entry - stop) if buy else (stop - entry)
        reward = (target - entry) if buy else (entry - target)
        return None if risk <= 0 else dict(
            entry=entry, risk_pct=risk / entry * 100, rr=reward / risk)

    legs = [leg(zone), leg(c)]
    money = lambda v: f"{v:,.2f}"

    def cells(fn, colour=""):
        out = []
        for g in legs:
            out.append(f"<td style='color:{colour}'>{fn(g)}</td>" if g else "<td>—</td>")
        return "".join(out)

    def rr_cell(g):
        return f"<span style='color:{GREEN if g['rr'] >= 2 else GOLD}'>{g['rr']:.2f}</span>"

    def pct_cell(g):
        return f"{g['risk_pct']:.2f}%"

    warn = ""
    if legs[1] is None:
        warn = ("<div class='warn'>Price has already closed beyond the stop — there is no trade "
                "left at the close, only the zone level as a limit order.</div>")
    elif legs[1]["rr"] < 1:
        warn = ("<div class='warn'>Taking the close pays less than it risks (R:R below 1). The "
                "zone level is the only entry that keeps their 3:1 shape.</div>")

    return (
        f"<div class='ticket'><div class='ohlc'>{symbol} · last candle {when} &nbsp;&nbsp;"
        f"O <b>{money(o)}</b>&nbsp; H <b>{money(h)}</b>&nbsp; L <b>{money(l)}</b>&nbsp; "
        f"C <b>{money(c)}</b></div><table>"
        f"<tr><th class='k'></th><th>Entry at zone</th><th>Entry at close</th></tr>"
        f"<tr><td class='k'>entry</td>{cells(lambda g: money(g['entry']), GOLD)}</tr>"
        f"<tr><td class='k'>stop</td>{cells(lambda g: money(stop), RED)}</tr>"
        f"<tr><td class='k'>target</td>{cells(lambda g: money(target), GREEN)}</tr>"
        f"<tr class='sep'><td class='k'>risk</td>{cells(pct_cell)}</tr>"
        f"<tr><td class='k'>R : R</td>{cells(rr_cell)}</tr>"
        f"</table>{warn}</div>")


def sd_chart(symbol, show, nbars=180):
    """The vendor's own chart drawn on NEPSE bars: candles, the zone boxes extended to the right
    edge, the Buy/Sell badge where price is at a zone, and the Stop Loss / Take Profit lines.

    `show` is the set of zone states to draw — their settings panel has exactly these toggles
    (weak / untested / turncoat), with the strong zone always on.
    """
    b = adjusted_bars(symbol)
    if not b or len(b[4]) < 60:
        return None, None
    d, o, h, l, c = (s[-supply_demand.MAX_BARS:] for s in (b[0], b[1], b[2], b[3], b[4]))
    row = supply_demand.dashboard(o, h, l, c)
    start = max(0, len(c) - nbars)

    fig = go.Figure(go.Candlestick(
        x=d[start:], open=o[start:], high=h[start:], low=l[start:], close=c[start:],
        showlegend=False, name=symbol,
        increasing_line_color="#12B886", decreasing_line_color="#FF6B5B",
        increasing_fillcolor="#12B886", decreasing_fillcolor="#FF6B5B"))

    # every zone born inside the window, each extended to the right edge the way they draw it
    drawn = 0
    for z in supply_demand.zones(o, h, l, c):
        if z["i"] < start:
            continue
        state = supply_demand.classify(z, h, l, c)[0]
        if state not in show:
            continue
        fill, edge = SD_FILL[(z["kind"], state)]
        fig.add_shape(type="rect", x0=d[z["i"]], x1=d[-1], y0=z["lo"], y1=z["hi"], layer="below",
                      fillcolor=fill, line=dict(color=edge, width=1))
        drawn += 1

    if row:
        # The badge goes on the LAST TRADED CANDLE, not on the bar that formed the zone — the
        # call you act on tomorrow is about today's close, and a badge parked 12 bars back reads
        # as history. An arrow pins it to that candle so there is no doubt which bar it means.
        buy = row["kind"] == "demand"
        live = row["signal"]
        colour = {"BUY": "#3B82F6", "SELL": "#FF6B5B"}.get(live, "#98A2B3")
        tail = "" if live == "WATCH" else (" (closed in zone)" if row["in_zone"] else " (wick only)")
        fig.add_annotation(
            x=d[-1], y=l[-1] if buy else h[-1], text=f" {live}{tail} · {d[-1]} ",
            showarrow=True, arrowhead=3, arrowsize=1.2, arrowwidth=1.5, arrowcolor=colour,
            ax=0, ay=40 if buy else -40, xanchor="right",
            font=dict(color="#ffffff", size=12), bgcolor=colour, borderpad=3)
        # add_shape, not add_hline — an hline's annotation leaks onto the price axis
        for level, label, colour in ((row["tp"], "Take Profit", "#12B886"),
                                     (row["sl"], "Stop Loss", "#FF6B5B"),
                                     (row["entry"], "Entry", "#F0A202")):
            fig.add_shape(type="line", x0=d[start], x1=d[-1], y0=level, y1=level,
                          line=dict(color=colour, width=1, dash="dot"))
            fig.add_annotation(x=d[-1], y=level, text=f"  {label}: {level:,.2f}", showarrow=False,
                               xanchor="left", font=dict(color=colour, size=11))

    fig.update_layout(
        # r=8 + automargin on the y axis. A fixed gutter is guessed twice over: 118 wasted 40%
        # of a phone screen, and 44 clips a 5-digit price (BNL trades at 14,900, whose label
        # needs ~45px). automargin makes Plotly reserve exactly what the labels measure.
        template="plotly_dark", height=560, margin=dict(l=8, r=8, t=34, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis_rangeslider_visible=False, dragmode="pan",
        title=dict(text=f"{symbol} · Supply Demand", font=dict(size=12)))
    fig.update_xaxes(rangebreaks=hidden_periods(d[start:], False), showgrid=False)
    fig.update_yaxes(side="right", gridcolor="rgba(255,255,255,.06)", automargin=True)
    return fig, drawn


if page == "Swing Trader Pro":
    rows = swing_pro_board()
    st.markdown("<div class='section'>Swing Trader Pro — the 22-section 1D framework, "
                "computed</div>", unsafe_allow_html=True)

    if not rows:
        st.info("No scores yet — press **Rebuild scores** at the bottom to run swing_pro.py.")
    else:
        st.caption(
            "The full professional swing checklist, run as **deterministic Python over the daily "
            "archive** — same numbers every time, every point traceable to a stated rule rather "
            "than to an opinion. **Daily bars only**, by design: mixing in 5m/1H is how a swing "
            "plan quietly becomes a day trade. Three gates veto a setup no matter how good the "
            "chart looks — **liquidity**, **risk/reward below 1:2**, and a **dangerous pullback** "
            "— which is why most of the market scores AVOID on any given day. Fundamentals come "
            "from SmartWealthPro and are a *quality filter*, never a reason to buy. Nothing here "
            "is backtested on NEPSE; it is a disciplined framework, not a proven edge.")

        keeps = {"Actionable": lambda r: r["decision"].startswith("BUY"),
                 "A + B grade": lambda r: r["grade"].startswith(("A+", "A ", "B")),
                 "Watch": lambda r: r["decision"] == "WAIT",
                 "Everything": lambda r: True}
        counts = {k: sum(1 for r in rows if f(r)) for k, f in keeps.items()}
        pick = st.radio("Show", list(keeps), horizontal=True, index=0, key="sp_pick",
                        label_visibility="collapsed", format_func=lambda k: f"{k} ({counts[k]})")
        block = [r for r in rows if keeps[pick](r)]
        block.sort(key=lambda r: -int(r["score"]))

        session = max((r["date"] for r in rows), default="")
        st.caption(f"Session **{session}** · {len(rows)} symbols scored · "
                   f"{counts['Actionable']} pass every gate · showing {len(block)}.")
        warn_if_behind_archive(session, "scores", "Rebuild scores")

        if not block:
            st.info("Nothing in this bucket today. An empty **Actionable** list is a normal "
                    "result for this framework, not a broken scan.")
        else:
            table = [{
                "symbol": r["symbol"], "grade": r["grade"], "score": int(r["score"]),
                "decision": r["decision"], "setup": r["setup"], "performer": r["performer"],
                "entry": float(r["entry"]), "stop": float(r["stop"]),
                "target1": float(r["target1"]), "target2": float(r["target2"]),
                "rr": float(r["rr"]), "risk_pct": float(r["risk_pct"]),
                "trend": r["trend"], "stage": r["stage"], "breakout": r["breakout"],
                "pullback": r["pullback"], "vol_x": float(r["vol_x"]),
                "rsi": float(r["rsi"]), "ret20": float(r["ret20"]), "flags": r["flags"],
            } for r in block]
            ev = st.dataframe(
                paint(table), width="stretch", hide_index=True,
                height=min(560, 38 * len(table) + 44),
                # decision numbers first — on a phone the later columns are off-screen, and
                # "what do I pay and where is the stop" must not be the part you scroll to
                column_order=["symbol", "decision", "entry", "stop", "target1", "rr",
                              "risk_pct", "score", "grade", "setup", "performer", "target2",
                              "trend", "stage", "breakout", "pullback", "vol_x", "rsi",
                              "ret20", "flags"],
                on_select="rerun", selection_mode="single-row", key="sp_tbl")
            st.caption("👆 **Click any row for its full 22-section report** — every section, the "
                       "100-point scorecard broken out, and the fifteen final questions.")

            picked = list(ev.selection.rows) if ev and ev.selection else []
            if picked:
                sym = block[picked[0]]["symbol"]
                with st.spinner(f"Running the full framework on {sym} …"):
                    f = swing_pro.analyse(sym, swing_pro._fundamentals(),
                                          swing_market_calendar())
                if not f:
                    st.info(f"{sym}: not enough daily history — the 200 EMA needs ~210 sessions, "
                            "and this framework will not fabricate it.")
                else:
                    st.markdown(
                        f"<div class='section'>{sym} — {f['grade']} · {f['decision']} "
                        f"<a href='?page=Chart&sym={sym}' target='_self' class='jump'>"
                        f"open chart ↗</a></div>", unsafe_allow_html=True)
                    live_vs_plan(sym, f["entry"], f["stop"], f["t1"], key="sp_live")
                    st.code(swing_pro.report(f), wrap_lines=True)
                    c1, c2 = st.columns(2)
                    with c1:
                        st.markdown("<div class='section'>Section 20 — the 100-point scorecard"
                                    "</div>", unsafe_allow_html=True)
                        st.dataframe(pd.DataFrame(
                            [{"category": n, "points": g, "max": m}
                             for n, g, m in swing_pro.scorecard(f)]
                            + [{"category": "TOTAL", "points": f["score"], "max": 100}]),
                            width="stretch", hide_index=True, height=430)
                    with c2:
                        st.markdown("<div class='section'>Section 21 — the fifteen questions"
                                    "</div>", unsafe_allow_html=True)
                        # Q9 is a WARNING question (YES = too extended = bad) and Q14 is not a
                        # yes/no at all — rendering both as a plain YES would invert the meaning
                        qrows = []
                        for n, (q, ans) in enumerate(swing_pro.answers(f), 1):
                            if isinstance(ans, str):
                                qrows.append({"": "->", "question": f"{q}  {ans}"})
                            elif n in swing_pro.WARNING_QUESTIONS:
                                qrows.append({"": "WARN" if ans else "ok",
                                              "question": f"{q}  (yes = bad)"})
                            else:
                                qrows.append({"": "YES" if ans else "no", "question": q})
                        st.dataframe(
                            pd.DataFrame(qrows), width="stretch", hide_index=True, height=430,
                            column_config={
                                "": st.column_config.TextColumn("", width="small"),
                                "question": st.column_config.TextColumn(
                                    "question", width="large")})
                    st.caption(
                        f"**Why {f['decision']}:** {f['why']}. "
                        f"**Trade invalidation:** {f['invalidation']} — and per the framework, "
                        "never widen a stop to avoid taking the loss. "
                        + (f"**False-signal flags that fired:** {', '.join(f['flags'])}."
                           if f["flags"] else "**No false-signal flag fired.**"))

    st.markdown("<div class='section'>Rebuild</div>", unsafe_allow_html=True)
    st.caption("Reads the daily bars and the SmartWealthPro fundamentals already on disk — no "
               "network, a couple of seconds for the whole market. It also runs automatically in "
               "the daily Cron pipeline.")
    if st.button("Rebuild scores", type="primary", key="sp_rebuild"):
        run_job("Swing Trader Pro", "swing_pro.py")
        st.rerun()


if page == "Supply Demand":
    rows = supply_demand_board()
    st.markdown("<div class='section'>Supply Demand Dashboard — what is at a zone in the last "
                "traded session</div>", unsafe_allow_html=True)

    if not rows:
        st.info("No board yet — press **Rebuild board** at the bottom to run supply_demand.py.")
    else:
        st.caption(
            "A port of Indicator Vault's *Supply Demand Dashboard for TradingView* onto the NEPSE "
            "archive. Their engine, our market: a fractal swing high prints a **supply** zone, a "
            "swing low a **demand** zone, the stop sits an ATR beyond the far edge and the target "
            "is exactly **3× that risk** — the ratio holds on all twelve rows published in their "
            "own screenshots, which is how it was checked. Defaults are theirs too "
            "(fuzz 0.75 · fractals 3/6 · ATR 14 · SL ×2 · TP ×3 · 700 bars). "
            "**BUY / SELL means price is touching the zone right now**, which is where their chart "
            "prints its Buy/Sell label; **WATCH** means the zone is drawn but price has not "
            "arrived. Read it as *where the levels are*, not as a tested edge — nothing here has "
            "been backtested on NEPSE, unlike **Master signal**.")

    if rows:
        keeps = {"Buy entries": lambda r: r["direction"] == "Bullish",
                 "Confirmed": lambda r: r["confirmed"] == "yes",
                 "At a zone": lambda r: r["signal"] != "WATCH",
                 "Closed in zone": lambda r: r["in_zone"] == "yes",
                 "BUY": lambda r: r["signal"] == "BUY",
                 "SELL": lambda r: r["signal"] == "SELL",
                 "Everything": lambda r: True}
        # the count rides on the label: without it a filtered board looks like a column that is
        # stuck on one value, which is exactly how "Closed in zone" as a default read
        counts = {k: sum(1 for r in rows if f(r)) for k, f in keeps.items()}
        pick = st.radio("Show", list(keeps), horizontal=True, index=0, key="sd_pick2",
                        label_visibility="collapsed",
                        format_func=lambda k: f"{k} ({counts[k]})")
        keep = keeps[pick]
        block = [r for r in rows if keep(r)]
        # View order = what you could act on soonest: the tested rule first, then price already
        # at the level, then whichever entry is nearest.
        block.sort(key=lambda r: (r["confirmed"] != "yes", r["in_zone"] != "yes",
                                  abs(float(r["dist_pct"]))))

        session = max((r["date"] for r in rows), default="")
        fired = sum(1 for r in rows if r["signal"] != "WATCH")
        held = sum(1 for r in rows if r["in_zone"] == "yes")
        st.caption(
            f"**Signals are as of the last traded candle, {session}** — this is the read for "
            f"tomorrow's open, not a historical mark. {len(rows)} symbols scanned · {fired} "
            f"touched a zone · **{held} actually closed inside one** · showing {len(block)}.")
        # this board makes the same "as of the last traded candle" claim, and it is only true
        # while the file is as new as the archive
        warn_if_behind_archive(session, "zones", "Rebuild board")

        if not block:
            st.info("Nothing in this bucket today.")
        else:
            table = [{
                "symbol": r["symbol"],
                "direction": r["direction"],
                # what order you would actually place: price is in the zone (buy it now) or the
                # zone is still away from price (rest a limit at the entry and wait)
                "order": "AT ENTRY" if r["in_zone"] == "yes" else "LIMIT",
                "age": int(r["age"]),
                "entry": float(r["entry"]),
                "sl": float(r["sl"]),
                "tp": float(r["tp"]),
                "signal": r["signal"],
                "confirmed": r["confirmed"],
                "vol_x": float(r["vol_x"]),
                "trend": r["trend"],
                "in_zone": r["in_zone"],
                "zone_history": r["state"],
                "close": float(r["close"]),
                "risk_pct": float(r["risk_pct"]),
                "dist_pct": float(r["dist_pct"]),
            } for r in block]
            ev = st.dataframe(
                paint(table), width="stretch", hide_index=True,
                height=min(620, 38 * len(table) + 44),
                column_order=["symbol", "signal", "order", "entry", "sl", "tp", "confirmed",
                              "vol_x", "trend", "in_zone", "direction", "zone_history", "age",
                              "close", "risk_pct", "dist_pct"],
                on_select="rerun", selection_mode="single-row", key="sd_tbl")
            st.caption("👆 **Click any row to chart it** — the Buy/Sell badge is pinned to the "
                       "last traded candle, with the zones and SL/TP lines behind it.")
            # ONE expander. Streamlit raises StreamlitAPIException on a nested one, which took
            # the whole Supply Demand page down until this was un-nested.
            with st.expander("ⓘ  What each column means"):
                st.caption(
                    "**order** — what you would actually place. `AT ENTRY` means the last candle "
                    "closed inside the zone, so the level is live now; `LIMIT` means price has not "
                    "come back yet, so the entry is a resting order at that price, not a fill today. "
                    "**confirmed** — the zone is being touched AND the day traded ≥1.5× its own "
                    "20-day average volume AND it is liquid enough to fill AND **trend** agrees. That "
                    "last clause matters: the rule backtest.py validated was volume confirmation *on "
                    "a confirmed uptrend*, and without it heavy volume into a FALLING stock at a "
                    "demand zone counts as confirmation — which is distribution, not accumulation. "
                    "**trend** is trade_setup's own verdict, the same one the Scanner shows. "
                    "This is the only column "
                    "here backed by measurement rather than by the vendor: across backtest.py's "
                    "7,349 replayed trades, volume confirmation was the single ingredient that kept "
                    "its edge out of sample (ADX and breakout filters both collapsed). Their "
                    "indicator reads no volume at all — this column is the bridge to **Master "
                    "signal**. **vol_x** — that volume multiple. "
                    "**in_zone** — did the last candle *close* inside the zone? A `signal` can fire "
                    "on a single wick that price left again; `in_zone = yes` is the one still standing "
                    "at tomorrow's open, which is why it sorts first. Do not read it as rare: about a "
                    "third of the market qualifies on any day, because the board picks the zone "
                    "*nearest* the close and the median zone is ~2.5% wide against a median distance "
                    "of ~1.2% — the close lands inside it often, by construction. "
                    "**direction** — Bullish is a demand zone (buy the retest), Bearish a supply zone. "
                    "**age** — bars since the zone formed. **zone_history** — how the ZONE has behaved, *not* a rating of the trade: `strong` does NOT mean strong buy, it means price came back to this zone exactly once and it held. Nothing on this board grades conviction. *untested* nobody has come back "
                    "yet, *strong* it was retested once and held, *weak* worn out by repeat visits; a "
                    "zone price CLOSED through is a *turncoat* and is dropped. **risk_pct** — entry to "
                    "stop, so a wide zone on a volatile stock costs more; **dist_pct** — how far price "
                    "sits from entry, negative meaning entry is below.")

            # --- the chart, for whichever row was clicked ------------------------------------
            picked = list(ev.selection.rows) if ev and ev.selection else []
            if picked:
                r = block[picked[0]]
                st.markdown(f"<div class='section'>{r['symbol']} — {r['direction']} "
                            f"{r['state']} zone, {r['signal']} "
                            f"<a href='?page=Chart&sym={r['symbol']}' target='_self' "
                            f"class='jump'>open chart ↗</a></div>", unsafe_allow_html=True)
                live_vs_plan(r["symbol"], float(r["entry"]), float(r["sl"]),
                             float(r["tp"]), key="sd_live")
                st.markdown(sd_ticket(r["symbol"], r), unsafe_allow_html=True)
                st.caption(
                    "Their entry is the zone edge. Once price has run past it that level is a "
                    "**limit order**, not a fill — so the same trade is costed again at the last "
                    "close, which is what you would actually pay. Stop and target do not move, so "
                    f"paying more only eats the R:R: their rule shapes the zone entry to exactly "
                    f"{supply_demand.TP_COEF:g}:1, and the close leg shows what is left of it.")
                # their settings panel has exactly these three toggles; the strong zone is always on
                t1, t2, t3, t4 = st.columns([1, 1, 1, 2])
                show = {"strong"}
                if t1.checkbox("Untested zones", value=True, key="sd_z_unt"):
                    show.add("untested")
                if t2.checkbox("Weak zones", value=False, key="sd_z_weak"):
                    show.add("weak")
                if t3.checkbox("Turncoat zones", value=False, key="sd_z_turn"):
                    show.add("turncoat")
                nbars = t4.slider("Bars", 60, 400, 120, 20, key="sd_z_bars",
                                  help="Window shown. Always widened to include this row's own "
                                       "zone, however old it is.")
                # never crop out the zone the row is about — the chart would contradict the table
                nbars = max(nbars, int(r["age"]) + 10)
                fig, drawn = sd_chart(r["symbol"], show, nbars)
                if fig is None:
                    st.info(f"Not enough history to chart {r['symbol']}.")
                else:
                    st.plotly_chart(fig, width="stretch",
                                    config={"scrollZoom": True, "displayModeBar": False})
                    st.caption(
                        f"{drawn} zone(s) born in the last {nbars} bars, each extended to the "
                        "right edge. Green is demand, red supply, purple a **turncoat** — a zone "
                        "price closed through, which flips polarity. The dotted lines are this "
                        f"row's entry {float(r['entry']):,.2f}, stop {float(r['sl']):,.2f} and "
                        f"target {float(r['tp']):,.2f}; the target is exactly "
                        f"{supply_demand.TP_COEF:g}× the stop distance, their rule. Older zones "
                        "are off-window by design — widen **Bars** to reach back for them.")

    # --- MTF: the vendor's second scanner, one symbol across seven timeframes -------------------
    st.markdown("<div class='section'>Multi-timeframe (MTF) — one symbol, seven timeframes</div>",
                unsafe_allow_html=True)
    st.caption("Their MTS board scans symbols; their MTF board scans timeframes. Same engine, "
               "built here from the minute and daily archive (their 240-minute slot is meaningless "
               "on a ~5-hour NEPSE session, so the swing frames take the top slots).")
    # symbols only — the MTF resampler reads Master_data/symbols/…, indices have no minute bars
    sd_names = sorted(n for n, kind in names.items() if kind == "symbols")
    mtf_sym = st.selectbox("Symbol", sd_names, key="sd_mtf",
                           index=sd_names.index("NABIL") if "NABIL" in sd_names else 0)
    if st.button("Scan timeframes", key="sd_mtf_go"):
        import supply_demand
        with st.spinner(f"Scanning {mtf_sym} across {len(supply_demand.TIMEFRAMES)} timeframes …"):
            # parked in session_state — a button is True for one rerun only, and without this the
            # table would vanish the moment anything else on the page is touched
            st.session_state["sd_mtf_rows"] = (supply_demand.scan_timeframes(mtf_sym), mtf_sym)
    if st.session_state.get("sd_mtf_rows"):
        tf_rows, tf_for = st.session_state["sd_mtf_rows"]
        if tf_for != mtf_sym:          # stale: the selectbox moved on since this was computed
            st.info(f"Showing **{tf_for}**. Press **Scan timeframes** to run {mtf_sym}.")
        st.caption(f"**{tf_for}**")
        if not tf_rows:
            st.info(f"Not enough history for {tf_for}.")
        else:
            st.dataframe(paint([{
                "timeframe": r["symbol"], "direction": r["direction"], "age": r["age"],
                "entry": round(r["entry"], 2), "sl": round(r["sl"], 2), "tp": round(r["tp"], 2),
                "signal": r["signal"], "zone_history": r["state"],
                "close": round(r["close"], 2),
                "risk_pct": round(r["risk_pct"], 2), "dist_pct": round(r["dist_pct"], 2),
            } for r in tf_rows]), width="stretch", hide_index=True)
            agree = {r["direction"] for r in tf_rows}
            st.caption("Every timeframe agrees — the cleanest read this board gives." if len(agree) == 1
                       else "Timeframes disagree, which is normal: the short frames turn first. "
                            "The longer frame is the context, the shorter one the timing.")

    st.markdown("<div class='section'>Rebuild</div>", unsafe_allow_html=True)
    st.caption("Reads the daily bars already on disk — no network, about ten seconds for the whole "
               "market. It also runs automatically at the end of the daily Cron pipeline, so the "
               "board matches the newest session without anyone pressing anything.")
    if st.button("Rebuild board", type="primary", key="sd_rebuild"):
        run_job("Supply Demand Dashboard", "supply_demand.py")
        st.rerun()


if page == "Cron":
    sched_state = cron_scheduler()          # start / reuse the background scheduler thread

    # Live banner — shows whichever is running now: an auto-fired scheduled job, a manual run, or
    # the older systemd service. Polls every few seconds and clears itself when idle.
    @st.fragment(run_every=1)
    def _bg_running():
        job = sched_state["running"]
        if job:                                   # pipeline running (job key, or transient "start")
            lbl = CRON_JOBS[job]["label"] if job in CRON_JOBS else "pipeline"
            st.markdown(f"<div class='cron-run-lbl'>⏳ Pipeline running — <b>{lbl}</b></div>"
                        "<div class='cron-run'></div>", unsafe_allow_html=True)
            return
        svc_running, svc_state = update_service_running()
        if svc_running:
            st.markdown("<div class='cron-run-lbl'>⏳ Scheduled update running on the server "
                        f"({svc_state})</div><div class='cron-run'></div>", unsafe_allow_html=True)
    _bg_running()

    # --- freshness snapshot ---------------------------------------------------------------------
    st.markdown("<div class='section'>Last traded data</div>", unsafe_allow_html=True)
    state = archive_state()
    session = max((v[0][:10] for v in state.values() if v[0]), default="")   # minute rows carry a time
    st.caption(f"Newest session in the archive: **{session or '—'}**. Fetching touches that one "
               "date only — new rows appended, a changed date's rows replaced — so re-running is "
               "safe. After the 15:00 NPT close it is the final session; before, a partial one.")
    st.dataframe(pd.DataFrame([{
        "Store": label,
        "Last data": stamp or "—",
        "At newest": f"{total - behind:,} / {total:,}" if total else "—",
        # same absolute rule as the sidebar — "yes" used to mean "agrees with the other
        # stores", which is "yes" for every row when the whole pipeline has stalled
        "Fresh": ("—" if not stamp else
                  "yes" if stamp[:10] == session and (_missed is not None and _missed <= 1)
                  else "no"),
    } for label, (stamp, behind, total) in state.items()]), width="stretch", hide_index=True)

    # --- MASTER pipeline: one Run-all + one timer, jobs run strictly one-by-one, colour-highlighted
    st.markdown("<div class='section' style='text-align:center'>Master data pipeline — runs one by "
                "one</div>", unsafe_allow_html=True)
    _, mid, _ = st.columns([1, 2, 1])            # narrow centered column = the flowchart spine
    with mid.container(key="cronflow"):
        busy = bool(sched_state["running"])
        if st.button("▶ Run all now (one by one)", key="run_all", type="primary", width="stretch",
                     disabled=busy):
            threading.Thread(target=sched_state["run"], daemon=True).start()
            st.rerun()

        # master timer — auto-runs the whole pipeline. Saved time(s) shown FIRST as 12h chips;
        # a compact 12-hour picker (Hour · Min · AM/PM) below to add one.
        st.markdown("<div class='cron-timebox-lbl'>⏱ Master timer — auto-runs the whole pipeline "
                    "(NPT)</div>", unsafe_allow_html=True)
        times = load_master_times()
        if times:
            cc = st.columns(len(times) + 2)
            for i, t in enumerate(times):
                if cc[i].button(f"⏱ {to_12h(t)}  ✕", key=f"rmmaster_{t}", help="Remove this time"):
                    times.remove(t)
                    save_master_times(times)
                    st.rerun()
        else:
            st.caption("No time set — pick one below and Add.")
        hc, mc2, pc, addc = st.columns([1, 1, 1, 2])
        hour = hc.selectbox("Hour", list(range(1, 13)), index=2, key="mh",
                            label_visibility="collapsed")
        minute = mc2.selectbox("Min", [f"{m:02d}" for m in range(0, 60, 5)], index=3, key="mm",
                              label_visibility="collapsed")
        ampm = pc.selectbox("AM/PM", ["AM", "PM"], index=1, key="map", label_visibility="collapsed")
        if addc.button("＋ Add time", key="addmaster", width="stretch"):
            h24 = (hour % 12) + (12 if ampm == "PM" else 0)
            hhmm = f"{h24:02d}:{minute}"
            if hhmm not in times:
                times.append(hhmm)
                save_master_times(times)
            st.rerun()

        # colour-highlighted flowchart: nodes turn orange (running) → green (done) / red (failed),
        # auto-refreshing so you watch the sequence advance one node at a time.
        _COL = {"idle": "#232833", "pending": "#39404d", "running": "#e07a3f",
                "done": "#1c8b45", "failed": "#8b1a1a"}
        _ICO = {"idle": "⚪", "pending": "🕓", "running": "⏳", "done": "✅", "failed": "❌"}

        @st.fragment(run_every=1)
        def _flow():
            running = sched_state["running"]
            html = ["<div class='cron-arrow'>↓</div>"]
            for n, (job, cfg) in enumerate(CRON_JOBS.items()):
                if n:
                    html.append("<div class='cron-arrow'>↓</div>")
                stt = sched_state["status"].get(job, "idle")
                pulse = " cron-node-running" if stt == "running" else ""
                html.append(
                    f"<div class='cron-node{pulse}' style='background:{_COL[stt]}'>"
                    f"<b>{_ICO[stt]} {cfg['label']}</b><br><span>{' → '.join(cfg['scripts'])}</span></div>")
            st.markdown("".join(html), unsafe_allow_html=True)
            if running and running in CRON_JOBS:
                st.markdown("<div class='cron-run'></div>", unsafe_allow_html=True)
                st.markdown(f"<div class='cron-flow-foot'>⏳ running <b>{CRON_JOBS[running]['label']}</b>"
                            " — waits for it to finish before the next</div>", unsafe_allow_html=True)
            else:
                st.markdown(f"<div class='cron-flow-foot'>last full run: "
                            f"{sched_state['last'] or '—'}</div>", unsafe_allow_html=True)
        _flow()

    st.caption("Daily run = **latest session only** (that's *last traded data* — today's rows, not "
               "history). A job starts only after the previous finishes; a master time already past "
               "today is caught up once. (Sun–Thu.)")

    # --- SEPARATE section: full-archive backfill (re-walks ALL history — slow, on demand) --------
    st.markdown("<div class='section' style='text-align:center;margin-top:14px'>📚 Full history "
                "backfill — all dates (rare · slow)</div>", unsafe_allow_html=True)
    _, hmid, _ = st.columns([1, 2, 1])
    with hmid.container(key="cronflow_hist"):
        st.caption("Re-fetches the WHOLE archive (years of bars / floorsheet). Run once for a new "
                   "symbol or to repair gaps — the daily pipeline above keeps things current.")

        @st.fragment(run_every=1)
        def _hist():
            for job, cfg in HIST_JOBS.items():
                stt = sched_state["hist_status"].get(job, "idle")
                pulse = " cron-node-running" if stt == "running" else ""
                st.markdown(f"<div class='cron-node{pulse}' style='background:{_COL[stt]}'>"
                            f"<b>{_ICO[stt]} {cfg['label']}</b><br>"
                            f"<span>{' → '.join(cfg['scripts'])}</span></div>", unsafe_allow_html=True)
                if st.button("▶ Backfill now", key=f"hist_{job}", width="stretch",
                             disabled=(stt == "running")):
                    threading.Thread(target=sched_state["run_hist"], args=(job,), daemon=True).start()
                    st.rerun()
                last = sched_state["hist_last"].get(job)
                st.markdown(f"<div class='cron-flow-foot'>runs in the background · last: {last or '—'}"
                            "</div>", unsafe_allow_html=True)
        _hist()

    log = Path(__file__).parent / "update_log.txt"
    if log.exists():
        st.markdown("<div class='section'>Last run</div>", unsafe_allow_html=True)
        st.code("===" + log.read_text(encoding="utf-8").strip().rsplit("===", 1)[-1])


# ---------------------------------------------------------------- volume spike

if page == "Volume spike":
    rows, screened = spike_screen()
    st.markdown("<div class='section'>Volume spike &amp; broker flow</div>", unsafe_allow_html=True)

    if not rows:
        st.info("No screen yet — press **Rebuild screen** below to run volume_spike.py.")
        rows = []
    if rows:
        st.caption(
            f"Session {screened}. Unusual volume, with the heaviest brokers on each side. "
            "**Activity screen, not a buy list, and not proof of an operator.** Two things the "
            "data forced: (1) a volume spike is *broad* — across 52k windows the top broker holds "
            "only ~8% of a spike, no more than on a quiet day, so a spike is many brokers, not "
            "one; the floorsheet also shows brokers, not clients, so concentration can never "
            "prove a single hand. (2) The spike is late — a flagged window already ran +22% over "
            "the prior 30 days, then returns −1.0% over the next 30 vs +1.6% unflagged. The one "
            "column with measured forward signal is **net_churn** (low = stock round-tripped "
            "between brokers). Read the rest as *look here*, then check the chart and floorsheet.")

    if st.session_state.get("spike_win") not in WINDOW_LABEL:
        st.session_state["spike_win"] = "2w"        # a segmented_control starts unselected
    label = st.segmented_control("Window", list(WINDOW_LABEL), key="spike_win",
                                 label_visibility="collapsed",
                                 format_func=lambda k: WINDOW_LABEL[k]) or "2w"
    only = st.radio("Show", ["Flagged only", "Everything"], horizontal=True,
                    index=1, key="spike_filter", label_visibility="collapsed")

    block = [r for r in rows if r["window"] == label]
    if only == "Flagged only":
        block = [r for r in block if r["flags"] != "-"]

    if not block:
        st.info("Nothing flagged in this window. Switch to *Everything* to see the full screen.")
    else:
        table = [{
            "symbol": r["symbol"],
            "spike_z": float(r["spike_z"]),
            "vol_x": float(r["spike_ratio"]),
            "net_churn": float(r["net_churn"]),
            "top_buyer": r["top_buyer"],
            "buyer_net": float(r["buyer_net"]),
            "top_seller": r["top_seller"],
            "seller_net": float(r["seller_net"]),
            "prior30_pct": float(r["prior30"]),
            "window_pct": float(r["window_pct"]),
            "vs_market": float(r["rel_move"]),
            "kind": r["kind"],
            "flags": r["flags"],
        } for r in block]
        st.dataframe(paint(table), width="stretch", hide_index=True,
                     height=min(560, 38 * len(table) + 44))
        st.caption(
            "**spike_z** — log-volume z against a 60-session baseline that ends before the "
            "window; ≥2.5 (3d) / ≥2.0 (2w, 1m) is roughly a once-a-year event for that stock. "
            "**net_churn** — share of volume that genuinely changed hands between brokers; "
            "**low is the interesting reading**, it means stock going round in circles. "
            "**top_buyer / top_seller** are NEPSE member codes with their net in shares. "
            "**prior30_pct** is the run-up *before* the flag — usually where the move already was.")

    st.markdown("<div class='section'>Rebuild</div>", unsafe_allow_html=True)
    st.caption("Reads the daily bars and broker_flow already on disk — no network, a few seconds. "
               "Run it after a Cron fetch so the screen reflects the newest session.")
    if st.button("Rebuild screen", type="primary"):
        run_job("Volume spike screen", "volume_spike.py")
        st.rerun()


# ---------------------------------------------------------------- operator radar

if page == "Operator radar":
    rows = operator_scan()
    st.markdown("<div class='section'>Who is buying more / selling more</div>",
                unsafe_allow_html=True)
    # operator_scan.txt carries no date of its own, so state the archive session it was built on
    # and when the file was written — this page used to show undated numbers.
    _osp = MASTER / "operator_scan.txt"
    if _osp.exists():
        st.caption(f"Archive session **{_session or '—'}** · scan file written "
                   f"{datetime.fromtimestamp(_osp.stat().st_mtime):%Y-%m-%d %H:%M}.")
    st.caption(
        "A stock is **PROVEN** only when **three independent sources agree**: "
        "**(1) Floorsheet** — one broker is the dominant net buyer/seller, regularly, still going "
        "across 1 month → 3 weeks → last 3 days; **(2) Chart** — price + volume alone (A/D line + "
        "up-vs-down volume, no broker data) show the same accumulation/distribution; "
        "**(3) Position** — that broker's net is a real slice of the free float. The chart leg is "
        "a true cross-check, so a false broker reading cannot fake a PROVEN. Strong candidate, "
        "not legal proof — the floorsheet shows the broker, not the client.")

    if not rows:
        st.info("No scan yet — run `python operator_scan.py`.")
    else:
        def yn(b):
            return "✅" if b else "▫️"

        def hit(val, ok):
            """Colour a result green (passes) or red (fails)."""
            return f"<b style='color:{'#3fb950' if ok else '#f85149'}'>{val}</b>"

        def mut(s):
            return f"<span style='color:#6b7280'>{s}</span>"

        def calc(rows, tone="#3a4250"):
            """'Show the math' strip under a claim. rows = list of (label, formula, coloured_result).
            Rendered as a compact aligned grid; tone sets the left-border accent colour."""
            body = "".join(
                f"<div style='display:flex;gap:10px;align-items:baseline'>"
                f"<span style='color:#6b7280;min-width:96px'>{lbl}</span>"
                f"<span style='color:#9aa4b2;flex:1'>{formula}</span>"
                f"<span style='text-align:right'>{res}</span></div>"
                for lbl, formula, res in rows)
            st.markdown(
                f"<div style='font-size:0.78em;background:rgba(255,255,255,0.03);"
                f"border-left:3px solid {tone};padding:6px 12px;margin:-2px 0 13px 20px;"
                "font-family:ui-monospace,SFMono-Regular,Menlo,monospace;line-height:1.7'>"
                f"{body}</div>", unsafe_allow_html=True)

        def card(r):
            reg = float(r["regular_pct"]); accel = float(r["accel"]); dom = float(r["vol_dom"])
            z = float(r["vol_z"]); pf = float(r["pct_float"]); pv = float(r["price_vs"])
            n1, n2, n3 = float(r["net_1m"]), float(r["net_3w"]), float(r["net_3d"])
            ad = float(r["ad_trend"]); ud = float(r["updown_vol"]); pchg = float(r["price_chg"])
            fok = r["floor_ok"] == "yes"; cok = r["chart_ok"] == "yes"; pok = r["pos_ok"] == "yes"
            proof = int(r["proof"])
            pubsh = float(r.get("pub_shares", 0) or 0); bbuy = float(r.get("broker_buy", 0) or 0)
            tape = float(r.get("tape_vol", 0) or 0)
            actd = int(float(r.get("active_days", 0) or 0)); samed = int(float(r.get("same_days", 0) or 0))
            verb = "buying" if r["side"] == "BUY" else "selling"
            head = f"{'🟢' if r['side']=='BUY' else '🔴'} {r['symbol']}  ·  broker {r['broker']}  ·  {r['verdict']}"
            with st.expander(head, expanded=(proof == 3)):
                st.markdown(f"**{r['symbol']}: {proof} of 3 independent sources agree**")
                st.markdown(
                    f"- {yn(fok)} **1 · Floorsheet (who)** — broker {r['broker']} is the dominant net "
                    f"{verb.rstrip('ing')}er: **{reg:.0f}%** of days same way, **{dom:.0f}%** of the whole "
                    f"tape, still going ({accel:.1f}× pace), 1m **{n1:+,.0f}** → 3w **{n2:+,.0f}** → "
                    f"3d **{n3:+,.0f}**")
                calc([
                    ("buys same way", f"{samed}/{actd} days", hit(f"{reg:.0f}%", reg >= 70) + " " + mut("≥70")),
                    ("share of tape", f"{bbuy:,.0f} ÷ {tape:,.0f}", hit(f"{dom:.0f}%", dom >= 15) + " " + mut("≥15")),
                    ("still going", f"({n3:+,.0f}/3) ÷ ({n1:+,.0f}/20)", hit(f"{accel:.2f}×", accel >= 0.7) + " " + mut("≥0.7")),
                    ("cascade 1m·3w·3d", f"{n1:+,.0f} · {n2:+,.0f} · {n3:+,.0f}", hit("all one way" if fok else "mixed", fok)),
                ], tone="#3fb950" if fok else "#f85149")
                st.markdown(
                    f"- {yn(cok)} **2 · Chart / price+volume (the tape, no broker data)** — "
                    f"A/D line **{'rising' if ad>0 else 'falling'}** ({ad:+.2f}), up-day vs down-day "
                    f"volume **{ud:.1f}×**, price **{pchg:+.1f}%** over the month "
                    f"→ tape {'confirms' if cok else 'does NOT confirm'} {verb}")
                calc([
                    ("A/D slope", "rising" if ad > 0 else "falling", hit(f"{ad:+.2f}", ad > 0) + " " + mut(">0")),
                    ("up ÷ down vol", "up-day vs down-day", hit(f"{ud:.2f}×", ud >= 1.0) + " " + mut("≥1.0")),
                    ("month price", "close-to-close", hit(f"{pchg:+.1f}%", pchg > -8) + " " + mut(">−8")),
                ], tone="#3fb950" if cok else "#f85149")
                st.markdown(
                    f"- {yn(pok)} **3 · Position (size)** — the 1-month net is **{pf:+.1f}%** of the free "
                    f"float (float only {float(r['float_pct']):.0f}% of the company)")
                calc([
                    ("net vs float", f"{n1:+,.0f} ÷ {pubsh:,.0f}", hit(f"{pf:+.1f}%", abs(pf) >= 3) + " " + mut("≥3")),
                    ("free float", f"{pubsh:,.0f} shares", mut(f"{float(r['float_pct']):.0f}% of company")),
                ], tone="#3fb950" if pok else "#f85149")
                n3m = float(r.get("net_3m", 0) or 0); pct3m = float(r.get("pct_3m", 0) or 0)
                coord2 = float(r.get("coord2_pct", 0) or 0)
                long_campaign = abs(pct3m) >= abs(pf) * 1.5   # much bigger over 3m = long-running
                st.markdown(f"**Campaign length** — over **3 months** this broker's net is "
                            f"**{n3m:+,.0f}** ({pct3m:+.1f}% of float), vs **{n1:+,.0f}** in the last month. "
                            + ("This is a **long-running** campaign (been building for months)."
                               if long_campaign else
                               "Most of it is **recent** (a fresh 1-month move, not a long campaign)."))
                calc([
                    ("3-month net", f"{n3m:+,.0f} ÷ {pubsh:,.0f}", f"{pct3m:+.1f}% of float"),
                    ("vs 1-month", f"{n1:+,.0f}", f"3m ÷ 1m = {(n3m/n1 if n1 else 0):.1f}×"),
                ], tone="#4a5568")
                coordinated = abs(coord2) >= abs(pf) * 1.6 and abs(coord2 - pf) >= 2
                st.markdown(f"**Coordination** — the top-2 brokers on this side hold **{coord2:+.1f}%** of "
                            f"float combined vs **{pf:+.1f}%** for this one. "
                            + ("A **second broker** is moving the same way — possible split/coordinated buying."
                               if coordinated else
                               "No meaningful second broker — this is a **single-broker** campaign."))
                calc([
                    ("top-2 brokers", "combined net ÷ float", f"{coord2:+.1f}%"),
                    ("this broker", "alone", f"{pf:+.1f}%  ·  gap {coord2 - pf:+.1f}pp"),
                ], tone="#4a5568")
                ad_days = float(r.get("accum_days", 0) or 0)
                grab = ("a **heavy, aggressive** grab" if ad_days >= 5 else
                        "a **moderate** position" if ad_days >= 2 else "a **light** position")
                st.markdown(f"**How hard they're cornering it** — broker {r['broker']}'s net equals "
                            f"**{ad_days:.1f} days** of this stock's entire average volume — {grab}. "
                            "The higher this is, the more of the tradeable liquidity one hand is quietly holding.")
                calc([
                    ("days to grab", f"|{abs(n1):,.0f}| × 20 ÷ {tape:,.0f}", hit(f"{ad_days:.1f} days", ad_days >= 5)),
                ], tone="#3fb950" if ad_days >= 5 else "#4a5568")
                rs = r.get("rel_str", "")
                if str(rs).strip() not in ("", "None"):
                    rs = float(rs); scg = float(r.get("sector_chg", 0) or 0); sec = r.get("sector", "its sector")
                    pc = float(r["price_chg"]); moved = f"{abs(pc):.0f}% {'up' if pc >= 0 else 'down'}"
                    want_up = r["side"] == "BUY"
                    idio = rs >= 3 if want_up else rs <= -3
                    if idio:
                        st.success(f"**Moving on its own** — this stock is **{moved}** while its **{sec}** "
                                   f"peers are **{scg:+.0f}%** — a **{rs:+.0f}pp** gap. The move is "
                                   "idiosyncratic, not sector beta: someone is working *this* stock, which "
                                   "is exactly what an operator looks like.")
                    elif abs(rs) < 3:
                        st.info(f"**Moving with its sector** — **{sec}** peers are **{scg:+.0f}%** and this "
                                f"is only **{rs:+.0f}pp** different. The move may be sector-wide beta, not a "
                                "single-stock operator — weaker confirmation.")
                    else:
                        st.warning(f"**Going against the flow** — {sec} peers are **{scg:+.0f}%** but this is "
                                   f"**{rs:+.0f}pp** the other way. The broker's side isn't pushing price past "
                                   "peers — treat the read with caution.")
                    calc([
                        ("rel strength", f"{pc:+.1f}% − {scg:+.1f}% ({sec})", hit(f"{rs:+.1f}pp", idio)),
                    ], tone="#3fb950" if idio else ("#f0a441" if abs(rs) < 3 else "#f85149"))
                py = r.get("profit_yoy", "")
                if str(py).strip() not in ("", "None"):
                    py = float(py); per = r.get("earn_period", "latest quarter")
                    if abs(py) > 300:
                        st.caption(f"Earnings ({per}): net profit {py:+.0f}% YoY — but off a small/volatile "
                                   "base, so read the growth number with care.")
                    elif py >= 30:
                        st.markdown(f"**Earnings backdrop** — {per} net profit **{py:+.0f}% YoY**. There *is* a "
                                    "real earnings story, so the move isn't in a vacuum — it may be "
                                    "fundamentally driven (or a broker front-running a strong result).")
                    elif py >= 0:
                        st.markdown(f"**Earnings backdrop** — {per} net profit **{py:+.0f}% YoY**, only modest. "
                                    "The accumulation isn't well explained by earnings — more consistent with "
                                    "operator flow than a fundamental re-rating.")
                    else:
                        st.markdown(f"**Earnings backdrop** — {per} net profit **{py:+.0f}% YoY** (falling), yet "
                                    "the stock is being worked. Flow *against* the fundamentals — a "
                                    "speculative / operator footprint.")
                    calc([
                        ("net profit YoY", f"SWP · {per}", f"{py:+.1f}%"),
                    ], tone="#4a5568")
                    ey = r.get("eps_yoy", "")
                    if str(ey).strip() not in ("", "None") and abs(py) <= 300:
                        ey = float(ey)
                        if abs(ey - py) > 10:
                            st.caption(f"Per-share view — EPS TTM grew **{ey:+.0f}%**, less than profit; the "
                                       "gap is bonus-share dilution (more shares splitting the same earnings).")
                        else:
                            st.caption(f"Per-share view — EPS TTM **{ey:+.0f}%**, in line with profit.")
                h1 = float(r.get("net_half1", 0) or 0); h2 = float(r.get("net_half2", 0) or 0)
                want_up = r["side"] == "BUY"
                dh1 = h1 if want_up else -h1; dh2 = h2 if want_up else -h2   # +ve = in operator's direction
                if dh1 <= 0 < dh2:
                    st.markdown(f"**Campaign trend** — **fresh**: almost all of it is in the **last 2 weeks** "
                                f"(first half ≈ 0, second half {abs(dh2):,.0f} shares). A newly started push.")
                elif dh2 <= 0 < dh1:
                    st.markdown(f"**Campaign trend** — **cooling off**: active early ({abs(dh1):,.0f} shares) but "
                                "essentially **stopped** in the last 2 weeks. The push may be over.")
                elif dh1 > 0 and dh2 > dh1 * 1.3:
                    st.markdown(f"**Campaign trend** — **intensifying**: bigger in the last 2 weeks "
                                f"({abs(dh2):,.0f}) than the first ({abs(dh1):,.0f} shares). Accelerating.")
                elif dh1 > 0 and dh2 < dh1 * 0.7:
                    st.markdown(f"**Campaign trend** — **fading**: lighter recently ({abs(dh2):,.0f}) than early "
                                f"({abs(dh1):,.0f} shares). Momentum is cooling.")
                else:
                    st.markdown(f"**Campaign trend** — **steady**: evenly paced across the month "
                                f"({abs(dh1):,.0f} then {abs(dh2):,.0f} shares).")
                calc([
                    ("first half", "sessions 1-10", f"{h1:+,.0f}"),
                    ("second half", "sessions 11-20", f"{h2:+,.0f}"
                     + (f"  ·  {dh2/dh1:.2f}× of 1st" if dh1 > 0 else "")),
                ], tone="#4a5568")
                ld = r.get("lockin_days", "")
                if str(ld).strip() not in ("", "None"):
                    ld = int(float(ld)); exp = r.get("lockin_expiry", "")
                    if ld < 0:
                        st.caption(f"Promoter lock-in already expired on {exp} — promoter shares are now "
                                   "freely sellable (overhang to watch, but the unlock is behind us).")
                    elif ld <= 45:
                        st.error(f"⚠ **Promoter lock-in expires in {ld} days** (on {exp}). Locked promoter "
                                 "shares can hit the market — a supply flood that often caps or crashes the "
                                 "price. Risky to ride an accumulation into this; wait until after it clears.")
                    elif ld <= 120:
                        st.warning(f"**Promoter lock-in expires in {ld} days** (on {exp}). Watch for promoter "
                                   "selling as the date nears — extra supply can stall the move.")
                    else:
                        st.caption(f"Promoter lock-in expires {exp} ({ld} days out) — far enough not to matter yet.")
                else:
                    st.caption("No promoter lock-in pending — the free float is stable (no supply shock ahead).")
                st.caption(f"Avg {verb} price Rs {float(r['avg_price']):,.0f} vs now Rs {float(r['close']):,.0f} "
                           f"({pv:+.1f}%).  Broker {r['broker']} is heavy in {r['breadth']} stocks.  "
                           "The chart leg uses NO broker data, so it is a true independent cross-check.")

                # exact day-by-day + period totals for THIS broker, newest = 1d
                led = broker_ledger(r["symbol"], r["broker"])
                if led:
                    st.markdown(f"**Broker {r['broker']}'s exact buying & selling in {r['symbol']} "
                                "(1d = today):**")
                    win_days = {"7 days": 7, "15 days": 15, "1 month": 22}
                    wkey = f"win_{r['symbol']}_{r['broker']}_{r['side']}"
                    if st.session_state.get(wkey) not in win_days:
                        st.session_state[wkey] = "7 days"
                    span = st.segmented_control("window", list(win_days), key=wkey,
                                                label_visibility="collapsed") or "1 month"
                    ndays = win_days[span]
                    recent = led[-ndays:][::-1]               # newest first, chosen window
                    day = []
                    for i, d in enumerate(recent):
                        prev = recent[i + 1] if i + 1 < len(recent) else None
                        chg = d[3] - prev[3] if prev else 0    # net today − net previous day
                        base = abs(prev[3]) if prev and prev[3] else 0
                        pct = (chg / base * 100) if base else 0
                        day.append({
                            "day": f"{i+1}d", "date": d[0], "bought": d[1], "sold": d[2], "net": d[3],
                            "vs_prev": chg, "vs_prev_pct": round(pct, 1) if prev else 0,
                        })

                    # totals for the SELECTED window (led[-ndays:])
                    sel = led[-ndays:]
                    tot_buy = sum(x[1] for x in sel)
                    tot_sell = sum(x[2] for x in sel)
                    tot_left = tot_buy - tot_sell
                    # green/red bar of daily net — buying above zero, selling below
                    def kfmt(v):
                        a = abs(v)
                        if a >= 1e6: return f"{v/1e6:.2f}M"
                        if a >= 1e4: return f"{v/1e3:.0f}k"     # 10k+ : whole k is precise enough
                        if a >= 1e3: return f"{v/1e3:.1f}k"     # 1-10k : one decimal (3.4k vs 3.8k)
                        return f"{v:,.0f}"

                    order = day[::-1]                          # oldest left, newest (1d) right
                    # NET chart: one two-line label OUTSIDE each bar — quantity (yellow) + date.
                    # Outside placement means it reads cleanly whether the bar is huge or a sliver.
                    lbl = [f'<b>{kfmt(d["net"])}</b><br>'
                           f'<span style="color:#8A93A5;font-size:0.85em">{d["date"]}</span>'
                           for d in order]
                    barfig = go.Figure(go.Bar(
                        x=[d["day"] for d in order], y=[d["net"] for d in order],
                        marker_color=[GREEN if d["net"] >= 0 else RED for d in order],
                        marker_line_width=0,
                        text=lbl, textposition="outside", textangle=0,
                        textfont=dict(size=13, color="#FFE066"), cliponaxis=False,
                        hovertext=[f"{d['date']}  net {d['net']:+,.0f}" for d in order],
                        hoverinfo="text"))
                    barfig.update_layout(
                        height=280, margin=dict(l=0, r=0, t=42, b=26), template="plotly_dark",
                        paper_bgcolor="#171A21", plot_bgcolor="#171A21", showlegend=False, bargap=0.25,
                        yaxis_title="net shares / day",
                        hoverlabel=dict(bgcolor="#20242F", font=dict(color="#E6E8EC", size=11)))
                    # headroom so the two-line label (positive above / negative below) never clips
                    nvals = [d["net"] for d in order]
                    hi, lo = max(nvals + [0]), min(nvals + [0])
                    npad = (hi - lo) * 0.42 or 1
                    barfig.update_xaxes(gridcolor="rgba(120,123,134,.12)")
                    barfig.update_yaxes(range=[lo - npad if lo < 0 else 0, hi + npad],
                                        gridcolor="rgba(120,123,134,.12)", zerolinecolor="#394050")
                    st.caption("Daily NET — quantity (yellow) and date shown above each bar; green = net buying, red = net selling")
                    st.plotly_chart(barfig, width="stretch",
                                    config={"displayModeBar": False, "staticPlot": True},
                                    key=f"opbar_{r['symbol']}_{r['broker']}_{r['side']}")

                    # SELL chart below: how much this broker sold each day (red)
                    sellfig = go.Figure(go.Bar(
                        x=[d["day"] for d in order], y=[d["sold"] for d in order],
                        marker_color=RED, marker_line_width=0,
                        text=[kfmt(d["sold"]) if d["sold"] else "" for d in order],
                        textposition="outside", textfont=dict(size=13, color="#FFB3AA"),
                        cliponaxis=False,
                        hovertext=[f"{d['date']}  sold {d['sold']:,.0f}" for d in order],
                        hoverinfo="text"))
                    sellfig.update_layout(
                        height=180, margin=dict(l=0, r=0, t=18, b=26), template="plotly_dark",
                        paper_bgcolor="#171A21", plot_bgcolor="#171A21", showlegend=False, bargap=0.25,
                        yaxis_title="sold / day",
                        hoverlabel=dict(bgcolor="#20242F", font=dict(color="#E6E8EC", size=11)))
                    smax = max([d["sold"] for d in order] + [1])
                    sellfig.update_xaxes(gridcolor="rgba(120,123,134,.12)")
                    sellfig.update_yaxes(range=[0, smax * 1.3], gridcolor="rgba(120,123,134,.12)")
                    st.caption("Daily SELL quantity — how many shares this broker sold each day (red)")
                    st.plotly_chart(sellfig, width="stretch",
                                    config={"displayModeBar": False, "staticPlot": True},
                                    key=f"opsell_{r['symbol']}_{r['broker']}_{r['side']}")

                    st.caption("Day by day — vs_prev = how many more/fewer net shares than the day before")
                    st.dataframe(paint(day), width="stretch", hide_index=True,
                                 height=min(360, 36 * len(day) + 40))

                    def words(n):                              # South-Asian readable form
                        a = abs(n); sign = "-" if n < 0 else ""
                        if a >= 1e7: return f"{sign}{a/1e7:.2f} crore"
                        if a >= 1e5: return f"{sign}{a/1e5:.2f} lakh"
                        if a >= 1e3: return f"{sign}{a/1e3:.1f} thousand"
                        return f"{sign}{a:.0f}"
                    st.markdown(f"**Totals over the last {span}:**")
                    t1, t2, t3 = st.columns(3)
                    t1.metric("Total buy quantity", f"{tot_buy:,.0f} shares", help=words(tot_buy))
                    t2.metric("Total sell quantity", f"{tot_sell:,.0f} shares", help=words(tot_sell))
                    t3.metric("Net kept (buy − sell)", f"{tot_left:+,.0f} shares", help=words(tot_left))
                    kept = (f"net **buying {words(tot_left)}**" if tot_left > 0
                            else f"net **selling {words(-tot_left)}**")
                    st.caption(f"In words — over the last {span}, broker {r['broker']} bought "
                               f"**{words(tot_buy)}** and sold **{words(tot_sell)}** — {kept} shares.")

                trade_setup_div(r["symbol"])
                render_market_depth(r["symbol"])

        def trade_setup_div(symbol):
            """Separate panel: the professional technical A+ setup + buy/target/stop levels."""
            ts = get_trade_setup(symbol)
            if not ts:
                return
            def f0(x): return f"{x:,.0f}" if isinstance(x, (int, float)) else "—"
            def f1(x): return f"{x:,.1f}" if isinstance(x, (int, float)) else "—"
            def f2(x): return f"{x:+.2f}" if isinstance(x, (int, float)) else "—"
            def gt(a, b): return a is not None and b is not None and a > b
            sig = ts["signal"]
            col = {"STRONG BUY": "#2ea043", "BUY": "#3fb950", "WATCH": "#d29922",
                   "SELL": "#f85149", "STRONG SELL": "#da3633"}.get(sig, "#6b7280")
            st.markdown("<hr style='margin:16px 0 8px;border:none;border-top:1px solid #2b3240'>",
                        unsafe_allow_html=True)
            # Two separate things: the badge = the SETUP signal (direction + quality); the chip =
            # today's ENTRY timing. A grade-A BUY setup can still be "wait" if price already ran.
            # The entry-timing chip is about BUYING, so only show it on a long/buy setup.
            is_long = sig in ("STRONG BUY", "BUY", "WATCH")
            sb = ts.get("still_buy") if is_long else None
            timing = ("<span style='background:#d29922;color:#0d1117;font-weight:700;padding:2px 10px;"
                      "border-radius:5px;font-size:.82em'>⏳ entry: wait (extended)</span>" if sb is False
                      else "<span style='background:#2ea043;color:#0d1117;font-weight:700;padding:2px 10px;"
                      "border-radius:5px;font-size:.82em'>✅ entry: buyable now</span>" if sb is True
                      else "")
            st.markdown(
                "<div style='display:flex;align-items:center;gap:12px;flex-wrap:wrap'>"
                "<span style='font-weight:700;font-size:1.03em'>📐 Professional trade setup</span>"
                f"<span style='background:{col};color:#0d1117;font-weight:700;padding:2px 11px;"
                f"border-radius:5px'>{sig} setup</span>"
                f"<span style='color:#8a93a5'>score <b style='color:{col}'>{ts['score']}/100</b>"
                f" · grade {ts['grade']}</span>{timing}</div>", unsafe_allow_html=True)
            st.caption(f"Daily (1D) as of {ts['date']} · liquidity → 200-SMA → 20/50-EMA → volume → ADX "
                       "→ RSI → MACD → ATR risk. The **badge is the setup signal** (is this a good buy "
                       "setup?); the **chip is today's entry timing** (can you get in *now*?) — a "
                       "grade-A BUY can still say *wait* if price already extended.")
            if is_long and ts["target1"] and ts["stop"]:
                e = ts["entry"]
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Entry (buy)", f"Rs {f0(e)}")
                m2.metric("Target 1", f"Rs {f0(ts['target1'])}", f"{(ts['target1']/e-1)*100:+.1f}%")
                m3.metric("Target 2", f"Rs {f0(ts['target2'])}", f"{(ts['target2']/e-1)*100:+.1f}%")
                m4.metric("Stop loss", f"Rs {f0(ts['stop'])}", f"{(ts['stop']/e-1)*100:+.1f}%")
                st.caption(f"Risk **{ts['risk_pct']}%** per share · reward:risk to T2 ≈ **{ts['rr']}:1** · "
                           f"support {f0(ts['support'])} / resistance {f0(ts['resistance'])} · ATR(14) {f1(ts['atr'])}")
                if ts.get("buy_date"):
                    ago = ts["days_active"]
                    when = "today" if ago == 0 else ("yesterday" if ago == 1 else f"{ago} sessions ago")
                    st.markdown(f"**Buy signal date** — triggered on **{ts['buy_date']}** ({when}) at "
                                f"Rs {f0(ts['buy_close'])}. Price is **{ts['runup']:+.1f}%** from the trigger now.")
                    oc = ts.get("outcome")
                    if oc:
                        vb, vc = {
                            "t2": ("🎯 Both targets reached — full winner", "#2ea043"),
                            "t1": ("🟢 Target 1 reached", "#3fb950"),
                            "stopped": ("🔴 Stopped out — the trade lost", "#da3633"),
                            "open": ("⚪ Still open — neither target nor stop hit yet", "#d29922"),
                        }[oc["verdict"]]
                        st.markdown(f"<b>Did it work?</b> from that entry (Rs {f0(oc['entry'])}) → "
                                    f"<span style='color:{vc};font-weight:700'>{vb}</span> · "
                                    f"best move since <b>{oc['peak_pct']:+.1f}%</b>", unsafe_allow_html=True)
                        calc([
                            ("target 1", f"Rs {f0(oc['t1'])}",
                             hit(f"hit {oc['t1_date']}", True) if oc["t1_date"] else mut("not reached yet")),
                            ("target 2", f"Rs {f0(oc['t2'])}",
                             hit(f"hit {oc['t2_date']}", True) if oc["t2_date"] else mut("not reached yet")),
                            ("stop loss", f"Rs {f0(oc['stop'])}",
                             hit(f"HIT {oc['sl_date']}", False) if oc["sl_date"] else hit("never hit — safe", True)),
                        ], tone=vc)
                if ts.get("still_buy") is True:
                    st.success(f"✅ **Can you still buy? Yes** — {ts['still_reason']}.")
                elif ts.get("still_buy") is False:
                    st.warning(f"⏳ **Can you still buy? Not now** — {ts['still_reason']}.")
            else:
                why = ("Too thin to trade (illiquid)." if sig == "ILLIQUID"
                       else "Price is below the major trend, or a false-signal filter blocks a long here.")
                st.markdown(f"<div style='color:#f0a441'>No long entry — signal is <b>{sig}</b>. {why} "
                            f"If already holding, protective stop ≈ <b>Rs {f0(ts['stop'])}</b>.</div>",
                            unsafe_allow_html=True)
            p = ts["parts"]
            calc([
                ("trend 30", "close&gt;200 · 50&gt;200 · 20&gt;50", hit(f"{p['trend']}/30", p['trend'] >= 20)),
                ("momentum 25", "RSI&gt;50 · MACD&gt;sig · hist↑", hit(f"{p['momentum']}/25", p['momentum'] >= 15)),
                ("strength 20", "ADX&gt;25 · +DI&gt;−DI", hit(f"{p['strength']}/20", p['strength'] >= 10)),
                ("volume 15", "1.5× / 2× avg20", hit(f"{p['volume']}/15", p['volume'] >= 10)),
                ("price 10", "breakout of 20d high", hit(f"{p['price']}/10", p['price'] >= 10)),
            ], tone=col)
            aligned = gt(ts['ema20'], ts['ema50']) and gt(ts['ema50'], ts['sma200'])
            calc([
                ("trend stack", f"20 {f0(ts['ema20'])} · 50 {f0(ts['ema50'])} · 200 {f0(ts['sma200'])}",
                 hit("aligned" if aligned else "mixed", aligned)),
                ("RSI(14)", "50+ healthy · &gt;75 hot",
                 hit(f0(ts['rsi']), ts['rsi'] is not None and 50 <= ts['rsi'] <= 75)),
                ("ADX(14)", f"+DI {f0(ts['plus_di'])} / −DI {f0(ts['minus_di'])}",
                 hit(f0(ts['adx']), ts['adx'] is not None and ts['adx'] > 25)),
                ("MACD", f"signal {f2(ts['macd_sig'])}", hit(f2(ts['macd']), gt(ts['macd'], ts['macd_sig']))),
                ("volume", "× 20-day average", hit(f"{ts['vol_ratio']:.2f}×", ts['vol_ratio'] > 1.5)),
            ], tone="#4a5568")

        proven = [r for r in rows if int(r["proof"]) == 3]
        partial = [r for r in rows if int(r["proof"]) < 3]

        st.markdown(f"**🔎 Strong lead — all 3 sources agree ({len(proven)})**")
        st.caption("A lead worth investigating, **not proof of an operator** — the floorsheet shows "
                   "the broker firm, not the client behind it. A hyperactive broker leading many "
                   "stocks is excluded; the survivors are one-directional in this stock specifically.")
        if proven:
            for r in proven:
                card(r)
        else:
            st.caption("None this session — no stock has floorsheet, chart and position all agreeing.")

        st.markdown(f"**◻️ Only some sources agree — not proven ({len(partial)})**")
        st.caption("Usually the floorsheet shows a broker buying/selling but the **chart (price+volume) "
                   "does not confirm** — e.g. the broker bought while the price fell, so the tape is "
                   "not backing them. Look, but do not trust.")
        for r in partial[:10]:
            card(r)

        st.markdown("<div class='section'>All, with the numbers</div>", unsafe_allow_html=True)
        table = [{
            "symbol": r["symbol"], "side": r["side"], "verdict": r["verdict"],
            "proof": f"{r['proof']}/3", "floor": r["floor_ok"], "chart": r["chart_ok"],
            "position": r["pos_ok"], "float%": float(r["float_pct"]), "broker": r["broker"],
            "net_1m": float(r["net_1m"]), "pct_float": float(r["pct_float"]),
            "regular%": float(r["regular_pct"]), "vol_dom%": float(r["vol_dom"]),
            "AD_trend": float(r["ad_trend"]), "updn_vol": float(r["updown_vol"]),
            "price_chg": float(r["price_chg"]),
        } for r in rows]
        st.dataframe(paint(table), width="stretch", hide_index=True,
                     height=min(460, 38 * len(table) + 44))
