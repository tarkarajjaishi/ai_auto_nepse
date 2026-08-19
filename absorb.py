"""QUIET ABSORPTION — my own factor, built from NEPSE's structure rather than from TA.

Reasoning, not indicators:

  * NEPSE has no short selling. The only way price rises is someone buying float and holding it.
  * Free floats are tiny, so one persistent buyer can take a real share of the supply.
  * The floorsheet publishes broker identity per trade — almost no market gives you this. We can
    watch the accumulation happen instead of inferring it from a price pattern.
  * It is retail-driven and momentum-chasing, so by the time price moves the information is public.
    The project already learned this: the volume-spike screen flags a move that has ALREADY run.

So every rule tested so far buys LOUDNESS (volume spike, breakout, upper circuit) and every one of
them decays out of sample. This factor deliberately inverts that:

    buy where a broker has persistently absorbed float and price has NOT moved yet.

Absorption is the cause; the price move is the effect that has not happened. If that is true,
forward returns should RISE with absorption and be HIGHER when the stock is still quiet. If it is
false, the quiet bucket will underperform the moved one and the idea dies here.

Measured as a factor, not a strategy: no stops, no targets, no entry timing — just
"does this number predict the next N days?", bucketed, split in half by date. A factor that
cannot show a monotonic relationship will never make a strategy work.

    python absorb.py            # decile table + quiet-vs-moved split
"""
import statistics
from collections import defaultdict

from fetch_ohlc import MASTER
from prices import bars

WINDOW = 20            # trading days of accumulation to look back over
HORIZON = 20           # trading days forward to measure
MIN_TURNOVER = 500_000


def broker_net(symbol):
    """{date: {broker: net_shares}} from the floorsheet-derived broker flow table."""
    p = MASTER / "broker_flow" / f"{symbol.replace('/', '-')}.txt"
    if not p.exists():
        return {}
    out = defaultdict(dict)
    for line in p.read_text(encoding="utf-8").splitlines()[1:]:
        f = line.split("\t")
        if len(f) > 4:
            try:
                out[f[0]][f[1]] = float(f[4])
            except ValueError:
                continue
    return out


def free_float():
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


def observations(symbol, flt):
    """One row per date: the absorption factor and what happened over the next HORIZON days."""
    b = bars(symbol)
    flow = broker_net(symbol)
    if not b or not flow or symbol not in flt:
        return []
    d, o, h, l, c, v = b
    idx = {dt: i for i, dt in enumerate(d)}
    float_shares = flt[symbol]
    dates = sorted(set(flow) & set(idx))
    rows = []

    for n in range(WINDOW, len(dates)):
        win = dates[n - WINDOW:n]
        today = dates[n - 1]
        i = idx[today]
        if i + HORIZON >= len(c):
            continue
        turn = statistics.median([c[j] * v[j] for j in range(max(0, i - 19), i + 1)])
        if turn < MIN_TURNOVER:
            continue

        # --- who absorbed, and how steadily -------------------------------------------------
        per_broker = defaultdict(float)
        days_net_buy = defaultdict(int)
        for dt in win:
            for br, net in flow[dt].items():
                per_broker[br] += net
                if net > 0:
                    days_net_buy[br] += 1
        if not per_broker:
            continue
        top = max(per_broker, key=per_broker.get)
        taken = per_broker[top]
        if taken <= 0:
            continue

        absorption = 100 * taken / float_shares           # % of free float one broker took
        persistence = days_net_buy[top] / len(win)        # steady accumulation vs a single block

        # --- has price already moved? -------------------------------------------------------
        start = idx[win[0]]
        price_chg = 100 * (c[i] / c[start] - 1) if c[start] else 0

        fwd = 100 * (c[i + HORIZON] / c[i] - 1) if c[i] else 0
        rows.append({"symbol": symbol, "date": today, "absorption": absorption,
                     "persistence": persistence, "price_chg": price_chg, "fwd": fwd})
    return rows


def bucket_table(rows, key, label, edges):
    """Forward return by bucket — the test is whether it rises monotonically."""
    print(f"\n{label}")
    print(f"  {'bucket':<18}{'n':>7}{'fwd avg%':>11}{'fwd med%':>11}{'win%':>8}")
    for lo, hi in edges:
        sel = [r for r in rows if lo <= r[key] < hi]
        if len(sel) < 40:
            continue
        f = [r["fwd"] for r in sel]
        print(f"  {f'{lo:g} to {hi:g}':<18}{len(sel):>7}{statistics.fmean(f):>11.2f}"
              f"{statistics.median(f):>11.2f}{100*len([x for x in f if x>0])/len(f):>8.1f}")


def main():
    flt = free_float()
    names = (MASTER / "symbols.txt").read_text(encoding="utf-8").split()
    rows = []
    for s in names:
        rows.extend(observations(s, flt))
    if not rows:
        print("no observations — broker_flow or fundamentals missing")
        return
    dates = sorted(r["date"] for r in rows)
    split = dates[len(dates) // 2]
    base = statistics.fmean([r["fwd"] for r in rows])
    print(f"{len(rows):,} observations · {dates[0]} → {dates[-1]} · split {split}")
    print(f"window {WINDOW}d accumulation → forward {HORIZON}d return")
    print(f"BASELINE forward {HORIZON}d return of all observations: {base:+.2f}%")
    print("(every bucket below must be read against that baseline, not against zero)")

    edges = [(0, 0.1), (0.1, 0.25), (0.25, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 100)]
    bucket_table(rows, "absorption", "FORWARD RETURN BY ABSORPTION (% of free float taken by the top broker)", edges)

    # the actual hypothesis: absorption pays MORE when price has not moved yet
    strong = [r for r in rows if r["absorption"] >= 0.5 and r["persistence"] >= 0.5]
    quiet = [r for r in strong if r["price_chg"] < 5]
    moved = [r for r in strong if r["price_chg"] >= 5]
    print(f"\nTHE HYPOTHESIS — absorption >=0.5% of float, persistence >=50% of days ({len(strong):,} obs)")
    for name, sel in (("QUIET  (price <+5% so far)", quiet), ("MOVED  (price >=+5% already)", moved)):
        if len(sel) < 40:
            continue
        f = [r["fwd"] for r in sel]
        print(f"  {name:<28}n={len(sel):>6}  fwd {statistics.fmean(f):>+6.2f}%  "
              f"med {statistics.median(f):>+6.2f}%  win {100*len([x for x in f if x>0])/len(f):>5.1f}%")

    print("\nOUT-OF-SAMPLE (second half only)")
    for name, sel in (("QUIET", [r for r in quiet if r["date"] >= split]),
                      ("MOVED", [r for r in moved if r["date"] >= split]),
                      ("all observations", [r for r in rows if r["date"] >= split])):
        if len(sel) < 40:
            continue
        f = [r["fwd"] for r in sel]
        print(f"  {name:<28}n={len(sel):>6}  fwd {statistics.fmean(f):>+6.2f}%  "
              f"win {100*len([x for x in f if x>0])/len(f):>5.1f}%")


if __name__ == "__main__":
    main()
