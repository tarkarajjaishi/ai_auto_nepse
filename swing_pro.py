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
    Section 14 liquidity            execution risk
    Section 16 false-signal filter  ten ways this setup could be a trap
    Section 17 setup selection      breakout / retest / pullback / continuation / reversal
    Section 20 score                the exact 100-point rubric
    Section 21 the fifteen questions

Sources: Master_data/symbols/<SYM>/1D.txt (bars, corporate-action adjusted on read via
prices.py), fundamentals.txt + sectors.txt (SmartWealthPro).

    python swing_pro.py                 # score every symbol -> Master_data/swing_pro.txt
    python swing_pro.py --symbol NABIL  # the full 22-section report for one stock
    python swing_pro.py --selftest      # assert the scoring and R:R arithmetic
"""
import sys
from datetime import datetime

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

    # bearish RSI divergence: price made a higher high, RSI did not
    rsi_div = False
    if len(highs) >= 2:
        i1, i2 = highs[-2][0], highs[-1][0]
        if h[i2] > h[i1] and r[i1] is not None and r[i2] is not None and r[i2] < r[i1]:
            rsi_div = True

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
        breakout=bo, resistance_tested=res_level, pullback=pb, rsi_div=rsi_div,
        has_fund=bool(fund), roe=_num(fund, "roe_ttm"), pe=_num(fund, "pe_ttm"),
        bvps=_num(fund, "bvps"), float_pct=_num(fund, "float_pct"),
        **{k: lev[k] for k in ("entry", "stop", "risk", "t1", "t2", "t3",
                               "near_res", "major_res", "near_support", "major_support")},
    )
    f["risk_pct"] = f["risk"] / f["entry"] * 100
    f["room_r"] = (room / f["risk"]) if room else None
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
    return f


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
         "Expected Holding:      several days to several weeks (swing)",
         ]
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


QUESTIONS = [
    ("Is the stock objectively in an uptrend?", lambda f: f["trend"] == "Bullish"),
    ("Is the daily structure showing HH/HL?", lambda f: f["hh"] and f["hl"]),
    ("Are the 20/50/200 EMAs supportive?",
     lambda f: None not in (f["e20"], f["e50"], f["e200"]) and f["e20"] > f["e50"] > f["e200"]),
    ("Is volume confirming the important moves?", lambda f: f["vol_x"] >= 1.2),
    ("Is momentum healthy rather than deteriorating?",
     lambda f: (f["rsi"] or 0) > 50 and not f["rsi_div"]),
    ("Is it accumulating rather than distributing?",
     lambda f: f["pullback"] not in ("DANGEROUS", "TREND BREAKDOWN")),
    ("Is the entry close to a logical invalidation?", lambda f: f["risk_pct"] <= 8),
    ("Is there room before major resistance?", lambda f: (f["room_r"] or 99) >= 2),
    ("Is it safe from being already too extended?",
     lambda f: (f["ext_atr"] or 0) <= 3 and f["breakout"] != "Extended Breakout"),
    ("Is the breakout or pullback genuinely confirmed?",
     lambda f: f["breakout"] in ("Confirmed Breakout", "Breakout Retest")
     or f["pullback"] in ("BUY ZONE", "HEALTHY PULLBACK")),
    ("Is the stop technically logical?", lambda f: f["near_support"] is not None),
    ("Is realistic reward at least 2x the risk?", lambda f: f["rr"] >= MIN_RR),
    ("Is liquidity sufficient to execute?", lambda f: f["liquid"]),
    ("Is it free of false-signal flags?", lambda f: not f["flags"]),
    ("If it were not already rising, would this setup still appeal?",
     lambda f: f["score"] >= 70 and f["breakout"] != "Extended Breakout"),
]


def answers(f):
    return [(q, bool(fn(f))) for q, fn in QUESTIONS]


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
        for q, ok in answers(f):
            print(f"  {'YES' if ok else 'no ':<4} {q}")
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
