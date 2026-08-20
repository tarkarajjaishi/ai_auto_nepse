"""Read the pre-computed .txt boards and hand them over as rows of dicts.

One reader for every board, because they all share a shape: a tab-separated file with a header
line. `ui.py` reads several of these POSITIONALLY (`int(r[9])`), which is exactly the fragility
test_ops now pins — the JSON API returns named fields so the frontend can never inherit that.
"""
from fetch_ohlc import MASTER

# board -> the file it reads. The frontend asks for the board, not the filename.
BOARDS = {
    "swing_pro": "swing_pro.txt",
    "supply_demand": "supply_demand.txt",
    "scan": "scan.txt",
    "volume_spike": "volume_spike.txt",
    "operator_scan": "operator_scan.txt",
    "operator_now": "operator_now.txt",
    "operator_verdict": "operator_verdict.txt",
    "master_signal": "master_signal.txt",
    "swing_master": "swing_master.txt",
    "backtest": "backtest.txt",
}

# columns that are numbers, so the frontend never parses strings into floats itself
_TEXT = {"symbol", "date", "verdict", "decision", "grade", "performer", "trend", "stage",
         "structure", "breakout", "pullback", "setup", "flags", "signal", "confirmed",
         "in_zone", "direction", "state", "side", "broker", "variant", "split", "from", "to",
         "kind", "window", "sector", "lockin_expiry", "earn_period", "order"}


def _num(v):
    if v in ("", "None", "-", None):
        return None
    try:
        f = float(v)
    except ValueError:
        return v
    return int(f) if f.is_integer() and abs(f) < 2 ** 53 else f


def read(board):
    """{"rows": [...], "columns": [...], "session": "YYYY-MM-DD"|None, "stale": bool}

    `session` is the newest date the board itself carries. `stale` compares that against the
    archive's newest daily bar — a board is only as fresh as the last time its script ran, and
    presenting yesterday's analysis as current is the failure this project keeps hitting.
    """
    name = BOARDS.get(board)
    if not name:
        return None
    p = MASTER / name
    if not p.exists():
        return {"rows": [], "columns": [], "session": None, "stale": False, "missing": True}
    lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    if len(lines) < 2:
        return {"rows": [], "columns": [], "session": None, "stale": False, "missing": False}
    cols = lines[0].split("\t")
    rows = []
    for line in lines[1:]:
        f = line.split("\t")
        if len(f) != len(cols):
            continue                      # a short row is corrupt, not a row with blanks
        rows.append({c: (f[i] if c in _TEXT else _num(f[i])) for i, c in enumerate(cols)})
    session = max((r["date"] for r in rows if r.get("date")), default=None)
    newest = newest_bar()
    # `stale` is False when the session is UNKNOWN as well as when the board is current, and
    # those are not the same thing. Four boards shipped without a `date` column and every one of
    # them reported itself fresh forever — CLAUDE.md's named failure mode, four times over. The
    # producing scripts now emit `date`; this flag is here so the next board that forgets says
    # "I cannot tell" on screen instead of quietly claiming to be up to date.
    return {"rows": rows, "columns": cols, "session": session,
            "stale": bool(session and newest and session < newest),
            "session_unknown": bool(rows) and session is None,
            "archive_session": newest, "missing": False}


_bar_cache = {"stamp": None, "value": None}


def newest_bar():
    """The newest date any symbol has a daily bar for. Cached on the directory's mtime, because
    364 tail-reads per request would make every board call cost more than the board itself."""
    d = MASTER / "symbols"
    if not d.exists():
        return None
    stamp = d.stat().st_mtime
    if _bar_cache["stamp"] == stamp:
        return _bar_cache["value"]
    newest = ""
    for sym in d.glob("*/1D.txt"):
        try:
            with sym.open("rb") as fh:
                fh.seek(0, 2)
                fh.seek(max(0, fh.tell() - 512))
                tail = fh.read().decode("utf-8", "replace").splitlines()
        except OSError:
            continue
        if tail:
            newest = max(newest, tail[-1].split("\t")[0])
    _bar_cache.update(stamp=stamp, value=newest or None)
    return _bar_cache["value"]
