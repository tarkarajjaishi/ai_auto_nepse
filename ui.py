"""Browser UI over the local NEPSE archive — candles, floorsheet, broker flow.

    streamlit run ui.py

Everything is read straight from Master_data/*.txt with the standard library; Streamlit and
Plotly only draw. No JavaScript is written here — the charting is the library's own.
"""

import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import date as date_cls, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import naasa
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
  .st-key-mainchart { flex: 1 1 0 !important; }

  /* resolution toolbar — flat TradingView pills, not Streamlit's chunky buttons */
  div[data-testid="stSegmentedControl"] button { background: transparent; border: none;
      color: #8A93A5; font-size: .78rem; font-weight: 600; padding: .1rem .5rem; }
  div[data-testid="stSegmentedControl"] button[aria-checked="true"] {
      background: #232833; color: #E6E8EC; border-radius: 5px; }
  ::-webkit-scrollbar { width: 0 !important; height: 0 !important; }
  * { scrollbar-width: none !important; }
</style>
""", unsafe_allow_html=True)


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
    return cols


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


@st.cache_data(show_spinner=False, ttl=30)
def archive_state():
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

    spike = MASTER / "volume_spike.txt"
    spike_day = ""
    if spike.exists():
        f = last_line(spike).split("	")
        spike_day = f[2] if len(f) > 2 else ""
    out["Volume spike"] = (spike_day, 0, sum(1 for _ in spike.open(encoding="utf-8")) - 1 if spike.exists() else 0)

    scan = MASTER / "scan.txt"
    scan_day = last_line(scan).split("	")[1] if scan.exists() else ""
    out["Signal scan"] = (scan_day, 0, sum(1 for _ in scan.open(encoding="utf-8")) - 1 if scan.exists() else 0)
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
    """Run one fetch script and show its tail. Blocking on purpose — these are manual."""
    with st.status(f"{label} …", expanded=True) as box:
        started = time.time()
        try:
            p = subprocess.run([sys.executable, script, *args], cwd=Path(__file__).parent,
                               capture_output=True, text=True, timeout=7200)
        except (OSError, subprocess.TimeoutExpired) as e:
            box.update(label=f"{label} — failed: {type(e).__name__}", state="error")
            return
        tail = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()[-12:]
        st.code(chr(10).join(tail) or "(no output)")
        ok = p.returncode == 0
        box.update(label=f"{label} — {'done' if ok else 'FAILED'} in {time.time() - started:,.0f}s",
                   state="complete" if ok else "error")
    st.cache_data.clear()          # the scanner and floorsheet readers cache by argument only


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
        return {"TARGET 2": f"background-color:rgba(18,184,134,.30);color:#0B7285;font-weight:600",
                "TARGET 1": "background-color:rgba(18,184,134,.14)",
                "STOP": f"background-color:rgba(255,107,91,.28);color:#B42318;font-weight:600",
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
    whole = {"volume", "bars_ago", "swing_age", "buyer_net", "seller_net"}
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
        elif col in ("return_pct", "open_pct", "change_pct", "progress"):
            tint(pnl, col)
        elif col in ("target1", "target2"):
            tint(lambda v: f"color:{GREEN}", col)
        elif col == "stop":
            tint(lambda v: f"color:{RED}", col)
        elif col == "strong":
            tint(lambda v: f"background-color:rgba(240,162,2,.18);color:{GOLD};font-weight:600"
                 if v == "yes" else "", col)
    return styler


# ---------------------------------------------------------------- sidebar

names = universe()
st.sidebar.title("NEPSE archive")
page = st.sidebar.radio("Menu", ["Chart", "Floorsheet", "Broker flow", "Scanner",
                                 "Volume spike", "Cron"],
                        label_visibility="collapsed", key="nav")
st.sidebar.divider()

# Indicators panel removed — Personal indicators drive the chart. These stay as the
# defaults the drawing code reads.
overlays = []
oscillator = "None"

with st.sidebar.expander("🧠  Personal indicators", expanded=False):
    st.caption("SMC — ported from Pine, computed in Python")

    smc_wave = st.checkbox("Trend wave (ALMA)", value=False)
    wave_len = st.slider("Wave period", 5, 100, 21, key="wave_len") if smc_wave else 21

    smc_struct = st.checkbox("Structure BOS / CHoCH", value=False)
    smc_sens = st.slider("Structure sensitivity", 2, 30, 7, key="smc_sens") if smc_struct else 7

    smc_badges = st.checkbox("Swing BUY / SELL badges", value=False)
    sig_sens = st.slider("Swing sensitivity", 3, 50, 10, key="sig_sens") if smc_badges else 10
    if smc_badges:
        st.caption("⚠ a swing is only confirmed this many bars later — historical marks, not live calls")

    smc_sd = st.checkbox("−2.5 SD target", value=False)
    sd_len = st.slider("SD period", 5, 100, 20, key="sd_len") if smc_sd else 20

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
    symbol = bar_sym.selectbox("Symbol", sorted(names), label_visibility="collapsed",
                               placeholder="Search symbol",
                               index=sorted(names).index("NABIL") if "NABIL" in names else 0)

    data = bars(symbol, timeframe, None)
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
# Credentials live in this browser session only: they are read from the form, exchanged for
# a token, and dropped. Nothing is written to disk, no .env, no cache. The token expires on
# its own. Only read endpoints are called — placing or cancelling orders is not implemented.

# A token can also come from the environment, so it need not be pasted every restart:
#     setx NAASA_TOKEN "eyJhbGciOi..."      (then reopen the terminal)
# Set it yourself — the app only reads it, and still never writes a credential anywhere.
if not st.session_state.get("naasa_token") and os.environ.get("NAASA_TOKEN"):
    try:
        st.session_state["naasa_who"] = naasa.whoami(os.environ["NAASA_TOKEN"].strip())
        st.session_state["naasa_token"] = os.environ["NAASA_TOKEN"].strip()
    except Exception:
        pass  # stale env token: fall through to the paste box

with st.sidebar.expander("🔐  NAASA account", expanded=False):
    if st.session_state.get("naasa_token"):
        who = st.session_state.get("naasa_who") or {}
        st.success(f"Signed in{' as ' + who['name'] if who.get('name') else ''}")
        st.caption("Token held in this session's memory only — never written to disk.")
        if st.button("Sign out", key="naasa_out"):
            for k in ("naasa_token", "naasa_who"):
                st.session_state.pop(k, None)
            st.rerun()
    else:
        st.caption(
            "Optional — market data works without it. Password login is impossible here: "
            "NAASA's Keycloak client has direct grant disabled and its redirect URI is "
            "whitelisted to their own domain, so paste the token your browser already holds."
        )
        with st.expander("How to copy it"):
            st.code("""// in the signed-in NAASA tab, DevTools console:
Object.entries(localStorage)
  .filter(([k, v]) => /token/i.test(k) || (v || '').startsWith('ey'))
  .map(([k, v]) => k + ' = ' + v.slice(0, 24) + '...')""", language="javascript")
            st.caption("Copy the value that starts with `ey` — that is the JWT.")
        token = st.text_input("Access token", type="password", key="naasa_tok_in",
                              placeholder="eyJhbGciOi...")
        if st.button("Use token", key="naasa_use") and token:
            try:
                st.session_state["naasa_who"] = naasa.whoami(token.strip())
                st.session_state["naasa_token"] = token.strip()
                st.rerun()
            except Exception as e:
                st.error(f"Token rejected: {str(e)[:200]}")

# ---------------------------------------------------------------- live quote

# NAASA pushes prices over an authenticated WebSocket we deliberately do not touch; their
# public REST quote carries the same fields, so poll it instead. A fragment reruns only
# this block, leaving the charts and scanner untouched.
@st.fragment(run_every=10)
def live_quote(symbol):
    try:
        q = naasa.quotes([symbol]).get(symbol.replace("/", "-"))
    except Exception:
        q = None
    if not q or not q.get("lp"):
        st.caption("NAASA live quote unavailable — showing archive values above.")
        return
    a, b, c, d, e = st.columns(5)
    a.metric("LTP", f"{q['lp']:,.2f}", f"{q.get('chp') or 0:+.2f}%")
    b.metric("Bid", f"{q.get('bid') or 0:,.2f}")
    c.metric("Ask", f"{q.get('ask') or 0:,.2f}")
    d.metric("Volume", f"{q.get('volume') or 0:,.0f}")
    e.metric("Prev close", f"{q.get('prev_close') or 0:,.2f}")
    st.caption("NAASA live quote · refreshes every 10s")


if st.sidebar.toggle("Live quote (NAASA)", value=False, help="Polls NAASA's public quote endpoint every 10 seconds"):
    live_quote(symbol)



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
        f"<span class='tv-meta'>· {unit} · NEPSE</span>"
        f"<span class='tv-ohlc' style='color:{tone}'>"
        f"O<b>{o:,.2f}</b> H<b>{h:,.2f}</b> L<b>{l:,.2f}</b> C<b>{c:,.2f}</b>"
        f"&nbsp;{diff:+,.2f} ({(diff / prev_c * 100 if prev_c else 0):+.2f}%)</span>"
        f"</div>"
        f"<div class='tv-vol'>Volume <span style='color:{tone}'>{vol_last:,.0f}</span></div>",
        unsafe_allow_html=True)

    # height="stretch" all the way down: the container claims the space left under the
    # header and the chart fills it. A CSS height here instead is racy — Plotly settles at
    # its own 450px default before the stylesheet lands, and the chart renders short.
    with st.container(key="mainchart", height="stretch"):
        fig = build_chart(data, symbol, timeframe)
        st.plotly_chart(fig, height="stretch", config={
            "scrollZoom": True,            # wheel / pinch zooms about the pointer
            "doubleClick": "reset",
            "displaylogo": False,
            "displayModeBar": "hover",
            "responsive": True,            # follow the CSS-sized container, not chart_px
            "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d", "toggleSpikelines"],
            "scrollZoomSpeed": 0.6,
        })


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
        st.plotly_chart(flow, use_container_width=True)
        st.caption("Positive = net buyer that session. Broker codes are NEPSE member numbers.")

        st.dataframe(
            [{"buyer": b, "seller": s, "quantity": q, "rate": r, "amount": a, "transaction": t}
             for b, s, q, r, a, t in rows],
            use_container_width=True, height=260,
        )

# ---------------------------------------------------------------- broker flow

if page == "Broker flow":
    dates = floorsheet_dates(symbol)
    if not dates:
        st.info(f"No floorsheet for {symbol}.")
    else:
        window = st.slider("Sessions", 5, min(120, len(dates)), min(20, len(dates)))
        picked = dates[:window]
        net, bought, sold = broker_flow(symbol, tuple(picked))
        ranked = sorted(net.items(), key=lambda kv: -kv[1])
        accum, distrib = ranked[:10], ranked[-10:][::-1]

        st.subheader(f"Net broker flow · last {window} sessions ({picked[-1]} → {picked[0]})")
        left, right = st.columns(2)
        left.markdown("**Accumulating**")
        left.dataframe([{"broker": b, "net shares": f"{v:,.0f}", "bought": f"{bought[b]:,.0f}",
                         "sold": f"{sold[b]:,.0f}"} for b, v in accum if v > 0],
                       use_container_width=True, hide_index=True)
        right.markdown("**Distributing**")
        right.dataframe([{"broker": b, "net shares": f"{v:,.0f}", "bought": f"{bought[b]:,.0f}",
                          "sold": f"{sold[b]:,.0f}"} for b, v in distrib if v < 0],
                        use_container_width=True, hide_index=True)

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

    chosen = st.dataframe(paint(shown), use_container_width=True, hide_index=True, height=300,
                          on_select="rerun", selection_mode="single-row", key="scan_rows")
    st.caption(f"{len(shown)} symbols · click a row to chart it")

    picked_rows = chosen.selection.rows if hasattr(chosen, "selection") else []
    if picked_rows:
        row = shown[picked_rows[0]]
        st.subheader(f"{row['symbol']} · {row['close']:,.2f}  ({row['change_pct']:+.2f}%)  → {row['signal']}")
        sub = bars(row["symbol"], "1D", SCAN_BARS)
        if sub:
            fig = build_chart(sub, row["symbol"], "1D", lock_price=True, chart_px=420)
            st.plotly_chart(fig, use_container_width=True, key="scan_chart",
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
                   use_container_width=True, hide_index=True, height=250)
    right.markdown(f"### 🔴 Strong SELL · {len(sell_side)}")
    right.dataframe(paint([{c: (r.get(c) if r.get(c) is not None else 0) for c in cols} for r in sell_side]),
                    use_container_width=True, hide_index=True, height=250)
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

        st.dataframe(paint(rows), use_container_width=True, hide_index=True, height=300)
        st.caption("ltp is the latest close; open_pct is the unrealised move in the signal's "
                   "direction, progress is 1.0 when price has reached target 1. "
                   "Entry is the close of the bar the signal became confirmable, never the pivot "
                   "itself. When one bar could have hit both the stop and a target, the stop is "
                   "counted — so these numbers are the pessimistic reading, before costs.")


# ---------------------------------------------------------------- cron

if page == "Cron":
    st.markdown("<div class='section'>Last traded data</div>", unsafe_allow_html=True)
    # Reserved now, filled after the buttons: a job run below changes what these say, and
    # Streamlit draws top-down, so rendering them here would show the pre-run state.
    note, table = st.empty(), st.empty()

    a, b, c = st.columns(3)
    if a.button("Fetch last traded data", width="stretch", type="primary"):
        run_job("Last session", "fetch_last_session.py")
        run_job("Signal scan", "scan.py")
        run_job("Volume spike screen", "volume_spike.py")
    if b.button("Full daily refresh", width="stretch",
                help="Every step of daily_update.py, including minute bars — slower."):
        run_job("Daily update", "daily_update.py")
    if c.button("Rebuild signals only", width="stretch"):
        run_job("Signal scan", "scan.py")

    state = archive_state()
    session = max((v[0][:10] for v in state.values() if v[0]), default="")   # minute rows carry a time
    note.caption(f"Newest session in the archive: **{session or '—'}**. Fetching touches that "
                 "one date only — rows already there are replaced, missing ones appended — so "
                 "it is safe to re-run. After the 15:00 NPT close it is the final session; "
                 "before, a partial one.")
    table.dataframe(pd.DataFrame([{
        "Store": label,
        "Last data": stamp or "—",
        "At newest": f"{total - behind:,} / {total:,}" if total else "—",
        "Fresh": "yes" if stamp and stamp[:10] == session else "no",
    } for label, (stamp, behind, total) in state.items()]), width="stretch", hide_index=True)

    st.markdown("<div class='section'>Historical</div>", unsafe_allow_html=True)
    st.caption("Full backfills. These re-walk the whole history and take minutes to hours — "
               "the daily refresh above is the one to run every day.")

    c, d = st.columns(2)
    if c.button("Backfill chart history", width="stretch"):
        run_job("Daily bars", "fetch_ohlc.py")
        run_job("Minute bars", "fetch_intraday.py")
    if d.button("Backfill floorsheet", width="stretch"):
        run_job("Floorsheet (merolagani)", "fetch_floorsheet_merolagani.py")
        run_job("Broker flow table", "build_broker_flow.py")

    log = Path(__file__).parent / "update_log.txt"
    if log.exists():
        st.markdown("<div class='section'>Last run</div>", unsafe_allow_html=True)
        # just the newest block, or the page outgrows the screen
        st.code("===" + log.read_text(encoding="utf-8").strip().rsplit("===", 1)[-1])

    st.caption("Nothing here schedules itself — no daemon, by design. For unattended runs point "
               "Windows Task Scheduler at `python daily_update.py` in this folder.")


# ---------------------------------------------------------------- volume spike

if page == "Volume spike":
    rows, screened = spike_screen()
    st.markdown("<div class='section'>Volume spike &amp; broker flow</div>", unsafe_allow_html=True)

    if not rows:
        st.info("No screen yet — press **Rebuild screen** below to run volume_spike.py.")
    else:
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
                    key="spike_filter", label_visibility="collapsed")

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
