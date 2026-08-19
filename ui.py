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

import naasa
import swp
import trade_setup
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
  .st-key-mainchart { flex: 1 1 0 !important;
      border: none !important; background: transparent !important; padding: 0 !important; }

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


def operator_scan():
    path = MASTER / "operator_scan.txt"
    if not path.exists():
        return []
    return read_operator_scan(str(path), path.stat().st_mtime)


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


@st.cache_data(ttl=15, show_spinner=False)
def live_snapshot(symbol):
    """NAASA's public quote for one symbol (no login), cached 15s so redraws don't refetch."""
    try:
        return naasa.quotes([symbol]).get(symbol.replace("/", "-")) or {}
    except Exception:
        return {}


@st.cache_resource(show_spinner=False)
def naasa_feed():
    """Server-wide NAASA live socket. A daemon thread that logs in, holds the socket, and keeps
    {symbol: {field: value}} fresh — ALWAYS ON (want=True) so live data is always available; it
    idles only when no login is saved. Survives Streamlit reruns. ONE NAASA session per account —
    this holds it, so a browser tab logged in as the same account gets evicted (use a dedicated
    feed account to avoid that). `subs` is updated to the symbol currently being viewed."""
    state = {"snap": {}, "want": True, "subs": ("NEPSE", "SENSIND"), "status": "off",
             "err": "", "lock": threading.Lock()}

    def on_tick(rec):
        with state["lock"]:
            d = state["snap"].setdefault(rec["symbol"], {})
            d.update(rec["fields"]); d["_t"] = rec.get("time", "")

    def loop():
        while True:
            if not state["want"]:
                state["status"] = "off"; time.sleep(0.4); continue
            em, pw = naasa.load_credentials()
            if not (em and pw):
                state["status"] = "no login saved"; state["want"] = False; continue
            subs = state["subs"]
            try:
                state["status"] = "live"; state["err"] = ""
                naasa.stream_ticks(em, pw, list(subs), on_tick,
                                   stop=lambda: (not state["want"]) or state["subs"] != subs)
            except Exception as e:
                state["err"] = str(e)[:110]; state["status"] = "reconnecting"; time.sleep(4)

    threading.Thread(target=loop, daemon=True).start()
    return state


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
    st.caption(f"🔴 Real-time from NAASA's WebSocket (x:8006), sub-second · last tick {q.get('_t','—')}.")


NPT = timezone(timedelta(hours=5, minutes=45))


def market_status():
    """NEPSE session right now in Nepal time — PRE-OPEN 10:30–10:45 (orders can be entered),
    PRE-OPEN CLOSE 10:46–10:59 (matching, no new entry), LIVE 11:00–15:00, else CLOSED — and
    CLOSED all day Fri/Sat, when NEPSE is shut."""
    now = datetime.now(NPT)
    mins = now.hour * 60 + now.minute
    if now.weekday() in (4, 5):          # Fri, Sat
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
    One quote, cached 15s, so a page full of cards stays cheap."""
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
    st.caption("Best bid/ask + day stats from NAASA's public feed (cached 15s). The full Top-5 "
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


@st.cache_data(ttl=8, show_spinner=False)
def indices_snapshot():
    """NEPSE index quotes from NAASA (batched SpecifiedQuote) — works live AND while closed.
    Cached 8s so the sidebar + heatmap page share one fetch. [] when signed out / unreachable."""
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
            f"<td style='padding:{pad}'>{n(r.get('LTP'))}</td><td style='padding:{pad}'>{n(r.get('High'))}</td>"
            f"<td style='padding:{pad}'>{n(r.get('Low'))}</td><td style='padding:{pad}'>{n(r.get('Open'))}</td>"
            f"<td style='padding:{pad}'>{n(r.get('Close'))}</td>"
            f"<td style='padding:{pad};color:{col};font-weight:600'>{chg:+.2f}%</td></tr>")
    st.markdown(f"<div style='overflow-x:auto'><table style='width:100%;border-collapse:collapse;"
                f"font-size:{fs};white-space:nowrap'>{head}{''.join(body)}</table></div>",
                unsafe_allow_html=True)


def render_index_heatmap(rows):
    """Our own NEPSE index heatmap — one tile per index, coloured by %change (diverging red→green),
    labelled with symbol, LTP and %change. Built from the same NAASA snapshot as the table."""
    if not rows:
        st.info("Sign in under 🔐 NAASA account (with Remember me) to load the heatmap.")
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
        text=labels, textinfo="text", textfont=dict(size=15, color="white"),
        textposition="middle center", hovertemplate="%{label}<extra></extra>", tiling=dict(pad=3)))
    fig.update_layout(margin=dict(t=0, l=0, r=0, b=0),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, height="stretch", config={"displayModeBar": False})


# --- NEPSE stock heatmap (sector-grouped, like nepsetrading) -----------------------------------
# Non-equity buckets NAASA's SectorStock lumps in — kept out of the equity heatmap.
_HM_EXCLUDE = {"", "Promotor Share", "Corporate Debenture", "Government Bond", "Mutual Fund"}
_SECTOR_NAME = {"Development Bank Limited": "Development Banks"}


@st.cache_data(ttl=3600, show_spinner=False)
def _sector_map():
    """Stock→sector membership (static-ish) — cached an hour."""
    em, pw = naasa.load_credentials()
    if not (em and pw):
        return []
    try:
        return naasa.heatmap_sectors(em, pw)
    except Exception:
        return []


@st.cache_data(ttl=20, show_spinner=False)
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
        st.info("Sign in under 🔐 NAASA account (with Remember me) to load the heatmap.")
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
        hovertemplate="%{label}<extra></extra>", textfont=dict(size=13, color="white"),
        textposition="middle center"))
    fig.update_layout(margin=dict(t=0, l=0, r=0, b=0),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, height="stretch", config={"displayModeBar": False})


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
                "scripts": ["fetch_symbols.py", "fetch_last_session.py", "scan.py", "volume_spike.py"]},
    "swp":     {"label": "SmartWealthPro → Operator",   # runs last, so operator has fresh inputs
                "scripts": ["fetch_swp.py", "operator_scan.py"]},
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
            today, weekend = now.strftime("%Y-%m-%d"), now.weekday() in (4, 5)
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
    whole = {"volume", "bars_ago", "swing_age", "buyer_net", "seller_net", "bought", "sold",
             "net", "vs_prev"}
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
        elif col in ("floor", "chart", "position"):
            tint(lambda v: f"color:{GREEN};font-weight:600" if v == "yes"
                 else f"color:{RED}", col)
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
PAGES = ["Chart", "Floorsheet", "Broker flow", "Scanner",
         "Volume spike", "Operator radar", "Master operator", "Swing master", "Master signal",
         "Backtest", "NAASA", "Heatmap", "Cron"]
# Persist the current page in the URL (?page=…) so a refresh / hard refresh / redeploy keeps you
# where you are — a browser reload starts a fresh Streamlit session, so session_state alone resets.
_qp_page = st.query_params.get("page")
page = st.sidebar.radio("Menu", PAGES, index=PAGES.index(_qp_page) if _qp_page in PAGES else 0,
                        label_visibility="collapsed", key="nav")
if st.query_params.get("page") != page:
    st.query_params["page"] = page
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
    @st.fragment(run_every=3)
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
            st.caption("Public quote (cached 15s) — go live on the NAASA page for the socket feed.")
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
    st.caption("Read-only monitor. PRE-OPEN 10:30–10:45 (queue orders), PRE-OPEN CLOSE 10:46–10:59, "
               "LIVE 11:00–15:00, CLOSED otherwise. Place actual orders in NAASA.")

    sym = st.text_input("Scrip", value=(symbol or "NABIL"), key="naasa_sym").strip().upper() or "NABIL"
    q = live_snapshot(sym)

    side = st.radio("Side", ["BUY", "SELL"], horizontal=True, key="naasa_side")
    f1, f2, f3, f4, f5 = st.columns([2, 1, 1, 1, 1])
    f1.text_input("Scrip ", value=sym, disabled=True, key="naasa_f_scrip")
    f2.text_input("Quantity", placeholder="0", disabled=True, key="naasa_f_qty")
    lo_hi = f"{q.get('low', '–')} – {q.get('high', '–')}"
    f3.text_input("Price", placeholder="Price", disabled=True, key="naasa_f_price",
                  help=f"Low–High: {lo_hi}")
    f4.selectbox("Validity", ["DAY"], disabled=True, key="naasa_f_val")
    f5.text_input("Valid Till", placeholder="Pick a date", disabled=True, key="naasa_f_till")
    st.radio("Order type", ["LMT", "MKT", "AMO"], horizontal=True, disabled=True,
             key="naasa_f_type", label_visibility="collapsed")

    # Live feed is always on (NAASA WSS) — no toggle, NO cache. Show the socket snapshot only, the
    # instant it arrives. No public/REST fallback here: this panel is live-or-nothing on purpose.
    feed = naasa_feed()
    want = (sym, "NEPSE", "SENSIND")
    if feed["subs"] != want:
        feed["subs"] = want
    @st.fragment(run_every=1)
    def _depth():
        render_socket_depth(sym, feed)
    _depth()

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

    st.markdown("<div class='section'>Order Book</div>", unsafe_allow_html=True)
    if not (em and pw):
        st.caption("Sign in under 🔐 **NAASA account** in the sidebar (with Remember me) to load your live order book.")
    else:
        try:
            rows = naasa.x_orderbook(em, pw)
            if rows:
                df = pd.DataFrame(rows)
                # (display name, first matching source column) — one source per name, no duplicates
                wanted = [("Symbol", ("Scrip",)), ("Side", ("BuySellType", "BuySellText", "B/S")),
                          ("Qty", ("Quantity", "RemainingQty")), ("Price", ("Price",)),
                          ("Status", ("OrderStatus",)), ("Validity", ("OrderTerms",)),
                          ("Time", ("BrokerOrderTime",)), ("Order No", ("ExchangeOrderNo",))]
                view = pd.DataFrame({name: df[next(s for s in srcs if s in df.columns)]
                                     for name, srcs in wanted
                                     if any(s in df.columns for s in srcs)})
                st.dataframe(view, use_container_width=True, hide_index=True, height=240)
                st.caption(f"🔴 {len(rows)} open order(s) · live from NAASA (MarketOrder/OrderBook) · read-only.")
            else:
                st.info("No open orders in your NAASA order book right now.")
        except Exception as e:
            st.caption(f"⚠ Could not load the order book — {str(e)[:120]}")

    # -- Holdings --
    st.markdown("<div class='section'>My Holdings</div>", unsafe_allow_html=True)
    if not (em and pw):
        st.caption("Sign in to load your holdings.")
    else:
        try:
            hold = naasa.x_holdings(em, pw)
            if hold:
                df = pd.DataFrame(hold)
                cols = [c for c in ("NEPSECode", "AvailableQty", "CDSFreeBalance", "LastTradedPrice",
                                    "ClosePrice", "ValueAsOfLTP", "DayGainLoss") if c in df.columns]
                view = df[cols].rename(columns={
                    "NEPSECode": "Symbol", "AvailableQty": "Qty", "CDSFreeBalance": "Free",
                    "LastTradedPrice": "LTP", "ClosePrice": "Prev close",
                    "ValueAsOfLTP": "Market value", "DayGainLoss": "Day P/L"})
                st.dataframe(view, use_container_width=True, hide_index=True)
                st.caption(f"🔴 {len(hold)} holding(s) · live from NAASA (TradeBook/HoldingDataReport) · read-only.")
            else:
                st.info("No holdings in your NAASA account.")
        except Exception as e:
            st.caption(f"⚠ Could not load holdings — {str(e)[:120]}")

    # -- Collateral & account summary --
    st.markdown("<div class='section'>Collateral & account summary</div>", unsafe_allow_html=True)
    if not (em and pw):
        st.caption("Sign in to load your collateral and trade summary.")
    else:
        try:
            f = _dash_fields(naasa.x_collateral(em, pw))     # groups [2]=holdings [3]=collateral, [4] PII ignored
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
            st.caption("🔴 Live from NAASA (Home/DashboardDetails) · read-only.")
        except Exception as e:
            st.caption(f"⚠ Could not load collateral — {str(e)[:120]}")


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
        st.warning("**Unusual ≠ profitable.** This ranks how abnormal today's concentration is and "
                   "attaches no expected return, deliberately — every predictive version of this "
                   "was tested and died (verdict table below).")
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
        st.info("No backtest.txt yet — run `python backtest.py` on the server (it replays ~7,600 "
                "trades and takes a few minutes).")
    else:
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
        st.dataframe(tab, width="stretch", hide_index=True,
                     column_config={"OUT avg%": st.column_config.NumberColumn(
                         "OUT avg%", help="The only column with any claim on the future")})
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
    hc1, hc2, hc3 = st.columns([2, 3, 1])
    view = hc1.segmented_control("Heatmap", ["Stocks by sector", "Indices"],
                                 default="Stocks by sector", key="hm_view",
                                 label_visibility="collapsed") or "Stocks by sector"
    with hc2:
        heatmap_legend()
    status, scol = market_status()
    hc3.markdown(f"<div style='text-align:right'><span style='background:{scol};color:#0d1117;"
                 f"font-weight:700;padding:2px 10px;border-radius:5px;font-size:0.82em'>{status}"
                 f"</span></div>", unsafe_allow_html=True)

    # Full-fit: the heatmap fills whatever height is left after the index table below it, so the
    # controls + heatmap + table exactly fill the screen — no gap, no scroll (flex chain does it).
    with st.container(key="mainchart", height="stretch"):
        if view == "Stocks by sector":
            render_stock_heatmap(stock_heatmap_rows())
        else:
            render_index_heatmap(indices_snapshot())
    render_index_table(indices_snapshot())


# ---------------------------------------------------------------- cron

if page == "Cron":
    sched_state = cron_scheduler()          # start / reuse the background scheduler thread

    # Live banner — shows whichever is running now: an auto-fired scheduled job, a manual run, or
    # the older systemd service. Polls every few seconds and clears itself when idle.
    @st.fragment(run_every=4)
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
        "Fresh": "yes" if stamp and stamp[:10] == session else "no",
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

        @st.fragment(run_every=2)
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

        @st.fragment(run_every=2)
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


# ---------------------------------------------------------------- operator radar

if page == "Operator radar":
    rows = operator_scan()
    st.markdown("<div class='section'>Who is buying more / selling more</div>",
                unsafe_allow_html=True)
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
                    st.plotly_chart(barfig, use_container_width=True,
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
                    st.plotly_chart(sellfig, use_container_width=True,
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
                      else "<span style='background:#2ea043;color:#fff;font-weight:700;padding:2px 10px;"
                      "border-radius:5px;font-size:.82em'>✅ entry: buyable now</span>" if sb is True
                      else "")
            st.markdown(
                "<div style='display:flex;align-items:center;gap:12px;flex-wrap:wrap'>"
                "<span style='font-weight:700;font-size:1.03em'>📐 Professional trade setup</span>"
                f"<span style='background:{col};color:#fff;font-weight:700;padding:2px 11px;"
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
