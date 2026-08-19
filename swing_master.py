"""Swing-trading master agent — screen, size, and plan the exit, on measured numbers only.

Horizon matches the evidence: every result in backtest.py was measured over ~20 trading days, so
this plans a 2-6 week swing and nothing else. Four rules, each traceable to a measurement:

 1. ENTRY  — volume-confirmed continuation, the only filter that held out of sample
              (vol>3x +2.44%, vol>2x +1.80%, vol>1.5x +1.72% vs +0.58% baseline).
 2. FRESH  — the backtest entered on the NEXT open after a fresh trigger. An extended setup is a
              different trade that was never measured, so it is listed as WAIT, never as a buy.
 3. TIME STOP — absorb.py found the MEDIAN forward 20d return is NEGATIVE across 212k observations
              (win rate 44%): the typical stock drifts down and a thin tail carries the mean. So
              time in a trade is a cost, not a neutral. Out at 20 bars if neither target hit.
 4. SIZE   — risk-based, never conviction-based. With a 44% base rate and a fat left side, the
              only reliable control is a fixed loss per trade.

    python swing_master.py                       # today's plan, Rs 100,000 book, 1% risk
    python swing_master.py --capital 500000 --risk 1.5
"""
import argparse
from datetime import datetime

from fetch_ohlc import MASTER
from indicators import sma
from trade_setup import LIQUID_MIN, setup

OUT = MASTER / "swing_master.txt"
HEADER = ("symbol\tverdict\tclose\tvol_x\tentry\tstop\ttarget1\ttarget2\tqty\trisk_rs"
          "\trisk_pct\trr\tedge_oos\thold_bars")

EDGE = [(3.0, 2.44), (2.0, 1.80), (1.5, 1.72)]     # measured out-of-sample avg per ~20-bar trade
HOLD_BARS = 20
BASE_WIN = 44                                       # NEPSE base rate, from 212k observations


def edge_for(vol_x):
    for threshold, avg in EDGE:
        if vol_x >= threshold:
            return avg
    return None


def market_up():
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


def plan(capital, risk_pct):
    """Today's candidates, sized so a stop-out costs the same fixed rupees on every trade."""
    names = (MASTER / "symbols.txt").read_text(encoding="utf-8").split()
    budget = capital * risk_pct / 100
    rows = []
    for sym in names:
        try:
            s = setup(sym)
        except Exception:
            continue
        if not s or s["signal"] not in ("STRONG BUY", "BUY"):
            continue
        if not s["liquid"] or s["med_turnover"] < LIQUID_MIN:
            continue
        edge = edge_for(s["vol_ratio"])
        if edge is None or not s["stop"] or not s["entry"]:
            continue
        per_share = s["entry"] - s["stop"]
        if per_share <= 0:
            continue
        qty = int(budget // per_share)                 # fixed rupee loss if the stop is hit
        rows.append({**s, "edge": edge, "qty": qty, "risk_rs": round(qty * per_share),
                     "verdict": "BUY" if s.get("still_buy") else "WAIT"})
    rows.sort(key=lambda r: (r["verdict"] == "BUY", r["edge"], r["vol_ratio"]), reverse=True)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--capital", type=float, default=100_000, help="book size in Rs")
    ap.add_argument("--risk", type=float, default=1.0, help="%% of capital risked per trade")
    a = ap.parse_args()

    rows = plan(a.capital, a.risk)
    up = market_up()
    lines = [HEADER] + ["\t".join(str(x) for x in (
        r["symbol"], r["verdict"], r["close"], round(r["vol_ratio"], 2), r["entry"], r["stop"],
        r["target1"], r["target2"], r["qty"], r["risk_rs"], r["risk_pct"], r["rr"],
        r["edge"], HOLD_BARS)) for r in rows]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    buys = [r for r in rows if r["verdict"] == "BUY"]
    print(f"=== SWING MASTER · {datetime.now():%Y-%m-%d %H:%M} · "
          f"Rs {a.capital:,.0f} book · {a.risk}% risk/trade ===")
    print(f"NEPSE is {'ABOVE' if up else 'BELOW'} its 200-SMA · "
          f"{len(buys)} actionable / {len(rows)} on the list -> {OUT}\n")
    if not rows:
        print("Nothing qualifies. Cash is a position — the rule only fires on volume confirmation.")
        return 0

    print(f"{'symbol':<9}{'action':<7}{'entry':>9}{'stop':>9}{'T1':>9}{'T2':>9}"
          f"{'qty':>7}{'risk Rs':>9}{'edge%':>7}")
    for r in rows:
        print(f"{r['symbol']:<9}{r['verdict']:<7}{r['entry']:>9.2f}{r['stop']:>9.2f}"
              f"{r['target1']:>9.2f}{r['target2']:>9.2f}{r['qty']:>7}{r['risk_rs']:>9,}"
              f"{r['edge']:>7.2f}")

    total_risk = sum(r["risk_rs"] for r in buys)
    print(f"\nPLAN — hold max {HOLD_BARS} bars (~4 weeks), then out whatever the price is.")
    print(f"  actionable risk if every BUY is taken: Rs {total_risk:,} "
          f"({100*total_risk/a.capital:.1f}% of the book)")
    print(f"  WAIT rows are already extended past the trigger the backtest measured — a different,")
    print(f"  worse trade. Wait for a pullback toward the 20-EMA or skip them.")
    print(f"\nBASE RATES, so the numbers above are read against something real:")
    print(f"  a random NEPSE 20-day hold wins {BASE_WIN}% of the time and its MEDIAN return is")
    print(f"  negative — the average is carried by a thin tail. This rule won ~58% out of sample.")
    print(f"  It still lost money in 7 of the last 13 years, 2025 and 2026 included.")
    if not up:
        print("  NEPSE below its 200-SMA: this is the regime where those losing years happened.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
