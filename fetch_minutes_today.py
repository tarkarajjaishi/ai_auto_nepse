"""Rebuild today's 1-minute bars from the per-trade floorsheet — the only exact source.

The newintrahistorydata feed used by fetch_intraday.py is a ~60s poller: its bars carry
the exchange's 1-3 minute reporting lag and straddle minute boundaries. The floorsheet
has real trade times, so bars built from it are exact — but only for today, the API
serves no per-trade times for past dates. Run after the 15:00 NPT close.

    python fetch_minutes_today.py              # today
    python fetch_minutes_today.py 2026-08-18   # explicit date, if the feed still has it
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from fetch_intraday import HEADER, add_bar, fill_minutes
from fetch_ohlc import MASTER, NPT, folder, get, write

FLOOR = "https://chukul.com/api/data/floorsheet/intradaysymbol/?symbol={}&date={}"


def candles(symbol, day):
    rows = {}
    trades = sorted(get(FLOOR.format(symbol, day)), key=lambda t: t["id"])  # transaction ids are not chronological
    for t in trades:
        minute = t["date"].replace("T", " ")[:17] + "00"
        add_bar(rows, minute, [t["rate"]] * 4 + [t["quantity"], t["amount"]])
    return rows


def one(kind, name, day):
    path = folder(kind, name) / "1minutes.txt"
    try:
        new = candles(name, day)
        if not new:
            return f"{name} no trades"  # indices never appear in the floorsheet
        rows = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines()[1:]:
                f = line.split("\t")
                if not f[0].startswith(day):  # keep history, replace today
                    add_bar(rows, f[0], [f[1], f[2], f[3], f[4], f[7], f[8]])
        rows.update(new)
        write(fill_minutes(dict(sorted(rows.items()))), path, HEADER)
        return f"{name} {len(new)} exact minutes"
    except (OSError, KeyError, IndexError, ValueError) as e:
        return f"{name} FAILED: {e}"


def run(kind, day):
    names = (MASTER / f"{kind}.txt").read_text(encoding="utf-8").split()
    # one network round-trip per symbol (~346) is the pipeline's biggest cost — it's I/O-wait, not
    # CPU, so more concurrency ~halves it. 16 is a safe 2× (dial back if chukul rate-limits).
    with ThreadPoolExecutor(16) as pool:
        for i, line in enumerate(pool.map(lambda n: one(kind, n, day), names), 1):
            print(f"[{i}/{len(names)}] {line}")


if __name__ == "__main__":
    import sys
    # a date may be passed, but the feed only serves per-trade times for the current day
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.now(NPT).strftime("%Y-%m-%d")
    run("symbols", day)          # indices never appear in the floorsheet
