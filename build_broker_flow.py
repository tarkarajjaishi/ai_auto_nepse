"""Aggregate the floorsheet into a daily broker-flow table for every symbol.

Reads Master_data/floorsheet/<SYMBOL>/<DATE>.txt (one row per trade) and writes
Master_data/broker_flow/<SYMBOL>.txt with one row per (date, broker):

    date  broker  bought  sold  net  buy_amount  sell_amount  trades

`net` is bought - sold in shares: positive means that broker took stock off the market
that session. This is the table the UI and any smart-broker model should read — scanning
raw trades per query re-reads millions of rows for numbers that never change.

    python build_broker_flow.py           # all symbols
    python build_broker_flow.py NABIL CGH # just these
"""

import sys
from collections import defaultdict
from pathlib import Path

from fetch_ohlc import MASTER

FLOORSHEET = MASTER / "floorsheet"
OUT = MASTER / "broker_flow"
HEADER = "date\tbroker\tbought\tsold\tnet\tbuy_amount\tsell_amount\ttrades"


def flow_for(symbol_dir):
    """{(date, broker): [bought, sold, buy_amount, sell_amount, trades]} for one symbol."""
    rows = {}
    for path in sorted(symbol_dir.glob("*.txt")):
        date = path.stem
        for line in path.read_text(encoding="utf-8").splitlines()[1:]:
            buyer, seller, qty, _rate, amount, _txn = line.split("\t")
            q, a = float(qty), float(amount)
            b = rows.setdefault((date, buyer), [0.0, 0.0, 0.0, 0.0, 0])
            b[0] += q
            b[2] += a
            b[4] += 1
            s = rows.setdefault((date, seller), [0.0, 0.0, 0.0, 0.0, 0])
            s[1] += q
            s[3] += a
            s[4] += 1
    return rows


def write(symbol, rows):
    lines = [HEADER]
    for (date, broker), (bought, sold, buy_amt, sell_amt, trades) in sorted(
        rows.items(), key=lambda kv: (kv[0][0], -(kv[1][0] - kv[1][1]))
    ):
        lines.append(
            f"{date}\t{broker}\t{bought:.0f}\t{sold:.0f}\t{bought - sold:.0f}"
            f"\t{buy_amt:.1f}\t{sell_amt:.1f}\t{trades}"
        )
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{symbol}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines) - 1


def main():
    wanted = set(sys.argv[1:])
    dirs = [d for d in sorted(FLOORSHEET.iterdir()) if d.is_dir() and (not wanted or d.name in wanted)]
    print(f"{len(dirs)} symbols to aggregate", flush=True)

    total = 0
    for i, d in enumerate(dirs, 1):
        rows = flow_for(d)
        if not rows:
            print(f"[{i}/{len(dirs)}] {d.name} — no floorsheet files", flush=True)
            continue
        n = write(d.name, rows)
        total += n
        print(f"[{i}/{len(dirs)}] {d.name:12} {n:>7,} broker-days", flush=True)
    print(f"done — {total:,} broker-day rows across {len(dirs)} symbols")


if __name__ == "__main__":
    main()
