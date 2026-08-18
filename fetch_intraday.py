"""Fetch 1-minute OHLC for every symbol and index into Master_data/<kind>/<SYMBOL>/1minutes.txt."""

import collections
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from fetch_ohlc import MASTER, NPT, folder, get, write

SNAP = "https://live.chukul.com/api/data/newintrahistorydata/data/?symbol={}&v=1"
HEADER = "datetime\topen\thigh\tlow\tclose\tchange\tpercent_change\tvolume\tamount"
STAMP = "%Y-%m-%d %H:%M:%S"


def num(x):
    return None if x in ("", None) else float(x)


def add_bar(rows, minute, bar):
    """Fold a bar into its minute bucket: first open, extreme high/low, last close, summed volume/amount."""
    o, h, l, c, v, a = (num(x) for x in bar)
    p = rows.get(minute)
    if p is None:
        rows[minute] = [o, h, l, c, v, a]
        return
    keep = lambda f, x, y: x if y is None else y if x is None else f(x, y)
    p[0] = p[0] if p[0] is not None else o
    p[1] = keep(max, p[1], h)
    p[2] = keep(min, p[2], l)
    p[3] = c if c is not None else p[3]
    p[4] = keep(lambda x, y: round(x + y, 2), p[4], v)
    p[5] = keep(lambda x, y: round(x + y, 2), p[5], a)


def bars(symbol):
    """{minute: [open, high, low, close, volume, amount]}, oldest first. History starts 2023-11-28."""
    d = get(SNAP.format(symbol))
    n = len(d["t"])
    blank = [""] * n
    vol, amt = d.get("cv") or blank, d.get("ca") or blank  # indices carry amount but no volume
    rows, seen = {}, set()
    for i, ts in sorted(enumerate(d["t"]), key=lambda p: p[1]):  # feed is newest-first
        bar = [d["o"][i], d["h"][i], d["l"][i], d["c"][i], vol[i], amt[i]]
        if (ts, *bar) in seen:  # the feed repeats whole records (every bar of 2026-04-27)
            continue
        seen.add((ts, *bar))
        # the feed polls at ~:03 past the minute and drifts, so snap to the minute grid
        add_bar(rows, datetime.fromtimestamp(ts, NPT).strftime("%Y-%m-%d %H:%M:00"), bar)
    return rows


def fill_minutes(rows):
    """Insert a flat zero-volume bar for every missing minute inside each day's trading range."""
    days = collections.defaultdict(list)
    for minute in sorted(rows):
        days[minute[:10]].append(minute)
    out = {}
    for minutes in days.values():
        t = datetime.strptime(minutes[0], STAMP)
        end = datetime.strptime(minutes[-1], STAMP)
        last = rows[minutes[0]]
        while t <= end:
            minute = t.strftime(STAMP)
            bar = rows.get(minute)
            if bar is None:  # no trade that minute: carry the close, zero the flow
                zero = lambda x: 0.0 if x is not None else None
                bar = [last[3]] * 4 + [zero(last[4]), zero(last[5])]
            out[minute] = last = bar
            t += timedelta(minutes=1)
    return out


def normalize(kind):
    """Re-grid already-downloaded files in place, so a 900 MB re-fetch isn't needed."""
    for path in sorted((MASTER / kind).glob("**/1minutes.txt")):  # ** for symbols with / in the name
        rows = {}
        for line in path.read_text(encoding="utf-8").splitlines()[1:]:
            f = line.split("\t")
            add_bar(rows, f[0][:17] + "00", [f[1], f[2], f[3], f[4], f[7], f[8]])
        rows = fill_minutes(rows)
        write(rows, path, HEADER)
        print(f"{path.parent.name} {len(rows)} minutes")


def one(kind, name):
    path = folder(kind, name) / "1minutes.txt"
    # doubles as resume-after-crash and as a daily refresh guard
    if path.exists() and date.fromtimestamp(path.stat().st_mtime) == date.today():
        return f"{name} already current"
    try:
        rows = bars(name)
        assert rows, "no bars"
        write(fill_minutes(rows), path, HEADER)
        return f"{name} {len(rows)} bars"
    except (OSError, KeyError, AssertionError, ValueError) as e:
        return f"{name} FAILED: {e}"


def run(kind):
    names = (MASTER / f"{kind}.txt").read_text(encoding="utf-8").split()
    with ThreadPoolExecutor(8) as pool:  # payloads run to several MB each; serial takes ~20 min
        for i, line in enumerate(pool.map(lambda n: one(kind, n), names), 1):
            print(f"[{i}/{len(names)}] {line}")


if __name__ == "__main__":
    job = normalize if "normalize" in sys.argv else run
    job("symbols")
    job("indices")
