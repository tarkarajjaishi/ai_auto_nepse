"""Which weekdays the market trades, and whether the live feed is armed.

Both facts used to be hardcoded in three different places in ui.py as `weekday() in (4, 5)` —
the session banner, the cron scheduler and the archive-freshness counter each carried their own
copy, so a holiday or a schedule change meant editing all three and hoping none was missed.
This is the single switch they all read.

Kept deliberately dumb: two settings in one small .txt file, re-read on every call. It is a
~40-byte read, the callers are 1s fragments, and a cached copy would mean a toggle silently not
taking effect until something invalidated it — which is exactly the failure this replaces.
"""
from datetime import date as _date, datetime, timedelta, timezone
from pathlib import Path

MASTER = Path(__file__).parent / "Master_data"
PATH = MASTER / "market_hours.txt"

# Python's weekday(): Mon=0 … Sun=6.
#
# NEPSE trades **Monday to Friday**. It used to be Sunday to Thursday and much of this codebase
# still assumes that, but the archive disagrees and the archive wins: NABIL's last Sunday session
# is 2026-04-05, and since 2026-06-01 four independent liquid symbols each show Mon=12 Tue=12
# Wed=11 Thu=11 Fri=11 and Sun=0. Check the data before changing this back --
#   python -c "import collections,datetime,pathlib;DAY=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
#   c=collections.Counter(DAY[datetime.date.fromisoformat(l.split(chr(9))[0]).weekday()]
#   for l in pathlib.Path('Master_data/symbols/NABIL/1D.txt').read_text().splitlines()[1:]
#   if l[:4]=='2026');print(c)"
# Getting this wrong is not cosmetic: a real trading day marked closed stops the crons and the
# archiver, and a non-trading day marked open lets a stale snapshot be written as a real session.
DEFAULT_OPEN = (0, 1, 2, 3, 4)
# Nepal has no DST, so a fixed offset is the whole story. The VPS runs UTC and is a day
# out for ~6h daily, which is why every date question here resolves in NPT.
NPT = timezone(timedelta(hours=5, minutes=45))
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


# NEPSE's day, in NPT minutes past midnight. Pre-open is a real state, not a nicety: orders can
# be entered 10:30-10:45 but nothing matches until 11:00, so a screen that calls it "closed" is
# telling a trader they cannot do the one thing they can.
PRE_OPEN = (10 * 60 + 30, 10 * 60 + 46)     # 10:30-10:45, entry allowed
PRE_CLOSE = (10 * 60 + 46, 11 * 60)         # 10:46-10:59, matching, no new entry
LIVE = (11 * 60, 15 * 60)                   # 11:00-15:00, continuous trading


def session_now(when=None):
    """(state, minutes_to_next) for NEPSE right now: PRE-OPEN, PRE-OPEN CLOSE, LIVE or CLOSED.

    Reads the same open/closed switch as everything else, so a day toggled shut here is shut
    everywhere. `minutes_to_next` counts to the next state change on a trading day, and is None
    once the session is over or the market is shut.
    """
    now = when or datetime.now(NPT)
    if not is_trading_day(now):
        return "CLOSED", None
    mins = now.hour * 60 + now.minute
    if PRE_OPEN[0] <= mins < PRE_OPEN[1]:
        return "PRE-OPEN", PRE_OPEN[1] - mins
    if PRE_CLOSE[0] <= mins < PRE_CLOSE[1]:
        return "PRE-OPEN CLOSE", PRE_CLOSE[1] - mins
    if LIVE[0] <= mins < LIVE[1]:
        return "LIVE", LIVE[1] - mins
    if mins < PRE_OPEN[0]:
        return "CLOSED", PRE_OPEN[0] - mins
    return "CLOSED", None


def missed_sessions(newest, today=None):
    """How many trading sessions have passed since `newest` (an ISO date string).

    Calendar age lies about a market: data from Friday read on Monday is three days old and
    perfectly current, while Tuesday data read on Thursday has missed a real session. Only days
    the market actually opens count, so this walks the calendar through `is_trading_day` rather
    than subtracting dates.

    Returns None when `newest` is missing or unparseable — "I cannot tell" is a different answer
    from "nothing is missing", and collapsing the two is how a stalled pipeline reads as fresh.

    This is the second half of the freshness verdict, and the half that works. Comparing the
    boards to EACH OTHER cannot see a whole-pipeline stall: every store freezes in lockstep, they
    all still agree, and the screen goes green over week-old prices. Only the calendar notices.
    """
    try:
        d = _date.fromisoformat(str(newest))
    except (TypeError, ValueError):
        return None
    if today is None:
        today = datetime.now(NPT).date()
    n = 0
    while d < today:
        d += timedelta(days=1)
        if is_trading_day(d):
            n += 1
    return n


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

            # nothing saved yet -> NEPSE's real week (Mon-Fri), and the feed armed
            assert open_days() == frozenset({0, 1, 2, 3, 4}), open_days()
            assert 5 not in open_days() and 6 not in open_days(), "Sat/Sun are shut by default"
            assert feed_on() is True
            assert summary() == (5, 7, True), summary()

            assert 6 in toggle_day(6), "toggling Sunday should open it"
            assert open_days() == frozenset({0, 1, 2, 3, 4, 6})
            assert 6 not in toggle_day(6), "toggling again should close it"

            set_feed_on(False)
            assert feed_on() is False
            assert open_days() == frozenset({0, 1, 2, 3, 4}), "feed toggle must not touch the days"
            set_feed_on(True)
            assert feed_on() is True

            # a date lands on the right side of the switch
            from datetime import date
            assert is_trading_day(date(2026, 8, 20)) is True, "Thursday trades"
            assert is_trading_day(date(2026, 8, 21)) is True, "Friday trades too, since 2026"
            assert is_trading_day(date(2026, 8, 22)) is False, "Saturday does not"
            assert is_trading_day(date(2026, 8, 23)) is False, "nor Sunday, any more"

            # every day off is a legal configuration, not a crash
            set_open_days([])
            assert open_days() == frozenset()
            assert is_trading_day(date(2026, 8, 20)) is False

            # missed_sessions counts SESSIONS, not days off the calendar
            set_open_days(DEFAULT_OPEN)
            fri, mon = date(2026, 8, 21), date(2026, 8, 24)
            assert missed_sessions("2026-08-21", today=fri) == 0, "same day has missed nothing"
            assert missed_sessions("2026-08-21", today=mon) == 1,                 "Fri -> Mon is 3 calendar days but only ONE session"
            assert missed_sessions("2026-08-18", today=fri) == 3, missed_sessions("2026-08-18", today=fri)
            # unknown must stay unknown -- None, never 0, or a stalled pipeline reads as fresh
            for bad in (None, "", "not-a-date", "2026-13-99"):
                assert missed_sessions(bad, today=fri) is None, bad
            # a market that never opens can never miss a session
            set_open_days([])
            assert missed_sessions("2026-08-18", today=fri) == 0
            set_open_days(DEFAULT_OPEN)

            # the session clock: every state, and the switch still governs it
            from datetime import datetime as _dt
            def at(h, m, day=21):
                return _dt(2026, 8, day, h, m, tzinfo=NPT)
            set_open_days(DEFAULT_OPEN)
            assert session_now(at(9, 0))[0] == "CLOSED", "before pre-open"
            assert session_now(at(10, 30))[0] == "PRE-OPEN", "entry opens at 10:30"
            assert session_now(at(10, 45))[0] == "PRE-OPEN", "10:45 is still entry"
            assert session_now(at(10, 46))[0] == "PRE-OPEN CLOSE", "matching, no new entry"
            assert session_now(at(11, 0))[0] == "LIVE"
            assert session_now(at(14, 59))[0] == "LIVE"
            assert session_now(at(15, 0))[0] == "CLOSED", "15:00 is the close, not still live"
            assert session_now(at(12, 0))[1] == 180, session_now(at(12, 0))
            # a day the switch has closed is CLOSED whatever the clock says
            assert session_now(at(12, 0, day=22))[0] == "CLOSED", "Saturday"
            set_open_days([])
            assert session_now(at(12, 0))[0] == "CLOSED", "no open days means never open"
            set_open_days(DEFAULT_OPEN)

            # garbage in the file falls back rather than raising into the page
            PATH.write_text("open_days\tnonsense,,9,-1\nfeed\t\n", encoding="utf-8")
            assert open_days() == frozenset(), "only 0-6 survive"
            assert feed_on() is True, "a blank feed value is not 'off'"
    finally:
        PATH = real
    print("market_hours demo ok")


if __name__ == "__main__":
    demo()
