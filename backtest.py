"""Measure what the buy rules actually returned — the evidence behind any "master" signal.

Nothing here invents a strategy. It replays the rules already in trade_setup.py across the whole
1D archive and records what each trigger did next, so a claim like "high return" is a measured
number instead of a hope.

Honesty rules baked in, because a backtest is trivial to fool:
  * entry is the NEXT bar's open, never the signal bar's close (no look-ahead)
  * indicators at bar k use only bars <= k
  * a bar that touches both target and stop counts as the STOP (pessimistic)
  * round-trip cost is charged on every trade
  * every variant is split in half by date: the second half is out-of-sample, and only a rule
    that survives OUT of sample means anything (see the spike x concentration lesson)

    python backtest.py             # all variants, in-sample vs out-of-sample
    python backtest.py --demo      # quick self-check on a few symbols
"""
import argparse
import statistics
import sys
from datetime import datetime

from fetch_ohlc import MASTER
from indicators import adx, atr, ema, macd, pivots, rsi, sma, trade_levels
from prices import bars as adjusted_bars
from trade_setup import LIQUID_MIN


def oos_edge():
    """[(volume threshold, measured out-of-sample avg %)] read from the last run's own output.

    This module is where the number comes from, so this is where the readers get it. It used to
    be transcribed by hand into master_signal.py and swing_master.py, and the two copies drifted
    apart from each other AND from the file — three different values shipped for every band
    (vol>3x: 2.39 / 2.44 / 2.51), each printed to the reader as a measurement. A hardcoded
    result of a rerunnable computation is a claim with an expiry date on it.

    Empty when backtest.py has not been run; callers already treat "no edge" as "not a candidate".
    """
    p = MASTER / "backtest.txt"
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()
    if len(lines) < 2:
        return []
    cols = lines[0].split("\t")
    if "variant" not in cols or "oos_avg" not in cols:
        return []
    vi, oi = cols.index("variant"), cols.index("oos_avg")
    out = []
    for line in lines[1:]:
        f = line.split("\t")
        if len(f) > max(vi, oi) and f[vi].startswith("+ volume>"):
            try:
                out.append((float(f[vi][len("+ volume>"):-1]), float(f[oi])))
            except ValueError:
                continue
    return sorted(out, reverse=True)


def _bars(symbol):
    """Bonus/rights ADJUSTED bars. Raw prices print -20%..-80% ex-date gaps that never traded,
    which fire stops that never happened — see prices.py."""
    b = adjusted_bars(symbol)
    return b if b and len(b[4]) >= 60 else None

COST = 0.008          # ~0.8% round trip: broker commission + SEBON + DP, both sides
MAX_HOLD = 60         # bars; a trade that neither targets nor stops is closed at this bar's close


def indicators(b):
    """Every series the rules need, computed once per symbol."""
    d, o, h, l, c, v = b
    mline, msig, mhist = macd(c, 12, 26, 9)
    adx_, pdi, mdi = adx(h, l, c, 14)
    ph, pl = pivots(h, l, 5, 5)
    return {"e20": ema(c, 20), "e50": ema(c, 50), "s200": sma(c, 200), "rsi": rsi(c, 14),
            "atr": atr(h, l, c, 14), "macd": mline, "msig": msig, "mhist": mhist,
            "adx": adx_, "pdi": pdi, "mdi": mdi, "vsma": sma(v, 20), "pl": pl}


def bull_at(k, c, x):
    """trade_setup's core buyable state: above 200SMA, EMAs stacked, RSI>50, MACD above signal."""
    vals = (x["s200"][k], x["e20"][k], x["e50"][k], x["rsi"][k], x["macd"][k], x["msig"][k])
    if any(y is None for y in vals):
        return False
    return (c[k] > x["s200"][k] and x["e20"][k] > x["e50"][k] and x["e50"][k] > x["s200"][k]
            and x["rsi"][k] > 50 and x["macd"][k] > x["msig"][k])


def triggers(b, x):
    """Every bar where the buyable state turns ON (a fresh crossover, not a continuation)."""
    d, o, h, l, c, v = b
    return [k for k in range(1, len(c) - 1) if bull_at(k, c, x) and not bull_at(k - 1, c, x)]


def trade(b, x, k):
    """Replay one trigger: enter next open, exit on target/stop/time. Returns the trade dict."""
    d, o, h, l, c, v = b
    entry_i = k + 1                                   # signal seen at close of k, filled next open
    if entry_i >= len(c):
        return None
    entry = o[entry_i]
    a = x["atr"][k]
    if not entry or not a:
        return None
    # a 5/5 pivot low is only CONFIRMED five bars after it prints, so at bar k we may only use
    # pivots up to k-5 — using pl[k] itself would be peeking at bars we haven't traded through yet
    swing = next((x["pl"][j] for j in range(k - 5, -1, -1)
                  if x["pl"][j] is not None and x["pl"][j] < entry), None)
    stop, t1, t2, _ = trade_levels("BUY", entry, swing, a)
    if not stop or not t1 or stop >= entry:
        return None

    exit_i, exit_px, verdict = None, None, "open"
    for j in range(entry_i, min(entry_i + MAX_HOLD, len(c))):
        if l[j] <= stop:                              # stop first when a bar spans both — pessimistic
            exit_i, exit_px, verdict = j, stop, "stop"
            break
        if t2 and h[j] >= t2:
            exit_i, exit_px, verdict = j, t2, "t2"
            break
        if h[j] >= t1:
            exit_i, exit_px, verdict = j, t1, "t1"
            break
    if exit_i is None:                                # ran out of room: mark to market
        exit_i = min(entry_i + MAX_HOLD, len(c)) - 1
        exit_px, verdict = c[exit_i], "time"

    ret = (exit_px / entry - 1) - COST
    # ---- NEPSE-specific context, all captured AT the signal bar (no peeking) ----
    day_pct = (c[k] / c[k - 1] - 1) * 100 if c[k - 1] else 0        # vs the +/-10% circuit
    rng = h[k] - l[k]
    return {"date": d[k], "entry": entry, "exit": exit_px, "ret": ret,
            "bars": exit_i - entry_i, "verdict": verdict,
            "adx": x["adx"][k], "vol_ratio": (v[k] / x["vsma"][k]) if x["vsma"][k] else 0,
            "turnover": statistics.median([c[j] * v[j] for j in range(max(0, k - 19), k + 1)]),
            "breakout": c[k] > max(h[max(0, k - 20):k], default=c[k]),
            "rsi": x["rsi"][k], "ext_atr": (c[k] - c[k - 1]) / a if a else 0,
            # % of the FREE FLOAT that changed hands — the Nepali size measure
            "float_turn": (100 * v[k] / FLOAT[SYM[0]]) if SYM[0] in FLOAT else None,
            # near the upper circuit = demand left unfilled in the queue at the close
            "circuit_up": day_pct >= 9.0,
            "day_pct": day_pct,
            # closing in the top of the day's range = buyers held it into the close
            "close_pos": ((c[k] - l[k]) / rng) if rng > 0 else 0.5,
            "to_bookclose": _days_to_bookclose(SYM[0], d[k])}


SYM = [None]           # symbol currently being replayed, for the per-symbol lookups above


def market_regime():
    """{date: True} when the NEPSE index closed above its own 200-SMA that day.

    Every rule below degraded out of sample in step with the market, which says the "edge" is
    partly just beta. This is the cheapest honest control: one economically-motivated switch
    (don't buy breakouts while the whole market is falling), not a tuned parameter."""
    p = MASTER / "indices" / "NEPSE" / "1D.txt"
    if not p.exists():
        return {}
    dates, closes = [], []
    for line in p.read_text(encoding="utf-8").splitlines()[1:]:
        f = line.split("\t")
        if len(f) > 4 and f[4] not in ("", "None"):
            try:
                dates.append(f[0]); closes.append(float(f[4]))
            except ValueError:
                continue
    s = sma(closes, 200)
    return {d: (s[i] is not None and closes[i] > s[i]) for i, d in enumerate(dates)}


REGIME = {}


def free_float():
    """symbol -> public shares. NEPSE floats are tiny, so volume measured against the FLOAT says
    far more than volume against its own 20-day average — 'x% of the float changed hands today'
    is the quantity a Nepali operator actually moves."""
    out = {}
    p = MASTER / "fundamentals.txt"
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines()[1:]:
        f = line.split("\t")
        try:
            if len(f) > 4 and float(f[4]) > 0:
                out[f[0]] = float(f[4])
        except ValueError:
            continue
    return out


def book_close_dates():
    """symbol -> sorted book-closure dates. Nepali stocks run INTO the book close for bonus."""
    out = {}
    p = MASTER / "corporate_actions.txt"
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines()[1:]:
        f = line.split("\t")
        if len(f) > 5 and f[5]:
            out.setdefault(f[0], []).append(f[5])
    return {k: sorted(v) for k, v in out.items()}


FLOAT, BOOKCLOSE = {}, {}


def _days_to_bookclose(symbol, date):
    """Calendar days until this symbol's next book closure, or None."""
    for bc in BOOKCLOSE.get(symbol, []):
        if bc > date:
            try:
                a = datetime.strptime(date, "%Y-%m-%d")
                b = datetime.strptime(bc, "%Y-%m-%d")
            except ValueError:
                return None
            return (b - a).days
    return None


def collect(symbols):
    """Every trade from every symbol, once — variants then filter this one list."""
    out = []
    for s in symbols:
        b = _bars(s)
        if not b:
            continue
        SYM[0] = s
        x = indicators(b)
        for k in triggers(b, x):
            t = trade(b, x, k)
            if t:
                t["symbol"] = s
                out.append(t)
    return out


def stats(trades):
    """Win rate, average return, and expectancy — expectancy is the number that matters."""
    if not trades:
        return None
    rets = [t["ret"] for t in trades]
    wins = [r for r in rets if r > 0]
    return {"n": len(trades), "win": 100 * len(wins) / len(rets),
            "avg": 100 * statistics.fmean(rets),
            "med": 100 * statistics.median(rets),
            "best": 100 * max(rets), "worst": 100 * min(rets),
            "total": 100 * sum(rets), "bars": statistics.fmean([t["bars"] for t in trades])}


# Each variant is a filter on the same trigger list, so differences are the filter's doing alone.
VARIANTS = {
    "all triggers (baseline)": lambda t: True,
    "liquid only": lambda t: t["turnover"] >= LIQUID_MIN,
    "+ ADX>25 (real trend)": lambda t: t["turnover"] >= LIQUID_MIN and (t["adx"] or 0) > 25,
    "+ volume>1.5x": lambda t: t["turnover"] >= LIQUID_MIN and t["vol_ratio"] > 1.5,
    "+ 20d breakout": lambda t: t["turnover"] >= LIQUID_MIN and t["breakout"],
    "+ ADX>25 & volume>1.5x": lambda t: (t["turnover"] >= LIQUID_MIN and (t["adx"] or 0) > 25
                                         and t["vol_ratio"] > 1.5),
    "+ ADX>25 & breakout": lambda t: (t["turnover"] >= LIQUID_MIN and (t["adx"] or 0) > 25
                                      and t["breakout"]),
    "+ RSI 50-70 (not overbought)": lambda t: (t["turnover"] >= LIQUID_MIN
                                               and t["rsi"] is not None and 50 < t["rsi"] < 70),
    # volume was the only ingredient that held its edge out of sample, so push on it
    "+ volume>2x": lambda t: t["turnover"] >= LIQUID_MIN and t["vol_ratio"] > 2.0,
    "+ volume>3x": lambda t: t["turnover"] >= LIQUID_MIN and t["vol_ratio"] > 3.0,
    "+ vol>2x & ADX>25": lambda t: (t["turnover"] >= LIQUID_MIN and t["vol_ratio"] > 2.0
                                    and (t["adx"] or 0) > 25),
    "+ vol>1.5x & RSI<70": lambda t: (t["turnover"] >= LIQUID_MIN and t["vol_ratio"] > 1.5
                                      and t["rsi"] is not None and t["rsi"] < 70),
    # the same rules, but only while NEPSE itself is above its 200-SMA
    "MKT-UP only": lambda t: t["turnover"] >= LIQUID_MIN and REGIME.get(t["date"], False),
    "MKT-UP + vol>1.5x": lambda t: (t["turnover"] >= LIQUID_MIN and REGIME.get(t["date"], False)
                                    and t["vol_ratio"] > 1.5),
    "MKT-UP + vol>2x": lambda t: (t["turnover"] >= LIQUID_MIN and REGIME.get(t["date"], False)
                                  and t["vol_ratio"] > 2.0),
    "MKT-UP + vol>3x": lambda t: (t["turnover"] >= LIQUID_MIN and REGIME.get(t["date"], False)
                                  and t["vol_ratio"] > 3.0),
    "MKT-UP + vol>2x & ADX>25": lambda t: (t["turnover"] >= LIQUID_MIN and REGIME.get(t["date"], False)
                                           and t["vol_ratio"] > 2.0 and (t["adx"] or 0) > 25),
    "MKT-DOWN + vol>2x (control)": lambda t: (t["turnover"] >= LIQUID_MIN
                                              and not REGIME.get(t["date"], True)
                                              and t["vol_ratio"] > 2.0),
    # ---- NEPSE-specific factors, each ALONE first so nothing hides behind volume ----
    # Ranked WITHIN the day, not against an absolute %, because the float is not point-in-time —
    # see rank_float_turn(). An absolute threshold made these a filter on the calendar.
    "NP float turn top 50% of day": lambda t: (t["turnover"] >= LIQUID_MIN
                                               and (t["float_rank"] or 0) > 0.50),
    "NP float turn top 20% of day": lambda t: (t["turnover"] >= LIQUID_MIN
                                               and (t["float_rank"] or 0) > 0.80),
    "NP float turn top 5% of day": lambda t: (t["turnover"] >= LIQUID_MIN
                                              and (t["float_rank"] or 0) > 0.95),
    "NP upper-circuit close": lambda t: t["turnover"] >= LIQUID_MIN and t["circuit_up"],
    "NP closed top 25% of day": lambda t: t["turnover"] >= LIQUID_MIN and t["close_pos"] > 0.75,
    "NP <45d to book close": lambda t: (t["turnover"] >= LIQUID_MIN
                                        and t["to_bookclose"] is not None
                                        and 0 < t["to_bookclose"] <= 45),
    # ---- and combined with the one filter already proven to survive ----
    "NP float top20% & vol>2x": lambda t: (t["turnover"] >= LIQUID_MIN
                                           and (t["float_rank"] or 0) > 0.80
                                           and t["vol_ratio"] > 2.0),
    "NP circuit & vol>2x": lambda t: (t["turnover"] >= LIQUID_MIN and t["circuit_up"]
                                      and t["vol_ratio"] > 2.0),
    "NP top25% & vol>2x": lambda t: (t["turnover"] >= LIQUID_MIN and t["close_pos"] > 0.75
                                     and t["vol_ratio"] > 2.0),
    "NP MASTER (float top20% & vol>2x & top25%)":
        lambda t: (t["turnover"] >= LIQUID_MIN and (t["float_rank"] or 0) > 0.80
                   and t["vol_ratio"] > 2.0 and t["close_pos"] > 0.75),
}


def rank_float_turn(trades):
    """Replace float_turn's absolute % with its RANK among that day's trades (0..1).

    `float_turn` divides bar k's volume by the free float in TODAY's fundamentals.txt, and there
    is no point-in-time float in the archive. Floats only grow — RBCL alone issued a 114.27%
    bonus — so an old bar is divided by a float that did not exist yet and its float_turn comes
    out far too small. Measured across the replay the median runs 0.007% in 2012 and 2013 against
    thresholds of 0.5%, 1% and 2%, so those variants passed almost nothing in the early years and
    steadily more later: they were selecting a DATE, not a stock. That silently breaks the one
    thing report() exists to do, because the in-sample and out-of-sample halves then hold
    different populations rather than the same rule tested twice.

    Ranking within the day removes the level entirely — whatever the float error is, it is
    roughly the same for every stock on that date. This is the repo's standing lesson from the
    floorsheet work: always date-demean before comparing across time.
    """
    byday = {}
    for t in trades:
        if t.get("float_turn") is not None:
            byday.setdefault(t["date"], []).append(t)
    for day, ts in byday.items():
        ts.sort(key=lambda t: t["float_turn"])
        n = len(ts)
        for i, t in enumerate(ts):
            t["float_rank"] = (i + 0.5) / n          # 0..1, 1 = heaviest float turnover that day
    for t in trades:
        t.setdefault("float_rank", None)
    return trades


def by_year(trades, name):
    """Is the edge steady, or one lucky stretch? Per-year is where a fragile rule shows itself."""
    years = {}
    for t in trades:
        years.setdefault(t["date"][:4], []).append(t["ret"])
    print(f"\nYear by year — {name}")
    for y in sorted(years):
        r = years[y]
        avg = 100 * statistics.fmean(r)
        win = 100 * len([x for x in r if x > 0]) / len(r)
        bar = "#" * max(0, min(28, int(round(avg * 4))))
        print(f"  {y}  n={len(r):>4}  win {win:>5.1f}%  avg {avg:>+6.2f}%  {bar}")


OUT = MASTER / "backtest.txt"
OUT_HEADER = ("variant\tis_n\tis_win\tis_avg\toos_n\toos_win\toos_avg\ttrades\tsplit"
              "\tfrom\tto\tcost_pct\tmax_hold")


def save(trades, split, rows):
    """Persist the table so the Backtest page reads a file instead of re-replaying 7k trades."""
    dates = sorted(t["date"] for t in trades)
    tail = f"{len(trades)}\t{split}\t{dates[0]}\t{dates[-1]}\t{COST*100}\t{MAX_HOLD}"
    lines = [OUT_HEADER] + [
        f"{name}\t{a['n']}\t{a['win']:.1f}\t{a['avg']:.2f}\t{o['n']}\t{o['win']:.1f}\t{o['avg']:.2f}\t{tail}"
        for name, a, o in rows]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nsaved -> {OUT}")


def report(trades):
    """In-sample vs out-of-sample table. A rule only counts if it holds in BOTH halves."""
    dates = sorted(t["date"] for t in trades)
    split = dates[len(dates) // 2]
    saved = []
    print(f"\n{len(trades):,} trades · split at {split} "
          f"(in-sample {dates[0]}..{split}, out-of-sample {split}..{dates[-1]})")
    print(f"cost {COST*100:.1f}% round trip · entry next open · stop wins ties · max hold {MAX_HOLD} bars\n")
    print(f"{'variant':<30} {'n':>5} {'win%':>6} {'avg%':>7} {'exp%':>7} │ "
          f"{'n':>5} {'win%':>6} {'avg%':>7} {'exp%':>7}")
    print(f"{'':<30} {'--- in-sample ---':^28} │ {'-- OUT of sample --':^28}")
    for name, keep in VARIANTS.items():
        sel = [t for t in trades if keep(t)]
        a = stats([t for t in sel if t["date"] < split])
        o = stats([t for t in sel if t["date"] >= split])
        if not a or not o:
            continue
        saved.append((name, a, o))
        print(f"{name:<30} {a['n']:>5} {a['win']:>6.1f} {a['avg']:>7.2f} {a['avg']:>7.2f} │ "
              f"{o['n']:>5} {o['win']:>6.1f} {o['avg']:>7.2f} {o['avg']:>7.2f}")
    save(trades, split, saved)

    base = stats(trades)
    print(f"\nAll trades: {base['n']:,} · win {base['win']:.1f}% · avg {base['avg']:+.2f}% "
          f"· median {base['med']:+.2f}% · avg hold {base['bars']:.0f} bars")
    ends = {}
    for t in trades:
        ends[t["verdict"]] = ends.get(t["verdict"], 0) + 1
    print("Exits: " + " · ".join(f"{k} {v} ({100*v/len(trades):.0f}%)" for k, v in sorted(ends.items())))

    # rank by OUT-OF-SAMPLE average — the only column that has any claim on the future
    ranked = []
    for name, keep in VARIANTS.items():
        o = stats([t for t in trades if keep(t) and t["date"] >= split])
        if o and o["n"] >= 100:                       # ignore rules too thin to judge
            ranked.append((o["avg"], name, o))
    ranked.sort(reverse=True)
    print("\nRanked by OUT-OF-SAMPLE avg (>=100 trades):")
    for avg, name, o in ranked:
        print(f"  {avg:>+6.2f}%  win {o['win']:>5.1f}%  n={o['n']:>4}   {name}")
    if ranked:
        best = ranked[0][1]
        by_year([t for t in trades if VARIANTS[best](t)], best)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--demo", action="store_true", help="quick self-check on a few symbols")
    a = ap.parse_args()
    names = (MASTER / "symbols.txt").read_text(encoding="utf-8").split()
    if a.demo:
        names = names[:25]
    REGIME.update(market_regime())
    FLOAT.update(free_float())
    BOOKCLOSE.update(book_close_dates())
    print(f"float for {len(FLOAT)} symbols · book closures for {len(BOOKCLOSE)} symbols")
    print(f"market regime loaded for {len(REGIME):,} sessions "
          f"({100*sum(REGIME.values())/max(len(REGIME),1):.0f}% of days NEPSE was above its 200-SMA)")
    print(f"replaying {len(names)} symbols ...", flush=True)
    trades = rank_float_turn(collect(names))
    if not trades:
        sys.exit("no trades — archive missing or too short")
    assert all(t["entry"] > 0 and t["bars"] >= 0 for t in trades), "bad trade record"
    report(trades)


if __name__ == "__main__":
    main()
