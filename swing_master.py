"""Swing-trading master agent — screen, size, and plan the exit, on measured numbers only.

Horizon matches the evidence: every result in backtest.py was measured over ~20 trading days, so
this plans a 2-6 week swing and nothing else. Four rules, each traceable to a measurement:

 1. ENTRY  — volume-confirmed continuation, the only filter that held out of sample. The exact
              averages per volume band are read from backtest.py's own output rather than quoted
              here, because a transcribed measurement goes stale silently and this one did.
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

import backtest
from fetch_ohlc import MASTER
from indicators import sma
from trade_setup import LIQUID_MIN, setup

OUT = MASTER / "swing_master.txt"
# cost_rs is appended at the END on purpose: ui.py reads this file positionally
# (`int(r[9])` for risk_rs), so inserting a column mid-header would silently shift it.
HEADER = ("symbol\tverdict\tclose\tvol_x\tentry\tstop\ttarget1\ttarget2\tqty\trisk_rs"
          "\trisk_pct\trr\tedge_oos\thold_bars\tcost_rs")

HOLD_BARS = 20
BASE_WIN = 44                                       # NEPSE base rate, from 212k observations


def edge_for(vol_x):
    """The measured out-of-sample average for this volume band, read from backtest.py's output.

    These three numbers used to be transcribed here by hand, and the copy drifted from the one
    in master_signal.py AND from the measurement itself — three different values shipped for
    every band, each printed in an 'edge%' column as fact. backtest.oos_edge() reads the file
    that produced them, so the sheet can no longer quote a stale figure.
    """
    for threshold, avg in backtest.oos_edge():
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


def size(capital, budget, entry, stop):
    """Shares to buy: a fixed rupee loss at the stop, but never more stock than the cash buys.

    Risk-based sizing fixes the LOSS, not the POSITION — `budget / (entry - stop)` blows up as
    the stop tightens. With a 0.23% stop it sized Rs 438,110 of SSHL against a Rs 100,000 book,
    4.4x geared, and 7 rows over the archive exceeded the whole book. Worse, the sheet printed
    qty and risk Rs but never the position value, so nothing on it revealed the exposure.
    """
    per_share = entry - stop
    if per_share <= 0 or entry <= 0 or capital <= 0:
        return 0
    return int(min(budget // per_share, capital // entry))


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
        qty = size(capital, budget, s["entry"], s["stop"])
        if qty <= 0:
            continue
        rows.append({**s, "edge": edge, "qty": qty, "risk_rs": round(qty * per_share),
                     "cost_rs": round(qty * s["entry"]),
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
        r["edge"], HOLD_BARS, r["cost_rs"])) for r in rows]
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
          f"{'qty':>7}{'cost Rs':>11}{'risk Rs':>9}{'edge%':>7}")
    for r in rows:
        print(f"{r['symbol']:<9}{r['verdict']:<7}{r['entry']:>9.2f}{r['stop']:>9.2f}"
              f"{r['target1']:>9.2f}{r['target2']:>9.2f}{r['qty']:>7}{r['cost_rs']:>11,}"
              f"{r['risk_rs']:>9,}{r['edge']:>7.2f}")

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
