"""Which weekdays the market trades, and whether the live feed is armed.

Both facts used to be hardcoded in three different places in ui.py as `weekday() in (4, 5)` —
the session banner, the cron scheduler and the archive-freshness counter each carried their own
copy, so a holiday or a schedule change meant editing all three and hoping none was missed.
This is the single switch they all read.

Kept deliberately dumb: two settings in one small .txt file, re-read on every call. It is a
~40-byte read, the callers are 1s fragments, and a cached copy would mean a toggle silently not
taking effect until something invalidated it — which is exactly the failure this replaces.
"""
from pathlib import Path

MASTER = Path(__file__).parent / "Master_data"
PATH = MASTER / "market_hours.txt"

# Python's weekday(): Mon=0 … Sun=6. NEPSE trades Sunday to Thursday and is shut Fri/Sat.
DEFAULT_OPEN = (6, 0, 1, 2, 3)
# Display order, Sunday first, as the pills read left to right.
WEEK = ((6, "Sun"), (0, "Mon"), (1, "Tue"), (2, "Wed"), (3, "Thu"), (4, "Fri"), (5, "Sat"))


def _read():
    """{key: value} from the settings file — missing or damaged file falls back to defaults."""
    out = {}
    try:
        for line in PATH.read_text(encoding="utf-8").splitlines():
            if "\t" in line:
                k, v = line.split("\t", 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def _write(settings):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text("".join("%s\t%s\n" % kv for kv in sorted(settings.items())), encoding="utf-8")


def open_days():
    """The weekdays the market is open, as a frozenset of Python weekday() numbers."""
    raw = _read().get("open_days")
    if raw is None:
        return frozenset(DEFAULT_OPEN)
    days = {int(t) for t in raw.split(",") if t.strip().isdigit() and 0 <= int(t) <= 6}
    return frozenset(days)                      # an empty set is legal: the market is never open


def set_open_days(days):
    s = _read()
    s["open_days"] = ",".join(str(d) for d in sorted(set(days)))
    _write(s)


def toggle_day(day):
    """Flip one weekday open/closed. Returns the new set."""
    days = set(open_days())
    days.symmetric_difference_update({int(day)})
    set_open_days(days)
    return frozenset(days)


def is_trading_day(when):
    """Is `when` (a date or datetime) a day the market trades at all?"""
    return when.weekday() in open_days()


def feed_on():
    """Is the NAASA live socket allowed to run? Default yes."""
    return _read().get("feed", "on").lower() != "off"


def set_feed_on(on):
    s = _read()
    s["feed"] = "on" if on else "off"
    _write(s)


def summary():
    """(open_day_count, 7, feed_on) for the status line."""
    return len(open_days()), 7, feed_on()


def demo():
    """Self-check: defaults, round-trips, and that a damaged file cannot take the market down."""
    import tempfile

    global PATH
    real = PATH
    try:
        with tempfile.TemporaryDirectory() as d:
            PATH = Path(d) / "market_hours.txt"

            # nothing saved yet -> NEPSE's real week, and the feed armed
            assert open_days() == frozenset({6, 0, 1, 2, 3}), open_days()
            assert 4 not in open_days() and 5 not in open_days(), "Fri/Sat are shut by default"
            assert feed_on() is True
            assert summary() == (5, 7, True), summary()

            assert 4 in toggle_day(4), "toggling Friday should open it"
            assert open_days() == frozenset({6, 0, 1, 2, 3, 4})
            assert 4 not in toggle_day(4), "toggling again should close it"

            set_feed_on(False)
            assert feed_on() is False
            assert open_days() == frozenset({6, 0, 1, 2, 3}), "feed toggle must not touch the days"
            set_feed_on(True)
            assert feed_on() is True

            # a date lands on the right side of the switch
            from datetime import date
            assert is_trading_day(date(2026, 8, 20)) is True, "Thursday trades"
            assert is_trading_day(date(2026, 8, 21)) is False, "Friday does not"

            # every day off is a legal configuration, not a crash
            set_open_days([])
            assert open_days() == frozenset()
            assert is_trading_day(date(2026, 8, 20)) is False

            # garbage in the file falls back rather than raising into the page
            PATH.write_text("open_days\tnonsense,,9,-1\nfeed\t\n", encoding="utf-8")
            assert open_days() == frozenset(), "only 0-6 survive"
            assert feed_on() is True, "a blank feed value is not 'off'"
    finally:
        PATH = real
    print("market_hours demo ok")


if __name__ == "__main__":
    demo()
