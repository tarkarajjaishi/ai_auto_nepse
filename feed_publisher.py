"""Hold the NAASA socket and publish what it sees. One process, run as a service.

**Why this exists.** NAASA allows one live session per account, so exactly one process may hold
the socket. Until now that process was Streamlit — `naasa_feed()` is an `@st.cache_resource`
thread, so it only ran once a browser had actually driven a script run, and it died with every
service restart. The Next terminal reads `feed_snapshot.txt`, so its live prices silently depended
on somebody having the *other* app open. After a deploy at 06:50 the feed simply never came back
and the chart sat on the previous close with nothing on screen saying why.

A feed is infrastructure. It should not be a side effect of someone looking at a page.

So: this daemon owns the socket, and everything else — the Next API via `/api/bar` and
`/api/quotes`, and `ui.py` — reads the file it writes. Do not let anything else open a socket at
the same time; whichever connects second evicts the first and they will fight forever.

It publishes two things, on two clocks:

  * `feed_snapshot.txt` every PUBLISH seconds — what a screen reads
  * today's bar into each `1D.txt` every FLUSH seconds — what the archive keeps

Both are already-written functions (`feed_snap.write`, `live_1d.flush`). This file is the socket
and the two timers around them; it computes nothing itself, on purpose.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

import feed_snap
import live_1d
import market_hours
import naasa

PUBLISH = 1.0        # snapshot -> disk. A screen polls at 1s; publishing slower just adds latency.
FLUSH = 20           # today's bar -> the archive, matching ui.py's LIVE_1D_FLUSH
IDLE = 2.0           # how often to re-check the switches while the market is shut
RETRY = 5            # after a socket error

_state = {"snap": {}, "status": "starting", "err": "", "ticks": 0,
          "lock": threading.Lock(), "published": 0, "flushed": 0}


def _armed():
    """Should the socket be open at all? The Trading days pills must actually govern this."""
    return (market_hours.feed_on()
            and market_hours.is_trading_day(datetime.now(market_hours.NPT)))


def _on_tick(rec):
    with _state["lock"]:
        _state["status"] = "live"       # only a delivered tick proves the feed is live
        _state["ticks"] += 1
        d = _state["snap"].setdefault(rec["symbol"], {})
        d.update(rec["fields"])
        d["_t"] = rec.get("time", "")


def _snapshot():
    with _state["lock"]:
        return {k: dict(v) for k, v in _state["snap"].items()}


def _publisher():
    """Write the snapshot for every other process to read."""
    while True:
        time.sleep(PUBLISH)
        try:
            snap = _snapshot()
            if snap:
                _state["published"] = feed_snap.write(snap)
        except Exception as e:                      # publishing must never kill the feed
            _state["err"] = "publish: %s" % str(e)[:90]


def _archiver():
    """Keep today's bar current in every 1D.txt, but only while the market is actually LIVE.

    A snapshot left over from a finished session would otherwise keep rewriting today's row on
    top of the official bar the daily fetch writes after the close.
    """
    while True:
        time.sleep(FLUSH)
        try:
            if market_hours.session_now()[0] != "LIVE":
                continue
            written, _ = live_1d.flush(_snapshot())
            if written:
                _state["flushed"] = written
        except Exception as e:
            _state["err"] = "archive: %s" % str(e)[:90]


def main():
    threading.Thread(target=_publisher, daemon=True).start()
    threading.Thread(target=_archiver, daemon=True).start()
    subs = tuple(live_1d.names())
    print("feed publisher: %d instruments, publishing every %gs, flushing every %ds"
          % (len(subs), PUBLISH, FLUSH), flush=True)

    while True:
        if not _armed():
            if _state["status"] != "off":
                _state["status"] = "off"
                print("feed: off (market closed or feed switched off)", flush=True)
            time.sleep(IDLE)
            continue

        email, password = naasa.load_credentials()
        if not (email and password):
            if _state["status"] != "no login saved":
                _state["status"] = "no login saved"
                print("feed: no NAASA login saved — idling", flush=True)
            time.sleep(IDLE)
            continue

        try:
            _state["status"], _state["err"] = "connecting", ""
            print("feed: connecting…", flush=True)
            naasa.stream_ticks(email, password, list(subs), _on_tick,
                               depth=["NEPSE"], stop=lambda: not _armed())
            # stream_ticks returning means stop() went true — the market closed or the switch
            # was thrown. That is not an error; go back round and idle.
            _state["status"] = "off"
        except Exception as e:
            _state["err"] = str(e)[:110]
            _state["status"] = "reconnecting"
            print("feed: %s — retrying in %ds" % (_state["err"], RETRY), flush=True)
            time.sleep(RETRY)


def demo():
    """Self-check: the switches gate the socket, and neither timer can kill the process."""
    real_feed_on, real_trading = market_hours.feed_on, market_hours.is_trading_day
    try:
        market_hours.feed_on = lambda: True
        market_hours.is_trading_day = lambda when: True
        assert _armed() is True

        market_hours.feed_on = lambda: False
        assert _armed() is False, "the feed switch must stop the socket"

        market_hours.feed_on = lambda: True
        market_hours.is_trading_day = lambda when: False
        assert _armed() is False, "a non-trading day must stop the socket"
    finally:
        market_hours.feed_on, market_hours.is_trading_day = real_feed_on, real_trading

    # a tick lands in the snapshot under the feed's own name, with its timestamp
    _state["snap"].clear()
    _on_tick({"symbol": "NEPSE", "fields": {"LTP": "2630.46"}, "time": "21/08/2026 12:26:11"})
    snap = _snapshot()
    assert snap["NEPSE"]["LTP"] == "2630.46" and snap["NEPSE"]["_t"] == "21/08/2026 12:26:11", snap
    # _snapshot must COPY: handing out the live dict would let a writer mutate it mid-publish
    snap["NEPSE"]["LTP"] = "0"
    assert _state["snap"]["NEPSE"]["LTP"] == "2630.46", "_snapshot() must return a copy"
    _state["snap"].clear()
    print("feed_publisher demo ok")


if __name__ == "__main__":
    import sys
    if "--demo" in sys.argv:
        demo()
    else:
        main()
