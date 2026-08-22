"""Read the pre-computed .txt boards and hand them over as rows of dicts.

One reader for every board, because they all share a shape: a tab-separated file with a header
line. `ui.py` reads several of these POSITIONALLY (`int(r[9])`), which is exactly the fragility
test_ops now pins — the JSON API returns named fields so the frontend can never inherit that.
"""
from datetime import datetime, timedelta

import market_hours
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
    # Its own folder rather than a top-level .txt: the floorsheet engine writes a
    # board plus one detail file per symbol, and they belong together.
    "swing_quantam": "swing_quantam/board.txt",
    # One row per BROKER rather than per symbol — the market-wide 30-session footprint
    # behind the console's Strong Brokers tab. Same folder, same date-last shape.
    "swing_quantam_brokers": "swing_quantam/brokers.txt",
    # First-passage odds on each symbol's own zone ladder — see swing_quantam/
    # probability.py for what the number is and, more importantly, what it is not.
    "swing_quantam_probability": "swing_quantam/probability.txt",
}

# columns that are numbers, so the frontend never parses strings into floats itself
_TEXT = {"symbol", "date", "verdict", "decision", "grade", "performer", "trend", "stage",
         "structure", "breakout", "pullback", "setup", "flags", "signal", "confirmed",
         "in_zone", "direction", "state", "side", "broker", "variant", "split", "from", "to",
         "kind", "window", "sector", "lockin_expiry", "earn_period", "order",
         # scan.txt's chart badge and the date it printed. `badge_since` is a date, and a date
         # that survives _num() only by accident -- name it here rather than rely on that.
         "badge", "badge_since",
         # brokers.txt: a symbol column that is not called "symbol"
         "top_symbol"}


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
    benchmark = newest_completed()
    # `stale` is False when the session is UNKNOWN as well as when the board is current, and
    # those are not the same thing. Four boards shipped without a `date` column and every one of
    # them reported itself fresh forever — CLAUDE.md's named failure mode, four times over. The
    # producing scripts now emit `date`; this flag is here so the next board that forgets says
    # "I cannot tell" on screen instead of quietly claiming to be up to date.
    return {"rows": rows, "columns": cols, "session": session,
            # Against the newest FINISHED session, not the newest bar. See newest_completed().
            "stale": bool(session and benchmark and session < benchmark),
            "session_unknown": bool(rows) and session is None,
            "archive_session": newest, "missing": False}


def newest_completed():
    """The newest bar that belongs to a session that has FINISHED.

    `newest_bar()` is what is on disk, and from 11:00 that includes today's partial bar because
    the live writer keeps it current. A board rebuilt after last night's close is not out of date
    just because a newer bar is still being written — judging it against that marked all eleven
    boards stale from the opening bell.

    So: today counts once the market has closed; while it is still trading the yardstick is the
    previous trading day, taken from the same open/closed switch as everything else.
    """
    newest = newest_bar()
    if not newest:
        return newest
    today = datetime.now(market_hours.NPT).date()
    if newest != today.isoformat():
        return newest                       # the archive has not reached today; nothing to adjust
    if market_hours.session_now()[0] == "CLOSED":
        return newest                       # today is over, so today is a fair comparison
    day = today - timedelta(days=1)
    for _ in range(14):                     # a bounded walk: holidays cannot spin this forever
        if market_hours.is_trading_day(day):
            return day.isoformat()
        day -= timedelta(days=1)
    return newest


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
