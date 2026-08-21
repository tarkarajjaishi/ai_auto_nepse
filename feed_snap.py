"""Put the live socket snapshot on disk, so a second process can read it.

NAASA allows **one session per account**. The socket therefore lives in exactly one process —
today the Streamlit app — and a second connection would evict the first. So the API cannot open
its own feed to serve live quotes to the Next terminal, and neither surface can simply be given
one: whichever connects second takes the feed away from the other.

The way out is one writer and one file. Whoever holds the socket writes what it sees here; every
reader reads it. `.txt`, per the storage rule, tab-separated with a header, written whole each
time — the file is one line per instrument and rewriting it is cheaper than trying to patch it.

**The staleness rule matters more here than anywhere else in the project.** A quote is only
meaningful with its age attached: a snapshot left over from Thursday's close renders exactly like
a live one, and this is the failure this project keeps finding in slower form. So the file carries
the moment it was written, `age()` is the first thing a reader asks, and `read()` refuses to
answer without it.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

MASTER = Path(__file__).parent / "Master_data"
PATH = MASTER / "feed_snapshot.txt"

# The fields the depth panel needs. Written in this order, and read back by NAME, so adding one
# later cannot shift the meaning of the others.
FIELDS = ("ltp", "close", "open", "high", "low", "volume", "turnover",
          "bid", "bid_qty", "ask", "ask_qty", "stamp")

# A quote older than this is not a live quote. Two minutes is generous for a socket that ticks
# sub-second, and short enough that a closed market is never dressed up as a running one.
FRESH_FOR = 120


def _num(v):
    try:
        f = float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return ""
    return "%g" % f


def _pick(quote):
    """One feed quote -> the values we store, in FIELDS order.

    The feed spells things its own way and not every instrument carries every field; an absent
    one is written empty rather than zero. Zero is a price.
    """
    g = quote.get
    return [
        _num(g("LTP")),
        _num(g("Close") or g("PreviousClose")),
        _num(g("Open")),
        _num(g("High")),
        _num(g("Low")),
        _num(g("Volume") or g("TotalQty")),
        _num(g("Turnover") or g("Amount")),
        _num(g("BidPrice") or g("Buy1")),
        _num(g("BidQty") or g("BuyQty1")),
        _num(g("OfferPrice") or g("Sell1")),
        _num(g("OfferQty") or g("SellQty1")),
        str(g("_t") or ""),
    ]


def write(snapshot, path=None):
    """Write the whole snapshot. Returns how many instruments were stored.

    Atomic: written to a temporary file and replaced, because a reader polling this at 1s would
    otherwise catch a half-written file and parse a truncated final row as a real quote.
    """
    path = Path(path or PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["written_at\t%.3f" % time.time(), "symbol\t" + "\t".join(FIELDS)]
    n = 0
    for name, quote in list(snapshot.items()):
        if not quote:
            continue
        lines.append(str(name) + "\t" + "\t".join(_pick(quote)))
        n += 1
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return n


def read(path=None):
    """{"written_at": float|None, "age": float|None, "fresh": bool, "quotes": {symbol: {...}}}.

    `age` and `fresh` are not decoration. A reader that renders `quotes` without them shows
    Thursday's close as a live price, which is precisely what this file must not enable.
    """
    path = Path(path or PATH)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {"written_at": None, "age": None, "fresh": False, "quotes": {}}
    if len(lines) < 2 or not lines[0].startswith("written_at\t"):
        return {"written_at": None, "age": None, "fresh": False, "quotes": {}}
    try:
        written_at = float(lines[0].split("\t", 1)[1])
    except (IndexError, ValueError):
        return {"written_at": None, "age": None, "fresh": False, "quotes": {}}

    header = lines[1].split("\t")[1:]
    quotes = {}
    for line in lines[2:]:
        f = line.split("\t")
        if len(f) < 2:
            continue
        row = {}
        for i, key in enumerate(header, start=1):
            v = f[i] if i < len(f) else ""
            if key == "stamp":
                row[key] = v or None
            else:
                try:
                    row[key] = float(v) if v != "" else None
                except ValueError:
                    row[key] = None
        quotes[f[0]] = row
    age = max(0.0, time.time() - written_at)
    return {"written_at": written_at, "age": round(age, 1), "fresh": age <= FRESH_FOR,
            "quotes": quotes}


def demo():
    """Self-check: round-trip, missing fields stay missing, and a stale file says so."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "feed_snapshot.txt"

        assert read(p) == {"written_at": None, "age": None, "fresh": False, "quotes": {}}, \
            "an absent file must read as empty and NOT fresh"

        snap = {
            "NABIL": {"LTP": "548.5", "Close": "550", "Open": "550", "High": "552",
                      "Low": "547", "Volume": "27241", "BidPrice": "548", "BidQty": "10",
                      "OfferPrice": "549", "OfferQty": "5", "_t": "21/08/2026 13:02:11"},
            "SAHAS": {"LTP": "701.8"},               # almost everything missing
            "EMPTY": {},                             # skipped entirely
        }
        assert write(snap, p) == 2, "an empty quote is not an instrument"

        out = read(p)
        assert out["fresh"] is True and out["age"] < 5, out
        n = out["quotes"]["NABIL"]
        assert n["ltp"] == 548.5 and n["close"] == 550.0 and n["bid"] == 548.0, n
        assert n["ask_qty"] == 5.0 and n["stamp"] == "21/08/2026 13:02:11", n

        s = out["quotes"]["SAHAS"]
        assert s["ltp"] == 701.8, s
        # A missing field must stay missing. Zero is a price, and "" -> 0.0 would print a bid of
        # zero as though the book were empty at any price.
        assert s["bid"] is None and s["volume"] is None, s

        # a snapshot from before the market closed must not read as live
        body = p.read_text(encoding="utf-8").splitlines()
        body[0] = "written_at\t%.3f" % (time.time() - FRESH_FOR - 10)
        p.write_text("\n".join(body) + "\n", encoding="utf-8")
        stale = read(p)
        assert stale["fresh"] is False and stale["age"] > FRESH_FOR, stale
        assert stale["quotes"]["NABIL"]["ltp"] == 548.5, "stale data is still returned, just flagged"

        # garbage must not raise into a page
        p.write_text("nonsense\n", encoding="utf-8")
        assert read(p)["fresh"] is False and read(p)["quotes"] == {}
    print("feed_snap demo ok")


if __name__ == "__main__":
    demo()
