"""Keep TODAY's daily bar current in the archive, straight off the NAASA socket.

`fetch_ohlc` writes 1D.txt once a day, so during a session every file stops at the previous
close and nothing that reads the archive — charts, scanners, the boards, the backtest — can see
today at all. The socket already pushes today's Open/High/Low/LTP/TTQ for every instrument, so
today's bar is not computed here: it is read off the feed and upserted as the last row.

Two things this deliberately does NOT do:

* It does not build a bar from ticks. NAASA sends the day's OHLC as fields, already correct
  across halts, auctions and the pre-open cross. Re-deriving them from LTP prints would be a
  second, worse implementation of something the exchange already publishes.
* It does not write on every tick. Only the last line of a file ever changes, but rewriting
  ~360 files of a few thousand rows once a second is ~35 MB/s of pointless I/O. The live values
  live in memory at feed speed; `flush` batches them to disk and skips instruments whose row is
  byte-identical to the one already written.

The archive is the most valuable thing in this project and a full-rewrite writer already
truncated it once (see fetch_ohlc.safe_rewrite), so `upsert` refuses to write anything that
would leave a file shorter than it found it.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

MASTER = Path(__file__).parent / "Master_data"
NPT = timezone(timedelta(hours=5, minutes=45))
HEADER = "date\topen\thigh\tlow\tclose\tchange\tpercent_change\tvolume\tamount"
COLUMNS = 9

_written = {}          # name -> the row last flushed, so an unchanged instrument costs nothing

# The socket abbreviates several index names differently from the archive directories. Subscribe
# under the wrong one and the feed never answers — no error, no frame, exactly like an index that
# has not moved. That silently cost the eight biggest sector indices their live bar while the six
# whose names happen to match worked fine, so the mismatch looked like quiet sectors.
FEED_ALIAS = {
    "BANKINGIND": "BANKSUBIND",
    "FINANCEIND": "FININD",
    "HYDROPOWIND": "HYDPOWIND",
    "LIFEINSUIND": "LIFINSIND",
    "MANUFACTUREIND": "MANPROCIND",
    "MICROFININD": "MICRFININD",
    "NONLIFEIND": "NONLIFIND",
    "TRADINGIND": "TRADIND",
}
_ARCHIVE_OF = {v: k for k, v in FEED_ALIAS.items()}


def feed_name(name):
    """The key this instrument ticks under on the socket."""
    return FEED_ALIAS.get(name, name)


def archive_name(name):
    """Inverse of feed_name — the archive directory for an instrument as the feed names it."""
    return _ARCHIVE_OF.get(name, name)


def _num(x):
    """A feed value as a float, or None when it is blank/absent/unparseable."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


def _fmt(v):
    """Match the archive's existing style: plain floats, no scientific notation, blank for None."""
    if v is None:
        return ""
    v = round(v, 2)
    return str(int(v)) + ".0" if v == int(v) else str(v)


def today(now=None):
    return (now or datetime.now(NPT)).date().isoformat()


def _same_day(stamp, day):
    """Is this quote's own timestamp from `day`? True / False / None when it cannot be told.

    The feed stamps `DD/MM/YYYY HH:MM:SS`, NOT ISO. Comparing the raw first ten characters to an
    ISO date never matches, so a guard written that way would reject every quote and silently
    stop the archiver dead — a failure that looks exactly like a quiet market.
    """
    head = (stamp or "").strip()[:10]
    if not head:
        return None
    try:
        d, m, y = head.split("/")
        return "%s-%s-%s" % (y, m, d) == day
    except ValueError:
        return None


def row(quote, day):
    """The archive's 9 columns for one instrument's live quote, or None if it cannot be trusted.

    `quote` is the socket snapshot for one symbol (FEED_SCHEMA field names). Returns None when
    the instrument has not traded today — no LTP, or no Open — rather than writing a hollow row
    that would read downstream as a real session at price 0.
    """
    ltp, opn = _num(quote.get("LTP")), _num(quote.get("Open"))
    if not ltp or opn is None:
        return None
    # The socket snapshot lives for the life of the process and is never cleared, so yesterday's
    # quote survives into today. Stamping that with today() writes a bar for a session that did
    # not happen — into ~364 files at once, on any day the clock says LIVE. Trust the feed's own
    # timestamp over our clock. Only a POSITIVE mismatch blocks: a quote with no readable stamp
    # is written as before, so a format change degrades to the old behaviour instead of silently
    # archiving nothing.
    if _same_day(quote.get("_t"), day) is False:
        return None
    high, low = _num(quote.get("High")), _num(quote.get("Low"))
    prev = _num(quote.get("Close"))              # the feed's `Close` is the PREVIOUS close
    vol = _num(quote.get("TTQ"))
    # Indices publish turnover directly as TTV; a stock quote carries no turnover field at all,
    # so it is the volume-weighted average price times volume (see naasa.FEED_SCHEMA).
    amount = _num(quote.get("TTV"))
    if amount is None and vol is not None:
        wap = _num(quote.get("WeightedAverage"))
        amount = vol * wap if wap else None
    change = pct = None
    if prev:
        change = ltp - prev
        pct = change / prev * 100
    return "\t".join(_fmt(v) if i else v for i, v in enumerate(
        (day, opn, high, low, ltp, change, pct, vol, amount)))


def upsert(path, day, line):
    """Replace today's row in `path`, or append it. True if the file changed.

    Refuses to shorten a file: this runs unattended against the archive, and the one failure
    mode that actually costs something here is losing history, not missing a refresh.
    """
    if not path.exists():
        return False                     # never conjure a history from one live bar
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    before = len(lines)
    if lines and lines[-1].startswith(day + "\t"):
        if lines[-1] == line:
            return False
        lines[-1] = line
    else:
        lines.append(line)
    if len(lines) < before:
        raise AssertionError("%s: upsert would drop rows (%d -> %d)" % (path, before, len(lines)))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def folder(name):
    """Where an instrument's 1D.txt lives — indices and symbols are separate trees."""
    kind = "indices" if name in _index_names() else "symbols"
    return MASTER / kind / name.replace("/", "-")


def _index_names(_cache=[]):
    if not _cache:
        _cache.append(set(MASTER.joinpath("indices.txt").read_text(encoding="utf-8").split()))
    return _cache[0]


def names():
    """Every instrument worth subscribing to, named the way the FEED names them — this list goes
    straight into the subscribe frame, so archive names would silently fetch nothing."""
    syms = MASTER.joinpath("symbols.txt").read_text(encoding="utf-8").split()
    return [feed_name(n) for n in syms + sorted(_index_names())]


def flush(snapshot, day=None):
    """Upsert today's bar for every instrument in `snapshot` whose row has changed.

    The caller decides WHEN — this writes whatever it is given. Gate it on market hours, or a
    stale snapshot will keep rewriting a finished session over the daily fetch's official bar.
    Returns (files_written, instruments_seen).
    """
    day = day or today()
    written = 0
    for name, quote in list(snapshot.items()):
        name = archive_name(name)      # the snapshot is keyed the way the FEED names things
        line = row(quote, day)
        if line is None or _written.get(name) == line:
            continue
        try:
            if upsert(folder(name) / "1D.txt", day, line):
                written += 1
            _written[name] = line
        except (OSError, AssertionError) as e:
            print("live_1d: %s not written — %s" % (name, e))
    return written, len(snapshot)


def demo():
    """Self-check: the bar maths, and that an upsert replaces today rather than piling up."""
    import tempfile

    q = {"LTP": "549", "Open": "555", "High": "555", "Low": "548", "Close": "550",
         "TTQ": "36097", "WeightedAverage": "550.6"}
    got = row(q, "2026-08-20").split("\t")
    assert got[0] == "2026-08-20", got
    assert got[1:5] == ["555.0", "555.0", "548.0", "549.0"], got[1:5]
    assert got[5] == "-1.0", got                       # 549 - 550, vs the PREVIOUS close
    assert got[6] == "-0.18", got                      # and the percentage of it
    assert got[7] == "36097.0", got
    assert got[8] == _fmt(36097 * 550.6), got          # turnover = volume x VWAP
    assert len(got) == COLUMNS, got

    # an index carries turnover directly and has no VWAP
    idx = row({"LTP": "2614.11", "Open": "2622.84", "High": "2624.96", "Low": "2611.03",
               "Close": "2610.0", "TTQ": "0", "TTV": "8602127"}, "2026-08-20").split("\t")
    assert idx[8] == "8602127.0", idx

    # an instrument that has not traded must produce nothing, not a row of zeros
    assert row({"LTP": "0", "Open": "0"}, "2026-08-20") is None

    # a quote the FEED dates to another session must never be stamped with today
    stale = dict(q, _t="19/08/2026 14:59:31")
    assert row(stale, "2026-08-20") is None, "yesterday's snapshot must not become today's bar"
    fresh = dict(q, _t="20/08/2026 11:55:35")
    assert row(fresh, "2026-08-20") is not None, "today's own quote must still be written"
    assert _same_day("20/08/2026 11:55:35", "2026-08-20") is True
    assert _same_day("19/08/2026 14:59:31", "2026-08-20") is False
    assert _same_day("", "2026-08-20") is None and _same_day(None, "2026-08-20") is None
    assert _same_day("2026-08-20 11:00:00", "2026-08-20") is None,         "ISO in, ISO out would silently pass — the feed sends DD/MM/YYYY"
    assert row({"Open": "100"}, "2026-08-20") is None

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "1D.txt"
        p.write_text(HEADER + "\n2026-08-18\t555.0\t555.0\t548.0\t550.0\t\t\t36097.0\t\n",
                     encoding="utf-8")
        assert upsert(p, "2026-08-20", "\t".join(["2026-08-20"] + ["1"] * 8)) is True
        assert len(p.read_text(encoding="utf-8").splitlines()) == 3, "today should APPEND"
        assert upsert(p, "2026-08-20", "\t".join(["2026-08-20"] + ["2"] * 8)) is True
        assert len(p.read_text(encoding="utf-8").splitlines()) == 3, "today must REPLACE, not pile"
        assert upsert(p, "2026-08-20", "\t".join(["2026-08-20"] + ["2"] * 8)) is False, "no-op"
        assert p.read_text(encoding="utf-8").splitlines()[1].startswith("2026-08-18"), "kept history"
        # a file we have never fetched is not seeded from one live bar
        assert upsert(Path(d) / "nope" / "1D.txt", "2026-08-20", "x") is False
    # the archive<->feed name bridge: a wrong name here is a silently missing index
    assert feed_name("MICROFININD") == "MICRFININD"
    assert archive_name("MICRFININD") == "MICROFININD"
    assert feed_name("NEPSE") == "NEPSE" and archive_name("NEPSE") == "NEPSE"
    for arch, fed in FEED_ALIAS.items():
        assert archive_name(fed) == arch, (arch, fed)
        assert feed_name(arch) == fed, (arch, fed)
    import naasa
    known = set(naasa.INDEX_SYMBOLS)
    for arch, fed in FEED_ALIAS.items():
        assert fed in known, "%s is not a name the feed publishes" % fed
    for arch in _index_names():
        assert feed_name(arch) in known, \
            "%s has no feed name — it would subscribe to nothing and never get a bar" % arch
    print("live_1d demo ok")


if __name__ == "__main__":
    demo()
