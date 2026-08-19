"""Swing Trader Pro — the 22-section professional 1D swing framework, computed not prompted.

Daily bars only. No 5m/15m/1H input touches the decision, by design: the framework is a
swing-trading one and mixing timeframes is how a swing plan turns into a day-trade.

Everything here is deterministic Python over the .txt archive — the same numbers every run,
auditable line by line. Where the framework asks a judgement question ("is this a healthy
pullback?") it is answered by an explicit, stated rule, never by vibe.

    Section 1  performing?          ELITE / STRONG / DEVELOPING / NEUTRAL / WEAK / AVOID
    Section 2  trend                20/50/200 EMA alignment, slope, separation, extension
    Section 3  structure            HH/HL/LH/LL, BOS, CHoCH -> trend stage
    Section 4  relative performance 5d / 20d / 60d vs its own history
    Section 5  volume               ratio vs 20d average, and WHICH side it is on
    Section 6  RSI 14               level, direction, divergence
    Section 7  MACD 12/26/9         line vs signal, histogram slope, zero line
    Section 8  breakout quality     6 states
    Section 9  pullback quality     5 states
    Section 10 support / resistance read off real pivots, never invented
    Section 11 ATR 14               is there enough movement to swing at all
    Section 12 risk / reward        MANDATORY gate: below 1:2 is not a trade
    Section 13 fundamentals         quality filter from SmartWealthPro, never a substitute
    Section 14 liquidity            turnover, gaps, manipulation shape, execution risk
    Section 15 smart money          demand/supply zone (via supply_demand.py), sweep, reclaim
    Section 16 false-signal filter  all ten ways this setup could be a trap
    Section 17 setup selection      A-E, with the setup's own A+/A/B/C/NO TRADE grade
    Section 18 invalidation         one stated level; never widened to avoid a loss
    Section 19 profit plan          partial, breakeven, trailing, momentum exit
    Section 20 score                the exact 100-point rubric
    Section 21 the fifteen questions, verbatim (Q9 is a WARNING, Q14 answers in text)
    Section 22 final output         all 37 named fields, in the framework's order

Conformance is enforced, not assumed: `python test_swing_pro.py` fails if any named
requirement of the written framework stops being implemented.

Sources: Master_data/symbols/<SYM>/1D.txt (bars, corporate-action adjusted on read via
prices.py), fundamentals.txt + sectors.txt (SmartWealthPro).

    python swing_pro.py                 # score every symbol -> Master_data/swing_pro.txt
    python swing_pro.py --symbol NABIL  # the full 22-section report for one stock
    python swing_pro.py --selftest      # assert the scoring and R:R arithmetic
    python test_swing_pro.py            # assert all 22 sections are implemented
"""
import sys
from datetime import datetime

import supply_demand
from fetch_ohlc import MASTER
from indicators import atr, ema, macd, pivots, rsi, sma, structure
from prices import bars

OUT = MASTER / "swing_pro.txt"
HEADER = ("symbol\tdate\tgrade\tscore\tdecision\tperformer\tclose\tentry\tstop\ttarget1\ttarget2"
          "\ttarget3\trr\trisk_pct\ttrend\tstage\tstructure\tbreakout\tpullback\tvol_x"
          "\trsi\tmacd_hist\tatr_pct\tret5\tret20\tret60\tturnover\tsetup\tflags")

MIN_RR = 2.0                 # section 12: the mandatory gate
LIQUID_MIN = 500_000         # Rs median 20-day turnover, same bar the rest of the repo uses


# ---------------------------------------------------------------- helpers

def _at(series, i=-1):
    try:
        v = series[i]
    except IndexError:
        return None
    return v


def _ret(c, n):
    """Percent return over the last n bars, or None when history is too short."""
    return None if len(c) <= n or c[-1 - n] == 0 else (c[-1] / c[-1 - n] - 1) * 100


def _slope(series, n=5):
    """Percent change of an indicator over n bars — 'is the EMA actually rising'."""
    a, b = _at(series, -1 - n), _at(series)
    return None if a in (None, 0) or b is None else (b / a - 1) * 100


def _fundamentals():
    p = MASTER / "fundamentals.txt"
    if not p.exists():
        return {}
    lines = p.read_text(encoding="utf-8").splitlines()
    cols = lines[0].split("\t")
    out = {}
    for line in lines[1:]:
        f = line.split("\t")
        if len(f) == len(cols):
            out[f[0]] = dict(zip(cols, f))
    return out


def _num(d, key):
    try:
        v = float(d.get(key, ""))
    except (ValueError, AttributeError):
        return None
    return v if v not in (0.0,) or key in ("cash_div", "bonus_div") else None


# ---------------------------------------------------------------- sections

def _crossovers(e20, e50, e200, look=25):
    """Section 2 — EMA crossovers inside the recent window, newest first."""
    out = []
    for fast, slow, lbl in ((e20, e50, "20/50"), (e50, e200, "50/200")):
        for i in range(max(1, len(fast) - look), len(fast)):
            a, b, pa, pb = fast[i], slow[i], fast[i - 1], slow[i - 1]
            if None in (a, b, pa, pb):
                continue
            if pa <= pb and a > b:
                out.append(f"{lbl} bullish cross {len(fast) - 1 - i}d ago")
            elif pa >= pb and a < b:
                out.append(f"{lbl} bearish cross {len(fast) - 1 - i}d ago")
    return out[::-1]


def _separation(close, e20, e50, e200):
    """Section 2 — EMA separation as % of price; a compressed stack is a coiled/unclear trend."""
    vals = [v for v in (e20, e50, e200) if v]
    if len(vals) < 3 or not close:
        return None
    return (max(vals) - min(vals)) / close * 100


def _accum_dist(o, c, v, look=20):
    """Section 3/5 — is volume arriving on up days or down days?"""
    up = sum(v[i] for i in range(-look, 0) if c[i] > o[i])
    dn = sum(v[i] for i in range(-look, 0) if c[i] < o[i])
    if not (up or dn):
        return "Neutral"
    if up > dn * 1.25:
        return "Accumulation"
    if dn > up * 1.25:
        return "Distribution"
    return "Neutral"


def _advances_declines(c, look=20):
    """Section 4 — are recent advances bigger than recent declines?"""
    up = sum(c[i] - c[i - 1] for i in range(-look, 0) if c[i] > c[i - 1])
    dn = sum(c[i - 1] - c[i] for i in range(-look, 0) if c[i] < c[i - 1])
    return (up / dn) if dn else (float("inf") if up else 1.0)


def _volume_regime(v, vsma):
    """Section 5 — expansion vs contraction, and whether today is outright abnormal."""
    if len(v) < 60 or not vsma or not vsma[-1]:
        return "unknown", False
    recent = sum(v[-20:]) / 20
    older = sum(v[-60:-20]) / 40
    regime = ("expansion" if recent > older * 1.2 else
              "contraction" if recent < older * 0.8 else "steady")
    return regime, (v[-1] > vsma[-1] * 3)


def _divergence(series, h, l, highs, lows):
    """Sections 6/7 — classic divergence on the last two confirmed swings."""
    bear = bull = False
    if len(highs) >= 2:
        i1, i2 = highs[-2][0], highs[-1][0]
        if h[i2] > h[i1] and None not in (series[i1], series[i2]) and series[i2] < series[i1]:
            bear = True
    if len(lows) >= 2:
        i1, i2 = lows[-2][0], lows[-1][0]
        if l[i2] < l[i1] and None not in (series[i1], series[i2]) and series[i2] > series[i1]:
            bull = True
    return bear, bull


def _macd_cross(mline, msig, look=15):
    """Section 7 — most recent MACD/signal crossover inside the window."""
    for i in range(len(mline) - 1, max(0, len(mline) - look), -1):
        a, b, pa, pb = mline[i], msig[i], mline[i - 1], msig[i - 1]
        if None in (a, b, pa, pb):
            continue
        if pa <= pb and a > b:
            return f"bullish cross {len(mline) - 1 - i}d ago"
        if pa >= pb and a < b:
            return f"bearish cross {len(mline) - 1 - i}d ago"
    return "none recent"


def _breakout_detail(c, o, h, l, v, vsma, a, res):
    """Section 8 — the mechanics behind the label: tests, base length, candle, follow-through."""
    if not res:
        return dict(res_tests=0, consol_days=0, bo_candle=None, close_pos=None, follow_through=0)
    tol = max(a * 0.5, res * 0.005)
    tests = sum(1 for i in range(max(0, len(c) - 90), len(c)) if abs(h[i] - res) <= tol)
    consol = 0                                          # bars price has sat under the level
    for i in range(len(c) - 1, 0, -1):
        if c[i] > res * 1.02:
            break
        consol += 1
    rng = h[-1] - l[-1]
    return dict(res_tests=tests, consol_days=consol,
                bo_candle=(abs(c[-1] - o[-1]) / a) if a else None,
                close_pos=((c[-1] - l[-1]) / rng * 100) if rng else None,
                follow_through=sum(1 for i in range(-3, 0) if c[i] > res))


def _repeated_failures(c, h, highs, look=120):
    """Section 16 — has this level rejected price more than once recently?"""
    if len(highs) < 2:
        return 0
    fails = 0
    for i, lvl in highs[-6:]:
        if i < len(c) - look:
            continue
        after = c[i + 1:i + 8]
        if after and h[i] > lvl * 0.999 and max(after) < lvl:
            fails += 1
    return fails


def _sweep_reclaim(c, h, l, highs, lows, a):
    """Section 15 — only what the daily bars actually show. Never forced onto an unclear chart."""
    sweep = reclaim = False
    if lows:
        prior_low = lows[-1][1]
        for i in range(max(0, len(c) - 10), len(c)):     # pierced the low, closed back above
            if l[i] < prior_low and c[i] > prior_low:
                sweep = True
    if highs:
        lvl = highs[-1][1]
        broke = any(c[i] < lvl for i in range(max(0, len(c) - 20), len(c) - 3))
        if broke and c[-1] > lvl:
            reclaim = True
    return sweep, reclaim


def _gaps(o, c, look=60):
    """Section 14 — unexplained gaps, the kind that make a stop unreliable."""
    return sum(1 for i in range(max(1, len(c) - look), len(c))
               if abs(o[i] / c[i - 1] - 1) > 0.05)


def _profit_plan(f):
    """Section 19 — early enough entry, controlled risk, trail the trend. Stated, not implied."""
    return [
        f"Initial target {f['t1']:,.2f} (2R) — take partial profit there and reduce risk.",
        f"Extended targets {f['t2']:,.2f} (3R) and {f['t3']:,.2f} (5R) only while the trend holds.",
        f"Move the stop to breakeven ({f['entry']:,.2f}) once price closes beyond "
        f"{f['entry'] + f['risk']:,.2f} (1R).",
        "Then trail below each new daily swing low — never widen the stop to avoid a loss.",
        "Exit early if momentum breaks down: MACD crosses below its signal, or a daily close "
        "back under the 20 EMA on expanding volume.",
    ]


def _swings(h, l):
    """Confirmed swing highs/lows, most recent last. 5/5 pivots — the standard swing lens."""
    ph, pl = pivots(h, l, 5, 5)
    highs = [(i, v) for i, v in enumerate(ph) if v is not None]
    lows = [(i, v) for i, v in enumerate(pl) if v is not None]
    return ph, pl, highs, lows


def _structure(c, ph, pl, highs, lows):
    """Section 3 — HH/HL/LH/LL and the last BOS/CHoCH."""
    hh = len(highs) >= 2 and highs[-1][1] > highs[-2][1]
    hl = len(lows) >= 2 and lows[-1][1] > lows[-2][1]
    lh = len(highs) >= 2 and highs[-1][1] < highs[-2][1]
    ll = len(lows) >= 2 and lows[-1][1] < lows[-2][1]
    evs = structure(c, ph, pl)
    last = evs[-1] if evs else None
    if hh and hl:
        label = "HH/HL"
    elif lh and ll:
        label = "LH/LL"
    elif hh or hl:
        label = "mixed-up"
    elif lh or ll:
        label = "mixed-down"
    else:
        label = "unclear"
    return label, (f"{last[2]} {last[3]}" if last else "none"), hh, hl, lh, ll


def _stage(c, e20, e50, e200, hh, hl, ret60, ext_atr):
    """Section 2/3 — where in the trend's life this is."""
    up = all(v is not None for v in (e20, e50, e200)) and c > e20 > e50 > e200
    if not up:
        if e50 is not None and e200 is not None and e50 < e200:
            return "Downtrend"
        return "Consolidation" if (hh or hl) else "Reversal Attempt"
    if ext_atr is not None and ext_atr > 4:
        return "Late Uptrend"
    if ret60 is not None and ret60 > 60:
        return "Late Uptrend"
    if hh and hl:
        return "Established Uptrend"
    return "Early Uptrend"


def _breakout(c, h, v, vsma, highs):
    """Section 8 — six states, decided on the real consolidation high."""
    if len(highs) < 1:
        return "No Breakout", None
    res = max(x[1] for x in highs[-4:])          # the level that actually mattered recently
    close, prev = c[-1], c[-2] if len(c) > 1 else c[-1]
    vx = (v[-1] / vsma[-1]) if vsma and vsma[-1] else 0
    above = close > res
    was_above = any(x > res for x in c[-6:-1])
    if not above and not was_above:
        return ("Attempting Breakout" if h[-1] > res else "No Breakout"), res
    if above and not was_above:
        return ("Confirmed Breakout" if vx >= 1.5 else "Attempting Breakout"), res
    if above and was_above:
        run = (close / res - 1) * 100
        return ("Extended Breakout" if run > 12 else "Confirmed Breakout"), res
    return ("Breakout Retest" if close > res * 0.98 else "Failed Breakout"), res


def _pullback(c, l, v, vsma, e20, e50, lows):
    """Section 9 — is the dip an opportunity or the start of the exit."""
    if e20 is None or e50 is None:
        return "WAIT"
    close = c[-1]
    peak = max(c[-20:]) if len(c) >= 20 else max(c)
    depth = (peak - close) / peak * 100 if peak else 0
    vx = (v[-1] / vsma[-1]) if vsma and vsma[-1] else 1.0
    broke_low = len(lows) >= 2 and close < lows[-2][1]
    if depth < 1.5:
        return "NO PULLBACK"
    if close < e50 and broke_low:
        return "TREND BREAKDOWN"
    if broke_low or (vx > 1.5 and c[-1] < c[-2]):        # heavy selling / lower low
        return "DANGEROUS"
    if close >= e20 and vx < 1.0:
        return "BUY ZONE"
    if close >= e50 and vx < 1.2:
        return "HEALTHY PULLBACK"
    return "WAIT"


MAJOR_LOOKBACK = 250        # ~1 trading year; an all-time high from 2019 is not "resistance"


def _levels(c, h, l, a, highs, lows):
    """Section 10/12 — entry, a structural stop, and R-multiple targets. No invented levels."""
    close, atr_now = c[-1], a
    cut = len(c) - MAJOR_LOOKBACK
    rh = [(i, v) for i, v in highs if i >= cut]      # major S/R inside the useful window only
    rl = [(i, v) for i, v in lows if i >= cut]
    swing_low = next((v for _, v in reversed(rl) if v < close), None)
    struct_stop = swing_low if swing_low and (close - swing_low) < 3.5 * atr_now else None
    stop = struct_stop if struct_stop else close - 2 * atr_now
    risk = close - stop
    if risk <= 0:
        return None
    near_res = next((v for _, v in reversed(rh) if v > close), None)
    return dict(entry=close, stop=stop, risk=risk,
                t1=close + 2 * risk, t2=close + 3 * risk, t3=close + 5 * risk,
                near_res=near_res,
                major_res=max((v for _, v in rh), default=None),
                near_support=swing_low,
                major_support=min((v for _, v in rl), default=None))


def _false_signals(f):
    """Section 16 — the ten ways this could be a trap. Returns the ones that fired."""
    flags = []
    if f["breakout"] == "Failed Breakout":
        flags.append("failed-breakout")
    if f["breakout"] == "Extended Breakout":
        flags.append("extended")
    if f["ext_atr"] is not None and f["ext_atr"] > 4:
        flags.append("far-above-20EMA")
    if f["vol_x"] < 1.0 and f["breakout"] in ("Confirmed Breakout", "Attempting Breakout"):
        flags.append("breakout-without-volume")
    if f["wick_pct"] > 55:
        flags.append("rejection-wick")
    if f["room_r"] is not None and f["room_r"] < 1:
        flags.append("resistance-overhead")
    if f["rsi_div"]:
        flags.append("bearish-divergence")
    if f["pullback"] in ("DANGEROUS", "TREND BREAKDOWN"):
        flags.append("distribution")
    if f["rr"] < MIN_RR:
        flags.append("poor-rr")
    if not f["liquid"]:
        flags.append("illiquid")
    if f.get("repeated_failures", 0) >= 2:
        flags.append("repeated-failures")
    if f.get("chasing"):
        flags.append("chasing-a-large-candle")
    return flags


def _score(f):
    """Section 20 — the exact 100-point rubric, each part capped at its stated weight."""
    p = {}
    # Daily Trend / EMA structure ....................................... 20
    t = 0
    t += 4 if f["close"] > (f["e20"] or 1e18) else 0
    t += 4 if (f["e20"] or 0) > (f["e50"] or 1e18) else 0
    t += 4 if (f["e50"] or 0) > (f["e200"] or 1e18) else 0
    t += 3 if (f["e20_slope"] or -1) > 0 else 0
    t += 3 if (f["e50_slope"] or -1) > 0 else 0
    t += 2 if f["ext_atr"] is not None and f["ext_atr"] <= 3 else 0
    p["trend"] = min(t, 20)
    # Price structure ................................................... 15
    s = (5 if f["hh"] else 0) + (5 if f["hl"] else 0)
    s += 5 if f["last_event"].startswith(("BOS up", "CHoCH up")) else 0
    p["structure"] = min(s, 15)
    # Volume confirmation ............................................... 15
    vscore = 6 if f["vol_x"] >= 1.5 else (3 if f["vol_x"] >= 1.2 else 0)
    vscore += 3 if f["vol_x"] >= 2.0 else 0
    vscore += 3 if f["up_day"] and f["vol_x"] >= 1.2 else 0      # buying, not just activity
    vscore += 3 if f["pullback_dry"] else 0                       # dips on falling volume
    p["volume"] = min(vscore, 15)
    # Entry location / breakout-pullback quality ........................ 15
    bq = {"Confirmed Breakout": 15, "Breakout Retest": 15, "Attempting Breakout": 9,
          "Extended Breakout": 4, "Failed Breakout": 0, "No Breakout": 0}[f["breakout"]]
    pq = {"BUY ZONE": 13, "HEALTHY PULLBACK": 10, "NO PULLBACK": 6, "WAIT": 4,
          "DANGEROUS": 0, "TREND BREAKDOWN": 0}[f["pullback"]]
    p["entry"] = min(max(bq, pq), 15)
    # Momentum .......................................................... 10
    m = (3 if (f["rsi"] or 0) > 50 else 0) + (2 if (f["rsi_slope"] or -1) > 0 else 0)
    m += 3 if f["macd"] is not None and f["macd_sig"] is not None and f["macd"] > f["macd_sig"] else 0
    m += 2 if (f["hist_slope"] or -1) > 0 else 0
    p["momentum"] = min(m, 10)
    # Relative performance .............................................. 5
    rp = (2 if (f["ret20"] or -1) > 0 else 0) + (2 if (f["ret60"] or -1) > 0 else 0)
    rp += 1 if f["off_60d_high"] is not None and f["off_60d_high"] <= 10 else 0
    p["relative"] = min(rp, 5)
    # Support / resistance room ......................................... 5
    p["sr"] = 5 if (f["room_r"] or 0) >= 2 else (3 if (f["room_r"] or 0) >= 1 else 0)
    # Risk / reward ..................................................... 5
    p["rr"] = 5 if f["rr"] >= 3 else (4 if f["rr"] >= 2 else (2 if f["rr"] >= 1.5 else 0))
    # Fundamental quality ............................................... 5
    if f["has_fund"]:
        fq = 1
        fq += 2 if (f["roe"] or 0) >= 15 else (1 if (f["roe"] or 0) >= 10 else 0)
        fq += 2 if f["pe"] is not None and 0 < f["pe"] <= 40 else 0
        p["fundamental"] = min(fq, 5)
    else:
        p["fundamental"] = 0
    # Liquidity ......................................................... 5
    tn = f["turnover"]
    p["liquidity"] = 5 if tn >= 5_000_000 else (3 if tn >= 1_000_000 else
                                                (2 if tn >= LIQUID_MIN else 0))
    return p, sum(p.values())


def _grade(score):
    return ("A+ ELITE" if score >= 90 else "A STRONG" if score >= 80 else
            "B WATCHLIST" if score >= 70 else "C WEAK" if score >= 60 else "AVOID")


def _performer(f):
    """Section 1 — is this genuinely performing, or just a spike?"""
    yes = sum([
        f["close"] > (f["e20"] or 1e18), f["hh"], f["hl"],
        (f["e20"] or 0) > (f["e50"] or 1e18), (f["e50"] or 0) > (f["e200"] or 1e18),
        (f["e20_slope"] or -1) > 0, (f["rsi"] or 0) > 50,
        f["vol_x"] >= 1.2, (f["ret20"] or -1) > 0, (f["ret60"] or -1) > 0,
    ])
    if f["pullback"] == "TREND BREAKDOWN" or not f["liquid"]:
        return "AVOID"
    return ("ELITE PERFORMER" if yes >= 9 else "STRONG PERFORMER" if yes >= 7 else
            "DEVELOPING" if yes >= 5 else "NEUTRAL" if yes >= 3 else "WEAK")


def _decision(f, score, flags):
    """Sections 12/16/17 — the gates, applied in order. Any hard gate = no trade."""
    if not f["liquid"]:
        return "AVOID", "liquidity below the fill threshold"
    if f["rr"] < MIN_RR:
        return "AVOID", f"risk/reward {f['rr']:.2f} is under the 1:{MIN_RR:g} gate"
    if f["pullback"] in ("DANGEROUS", "TREND BREAKDOWN"):
        return "AVOID", f"pullback is {f['pullback'].lower()}"
    if score < 60:
        return "AVOID", f"score {score} is below 60"
    if score < 70:
        return "WAIT", f"score {score} is only C-grade"
    if f["breakout"] == "Extended Breakout":
        return "WAIT", "already extended — the reward left does not justify chasing"
    if f["breakout"] == "Attempting Breakout":
        return "BUY ON BREAKOUT", "needs a daily close above resistance on volume"
    if f["breakout"] == "Breakout Retest":
        return "BUY ON RETEST", "breakout is being retested and holding"
    if f["pullback"] in ("BUY ZONE", "HEALTHY PULLBACK"):
        return "BUY", f"{f['pullback'].lower()} into trend support"
    if f["breakout"] == "Confirmed Breakout":
        return "BUY", "confirmed breakout with volume"
    return "WAIT", "no A-grade entry trigger today"


def _setup(f):
    """Section 17 — which of the five setups this actually is."""
    if f["breakout"] == "Breakout Retest":
        return "B. Breakout Retest"
    if f["breakout"] in ("Confirmed Breakout", "Attempting Breakout"):
        return "A. Breakout"
    if f["pullback"] in ("BUY ZONE", "HEALTHY PULLBACK"):
        return "C. Pullback"
    if f["stage"] in ("Established Uptrend", "Early Uptrend"):
        return "D. Continuation"
    if f["last_event"].startswith("CHoCH up"):
        return "E. Early Reversal"
    return "none"


# ---------------------------------------------------------------- the analysis

def analyse(symbol, funds=None):
    """Every field the 22-section report needs, or None when there is not enough daily data."""
    b = bars(symbol)
    if not b or len(b[4]) < 210:            # 200 EMA needs real history; never fabricate it
        return None
    d, o, h, l, c, v = b
    e20, e50, e200 = ema(c, 20), ema(c, 50), ema(c, 200)
    r = rsi(c, 14)
    a = atr(h, l, c, 14)
    mline, msig, mhist = macd(c, 12, 26, 9)
    vsma = sma(v, 20)
    ph, pl, highs, lows = _swings(h, l)
    atr_now = _at(a)
    if atr_now in (None, 0):
        return None

    lev = _levels(c, h, l, atr_now, highs, lows)
    if not lev:
        return None

    turn = sorted(c[k] * v[k] for k in range(max(0, len(c) - 20), len(c)))
    turnover = turn[len(turn) // 2] if turn else 0.0
    rng = h[-1] - l[-1]
    upper_wick = h[-1] - max(o[-1], c[-1])
    fund = (funds or {}).get(symbol, {})

    rsi_div, rsi_bull_div = _divergence(r, h, l, highs, lows)
    macd_div, macd_bull_div = _divergence(mline, h, l, highs, lows)

    bo, res_level = _breakout(c, h, v, vsma, highs)
    pb = _pullback(c, l, v, vsma, _at(e20), _at(e50), lows)
    struct_lbl, last_event, hh, hl, lh, ll = _structure(c, ph, pl, highs, lows)
    ext_atr = (c[-1] - _at(e20)) / atr_now if _at(e20) is not None else None
    room = (lev["near_res"] - lev["entry"]) if lev["near_res"] else None

    f = dict(
        symbol=symbol, date=d[-1], close=c[-1],
        e20=_at(e20), e50=_at(e50), e200=_at(e200),
        e20_slope=_slope(e20), e50_slope=_slope(e50), e200_slope=_slope(e200),
        ext_atr=ext_atr, rsi=_at(r), rsi_slope=_slope(r, 3),
        macd=_at(mline), macd_sig=_at(msig), macd_hist=_at(mhist),
        hist_slope=(None if _at(mhist) is None or _at(mhist, -4) is None
                    else _at(mhist) - _at(mhist, -4)),
        atr=atr_now, atr_pct=atr_now / c[-1] * 100,
        vol_x=(v[-1] / vsma[-1]) if vsma and vsma[-1] else 0.0,
        up_day=c[-1] > o[-1],
        pullback_dry=(c[-1] < c[-2] and vsma and vsma[-1] and v[-1] < vsma[-1]),
        wick_pct=(upper_wick / rng * 100) if rng else 0.0,
        turnover=turnover, liquid=turnover >= LIQUID_MIN,
        ret5=_ret(c, 5), ret20=_ret(c, 20), ret60=_ret(c, 60),
        off_60d_high=((max(c[-60:]) - c[-1]) / max(c[-60:]) * 100) if len(c) >= 60 else None,
        hh=hh, hl=hl, lh=lh, ll=ll, structure=struct_lbl, last_event=last_event,
        breakout=bo, resistance_tested=res_level, pullback=pb,
        rsi_div=rsi_div, rsi_bull_div=rsi_bull_div, rsi_overext=(_at(r) or 50) > 70,
        macd_div=macd_div, macd_bull_div=macd_bull_div,
        macd_above_zero=(_at(mline) or 0) > 0, macd_cross=_macd_cross(mline, msig),
        crossovers=_crossovers(e20, e50, e200),
        separation=_separation(c[-1], _at(e20), _at(e50), _at(e200)),
        accum=_accum_dist(o, c, v), adv_decl=_advances_declines(c),
        off_20d_high=((max(c[-20:]) - c[-1]) / max(c[-20:]) * 100) if len(c) >= 20 else None,
        avg_vol=(sum(v[-20:]) / 20) if len(v) >= 20 else None,
        gap_flag=_gaps(o, c), repeated_failures=_repeated_failures(c, h, highs),
        has_fund=bool(fund), roe=_num(fund, "roe_ttm"), pe=_num(fund, "pe_ttm"),
        bvps=_num(fund, "bvps"), float_pct=_num(fund, "float_pct"),
        **_breakout_detail(c, o, h, l, v, vsma, atr_now, res_level),
        **{k: lev[k] for k in ("entry", "stop", "risk", "t1", "t2", "t3",
                               "near_res", "major_res", "near_support", "major_support")},
    )
    f["risk_pct"] = f["risk"] / f["entry"] * 100
    f["room_r"] = (room / f["risk"]) if room else None
    f["vol_regime"], f["abnormal"] = _volume_regime(v, vsma)
    f["chasing"] = (f["bo_candle"] or 0) > 2.0            # today's body is 2+ ATRs — chasing
    f["sweep"], f["reclaim"] = _sweep_reclaim(c, h, l, highs, lows, atr_now)
    f["prev_breakout"] = f["resistance_tested"]
    # Section 15 — the demand/supply zone comes from supply_demand.py's engine rather than a
    # second implementation here, so this page and the Indicator cron board can never disagree.
    n = supply_demand.MAX_BARS
    try:
        z = supply_demand.dashboard(o[-n:], h[-n:], l[-n:], c[-n:])
    except (ValueError, IndexError, KeyError, ZeroDivisionError):
        z = None
    f["demand_zone"] = (None if not z else
                        f"{'demand' if z['kind'] == 'demand' else 'supply'} zone "
                        f"{z['lo']:,.2f}-{z['hi']:,.2f} ({z['state']}, {z['age']}d old"
                        + (", price is in it" if z["in_zone"] else "") + ")")
    # Section 11 — is there enough daily movement to swing at all, and is the stop sane?
    f["enough_vol"] = 1.0 <= f["atr_pct"] <= 12.0
    f["stop_width"] = ("too tight" if f["risk"] < 0.8 * atr_now else
                       "too wide" if f["risk"] > 3.5 * atr_now else "sensible")
    # Section 13 — P/B and EPS derived from what SmartWealthPro actually gives us
    f["pb"] = (f["close"] / f["bvps"]) if f["bvps"] else None
    f["eps"] = (f["close"] / f["pe"]) if f["pe"] else None
    f["fund_warn"] = []
    if f["has_fund"]:
        if (f["roe"] or 99) < 8:
            f["fund_warn"].append("ROE under 8%")
        if f["pe"] and f["pe"] > 60:
            f["fund_warn"].append(f"P/E {f['pe']:.0f} is stretched")
        if f["pb"] and f["pb"] > 6:
            f["fund_warn"].append(f"P/B {f['pb']:.1f} is stretched")
    # Section 14 — thin float plus an abnormal print is the manipulation shape
    f["manip"] = bool(f["abnormal"] and (f["float_pct"] or 100) < 25) or f["gap_flag"] >= 4
    # R:R measured to the realistic target — near resistance if it is closer than T1
    realistic = min(x for x in (f["t1"], f["near_res"] or f["t1"]) if x)
    f["rr"] = (realistic - f["entry"]) / f["risk"]
    f["stage"] = _stage(c[-1], f["e20"], f["e50"], f["e200"], hh, hl, f["ret60"], ext_atr)
    f["trend"] = ("Bullish" if f["close"] > (f["e20"] or 1e18) > (f["e50"] or 1e18)
                  else "Bearish" if f["close"] < (f["e50"] or 0) else "Neutral")
    f["flags"] = _false_signals(f)
    f["parts"], f["score"] = _score(f)
    f["grade"] = _grade(f["score"])
    f["performer"] = _performer(f)
    f["setup"] = _setup(f)
    f["decision"], f["why"] = _decision(f, f["score"], f["flags"])
    f["invalidation"] = (f"a daily close below {f['stop']:.2f}"
                         + (f" (the last swing low)" if f["near_support"] else " (2x ATR)"))
    # Section 17 — the SETUP's own grade, separate from the 100-point score
    f["setup_grade"] = ("NO TRADE" if f["setup"] == "none" or f["decision"] == "AVOID" else
                        "A+" if f["score"] >= 90 and not f["flags"] else
                        "A" if f["score"] >= 80 and len(f["flags"]) <= 1 else
                        "B" if f["score"] >= 70 else "C")
    f["profit_plan"] = _profit_plan(f)
    f["bull_evidence"] = _bull_evidence(f)
    f["bear_evidence"] = _bear_evidence(f)
    f["verdict"] = ("NO TRADE — WAIT FOR A BETTER 1D SETUP"
                    if f["decision"] in ("AVOID", "WAIT")
                    else f"{f['decision']} — {f['setup_grade']} setup, {f['grade']}, "
                         f"1:{f['rr']:.2f} to the realistic target")
    return f


def _bull_evidence(f):
    """Section 22 — the strongest things actually true, strongest first. No invention."""
    e = []
    if f["e20"] and f["e50"] and f["e200"] and f["e20"] > f["e50"] > f["e200"]:
        e.append("20>50>200 EMA stack, all three rising"
                 if (f["e20_slope"] or 0) > 0 and (f["e50_slope"] or 0) > 0
                 else "20>50>200 EMA stack")
    if f["hh"] and f["hl"]:
        e.append(f"higher highs and higher lows, last event {f['last_event']}")
    if f["vol_x"] >= 1.5:
        e.append(f"{f['vol_x']:.2f}x volume confirming the move")
    if f["accum"] == "Accumulation":
        e.append("volume arriving on up days (accumulation)")
    if f["breakout"] in ("Confirmed Breakout", "Breakout Retest"):
        e.append(f"{f['breakout'].lower()} after {f['consol_days']} sessions of base, "
                 f"{f['res_tests']} prior tests of the level")
    if f["pullback"] in ("BUY ZONE", "HEALTHY PULLBACK"):
        e.append(f"{f['pullback'].lower()} holding trend support on dry volume")
    if f["rsi_bull_div"]:
        e.append("bullish RSI divergence at the lows")
    if f["reclaim"]:
        e.append("support reclaimed after a break")
    if (f["room_r"] or 0) >= 3:
        e.append(f"{f['room_r']:.1f}R of room before resistance")
    return e or ["nothing on the bullish side clears the bar today"]


def _bear_evidence(f):
    """Section 22 — and the strongest reasons it fails. This half is never optional."""
    e = []
    if f["flags"]:
        e.append("false-signal flags: " + ", ".join(f["flags"]))
    if f["rr"] < MIN_RR:
        e.append(f"reward is only {f['rr']:.2f}x the risk, under the 1:{MIN_RR:g} gate")
    if f["vol_x"] < 1.0:
        e.append(f"volume is {f['vol_x']:.2f}x — participation is not confirming")
    if f["accum"] == "Distribution":
        e.append("volume arriving on down days (distribution)")
    if f["rsi_div"] or f["macd_div"]:
        e.append("negative momentum divergence against the last high")
    if f["trend"] != "Bullish":
        e.append(f"daily trend reads {f['trend']}, not bullish")
    if (f["room_r"] or 9) < 2:
        e.append("major resistance sits close overhead")
    if f["fund_warn"]:
        e.append("fundamentals: " + ", ".join(f["fund_warn"]))
    if not f["liquid"]:
        e.append("HIGH EXECUTION RISK — turnover below the fill threshold")
    if f["manip"]:
        e.append("abnormal print on a thin float, or repeated unexplained gaps")
    if f["stop_width"] != "sensible":
        e.append(f"stop is {f['stop_width']} against a {f['atr']:,.2f} ATR")
    return e or ["no material bearish evidence found on the daily chart"]


# ---------------------------------------------------------------- scan + report

def scan(names=None):
    funds = _fundamentals()
    names = names or (MASTER / "symbols.txt").read_text(encoding="utf-8").split()
    rows = []
    for s in names:
        try:
            f = analyse(s, funds)
        except (ValueError, IndexError, KeyError, ZeroDivisionError):
            continue
        if f:
            rows.append(f)
    order = {"BUY": 0, "BUY ON RETEST": 1, "BUY ON BREAKOUT": 2, "WAIT": 3, "AVOID": 4}
    rows.sort(key=lambda r: (order[r["decision"]], -r["score"]))
    return rows


def write(rows, path=OUT):
    lines = [HEADER]
    for r in rows:
        lines.append("\t".join(str(x) for x in (
            r["symbol"], r["date"], r["grade"], r["score"], r["decision"], r["performer"],
            round(r["close"], 2), round(r["entry"], 2), round(r["stop"], 2),
            round(r["t1"], 2), round(r["t2"], 2), round(r["t3"], 2),
            round(r["rr"], 2), round(r["risk_pct"], 2), r["trend"], r["stage"],
            r["structure"], r["breakout"], r["pullback"], round(r["vol_x"], 2),
            round(r["rsi"] or 0, 1), round(r["macd_hist"] or 0, 3), round(r["atr_pct"], 2),
            round(r["ret5"] or 0, 2), round(r["ret20"] or 0, 2), round(r["ret60"] or 0, 2),
            round(r["turnover"]), r["setup"], ",".join(r["flags"]) or "-")))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _n(v, dp=2, suffix=""):
    return "n/a" if v is None else f"{v:,.{dp}f}{suffix}"


def report(f):
    """Section 22 — the final output block, in the framework's exact order."""
    L = [f"Stock:                 {f['symbol']}",
         f"Current Price:         {_n(f['close'])}",
         f"Daily Trend:           {f['trend']}",
         f"Trend Stage:           {f['stage']}",
         f"Market Structure:      {f['structure']}  (last event: {f['last_event']})",
         f"20 EMA:                {_n(f['e20'])}   slope {_n(f['e20_slope'], 2, '%')}",
         f"50 EMA:                {_n(f['e50'])}   slope {_n(f['e50_slope'], 2, '%')}",
         f"200 EMA:               {_n(f['e200'])}   slope {_n(f['e200_slope'], 2, '%')}",
         f"Volume Ratio:          {_n(f['vol_x'])}x of its 20-day average",
         f"Volume Interpretation: {'buying' if f['up_day'] else 'selling'} pressure"
         f"{', pullback on dry volume' if f['pullback_dry'] else ''}",
         f"RSI 14:                {_n(f['rsi'], 1)}   {'rising' if (f['rsi_slope'] or 0) > 0 else 'falling'}"
         f"{'   BEARISH DIVERGENCE' if f['rsi_div'] else ''}",
         f"MACD 12/26/9:          {_n(f['macd'], 3)} vs signal {_n(f['macd_sig'], 3)}, "
         f"hist {_n(f['macd_hist'], 3)} {'rising' if (f['hist_slope'] or 0) > 0 else 'falling'}",
         f"ATR 14:                {_n(f['atr'])}  ({_n(f['atr_pct'], 2, '%')} of price)",
         f"Support:               near {_n(f['near_support'])}   major {_n(f['major_support'])}",
         f"Resistance:            near {_n(f['near_res'])}   major {_n(f['major_res'])}",
         f"Breakout/Pullback:     {f['breakout']}  /  {f['pullback']}",
         f"Smart-Money Evidence:  " + ", ".join(x for x in (
             f["demand_zone"], "liquidity sweep" if f["sweep"] else None,
             "support reclaim" if f["reclaim"] else None,
             f"structure {f['last_event']}" if f["last_event"] != "none" else None,
             f"{f['accum'].lower()} on volume" if f["accum"] != "Neutral" else None,
         ) if x) or "nothing the daily bars clearly show",
         f"Relative Performance:  5d {_n(f['ret5'], 2, '%')}   20d {_n(f['ret20'], 2, '%')}   "
         f"60d {_n(f['ret60'], 2, '%')}   {_n(f['off_60d_high'], 1, '%')} off its 60d high",
         f"Fundamental Quality:   " + (f"ROE {_n(f['roe'], 1, '%')}  P/E {_n(f['pe'], 1)}  "
                                       f"BVPS {_n(f['bvps'])}  float {_n(f['float_pct'], 1, '%')}"
                                       if f["has_fund"] else "no data on file — not scored"),
         f"Liquidity:             Rs {f['turnover']:,.0f} median 20d turnover"
         f"{'' if f['liquid'] else '   HIGH EXECUTION RISK'}",
         f"False-Signal Risk:     {', '.join(f['flags']) if f['flags'] else 'none of the ten fired'}",
         "",
         f"Score:                 {f['score']}/100",
         f"Grade:                 {f['grade']}",
         f"Decision:              {f['decision']}   ({f['why']})",
         f"Entry Zone:            {_n(f['entry'])}",
         f"Stop Loss:             {_n(f['stop'])}   (risk {_n(f['risk_pct'], 2, '%')})",
         f"Target 1:              {_n(f['t1'])}   (2R)",
         f"Target 2:              {_n(f['t2'])}   (3R)",
         f"Target 3:              {_n(f['t3'])}   (5R)",
         f"Risk/Reward:           1:{_n(f['rr'], 2)} to the realistic target"
         f"{'   BELOW THE 1:2 GATE' if f['rr'] < MIN_RR else ''}",
         f"Trade Invalidation:    {f['invalidation']}",
         "Expected Holding Period: several days to several weeks (swing)",
         "",
         "Profit-Maximization Plan:"]
    L += [f"  - {x}" for x in f["profit_plan"]]
    L += ["", "Strongest Bullish Evidence:"]
    L += [f"  + {x}" for x in f["bull_evidence"]]
    L += ["", "Strongest Bearish Evidence:"]
    L += [f"  - {x}" for x in f["bear_evidence"]]
    L += ["",
          f"Why This Trade Could Profit:  {f['bull_evidence'][0]}"
          + (f"; {f['room_r']:.1f}R of room to resistance" if f["room_r"] else "")
          + f"; risk is capped at {f['risk_pct']:.2f}% with a {f['stop_width']} stop.",
          f"Why This Trade Could Fail:    {f['bear_evidence'][0]}.",
          "",
          f"FINAL VERDICT:         {f['verdict']}",
          "",
          "No claim of profit or certainty is made. This is a framework score on daily data, "
          "not a prediction."]
    return "\n".join(L)


def scorecard(f):
    """Section 20 as a table — every point traceable to a rule."""
    cap = {"trend": 20, "structure": 15, "volume": 15, "entry": 15, "momentum": 10,
           "relative": 5, "sr": 5, "rr": 5, "fundamental": 5, "liquidity": 5}
    name = {"trend": "Daily Trend / EMA Structure", "structure": "Price Structure / HH-HL",
            "volume": "Volume Confirmation", "entry": "Entry Location / Breakout-Pullback",
            "momentum": "Momentum / RSI / MACD", "relative": "Relative Performance",
            "sr": "Support / Resistance", "rr": "Risk/Reward",
            "fundamental": "Fundamental Quality", "liquidity": "Liquidity / Execution"}
    return [(name[k], f["parts"][k], cap[k]) for k in cap]


# Section 21 — the framework's fifteen questions VERBATIM, in its order and its polarity.
# Q9 is deliberately phrased so that YES is BAD ("already too extended"); do not flip it to
# read nicely, the whole point is that it is a warning question. Q14 is not a yes/no at all,
# so it is answered with text.
QUESTIONS = [
    ("Is the stock objectively in an uptrend?", lambda f: f["trend"] == "Bullish"),
    ("Is the daily structure showing HH/HL?", lambda f: f["hh"] and f["hl"]),
    ("Are the 20/50/200 EMAs supportive of the trend?",
     lambda f: None not in (f["e20"], f["e50"], f["e200"]) and f["e20"] > f["e50"] > f["e200"]),
    ("Is volume confirming the important price moves?", lambda f: f["vol_x"] >= 1.2),
    ("Is momentum healthy rather than deteriorating?",
     lambda f: (f["rsi"] or 0) > 50 and not f["rsi_div"]),
    ("Is the stock accumulating rather than distributing?",
     lambda f: f["accum"] == "Accumulation"
     and f["pullback"] not in ("DANGEROUS", "TREND BREAKDOWN")),
    ("Is the current entry close to a logical support/invalidation level?",
     lambda f: f["risk_pct"] <= 8 and f["near_support"] is not None),
    ("Is there sufficient room before major resistance?", lambda f: (f["room_r"] or 99) >= 2),
    ("Is the stock already too extended to enter safely?",          # YES here is a WARNING
     lambda f: (f["ext_atr"] or 0) > 3 or f["breakout"] == "Extended Breakout"),
    ("Is the breakout or pullback genuinely confirmed?",
     lambda f: f["breakout"] in ("Confirmed Breakout", "Breakout Retest")
     or f["pullback"] in ("BUY ZONE", "HEALTHY PULLBACK")),
    ("Is the stop-loss technically logical?",
     lambda f: f["near_support"] is not None and f["stop_width"] == "sensible"),
    ("Is realistic reward at least 2x the risk?", lambda f: f["rr"] >= MIN_RR),
    ("Is liquidity sufficient for practical execution?", lambda f: f["liquid"]),
    ("What is the strongest reason this trade could fail?", lambda f: f["bear_evidence"][0]),
    ("If this stock were not already rising, would the current 1D setup still make me want "
     "to buy it?", lambda f: f["score"] >= 70 and f["breakout"] != "Extended Breakout"
     and f["rr"] >= MIN_RR),
]

# Q9 is the one where YES counts against the trade — everything else reads YES = good.
WARNING_QUESTIONS = {9}


def answers(f):
    """[(question, answer)] — answer is bool except Q14, which is text by design."""
    out = []
    for n, (q, fn) in enumerate(QUESTIONS, 1):
        v = fn(f)
        out.append((q, v if isinstance(v, str) else bool(v)))
    return out


# ---------------------------------------------------------------- cli

def selftest():
    """The arithmetic and the gates, on synthetic bars — no network, no archive."""
    caps = {"trend": 20, "structure": 15, "volume": 15, "entry": 15, "momentum": 10,
            "relative": 5, "sr": 5, "rr": 5, "fundamental": 5, "liquidity": 5}
    assert sum(caps.values()) == 100, "the rubric must total exactly 100"

    # grade boundaries, exactly as the framework states them
    assert _grade(90) == "A+ ELITE" and _grade(89) == "A STRONG"
    assert _grade(80) == "A STRONG" and _grade(79) == "B WATCHLIST"
    assert _grade(70) == "B WATCHLIST" and _grade(69) == "C WEAK"
    assert _grade(60) == "C WEAK" and _grade(59) == "AVOID"

    # no part may ever exceed its cap, on random-ish field combinations
    import itertools
    base = dict(close=100, e20=99, e50=98, e200=90, e20_slope=1, e50_slope=1, ext_atr=1,
                hh=True, hl=True, last_event="BOS up", vol_x=3.0, up_day=True,
                pullback_dry=True, breakout="Confirmed Breakout", pullback="BUY ZONE",
                rsi=60, rsi_slope=1, macd=1, macd_sig=0.5, hist_slope=1, ret20=5, ret60=10,
                off_60d_high=2, room_r=5, rr=4, has_fund=True, roe=20, pe=20,
                turnover=10_000_000)
    parts, total = _score(base)
    for k, cap in caps.items():
        assert parts[k] <= cap, (k, parts[k], cap)
    assert total == 100, f"a perfect setup must score 100, got {total} {parts}"

    # a hopeless setup must floor at zero, not go negative
    bad = dict(base, close=50, e20=60, e50=70, e200=80, e20_slope=-1, e50_slope=-1, ext_atr=9,
               hh=False, hl=False, last_event="BOS down", vol_x=0.2, up_day=False,
               pullback_dry=False, breakout="Failed Breakout", pullback="TREND BREAKDOWN",
               rsi=30, rsi_slope=-1, macd=-1, macd_sig=0.5, hist_slope=-1, ret20=-9, ret60=-20,
               off_60d_high=40, room_r=0, rr=0.2, has_fund=False, roe=None, pe=None,
               turnover=1000)
    parts2, total2 = _score(bad)
    assert all(x >= 0 for x in parts2.values()) and total2 == 0, (parts2, total2)

    # the mandatory R:R gate must veto even a beautiful chart
    good = dict(base, rr=1.0, liquid=True, flags=[])
    dec, why = _decision(good, 95, [])
    assert dec == "AVOID" and "risk/reward" in why, (dec, why)
    # and illiquidity must veto ahead of everything else
    dec2, _ = _decision(dict(base, rr=5, liquid=False), 95, [])
    assert dec2 == "AVOID", dec2
    print(f"selftest ok — rubric totals 100, caps hold, gates veto (perfect={total}, floor={total2})")
    return 0


def main():
    argv = sys.argv[1:]
    if "--selftest" in argv:
        return selftest()

    if "--symbol" in argv:
        sym = argv[argv.index("--symbol") + 1].upper()
        f = analyse(sym, _fundamentals())
        if not f:
            print(f"{sym}: not enough daily history (200 EMA needs ~210 sessions).")
            return 1
        print(report(f))
        print("\n--- Section 20: the 100-point scorecard ---")
        for label, got, cap in scorecard(f):
            print(f"  {label:<38}{got:>3} / {cap}")
        print(f"  {'TOTAL':<38}{f['score']:>3} / 100   {f['grade']}")
        print("\n--- Section 21: the fifteen questions ---")
        for n, (q, ans) in enumerate(answers(f), 1):
            if isinstance(ans, str):                       # Q14 is not a yes/no
                print(f"  ->   {q}\n         {ans}")
            elif n in WARNING_QUESTIONS:                   # Q9: YES is the warning
                print(f"  {'WARN' if ans else 'ok':<4} {q}  (yes = bad)")
            else:
                print(f"  {'YES' if ans else 'no':<4} {q}")
        return 0

    rows = scan()
    write(rows)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    buys = [r for r in rows if r["decision"].startswith("BUY")]
    print(f"=== Swing Trader Pro · {stamp} · {len(rows)} symbols scored ===")
    print(f"{len(buys)} actionable -> {OUT}\n")
    print(f"{'symbol':<10}{'grade':<13}{'score':>6}{'decision':>17}{'setup':<22}"
          f"{'entry':>9}{'stop':>9}{'T1':>9}{'rr':>6}")
    for r in (buys or rows[:15]):
        print(f"{r['symbol']:<10}{r['grade']:<13}{r['score']:>6}{r['decision']:>17}  "
              f"{r['setup']:<22}{r['entry']:>9.2f}{r['stop']:>9.2f}{r['t1']:>9.2f}{r['rr']:>6.2f}")
    if not buys:
        print("\nNothing passes the gates today — that is a normal result, not a broken scan.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
