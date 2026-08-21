"""Per-STORE archive freshness — the half of the Cron page that is not about boards.

A board is a table somebody's script wrote. A *store* is the raw archive underneath it: the daily
bars, the minute bars, the floorsheet, the broker-flow files. Boards can all agree with each other
and still be built on a store that stopped updating a week ago, which is exactly the blind spot
`missed_sessions` was added to close at the archive level and this closes per store.

`behind` counts members that fall short of the newest date any member reached. Read it as a prompt
to look, not an error: a symbol that simply did not trade that session counts as behind, and on
NEPSE most of them do.

Ported from ui.py's `archive_state`, with one change — the two flat files resolve their date
column out of the HEADER by name instead of by a hardcoded index. That is the fragility this API
exists to escape, and appending a column to scan.txt already moved a positional read once.
"""
from fetch_ohlc import MASTER

# Every store, and how to date it. The Cron page prints these labels in this order.
LABELS = ("Daily bars", "Minute bars", "Floorsheet", "Broker flow", "Volume spike", "Signal scan")

_cache = {"stamp": None, "value": None}


def _mtime(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _stamp():
    """Cheap fingerprint of everything below, so a page poll does not re-walk 700 files."""
    return tuple(_mtime(MASTER / p) for p in
                 ("symbols", "indices", "floorsheet", "broker_flow",
                  "volume_spike.txt", "scan.txt"))


def _last_line(path, tail=4096):
    """Final line of a file without reading the whole thing — these archives are large."""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - tail))
            rows = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return ""
    return rows[-1] if rows else ""


def _by_date(path, column):
    """(newest date, members not at it, total) for a flat board file.

    MAX over the column, never the last line: these files are sorted by rank, not by date, so the
    physical last row is routinely an older session. The column is found by NAME — scan.txt grew
    four columns and a hardcoded index would have started reading a price as a date.
    """
    if not path.exists():
        return "", 0, 0
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 2:
        return "", 0, 0
    header = lines[0].split("\t")
    if column not in header:
        return "", 0, 0
    i = header.index(column)
    dates = [f[i] for f in (l.split("\t") for l in lines[1:]) if len(f) > i and f[i]]
    newest = max(dates, default="")
    return newest, sum(1 for d in dates if d != newest), len(dates)


def _per_symbol(filename):
    """(newest, behind, total) across every symbol and index folder holding `filename`."""
    stamps = []
    for kind in ("symbols", "indices"):
        root = MASTER / kind
        if not root.exists():
            continue
        for d in root.glob("*"):
            line = _last_line(d / filename)
            if line:
                # Minute bars stamp "2026-08-18 15:00:00", daily bars "2026-08-18".
                # Compare DATES: at full-timestamp precision every symbol that did not
                # trade in the closing minute counts as behind -- 105 of 364 here -- and
                # that says nothing. The question is "does this store have the newest
                # session", which is a date question.
                stamps.append(line.split("\t")[0][:10])
    newest = max(stamps, default="")
    return newest, sum(1 for s in stamps if s != newest), len(stamps)


def _floorsheet():
    """The floorsheet is one file per session per symbol, so its date is the filename."""
    root = MASTER / "floorsheet"
    days = []
    if root.exists():
        for d in root.iterdir():
            if d.is_dir():
                # max(), not sorted()[-1]: these directories hold one file per session,
                # and sorting every one of them was the slowest thing on this page.
                newest_day = max((f.stem for f in d.glob("*.txt")), default="")
                if newest_day:
                    days.append(newest_day)
    newest = max(days, default="")
    return newest, sum(1 for d in days if d != newest), len(days)


def _broker_flow():
    root = MASTER / "broker_flow"
    stamps = [_last_line(f).split("\t")[0][:10] for f in root.glob("*.txt")] if root.exists() else []
    stamps = [s for s in stamps if s]
    newest = max(stamps, default="")
    return newest, sum(1 for s in stamps if s != newest), len(stamps)


def state():
    """{label: {newest, behind, total}} for every store. Cached on the archive's own mtimes."""
    stamp = _stamp()
    if _cache["stamp"] == stamp:
        return _cache["value"]
    raw = {
        "Daily bars": _per_symbol("1D.txt"),
        "Minute bars": _per_symbol("1minutes.txt"),
        "Floorsheet": _floorsheet(),
        "Broker flow": _broker_flow(),
        "Volume spike": _by_date(MASTER / "volume_spike.txt", "date"),
        "Signal scan": _by_date(MASTER / "scan.txt", "date"),
    }
    out = {k: {"newest": n or None, "behind": b, "total": t} for k, (n, b, t) in raw.items()}
    _cache.update(stamp=stamp, value=out)
    return out


def demo():
    """Self-check: every store is reported, and a column is found by name rather than position."""
    s = state()
    assert set(s) == set(LABELS), sorted(s)
    for label, v in s.items():
        assert set(v) == {"newest", "behind", "total"}, (label, v)
        assert v["behind"] <= v["total"], label
        assert v["newest"] is None or len(v["newest"]) == 10, (label, v["newest"])

    # the date column must be located by NAME: scan.txt already grew four columns once, and a
    # hardcoded index would silently start reading something else as a date
    scan = MASTER / "scan.txt"
    if scan.exists():
        header = scan.read_text(encoding="utf-8").splitlines()[0].split("\t")
        assert header.index("date") == 1, "scan.txt's date moved — by-name lookup still finds it"
        assert _by_date(scan, "nosuchcolumn") == ("", 0, 0), "an absent column must not raise"
    assert _by_date(MASTER / "does_not_exist.txt", "date") == ("", 0, 0)

    # Minute bars carry a full timestamp; if that ever reaches the comparison again,
    # "behind" silently becomes "did not trade in the closing minute" for every store.
    assert all(v["newest"] is None or len(v["newest"]) == 10 for v in s.values()), s
    assert state() is _cache["value"], "a second call must come from the cache"
    print("stores demo ok")


if __name__ == "__main__":
    demo()
