"""Are the daily prices adjusted for bonus / rights issues?

This decides whether any backtest on this archive means anything. Nepali bonus issues are large
(10-30%+), and on the ex-date an UNADJUSTED series prints a -20% "crash" that never happened. Every
stop-loss test walks straight into those fake cliffs, so returns come out too low and stop rates too
high — the errors do not cancel.

Test: for each company with a known book-closure date, look at the biggest single-day drop in the
window just after it. A real market drop is bounded by the +/-10% circuit; a bonus adjustment gap
is not. Anything below -10% is arithmetic, not trading.

    python check_adjust.py
"""
from collections import defaultdict

from fetch_ohlc import MASTER

CIRCUIT = -10.0          # NEPSE daily lower circuit; a real close cannot be worse than this


def closes(symbol):
    p = MASTER / "symbols" / symbol.replace("/", "-") / "1D.txt"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines()[1:]:
        f = line.split("\t")
        if len(f) > 4 and f[4] not in ("", "None"):
            try:
                out.append((f[0], float(f[4])))
            except ValueError:
                continue
    return out


def book_closes():
    """symbol -> [book-closure dates] from the SWP-fed corporate actions file."""
    bc = defaultdict(list)
    p = MASTER / "corporate_actions.txt"
    if not p.exists():
        return bc
    for line in p.read_text(encoding="utf-8").splitlines()[1:]:
        f = line.split("\t")
        if len(f) > 5 and f[5]:
            bc[f[0]].append(f[5])
    return bc


def main():
    bc = book_closes()
    print(f"{len(bc)} companies carry a book-closure date\n")

    breaches, checked, worst = [], 0, []
    for sym, dates in bc.items():
        c = closes(sym)
        if len(c) < 30:
            continue
        checked += 1
        for i in range(1, len(c)):
            prev, cur = c[i - 1][1], c[i][1]
            if prev <= 0:
                continue
            pct = (cur / prev - 1) * 100
            if pct < CIRCUIT - 0.5:                     # beyond the circuit = not a real trade move
                near = any(abs((int(d.replace("-", "")) - int(c[i][0].replace("-", "")))) < 120
                           for d in dates)
                breaches.append((sym, c[i][0], pct, near))
        drops = [(c[i][1] / c[i - 1][1] - 1) * 100 for i in range(1, len(c)) if c[i - 1][1] > 0]
        if drops:
            worst.append((min(drops), sym))

    print(f"checked {checked} symbols")
    print(f"impossible drops (worse than the {CIRCUIT:.0f}% circuit): {len(breaches)}")
    if breaches:
        near_bc = sum(1 for b in breaches if b[3])
        print(f"  of those, {near_bc} land within ~4 months of a known book closure "
              f"({100*near_bc/len(breaches):.0f}%)\n")
        print("  worst 12:")
        for sym, date, pct, near in sorted(breaches, key=lambda b: b[2])[:12]:
            print(f"    {sym:<10} {date}  {pct:>8.1f}%   {'<- near a book closure' if near else ''}")
        print("\n  VERDICT: prices are NOT adjusted for bonus/rights. Backtest stop-losses are")
        print("  being triggered by arithmetic, not by the market.")
    else:
        print("  none — the series look adjusted (or no corporate action fell in range).")

    worst.sort()
    print("\nworst single-day drop per symbol, 8 most extreme:")
    for pct, sym in worst[:8]:
        print(f"    {sym:<10} {pct:>8.1f}%")


if __name__ == "__main__":
    main()
