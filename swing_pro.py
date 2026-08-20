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


def _table(name, key="symbol"):
    """{symbol: row} from any tab-separated Master_data table, {} when it is not there."""
    p = MASTER / name
    if not p.exists():
        return {}
    lines = p.read_text(encoding="utf-8").splitlines()
    if len(lines) < 2:
        return {}
    cols = lines[0].split("\t")
    out = {}
    for line in lines[1:]:
        f = line.split("\t")
        if len(f) == len(cols):
            out[dict(zip(cols, f))[key]] = dict(zip(cols, f))
    return out


# Section 13 asks for eleven fundamentals. These are the ones NEPSE/SmartWealthPro simply do
# not publish anywhere we can reach, so they are declared unavailable rather than invented —
# the spec's closing rule is "never fabricate missing data".
FUND_UNAVAILABLE = ["revenue growth", "debt", "cash flow"]

# Section 14 asks for "bid/ask or execution conditions if available". NEPSE publishes no order
# book we can reach, so it is declared rather than silently omitted — the spec's closing rule is
# to state when required data is unavailable, and turnover is a proxy for fillability, not depth.
EXEC_UNAVAILABLE = "bid/ask spread and market depth (no NEPSE order book is published)"


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


def _momentum_accel(c):
    """Section 4 — "momentum acceleration or deterioration", which is a SEPARATE bullet from
    "are recent advances stronger than recent declines" (_advances_declines). Per-day pace over
    the last 5 sessions minus the same over 20: positive means the move is speeding up.
    Returned in percentage points per day, so it is comparable across stocks."""
    r5, r20 = _ret(c, 5), _ret(c, 20)
    return None if r5 is None or r20 is None else r5 / 5 - r20 / 20


def _ema_respected(l, c, e, look=60):
    """Section 2 — "whether pullbacks respect the 20 EMA or 50 EMA". Not "is price above it
    today", which is a different question the trend block already answers: of the dips that
    actually reached the EMA in the window, what share closed back above it. None when price
    has not tested that EMA at all, because 0-of-0 is not a failure."""
    tests = held = 0
    for i in range(max(0, len(c) - look), len(c)):
        if e[i] is None:
            continue
        if l[i] <= e[i]:
            tests += 1
            held += c[i] >= e[i]
    return None if not tests else held / tests * 100


def _buyers_return(h, l, c, support, look=5):
    """Section 9 — "buyers return near support", the one healthy-pullback trait that was never
    implemented. Its daily footprint is a bar that traded down to the level and closed in the
    upper half of its own range: sellers reached the level, buyers took it back."""
    if not support:
        return None
    for i in range(max(0, len(c) - look), len(c)):
        rng = h[i] - l[i]
        if l[i] <= support and rng and (c[i] - l[i]) / rng >= 0.5:
            return True
    return False


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
    # How long the base under this level actually ran: walk back while price stays inside a
    # band beneath it, capped at one year. The old version counted "sessions since the close was
    # last 2% above the level", which collapsed to 0 exactly when a breakout surfaced it, and
    # ran to thousands of bars when the level had never been exceeded.
    floor_, ceil_ = res - max(4 * a, res * 0.08), res * 1.02
    # Walk back over the ENTIRE current breakout/retest episode before measuring anything: a
    # breakout that popped above and pulled back still has today's close near the level, so
    # counting from today collapses the base to ~1 on exactly the setups that surface it.
    i = len(c) - 1
    while i > 0 and c[i] > res * 0.98:
        i -= 1
    consol = 0
    stop_at = max(0, i - 250)
    while i > stop_at and floor_ <= c[i] <= ceil_:
        consol += 1
        i -= 1
    rng = h[-1] - l[-1]
    return dict(res_tests=tests, consol_days=consol,
                bo_candle=(abs(c[-1] - o[-1]) / a) if a else None,
                close_pos=((c[-1] - l[-1]) / rng * 100) if rng else None,
                follow_through=sum(1 for i in range(-3, 0) if c[i] > res))


def _retest_detail(c, h, l, v, vsma, a, res, look=30):
    """Section 8 — "retest behaviour": what price actually DID on its way back to the level.

    Every other §8 bullet has a number behind it; this one had only the six-state label, so the
    board could say "Breakout Retest" without ever saying whether the retest was any good. The
    spec's preferred sequence is Consolidation -> Breakout -> Volume Confirmation -> Retest ->
    Continuation, and §17.B wants "a controlled retest that HOLDS", so three things must be
    measurable: did price come back, how deep did it cut, and on what volume.

    The spec does not define "holds", so the definitions are stated here rather than implied:
      breakout bar  the most recent CROSSING inside `look` — closed above res, previous close
                    at or below it. The crossing, not merely any close above, or an old
                    breakout that has long since resolved would anchor the measurement.
      retest        a later bar whose LOW came within tol of res, tol being the same
                    max(0.5 ATR, 0.5%) this module already uses for "tagged the level".
      held          no close since the breakout finished below res - tol. A wick through the
                    level is a retest; a CLOSE through it is a failed one.
      depth         deepest low since the breakout, in ATR below res. Negative means price
                    pulled back but never actually reached the level.
      dry           volume on that deepest bar against its 20-day average. §5 asks for a
                    pullback on FALLING volume, so under 1.0 is the healthy shape here.

    All None when there is no breakout in the window or price has not come back yet — "no
    retest" is a different statement from "a bad retest" and must not read as one.
    """
    blank = dict(retest_depth=None, retest_held=None, retest_vol=None, retest_bars=None)
    if not res or not a:
        return blank
    n = len(c)
    bo = next((i for i in range(n - 1, max(1, n - look) - 1, -1)
               if c[i] > res and c[i - 1] <= res), None)
    if bo is None or bo >= n - 1:
        return blank
    tol = max(a * 0.5, res * 0.005)
    after = list(range(bo + 1, n))
    deep, di = min((l[i], i) for i in after)
    if deep > res + tol:                       # pulled back, but never reached the level
        return blank
    return dict(retest_depth=(res - deep) / a,
                retest_held=not any(c[i] < res - tol for i in after),
                retest_vol=(v[di] / vsma[di]) if vsma and vsma[di] else None,
                retest_bars=n - 1 - bo)


def _repeated_failures(c, h, res, look=120):
    """Section 16 — how many times has THIS level actually rejected price recently?

    The old version walked the swing highs testing `h[i] > lvl * 0.999`, where lvl WAS h[i] —
    so the test read `x > 0.999x` and was always true. What it really counted was "a swing high
    exists", it fired on 307 of 307 symbols, and because Section 17 grades A+ only on a clean
    flag list it made the top setup grade structurally unreachable.

    A FAILED BREAKOUT — the spec's words — needs price to have actually breached the level and
    then lost it: the bar's high went above res, the close came back under, and the level was
    not reclaimed inside the following week. Merely tagging the level from below is a rejection,
    a different and much commoner event; counting those fired on 83% of the market instead of 34%.
    """
    if not res:
        return 0
    fails, i = 0, max(0, len(c) - look)
    while i < len(c):
        if h[i] > res and c[i] < res and max(c[i + 1:i + 8], default=0) < res:
            fails += 1
            i += 8                      # count the episode once, not once per bar in it
        else:
            i += 1
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


def _frequency(dates, market_days, look=60):
    """Section 14 — how many of the last `look` MARKET sessions this symbol actually traded.
    Without it, 'median 20d turnover' silently stretches across months on a thin instrument."""
    if not market_days:
        return None
    recent = market_days[-look:]
    have = set(dates)
    return sum(1 for d in recent if d in have) / len(recent) * 100


def _gaps(o, c, look=60):
    """Section 14 — unexplained gaps, the kind that make a stop unreliable."""
    return sum(1 for i in range(max(1, len(c) - look), len(c))
               if abs(o[i] / c[i - 1] - 1) > 0.05)


def _profit_plan(f):
    """Section 19 — early enough entry, controlled risk, trail the trend. Stated, not implied."""
    return [
        f"Initial target {f['t1']:,.2f} ({f['rr']:.1f}R) — take partial profit there and "
        f"reduce risk.",
        f"Extended targets {f['t2']:,.2f} ({f['t2_r']:.1f}R) and {f['t3']:,.2f} "
        f"({f['t3_r']:.1f}R) only while the trend holds.",
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


def _ranging(c, h, l, a, look=20):
    """Is the last `look` bars a genuine base? Range measured in ATR, so it is comparable across
    stocks: under ~2.5 ATR of travel over 20 sessions is a contraction, not a trend leg."""
    if len(c) < look or not a:
        return False
    return (max(h[-look:]) - min(l[-look:])) / a <= 2.5


def _stage(c, h, l, a, e20, e50, e200, hh, hl, ret60, ext_atr):
    """Section 2/3 — where in the trend's life this is.

    Consolidation is a RANGE statement, so it is decided by measuring the range. Deciding it
    from EMA order alone inverted the label: the names called Consolidation had a wider median
    20-day range than the ones called Established Uptrend.
    """
    close = c[-1]
    up = all(v is not None for v in (e20, e50, e200)) and close > e20 > e50 > e200
    tight = _ranging(c, h, l, a)
    if not up:
        if e50 is not None and e200 is not None and e50 < e200:
            return "Consolidation" if tight else "Downtrend"
        if tight:
            return "Consolidation"
        return "Consolidation" if (hh or hl) else "Reversal Attempt"
    if ext_atr is not None and ext_atr > 4:
        return "Late Uptrend"
    if ret60 is not None and ret60 > 60:
        return "Late Uptrend"
    if tight:
        return "Consolidation"                 # a trend that has stopped travelling is a base
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


def _selling_weakens(o, c, look=10):
    """Section 9 — 'selling candles weaken': are the recent down-bodies smaller than the earlier
    ones in the same leg? None when there is not a down candle on each side to compare."""
    downs = [(i, o[i] - c[i]) for i in range(-look, 0) if c[i] < o[i]]
    if len(downs) < 4:
        return None
    half = len(downs) // 2
    early = sum(b for _, b in downs[:half]) / half
    late = sum(b for _, b in downs[half:]) / (len(downs) - half)
    return late < early


def _confirmed(o, c):
    """Section 9/17.C — "bullish daily confirmation appears". ONE definition, used by the
    pullback classifier, the report and the conformance test, so they cannot drift apart."""
    return c[-1] > o[-1] or c[-1] > c[-2]


def _pullback(o, c, l, v, vsma, e20, e50, lows, accum="Neutral"):
    """Section 9 — is the dip an opportunity or the start of the exit.

    'Bullish daily confirmation appears' is a REQUIREMENT of the healthy case, not decoration.
    Without it the top state fired on stocks that closed down on the day: 16 of 28 names it
    called BUY ZONE / HEALTHY PULLBACK had a red candle, which is a falling knife, not a buy.
    """
    if e20 is None or e50 is None:
        return "WAIT"
    close = c[-1]
    peak = max(c[-20:]) if len(c) >= 20 else max(c)
    depth = (peak - close) / peak * 100 if peak else 0
    vx = (v[-1] / vsma[-1]) if vsma and vsma[-1] else 1.0
    broke_low = len(lows) >= 2 and close < lows[-2][1]
    confirmed = _confirmed(o, c)                         # today actually closed up
    weakening = _selling_weakens(o, c)
    if depth < 1.5:
        return "NO PULLBACK"
    if close < e50 and broke_low:
        return "TREND BREAKDOWN"
    # Section 9's dangerous list is: heavy selling volume, breaks major support, creates a lower
    # low, loses EMA structure, STRONG DISTRIBUTION CANDLES. The last one was never wired in —
    # _pullback could not see accum at all — so five symbols were called BUY ZONE or HEALTHY
    # PULLBACK while volume was arriving on their down days, which is the definition of the
    # dangerous case, not the healthy one.
    if broke_low or (vx > 1.5 and c[-1] < c[-2]) or accum == "Distribution":
        return "DANGEROUS"
    # `weakening is not False` — None means too few down candles to judge, which must not veto.
    # This has to sit INSIDE the healthy branches: as a separate pre-check it was dead code,
    # because every path reaching it without `confirmed` already fell through to WAIT anyway.
    if close >= e20 and vx < 1.0 and confirmed and weakening is not False:
        return "BUY ZONE"
    if close >= e50 and vx < 1.2 and confirmed and weakening is not False:
        return "HEALTHY PULLBACK"
    return "WAIT"


MAJOR_LOOKBACK = 250        # ~1 trading year; an all-time high from 2019 is not "resistance"


def _levels(c, h, l, a, highs, lows):
    """Section 10/12 — entry, a structural stop, and realistic targets. No invented levels."""
    close, atr_now = c[-1], a
    cut = len(c) - MAJOR_LOOKBACK
    rh = [(i, v) for i, v in highs if i >= cut]      # major S/R inside the useful window only
    rl = [(i, v) for i, v in lows if i >= cut]
    swing_low = next((v for _, v in reversed(rl) if v < close), None)
    # Whether the real structural level was USABLE is itself an output: when the last swing low
    # sits 3.5+ ATR away we fall back to a 2 ATR stop, and then the stop is NOT at structure.
    # Section 11 asks "is the stop too wide" and Section 18 names the invalidation level, so
    # both have to know. Without this, "too wide" was unreachable (risk can never exceed
    # 3.5 ATR by construction) and Section 18 called a 2 ATR stop "the last swing low".
    stop_far = bool(swing_low and (close - swing_low) >= 3.5 * atr_now)
    struct_stop = None if (swing_low is None or stop_far) else swing_low
    stop = struct_stop if struct_stop else close - 2 * atr_now
    risk = close - stop
    if risk <= 0:
        return None
    # Section 10 lists "near resistance" and "major resistance" as different things: NEAR is the
    # closest level overhead, MAJOR the highest. This used to take the most RECENT swing high
    # above price instead of the closest, which is not the same level on 227 of 307 symbols and
    # overstated the distance to resistance by 3.9% at the median, 22% at worst. It matters more
    # than a printed line: T1 is measured to near_res, so the mandatory R:R gate was being fed a
    # resistance further away than the one price will actually hit first.
    near_res = min((v for _, v in rh if v > close), default=None)
    major_res = max((v for _, v in rh if v > close), default=None)
    # Targets come from STRUCTURE first, R-multiples only as the fallback. Defining T1 as a flat
    # 2R made the whole thing circular: reward/risk was then arithmetically pinned at exactly
    # 2.00 for every stock alive, the spec's "excellent 1:3+" band was unreachable, and the
    # 5-point R:R bucket could never be earned. Real resistance decides the realistic target.
    # ...but "realistic" is the operative word in the spec. A resistance 90R away is not a swing
    # target, it is a different trade, so T1 is bounded by a swing horizon of normal daily
    # movement. Floor 2R, ceiling ~10 ATR, resistance in between when it sits there.
    # Two independent ceilings, because either alone leaks: a distant resistance blows up the
    # reward, and a very tight swing-low stop makes even a near one look like 90R.
    horizon = close + 10 * atr_now          # ~2 weeks of normal daily movement
    # T1 is the REALISTIC target and is never lifted to make a trade look acceptable. It used
    # to be floored at 2R — `max(close + 2*risk, ...)` — which pinned rr >= 2 by construction.
    # Section 12 calls the 1:2 rule a MANDATORY GATE, but with that floor the gate rejected 0
    # real setups across the whole market and fired only twice, on floating-point noise, telling
    # the reader "risk/reward 2.00 is under the 1:2 gate". Section 20's 0-point R:R bucket was
    # likewise never awarded. With no floor, resistance sitting close overhead genuinely fails
    # the gate, which is what Section 10 ("avoid buying directly under major resistance") and
    # Section 12 ("reject if the realistic reward is too small") actually ask for.
    # ...but a level price is ALREADY at is not a target. Section 10's "near resistance" is
    # reported as the closest level overhead, whatever it is; the level that SETS T1 has to be
    # far enough away to be worth travelling to, or T1 collapses onto the close — C30MF closed
    # at 10.00 against a swing high of 10.01 and got a "realistic target" 0.1% away, rr 0.04.
    # Half an ATR is the same tolerance _breakout_detail uses to decide a level was tagged.
    target_res = min((v for _, v in rh if v > close + 0.5 * atr_now), default=None)
    t1 = min(target_res if target_res else horizon, close + 5 * risk, horizon)
    # T2 and T3 need the SAME two ceilings as T1. Bounding T2 by ATR alone leaked exactly the
    # way the note above warns: against a very tight swing-low stop, `close + 20*ATR` reached
    # 308R, and the report still labelled it "3R".
    t2_cap = min(close + 20 * atr_now, close + 10 * risk)
    t2 = major_res if (major_res and t1 + risk < major_res <= t2_cap) else t1 + risk
    t3 = max(t2 + 2 * risk, close + 5 * risk)
    return dict(entry=close, stop=stop, risk=risk, t1=t1, t2=t2, t3=t3,
                near_res=near_res, major_res=major_res, stop_far=stop_far,
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
    # Section 16's seventh trap is "Is distribution visible?", which is a volume question —
    # accum answers it. This used to fire on the PULLBACK STATE instead, so it was measuring a
    # different §16 item (there isn't one for "dangerous pullback"; that is already a hard gate
    # in _decision). The two populations barely overlapped: of 307 symbols, 55 were in genuine
    # distribution and carried no flag, while 72 carried the flag without being in distribution.
    if f["accum"] == "Distribution":
        flags.append("distribution")
    if f["rr"] < MIN_RR:
        flags.append("poor-rr")
    if not f["liquid"]:
        flags.append("illiquid")
    if f.get("thin_calendar"):
        flags.append("trades-infrequently")
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
    # The stated cap is 15 and it has to be attainable. The base leg used to pay 6, so the best
    # a real stock could score here was 6+3+3 = 12: the fourth leg, pullback_dry, requires
    # v[-1] < vsma[-1] (i.e. vol_x < 1.0) and can never coexist with the up_day leg's vol_x >= 1.2.
    # That silently made the rubric total 97, while scorecard() and Section 20 both printed /15
    # — and selftest's `assert total == 100` only passed on a synthetic dict analyse() cannot
    # produce. Section 5's two preferred shapes stay separate: expansion tops out at 15, and a
    # dip on drying volume still earns its own 3.
    vscore = 9 if f["vol_x"] >= 1.5 else (5 if f["vol_x"] >= 1.2 else 0)
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
    p["sr"] = 5 if f["room_r"] >= 2 else (3 if f["room_r"] >= 1 else 0)
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
    if f.get("thin_calendar"):        # per-bar stats mean little if it barely prints bars
        p["liquidity"] = min(p["liquidity"], 2)
    return p, sum(p.values())


def _grade(score):
    return ("A+ ELITE" if score >= 90 else "A STRONG" if score >= 80 else
            "B WATCHLIST" if score >= 70 else "C WEAK" if score >= 60 else "AVOID")


def _performer(f):
    """Section 1 — is this genuinely performing, or just a spike?

    ONE boolean per one of the framework's ten questions, in its order. The earlier version
    summed ten booleans that only covered seven questions — HH and HL took a slot each, both
    EMA comparisons took a slot each, and 20d/60d returns took a slot each — so Q7
    (accumulation), Q9 (overextended) and Q10 (upside before resistance) were never asked at
    all. That let stocks in outright distribution be labelled ELITE PERFORMER.
    """
    yes = sum([
        f["trend"] == "Bullish",                                    # 1 daily trend bullish
        f["hh"] and f["hl"],                                        # 2 higher highs AND lows
        None not in (f["e20"], f["e50"], f["e200"])
        and f["e20"] > f["e50"] > f["e200"],                        # 3 EMAs aligned
        (f["e20_slope"] or -1) > 0 and (f["e50_slope"] or -1) > 0,  # 4 EMAs rising, not flat
        (f["rsi"] or 0) > 50 and (f["hist_slope"] or -1) > 0,       # 5 momentum increasing
        f["vol_x"] >= 1.2,                                          # 6 volume confirming
        f["accum"] == "Accumulation",                               # 7 accumulating
        (f["ret20"] or -1) > 0 and (f["ret60"] or -1) > 0,          # 8 beating its own history
        (f["ext_atr"] or 0) <= 3 and f["breakout"] != "Extended Breakout",   # 9 not overextended
        f["room_r"] >= 2,                                           # 10 upside before resistance
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

def analyse(symbol, funds=None, calendar=None):
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
    accum = _accum_dist(o, c, v)
    pb = _pullback(o, c, l, v, vsma, _at(e20), _at(e50), lows, accum)
    struct_lbl, last_event, hh, hl, lh, ll = _structure(c, ph, pl, highs, lows)
    ext_atr = (c[-1] - _at(e20)) / atr_now if _at(e20) is not None else None
    room = (lev["near_res"] - lev["entry"]) if lev["near_res"] else None

    f = dict(
        symbol=symbol, date=d[-1], close=c[-1],
        e20=_at(e20), e50=_at(e50), e200=_at(e200),
        e20_slope=_slope(e20), e50_slope=_slope(e50), e200_slope=_slope(e200),
        ext_atr=ext_atr, rsi=_at(r), rsi_slope=_slope(r, 3),
        # Section 6 lists RSI direction and momentum acceleration as separate bullets; only
        # direction was implemented. This is the change in that slope — is momentum still
        # building, or merely still positive while flattening out.
        rsi_accel=(None if _slope(r, 3) is None or _slope(r[:-3], 3) is None
                   else _slope(r, 3) - _slope(r[:-3], 3)),
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
        accum=accum, adv_decl=_advances_declines(c),
        mom_accel=_momentum_accel(c),                 # section 4, its own bullet
        e20_respect=_ema_respected(l, c, e20), e50_respect=_ema_respected(l, c, e50),
        off_20d_high=((max(c[-20:]) - c[-1]) / max(c[-20:]) * 100) if len(c) >= 20 else None,
        avg_vol=(sum(v[-20:]) / 20) if len(v) >= 20 else None,
        gap_flag=_gaps(o, c),
        repeated_failures=_repeated_failures(c, h, res_level),
        has_fund=bool(fund), roe=_num(fund, "roe_ttm"), pe=_num(fund, "pe_ttm"),
        bvps=_num(fund, "bvps"), float_pct=_num(fund, "float_pct"),
        **_breakout_detail(c, o, h, l, v, vsma, atr_now, res_level),
        **_retest_detail(c, h, l, v, vsma, atr_now, res_level),
        **{k: lev[k] for k in ("entry", "stop", "risk", "t1", "t2", "t3", "stop_far",
                               "near_res", "major_res", "near_support", "major_support")},
    )
    f["risk_pct"] = f["risk"] / f["entry"] * 100
    # No confirmed swing high overhead means UNLIMITED room, which is the framework's ideal.
    # It used to be None, and the five readers each guessed a different default (0, 99, 0, 9,
    # skip) — so a stock at a 250-day high scored 0/5 for support/resistance while Q8 said YES.
    f["room_r"] = float("inf") if f["near_res"] is None else (room / f["risk"])
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
    # "too wide" means the real invalidation level is too far to swing against — measured on
    # the STRUCTURAL level, not on the risk we ended up taking. Testing `risk > 3.5*ATR` could
    # never be true: _levels discards a swing low that far away and falls back to a 2 ATR stop,
    # so the widest risk possible was 3.5 ATR and this branch was dead on all 307 symbols.
    f["stop_width"] = ("too tight" if f["risk"] < 0.8 * atr_now else
                       "too wide" if f["stop_far"] else "sensible")
    # Section 13 — P/B and EPS derived from what SmartWealthPro actually gives us
    f["pb"] = (f["close"] / f["bvps"]) if f["bvps"] else None
    f["eps"] = (f["close"] / f["pe"]) if f["pe"] else None
    # Section 13 — net-profit and EPS growth from earnings.txt; corporate developments from
    # corporate_actions.txt and lockin.txt. Only the three in FUND_UNAVAILABLE are truly absent.
    e = _table("earnings.txt").get(symbol, {})
    f["profit_yoy"] = _num(e, "profit_yoy")
    f["eps_yoy"] = _num(e, "eps_yoy")
    f["earn_period"] = f"{e.get('fiscal_year', '')} {e.get('quarter', '')}".strip() or None
    ca = _table("corporate_actions.txt").get(symbol, {})
    f["corp_action"] = (f"{ca.get('fiscal_year', '')}: bonus {ca.get('bonus') or 0}%, "
                        f"cash {ca.get('cash') or 0}%"
                        + (f", book close {ca['book_close']}" if ca.get("book_close") else "")
                        ) if ca else None
    lk = _table("lockin.txt").get(symbol, {})
    f["lockin_expiry"] = lk.get("lockin_expiry")
    f["fund_missing"] = list(FUND_UNAVAILABLE)

    f["fund_warn"] = []
    if f["has_fund"]:
        if (f["roe"] or 99) < 8:
            f["fund_warn"].append("ROE under 8%")
        if f["pe"] and f["pe"] > 60:
            f["fund_warn"].append(f"P/E {f['pe']:.0f} is stretched")
        if f["pb"] and f["pb"] > 6:
            f["fund_warn"].append(f"P/B {f['pb']:.1f} is stretched")
    if f["profit_yoy"] is not None and f["profit_yoy"] < 0:
        f["fund_warn"].append(f"net profit {f['profit_yoy']:.1f}% YoY — deteriorating")
    if f["eps_yoy"] is not None and f["eps_yoy"] < 0:
        f["fund_warn"].append(f"EPS {f['eps_yoy']:.1f}% YoY — deteriorating")
    if f["lockin_expiry"]:
        f["fund_warn"].append(f"lock-in expires {f['lockin_expiry']} — supply overhang")
    # Section 14 — thin float plus an abnormal print is the manipulation shape
    f["confirmed_candle"] = _confirmed(o, c)
    f["selling_weakens"] = _selling_weakens(o, c)
    # Section 9's seventh healthy trait, previously the only one with no implementation.
    # "Support" is the highest level actually beneath price — the swing low or either EMA,
    # whichever a dip would meet first.
    _sup = [x for x in (f["near_support"], f["e20"], f["e50"])
            if x is not None and x < f["close"]]
    f["buyers_return"] = _buyers_return(h, l, c, max(_sup) if _sup else None)
    f["trade_freq"] = _frequency(d, calendar)
    f["thin_calendar"] = f["trade_freq"] is not None and f["trade_freq"] < 80
    f["manip"] = bool(f["abnormal"] and (f["float_pct"] or 100) < 25) or f["gap_flag"] >= 4
    # R:R measured to the realistic target — near resistance if it is closer than T1
    f["rr"] = (f["t1"] - f["entry"]) / f["risk"]   # T1 is the realistic, structural target
    f["stage"] = _stage(c, h, l, atr_now, f["e20"], f["e50"], f["e200"],
                        hh, hl, f["ret60"], ext_atr)
    f["trend"] = ("Bullish" if f["close"] > (f["e20"] or 1e18) > (f["e50"] or 1e18)
                  else "Bearish" if f["close"] < (f["e50"] or 0) else "Neutral")
    f["flags"] = _false_signals(f)
    f["parts"], f["score"] = _score(f)
    f["grade"] = _grade(f["score"])
    f["performer"] = _performer(f)
    f["setup"] = _setup(f)
    f["decision"], f["why"] = _decision(f, f["score"], f["flags"])
    # Section 21 — "Before issuing BUY, answer this question ... If the answer to the critical
    # questions is NO, return NO TRADE." So it has to run BEFORE the decision stands, not after
    # it as a printed footnote. Participation is judged in context: a breakout needs volume, a
    # pullback needs the confirmation candle on DRY volume, which is what section 5 asks for.
    participation = (f["vol_x"] >= 1.5 if f["breakout"] in ("Confirmed Breakout",
                                                            "Attempting Breakout")
                     else (f["confirmed_candle"] and f["accum"] != "Distribution"))
    f["critical"] = [
        ("strong trend evidence", f["trend"] == "Bullish" and f["hh"] and f["hl"]),
        ("confirmed participation", bool(participation)),
        ("logical invalidation", f["near_support"] is not None and f["stop_width"] == "sensible"),
        ("at least 1:2 reward-to-risk", f["rr"] >= MIN_RR),
        ("not merely chasing", (f["ext_atr"] or 0) <= 3
         and f["breakout"] != "Extended Breakout" and not f["chasing"]),
    ]
    failed = [n for n, ok in f["critical"] if not ok]
    if failed and f["decision"].startswith("BUY"):
        f["decision"] = "WAIT"
        f["why"] = "critical questions answered NO: " + ", ".join(failed)
    # Name the level the stop is ACTUALLY at. A swing low 3.5+ ATR away is discarded by
    # _levels, and calling the 2 ATR fallback "the last swing low" misdescribed the trade's
    # invalidation on 9 of 307 symbols — while Q11 still answered YES for every one of them.
    f["invalidation"] = (f"a daily close below {f['stop']:.2f}"
                         + (" (the last swing low)"
                            if f["near_support"] is not None and not f["stop_far"]
                            else " (2x ATR — the last swing low is too far to swing against)"
                            if f["stop_far"] else " (2x ATR)"))
    # Section 17 — the SETUP's own grade, separate from the 100-point score
    f["setup_grade"] = ("NO TRADE" if f["setup"] == "none" or f["decision"] == "AVOID" else
                        "A+" if f["score"] >= 90 and not f["flags"] else
                        "A" if f["score"] >= 80 and len(f["flags"]) <= 1 else
                        "B" if f["score"] >= 70 else "C")
    # Section 5 — the spec's own strength wording, not a bare number
    f["vol_label"] = ("very strong (2x+)" if f["vol_x"] >= 2 else
                      "strong (1.5x+)" if f["vol_x"] >= 1.5 else
                      "above average" if f["vol_x"] >= 1 else "below average")
    # Section 12 — the spec's bands: 1:2 minimum preferred, 1:3+ excellent
    f["rr_band"] = ("excellent (1:3+)" if f["rr"] >= 3 else
                    "acceptable (1:2+)" if f["rr"] >= MIN_RR else "BELOW THE 1:2 GATE")
    # Section 11 — is the target realistic against normal daily movement?
    f["t1_atr"] = (f["t1"] - f["entry"]) / atr_now
    f["t2_atr"] = (f["t2"] - f["entry"]) / atr_now
    f["t3_atr"] = (f["t3"] - f["entry"]) / atr_now
    # The R multiple of each target, measured rather than assumed. The report used to label
    # them "(2R)", "(3R)" and "(5R)" from the old flat-R design; after targets became
    # structural those labels were wrong on 188 of 307 symbols, T2 reaching 308R under a "3R" tag.
    f["t2_r"] = (f["t2"] - f["entry"]) / f["risk"]
    f["t3_r"] = (f["t3"] - f["entry"]) / f["risk"]
    # Measured on the FURTHEST stated target. Testing T1 was dead: _levels caps T1 at the 10-ATR
    # horizon, so `t1_atr <= 15` was true by construction and the warning never printed. T2/T3
    # carry no ATR cap of their own, and 31 of 307 symbols state a T3 beyond 15 ATR — roughly a
    # quarter's worth of normal movement, which is not a swing target.
    f["target_realistic"] = f["t3_atr"] <= 15          # ~3 weeks of normal movement for a swing
    # Section 21 — the professional trader question that must be answered BEFORE issuing BUY
    f["capital_question"] = (
        "If I were risking my own capital on this stock using only the 1D chart, does the "
        "current price offer a high-quality, asymmetric swing-trading opportunity with strong "
        "trend evidence, confirmed participation, a logical invalidation level, and at least "
        "1:2 realistic reward-to-risk — or am I simply chasing a stock because it has already "
        "risen?")
    have = [n for n, ok in f["critical"] if ok]
    lack = [n for n, ok in f["critical"] if not ok]
    f["capital_answer"] = (
        ("YES — " + ", ".join(have) + ".") if not lack else
        ("NO — has " + (", ".join(have) if have else "none of it")
         + "; missing " + ", ".join(lack) + "."))
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
    if f["room_r"] == float("inf"):
        e.append("no resistance overhead inside the 250-day window — unlimited room")
    elif f["room_r"] >= 3:
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
    if f["room_r"] < 2:
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

def market_calendar(names):
    """Every date any symbol traded, ascending — the market's own session calendar."""
    days = set()
    for s in names[:60]:                     # a sample is enough to reconstruct the calendar
        b = bars(s)
        if b:
            days.update(b[0][-400:])
    return sorted(days)


def scan(names=None):
    funds = _fundamentals()
    names = names or (MASTER / "symbols.txt").read_text(encoding="utf-8").split()
    cal = market_calendar(names)
    rows = []
    for s in names:
        try:
            f = analyse(s, funds, cal)
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
         f"EMA separation:        {_n(f['separation'], 2, '%')} of price"
         + ("   (compressed — trend unclear)" if (f["separation"] or 9) < 1.5 else "")
         + f"   |  crossovers: {', '.join(f['crossovers'][:3]) if f['crossovers'] else 'none in 25d'}",
         f"Pullbacks respect:     20 EMA "
         + ("not tested in 60d" if f["e20_respect"] is None
            else f"{f['e20_respect']:.0f}% of dips held")
         + "   50 EMA "
         + ("not tested in 60d" if f["e50_respect"] is None
            else f"{f['e50_respect']:.0f}% of dips held"),
         f"Volume Ratio:          {_n(f['vol_x'])}x of its 20-day average — {f['vol_label']}"
         f"   (20d volume regime: {f['vol_regime']}"
         + (", ABNORMAL print today" if f["abnormal"] else "") + ")",
         f"Volume Interpretation: {'buying' if f['up_day'] else 'selling'} pressure"
         f"{', pullback on dry volume' if f['pullback_dry'] else ''}",
         f"RSI 14:                {_n(f['rsi'], 1)}   {'rising' if (f['rsi_slope'] or 0) > 0 else 'falling'}"
         + ("   OVEREXTENDED >70 (not a sell on its own — a strong trend can hold it)"
            if f["rsi_overext"] else "")
         + ("" if f["rsi_accel"] is None else
            ", momentum accelerating" if f["rsi_accel"] > 0 else ", momentum decelerating")
         + ("   BEARISH DIVERGENCE" if f["rsi_div"] else "")
         + ("   bullish divergence" if f["rsi_bull_div"] else ""),
         f"MACD 12/26/9:          {_n(f['macd'], 3)} vs signal {_n(f['macd_sig'], 3)}, "
         f"hist {_n(f['macd_hist'], 3)} {'rising' if (f['hist_slope'] or 0) > 0 else 'falling'}, "
         f"{'above' if f['macd_above_zero'] else 'below'} zero, {f['macd_cross']}"
         + ("   BEARISH DIVERGENCE" if f["macd_div"] else ""),
         f"ATR 14:                {_n(f['atr'])}  ({_n(f['atr_pct'], 2, '%')} of price)   "
         f"{'enough volatility to swing' if f['enough_vol'] else 'TOO FLAT/WILD to swing'}"
         f", stop is {f['stop_width']}",
         f"Support:               near {_n(f['near_support'])}   major {_n(f['major_support'])}",
         f"Resistance:            near {_n(f['near_res'])}   major {_n(f['major_res'])}",
         f"Breakout/Pullback:     {f['breakout']}  /  {f['pullback']}"
         + ("   bullish daily confirmation" if f["confirmed_candle"] else "   NO bullish confirmation")
         + ("" if f["selling_weakens"] is None
            else ", selling candles weakening" if f["selling_weakens"]
            else ", selling candles still growing")
         + ("" if f["buyers_return"] is None
            else ", buyers returned at support" if f["buyers_return"]
            else ", no buyers stepping in at support"),
         f"                       level {_n(f['prev_breakout'])}, {f['res_tests']} prior tests, "
         f"{f['consol_days']}d base, candle {_n(f['bo_candle'], 2)} ATR closing "
         f"{_n(f['close_pos'], 0, '%')} up its range, {f['follow_through']}/3 follow-through",
         f"                       retest: "
         + ("none — price has not come back to the level since the breakout"
            if f["retest_depth"] is None else
            f"{'HELD' if f['retest_held'] else 'FAILED — closed back under the level'}, "
            f"cut {f['retest_depth']:.2f} ATR below it {f['retest_bars']}d after the breakout"
            + ("" if f["retest_vol"] is None else
               f", on {f['retest_vol']:.2f}x volume"
               f" ({'dry, as a healthy retest should be' if f['retest_vol'] < 1.0 else 'heavy — sellers are still there'})")),
         f"Smart-Money Evidence:  " + ", ".join(x for x in (
             f["demand_zone"], "liquidity sweep" if f["sweep"] else None,
             "support reclaim" if f["reclaim"] else None,
             f"structure {f['last_event']}" if f["last_event"] != "none" else None,
             f"{f['accum'].lower()} on volume" if f["accum"] != "Neutral" else None,
         ) if x) or "nothing the daily bars clearly show",
         f"Relative Performance:  5d {_n(f['ret5'], 2, '%')}   20d {_n(f['ret20'], 2, '%')}   "
         f"60d {_n(f['ret60'], 2, '%')}   {_n(f['off_20d_high'], 1, '%')} off 20d high, "
         f"{_n(f['off_60d_high'], 1, '%')} off 60d high",
         f"                       advances/declines {_n(f['adv_decl'], 2)}x over 20d "
         f"({'advances dominate' if f['adv_decl'] > 1 else 'declines dominate'})"
         + ("" if f["mom_accel"] is None else
            f", momentum {'accelerating' if f['mom_accel'] > 0 else 'deteriorating'} "
            f"({f['mom_accel']:+.2f}%/day, 5d pace vs 20d)"),
         f"Fundamental Quality:   " + ((
             f"ROE {_n(f['roe'], 1, '%')}  P/E {_n(f['pe'], 1)}  P/B {_n(f['pb'], 2)}  "
             f"EPS {_n(f['eps'], 2)}  BVPS {_n(f['bvps'])}  float {_n(f['float_pct'], 1, '%')}")
             if f["has_fund"] else "no data on file - not scored, not guessed"),
]
    # Section 13 continued - growth, corporate developments, and what is honestly unavailable
    if f["profit_yoy"] is not None:
        L.append(f"                       net profit {_n(f['profit_yoy'], 1, '%')} YoY, "
                 f"EPS {_n(f['eps_yoy'], 1, '%')} YoY ({f['earn_period']})")
    if f["corp_action"]:
        L.append(f"                       corporate action - {f['corp_action']}")
    if f["lockin_expiry"]:
        L.append(f"                       lock-in expiry {f['lockin_expiry']}")
    if f["fund_warn"]:
        L.append(f"                       WARNINGS: {', '.join(f['fund_warn'])}")
    L.append(f"                       not published for NEPSE, so not scored: "
             f"{', '.join(f['fund_missing'])}")
    L += [
         f"Liquidity:             Rs {f['turnover']:,.0f} median 20d turnover, "
         f"{f['avg_vol']:,.0f} avg daily shares"
         + (f", {f['gap_flag']} unexplained gap(s) in 60d" if f["gap_flag"] else "")
         + (f", traded {f['trade_freq']:.0f}% of the last 60 sessions"
            if f["trade_freq"] is not None else "")
         + ("   POSSIBLE MANIPULATION SHAPE" if f["manip"] else "")
         + ("" if f["liquid"] else "   HIGH EXECUTION RISK"),
         f"                       not published for NEPSE, so not scored: {EXEC_UNAVAILABLE}",
         f"False-Signal Risk:     {', '.join(f['flags']) if f['flags'] else 'none of the ten fired'}",
         "",
         f"Score:                 {f['score']}/100",
         f"Grade:                 {f['grade']}",
         f"Decision:              {f['decision']}   ({f['why']})",
         f"Entry Zone:            {_n(f['entry'])}",
         f"Stop Loss:             {_n(f['stop'])}   (risk {_n(f['risk_pct'], 2, '%')})",
         f"Target 1:              {_n(f['t1'])}   ({f['rr']:.1f}R, {f['t1_atr']:.1f} ATR)",
         f"Target 2:              {_n(f['t2'])}   ({f['t2_r']:.1f}R, {f['t2_atr']:.1f} ATR)",
         f"Target 3:              {_n(f['t3'])}   ({f['t3_r']:.1f}R, {f['t3_atr']:.1f} ATR)"
         + ("" if f["target_realistic"] else "   FAR vs NORMAL DAILY MOVEMENT"),
         f"Risk/Reward:           1:{_n(f['rr'], 2)} to the realistic target — {f['rr_band']}",
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
          + ("; unlimited room — no resistance overhead" if f["room_r"] == float("inf")
             else f"; {f['room_r']:.1f}R of room to resistance")
          + f"; risk is capped at {f['risk_pct']:.2f}% with a {f['stop_width']} stop.",
          f"Why This Trade Could Fail:    {f['bear_evidence'][0]}.",
          "",
          "The professional trader question:",
          f"  Q: {f['capital_question']}",
          f"  A: {f['capital_answer']}",
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
    ("Is there sufficient room before major resistance?", lambda f: f["room_r"] >= 2),
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
        names_all = (MASTER / "symbols.txt").read_text(encoding="utf-8").split()
        f = analyse(sym, _fundamentals(), market_calendar(names_all))
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
