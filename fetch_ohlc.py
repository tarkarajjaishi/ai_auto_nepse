"""Fetch daily OHLC for every symbol and index into Master_data/<kind>/<SYMBOL>/1D.txt."""

import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

MASTER = Path(__file__).parent / "Master_data"
FULL = "https://live.chukul.com/api/data/adjhistorydata/data/?symbol={}&v=1"  # whole history, adjusted, lags a few days
RECENT = "https://chukul.com/api/data/adjhistorydata/?symbol={}"  # last ~3 months, includes today
NPT = timezone(timedelta(hours=5, minutes=45))
HEADER = "date\topen\thigh\tlow\tclose\tchange\tpercent_change\tvolume\tamount"


def folder(kind, name):
    return MASTER / kind / name.replace("/", "-")  # a few debentures are named like NMBD87/88


def get(url, tries=3):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except OSError:  # covers URLError and the read timeouts the big payloads hit
            if attempt == tries - 1:
                raise


def bars(symbol):
    """{date: [open, high, low, close, volume, amount]}, oldest first."""
    col = lambda d, k, i: d[k][i] if i < len(d.get(k) or ()) else ""

    d = get(FULL.format(symbol))
    rows = {
        datetime.fromtimestamp(ts, NPT).strftime("%Y-%m-%d"): [
            col(d, "o", i), col(d, "h", i), col(d, "l", i),
            col(d, "c", i), col(d, "vol", i), col(d, "amt", i),
        ]
        for i, ts in enumerate(d["t"])
    }
    # ponytail: the snapshot above lags a few days, so glue on newer bars from the live
    # feed. Only strictly newer dates, to avoid mixing unadjusted prices into adjusted ones.
    newest = max(rows, default="")
    for r in get(RECENT.format(symbol)):
        if r["date"] > newest:
            rows[r["date"]] = [r["open"], r["high"], r["low"], r["close"],
                               r.get("volume", ""), r.get("amount", "")]
    return dict(sorted(rows.items()))


def safe_rewrite(path, header, rows, label, floor=0.5):
    """Replace a whole table — but refuse to overwrite a good archive with a failed fetch.

    Every full-rewrite writer in this project has the same failure mode: it persists whatever
    the feed happened to return, so a short or partial response silently truncates history and
    the run still exits 0. Losing the archive is far worse than skipping a refresh, so a result
    that has shrunk by more than `floor` is treated as a failed fetch rather than as the new
    truth. Shared by fetch_ohlc and fetch_swp because both learned it the same way.

    Returns True if written. A file that does not exist yet is always written — a first fetch
    has nothing to protect.
    """
    have = max(0, len(path.read_text(encoding="utf-8").splitlines()) - 1) if path.exists() else 0
    if have and len(rows) < have * floor:
        print(f"  {label}: REFUSED — {len(rows)} rows would replace {have}; keeping what is on "
              f"disk. Re-run when the feed is healthy.")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return True


def write(rows, path, header=HEADER):
    lines = []
    prev = None
    for date, (o, h, l, c, vol, amt) in rows.items():
        change = pct = ""
        if prev and c is not None:  # the feed does serve the odd null close
            change = round(c - prev, 2)
            pct = round(change / prev * 100, 2)
        prev = c if c is not None else prev
        lines.append("\t".join("" if v is None else str(v)
                               for v in (date, o, h, l, c, change, pct, vol, amt)))
    # This rewrites a symbol's ENTIRE price history from one response. `run()` only asserts the
    # result is non-empty, so a feed that answered with 50 bars instead of 1,600 passed that
    # check and truncated the file — the most valuable data in the project, silently, exit 0.
    return safe_rewrite(path, header, lines, path.parent.name)


def run(kind):
    names = (MASTER / f"{kind}.txt").read_text(encoding="utf-8").split()
    failed = []
    for i, name in enumerate(names, 1):
        try:
            rows = bars(name)
            assert rows, "no bars"
            write(rows, folder(kind, name) / "1D.txt")
            print(f"[{i}/{len(names)}] {name} {len(rows)} bars")
        except (OSError, KeyError, AssertionError, ValueError) as e:
            failed.append(name)
            print(f"[{i}/{len(names)}] {name} FAILED: {e}")
    if failed:
        print(f"{kind}: {len(failed)} failed -> {' '.join(failed)}")


if __name__ == "__main__":
    run("symbols")
    run("indices")
