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


def row(quote, day):
    """The archive's 9 columns for one instrument's live quote, or None if it cannot be trusted.

    `quote` is the socket snapshot for one symbol (FEED_SCHEMA field names). Returns None when
    the instrument has not traded today — no LTP, or no Open — rather than writing a hollow row
    that would read downstream as a real session at price 0.
    """
    ltp, opn = _num(quote.get("LTP")), _num(quote.get("Open"))
    if not ltp or opn is None:
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
    """Every instrument worth subscribing to: the archive's own symbol and index lists."""
    syms = MASTER.joinpath("symbols.txt").read_text(encoding="utf-8").split()
    return syms + sorted(_index_names())


def flush(snapshot, day=None):
    """Upsert today's bar for every instrument in `snapshot` whose row has changed.

    The caller decides WHEN — this writes whatever it is given. Gate it on market hours, or a
    stale snapshot will keep rewriting a finished session over the daily fetch's official bar.
    Returns (files_written, instruments_seen).
    """
    day = day or today()
    written = 0
    for name, quote in list(snapshot.items()):
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
    print("live_1d demo ok")


if __name__ == "__main__":
    demo()
