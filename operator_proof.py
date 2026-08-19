"""Can a flagged name actually be called an operator? Test it, do not assert it.

The grip scan says "broker 92 net-bought 48% of NTC volume". On its own that proves nothing, for
two reasons that have to be dealt with separately:

  CONFOUND 1 - the broker is just BIG. If broker 92 handles 15% of all NEPSE volume, it leading
    any given stock is arithmetic. The fix is to compare its share IN THE STOCK against its own
    share EVERYWHERE ELSE over the same sessions. That is computable, and it is what this does.

  CONFOUND 2 - a broker is a PIPE, not a person. The floorsheet publishes broker IDs, never client
    IDs, so 48% through one broker may be one operator or four hundred unrelated retail clients.
    This is NOT computable from this data. No amount of arithmetic fixes it, and any claim of
    "an operator" is an assumption bolted onto the number, not a finding.

So the strongest honest statement available is:

    "broker B's share of this stock is X times its share of everything else, and a share that
     extreme would arise by chance with probability p"

which is concentration, not identity. Reported with a two-proportion z-test so the size of the
claim is visible.

    python operator_proof.py                 # test the current buy-side flags
    python operator_proof.py --symbol NTC    # one name, in full
"""
import argparse
import math
import statistics
from collections import defaultdict
from pathlib import Path

FLOOR = Path(__file__).parent / "Master_data" / "floorsheet"
FLAGS = Path(__file__).parent / "Master_data" / "operator_now.txt"
WINDOW = 20


def read_day(path):
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
        f = line.rstrip("\r").split("\t")
        if len(f) < 5:
            continue
        try:
            out.append((f[0], f[1], int(float(f[2]))))
        except ValueError:
            continue
    return out


def market_baseline(dates):
    """Each broker's share of TOTAL market buy volume over the same sessions — the null model.

    This is the number the grip scan never had: what does this broker do everywhere else?"""
    bought = defaultdict(int)
    total = 0
    per_symbol = defaultdict(lambda: defaultdict(int))
    for sym_dir in FLOOR.iterdir():
        if not sym_dir.is_dir():
            continue
        for dt in dates:
            p = sym_dir / f"{dt}.txt"
            if not p.exists():
                continue
            for b, s, q in read_day(p):
                if b == s:
                    continue
                bought[b] += q
                per_symbol[sym_dir.name][b] += q
                total += q
    return bought, total, per_symbol


def two_prop_z(x1, n1, x2, n2):
    """Is this broker's share of THIS stock different from its share of everything else?"""
    if n1 <= 0 or n2 <= 0:
        return 0.0, 1.0
    p1, p2 = x1 / n1, x2 / n2
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 0.0, 1.0
    z = (p1 - p2) / se
    # two-sided normal tail
    pval = math.erfc(abs(z) / math.sqrt(2))
    return z, pval


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", help="test one symbol instead of the current flags")
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args()

    # the sessions the flags were computed over
    ref = sorted((FLOOR / "NABIL").glob("*.txt"))[-WINDOW:]
    dates = [p.stem for p in ref]
    print(f"baseline over {len(dates)} sessions: {dates[0]} .. {dates[-1]}", flush=True)

    if a.symbol:
        targets = [(a.symbol.upper(), None)]
    else:
        rows = [ln.split("\t") for ln in
                FLAGS.read_text(encoding="utf-8").splitlines()[1:] if ln.strip()]
        targets = [(r[1], r[3]) for r in rows if r[0] == "BUY"][:a.top]

    print("computing every broker's market-wide share ...", flush=True)
    bought, total, per_symbol = market_baseline(dates)
    print(f"market buy volume over the window: {total:,} shares across "
          f"{len(per_symbol)} symbols, {len(bought)} brokers\n")

    big = sorted(bought.items(), key=lambda kv: -kv[1])[:8]
    print("largest brokers market-wide (this is the null they must beat):")
    for b, q in big:
        print(f"  broker {b:<5} {100*q/total:>6.2f}% of all buy volume")

    print(f"\n{'symbol':<9}{'brkr':>5}{'in-stock':>10}{'elsewhere':>11}{'ratio':>8}"
          f"{'z':>9}{'p':>11}  verdict")
    verdicts = []
    for sym, broker in targets:
        sym_vol = per_symbol.get(sym)
        if not sym_vol:
            print(f"{sym:<9}  no floorsheet in window")
            continue
        n1 = sum(sym_vol.values())
        if broker is None:
            broker = max(sym_vol, key=sym_vol.get)
        x1 = sym_vol.get(broker, 0)
        # everywhere ELSE: subtract this stock so the comparison is independent
        x2, n2 = bought[broker] - x1, total - n1
        share_in, share_out = 100 * x1 / n1, 100 * x2 / max(n2, 1)
        ratio = share_in / share_out if share_out > 0 else float("inf")
        z, p = two_prop_z(x1, n1, x2, n2)
        verdict = ("CONCENTRATED" if (p < 0.001 and ratio >= 2) else
                   "elevated" if (p < 0.05 and ratio >= 1.5) else "just a big broker")
        verdicts.append(verdict)
        print(f"{sym:<9}{broker:>5}{share_in:>9.1f}%{share_out:>10.2f}%{ratio:>8.1f}x"
              f"{z:>9.1f}{p:>11.1e}  {verdict}")

    print(f"\nin-stock  = this broker's share of THIS symbol's buy volume")
    print(f"elsewhere = the same broker's share of every OTHER symbol, same sessions")
    print(f"ratio     = how many times more concentrated it is here than its own norm")
    print(f"z, p      = two-proportion test that the two shares differ")
    n_conc = sum(1 for v in verdicts if v == "CONCENTRATED")
    print(f"\n{n_conc} of {len(verdicts)} flags are genuinely concentrated versus the broker's own "
          f"baseline.")
    print("\nWHAT THIS DOES AND DOES NOT PROVE")
    print("  proves    : the concentration is real and not just a large broker doing normal volume.")
    print("  DOES NOT  : show an operator. The floorsheet carries BROKER ids, not CLIENT ids, so a")
    print("              concentrated broker is one desk's order flow - possibly one client,")
    print("              possibly hundreds. That distinction is not in this dataset at any")
    print("              significance level, and no calculation here can recover it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
