"""Today's buy candidates from the ONE rule that survived out-of-sample testing.

Built from backtest.py's evidence, not from opinion. Across 7,349 replayed trades (2012-2026,
next-open fills, 0.8% round-trip cost, stop wins ties) exactly one ingredient kept its edge in
BOTH halves of history:

    volume confirmation on a confirmed uptrend

                        in-sample   out-of-sample
    baseline trigger      +2.75%        +0.44%      <- collapses
    + ADX>25              +3.77%        +0.82%      <- collapses (classic overfit)
    + 20d breakout        +3.28%        +1.01%      <- collapses
    + volume >1.5x        +2.15%        +1.68%      <- holds
    + volume >2x          +2.66%        +1.79%      <- holds
    + volume >3x          +3.70%        +2.39%      <- holds, and scales with volume

What this is NOT: a high-return machine. The same rule lost money in 7 of the last 13 years,
including 2025 (-0.93%) and 2026 (-3.77%). It is a bull-market amplifier: it pays in trending
years and bleeds in flat ones. Position size accordingly, and read `verdict` before `score`.

    python master_signal.py           # write Master_data/master_signal.txt + print the list
"""
import sys
from datetime import datetime

import backtest
from fetch_ohlc import MASTER
from indicators import sma
from trade_setup import LIQUID_MIN, setup

OUT = MASTER / "master_signal.txt"
# `date` is LAST on purpose — ui.py reads this sheet positionally (r[1] == "BUY"), so a column
# inserted mid-header changes what the page reads with no error at all. test_ops pins the indices.
HEADER = ("symbol\tverdict\tclose\tvol_x\tentry\tstop\ttarget1\ttarget2\trisk_pct\trr"
          "\tadx\trsi\tscore\tedge_oos\tdate")

def edge_for(vol_x):
    """The measured OOS average (~20 bars) for the volume band this candidate falls in.

    Read from backtest.py's own output, not transcribed. The three numbers used to be a literal
    here and another literal in swing_master.py, and both had drifted from the measurement and
    from each other — three different values shipped for every band (vol>3x: 2.39 here, 2.44
    there, 2.51 in backtest.txt), each printed in an 'edge%' column as measured fact.
    Historical, not a prediction.
    """
    for threshold, avg in backtest.oos_edge():
        if vol_x >= threshold:
            return threshold, avg
    return None, None


def market_up():
    """Is NEPSE above its own 200-SMA today? Context only — as a FILTER it tested worse than
    useless (MKT-UP +1.32% vs plain volume +1.79%), so it is reported, never used to gate."""
    p = MASTER / "indices" / "NEPSE" / "1D.txt"
    if not p.exists():
        return None
    closes = []
    for line in p.read_text(encoding="utf-8").splitlines()[1:]:
        f = line.split("\t")
        if len(f) > 4 and f[4] not in ("", "None"):
            try:
                closes.append(float(f[4]))
            except ValueError:
                continue
    s = sma(closes, 200)
    return bool(closes and s[-1] is not None and closes[-1] > s[-1])


def candidates(symbols):
    """Every symbol whose setup is a live long AND carries the volume confirmation that survived."""
    rows = []
    for sym in symbols:
        try:
            s = setup(sym)
        except Exception:
            continue
        if not s or s["signal"] not in ("STRONG BUY", "BUY"):
            continue
        if not s["liquid"] or s["med_turnover"] < LIQUID_MIN:
            continue
        band, edge = edge_for(s["vol_ratio"])
        if band is None:                       # no volume confirmation = no measured edge
            continue
        # the backtest entered on a FRESH trigger; an extended one is a different, worse trade
        verdict = "BUY" if s.get("still_buy") else "WAIT-PULLBACK"
        rows.append({**s, "band": band, "edge": edge, "verdict": verdict})
    rows.sort(key=lambda r: (r["edge"], r["vol_ratio"]), reverse=True)
    return rows


def main():
    names = (MASTER / "symbols.txt").read_text(encoding="utf-8").split()
    rows = candidates(names)
    up = market_up()

    lines = [HEADER]
    for r in rows:
        lines.append("\t".join(str(x) for x in (
            r["symbol"], r["verdict"], r["close"], round(r["vol_ratio"], 2), r["entry"],
            r["stop"], r["target1"], r["target2"], r["risk_pct"], r["rr"],
            r["adx"], r["rsi"], r["score"], r["edge"], r["date"])))
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"=== master signal · {stamp} · NEPSE {'ABOVE' if up else 'BELOW'} its 200-SMA ===")
    print(f"{len(rows)} candidate(s) with volume confirmation -> {OUT}\n")
    if not rows:
        print("Nothing qualifies today. That is a normal result — the rule only fires on volume.")
        return 0
    print(f"{'symbol':<10}{'verdict':<15}{'close':>9}{'vol':>7}{'entry':>9}{'stop':>9}"
          f"{'T1':>9}{'T2':>9}{'risk%':>7}{'edge%':>7}")
    for r in rows:
        print(f"{r['symbol']:<10}{r['verdict']:<15}{r['close']:>9.2f}{r['vol_ratio']:>6.1f}x"
              f"{r['entry']:>9.2f}{r['stop'] or 0:>9.2f}{r['target1'] or 0:>9.2f}"
              f"{r['target2'] or 0:>9.2f}{r['risk_pct'] or 0:>7.1f}{r['edge']:>7.2f}")
    print("\nedge% = the measured OUT-OF-SAMPLE average for that volume band over ~20 bars.")
    print("It is a historical average with losing years in it, not an expected return.")
    if not up:
        print("NEPSE is below its 200-SMA — every one of these rules had losing years in "
              "exactly this kind of market.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
