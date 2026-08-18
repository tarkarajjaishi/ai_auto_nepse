"""Fetch one year of broker-level floorsheet into Master_data/floorsheet/<SYMBOL>/<DATE>.txt.

Source is chukul's by-date feed: one request returns every trade of that session for all
symbols, which is then split per symbol into that day's file. Merolagani carries the same
fields but only behind paginated ASP.NET postbacks, so it would take thousands of times
more requests for the same rows. Broker ids are the raw NEPSE member codes.

    python fetch_floorsheet.py              # last 365 days
    python fetch_floorsheet.py 2022-01-25   # from an explicit start date (feed begins here)
    python fetch_floorsheet.py split        # one-shot: convert old single-file layout

Fetched dates are listed in Master_data/floorsheet/_fetched.txt and skipped, so the script
resumes after an interruption and can be re-run daily.
"""

import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from fetch_ohlc import MASTER, NPT, folder, get

BYDATE = "https://chukul.com/api/data/floorsheet/bydate/?date={}"
OUT = MASTER / "floorsheet"
DONE = OUT / "_fetched.txt"
HEADER = "buyer\tseller\tquantity\trate\tamount\ttransaction"  # the date is the filename


def trading_days(start):
    """NEPSE sessions on or after `start`, oldest first — taken from the index's own file."""
    rows = (MASTER / "indices" / "NEPSE" / "1D.txt").read_text(encoding="utf-8").splitlines()[1:]
    return [line.split("\t", 1)[0] for line in rows if line[:10] >= start]


def write_day(symbol, date, lines):
    path = folder("floorsheet", symbol) / f"{date}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER + "\n" + "\n".join(lines) + "\n", encoding="utf-8")


def fetch(date):
    """Write one session's trades into each symbol's <date>.txt. Returns (rows, symbols)."""
    by_symbol = defaultdict(list)
    for t in get(BYDATE.format(date)):
        by_symbol[t["symbol"]].append(
            f"{t['buyer']}\t{t['seller']}\t{t['quantity']}\t{t['rate']}\t{t['amount']}\t{t['transaction']}"
        )
    for symbol, lines in by_symbol.items():
        write_day(symbol, date, lines)
    return sum(len(v) for v in by_symbol.values()), len(by_symbol)


def split():
    """Convert the earlier one-file-per-symbol layout into one file per date."""
    files = sorted(OUT.glob("*/floorsheet.txt"))
    for i, path in enumerate(files, 1):
        by_date = defaultdict(list)
        for line in path.read_text(encoding="utf-8").splitlines()[1:]:
            date, rest = line.split("\t", 1)
            by_date[date].append(rest)
        for date, lines in by_date.items():
            (path.parent / f"{date}.txt").write_text(HEADER + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
        path.unlink()
        print(f"[{i}/{len(files)}] {path.parent.name} -> {len(by_date)} daily files", flush=True)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if len(sys.argv) > 1 and sys.argv[1] == "split":
        return split()

    start = sys.argv[1] if len(sys.argv) > 1 else (datetime.now(NPT) - timedelta(days=365)).strftime("%Y-%m-%d")
    done = set(DONE.read_text(encoding="utf-8").split()) if DONE.exists() else set()
    days = [d for d in trading_days(start) if d not in done]
    print(f"{len(days)} sessions to fetch from {start} ({len(done)} already on disk)")

    total = 0
    for i, date in enumerate(days, 1):
        try:
            rows, symbols = fetch(date)
        except (OSError, KeyError, ValueError) as e:
            print(f"[{i}/{len(days)}] {date} FAILED: {e}")
            continue
        total += rows
        with DONE.open("a", encoding="utf-8") as f:  # recorded per day, so a crash loses nothing
            f.write(date + "\n")
        print(f"[{i}/{len(days)}] {date} {rows:>7,} trades across {symbols:>3} symbols", flush=True)
    print(f"done — {total:,} trades written")


if __name__ == "__main__":
    main()
