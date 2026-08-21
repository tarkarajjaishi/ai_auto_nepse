"""The HTTP server. Read-only, localhost, stdlib only.

    python -m api                 # 127.0.0.1:8600
    python -m api --port 9000

Routes (all GET, all JSON):
    /api/health                       liveness + what the archive holds
    /api/boards                       which boards exist and whether each is stale
    /api/board/<name>                 rows of one board, named fields, numbers as numbers
    /api/symbols                      the tradeable universe
    /api/bars/<symbol>?limit=500      corporate-action ADJUSTED daily bars
    /api/report/<symbol>              the full 22-section swing_pro analysis
    /api/scorecard/<symbol>           section 20, ten parts
    /api/questions/<symbol>           section 21, the fifteen answers
    /api/floorsheet/<symbol>          the session dates on file
    /api/floorsheet/<symbol>?date=…   one session: every trade, and each broker's net
    /api/brokerflow/<symbol>?n=20     who accumulated and who distributed over n sessions
    /api/swingquantam/<symbol>        the floorsheet engine's sections, zones and reasons
    /api/heatmap                      every equity under its sector, from the archive
    /api/stores                       per-store archive freshness (bars, floorsheet, flow)
    /api/timeframes/<symbol>          supply/demand across 5m..1M for one scrip
    /api/zones/<symbol>?bars=180      every supply/demand zone, classified, with its bars
    /api/ledger/<symbol>?broker=&days=  one broker's daily bought/sold/net in that stock
    /api/setup/<symbol>               trade_setup's score, grade, entry timing and levels
    /api/depth[/<symbol>]             the live socket book, with the age of the snapshot
    /api/bar/<symbol>                 today's bar live off the socket, poll-sized
    /api/quotes?symbols=A,B,C         ltp / prev close / % for many instruments at once
    /api/indices                      the last bar of every index
    /api/account/holdings|orderbook|collateral      NAASA, read-only
    /api/auth?probe=1                 whether the saved NAASA / SWP logins still work

Reads are GET. There are exactly TWO write routes, both narrow on purpose:

    POST /api/rebuild/<board>          re-run that board's script; 409 if one is already running
    GET  /api/rebuild                  what is running, and how the last run went
    POST /api/access-request           one landing-page form submission -> access_requests.txt
    GET  /api/access-requests          those submissions, newest first (behind the nginx wall)

Both are load-bearing rather than incidental, and the rule that replaced "no do_POST at all" is
still checkable. `do_POST` serves those two EXACT paths and 404s everything else. The rebuild
board name is looked up in `jobs.SCRIPTS`, so a client can never name a script or a path, and
`jobs.py` does not import naasa — so no rebuild can reach a money call.

/api/access-request is the only route a STRANGER can reach: it is public at nginx, because a
demo-request form behind a password is a form nobody fills in. What that buys an attacker is
bounded by construction — the target filename is a module constant in api/access.py, the body is
capped before it is read, every field is validated and length-capped, and the only operation is
appending one escaped line. There is no path, no filename and no command anywhere in its input.
The account routes reach a live broker session but only ever read from it; see api/account.py
for why no order path is importable here.

Nothing here computes. Every route calls a function that already exists and is already tested.
"""
import argparse
import json
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from fetch_ohlc import MASTER

import jobs
import market_hours
from . import access
from . import tables

# The Next dev server's origin, for CORS. Only ever used in DEVELOPMENT — in production nginx
# serves the frontend and the API from the same origin, so no cross-origin request happens and
# this header is irrelevant. Overridable because the dev port is not reliably free: 3000 is often
# already held by another project on this machine, and a hardcoded port fails as a wall of CORS
# errors with a page that renders perfectly and shows no data.
_ALLOW = "http://127.0.0.1:3000"


def _universe():
    p = MASTER / "symbols.txt"
    return p.read_text(encoding="utf-8").split() if p.exists() else []


def _int(query, key, default):
    """One query parameter as an int, or None when the caller sent something that is not one.

    `int(query["limit"][0])` raises straight out of route() and the handler renders the raw
    Python message as a 500 body — a client error reported as a server fault, with our internals
    in it. Every numeric parameter goes through here.
    """
    raw = query.get(key, [str(default)])[0]
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _float(query, key, default):
    """One query parameter as a float, or None when it is not a number."""
    raw = query.get(key, [str(default)])[0]
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _resize(board, capital, risk_pct):
    """Re-size every row of the swing_master board for a different book.

    Position sizing is a pure function of the book, the risk budget and the row's own entry and
    stop, so this is a RECOMPUTATION OF A READ rather than a mutation -- the file on disk is not
    touched and no write verb exists. It calls swing_master.size() itself rather than reproducing
    the arithmetic: the cap "never more stock than the cash buys" is the part that is easy to get
    wrong, and it once sized a position 4.4x geared against the book.

    `qty`, `risk_rs` and `cost_rs` are restated; every other column is the analysis, which does
    not depend on how much money you have.
    """
    import swing_master

    budget = capital * risk_pct / 100
    rows = []
    for r in board["rows"]:
        entry, stop = r.get("entry"), r.get("stop")
        if not isinstance(entry, (int, float)) or not isinstance(stop, (int, float)):
            rows.append(r)
            continue
        per_share = entry - stop
        qty = swing_master.size(capital, budget, entry, stop)
        rows.append({**r, "qty": qty,
                     "risk_rs": round(qty * per_share) if per_share > 0 else 0,
                     "cost_rs": round(qty * entry)})
    return {**board, "rows": rows, "sized_for": {"capital": capital, "risk_pct": risk_pct}}


def route(path, query):
    """(status, payload). Raising is fine — the handler turns it into a 500 with the message."""
    parts = [p for p in path.strip("/").split("/") if p]
    if not parts or parts[0] != "api":
        return 404, {"error": "not found"}
    parts = parts[1:]
    head = parts[0] if parts else ""
    arg = unquote(parts[1]) if len(parts) > 1 else None

    if head == "health":
        newest = tables.newest_bar()
        return 200, {"ok": True, "archive_session": newest,
                     "missed_sessions": market_hours.missed_sessions(newest),
                     "symbols": len(_universe())}

    if head == "access-requests":
        # Public to WRITE, private to READ: these rows are somebody's name, email and phone.
        # nginx keeps every /api path except the POST behind the wall, so this needs no check
        # of its own — but that is a fact about the deployment, so test_ops pins it.
        rows = access.read_all()
        return 200, {"count": len(rows), "rows": rows}

    if head == "boards":
        out = {}
        for name in tables.BOARDS:
            t = tables.read(name)
            # session_unknown travels too. Without it every consumer of THIS route sees
            # stale=false for an undated board and badges it "synced" — the same collapse of
            # "unknown" into "current" that the flag was added to stop, one layer up.
            out[name] = {"rows": len(t["rows"]), "session": t["session"],
                         "stale": t["stale"],
                         "session_unknown": t.get("session_unknown", False),
                         "missing": t.get("missing", False)}
        newest = tables.newest_bar()
        # How many SESSIONS the archive itself is behind, which comparing the boards to each
        # other can never see: a whole-pipeline stall freezes every store in lockstep, they all
        # still agree, and the screen goes green over week-old prices. None means "cannot tell".
        return 200, {"archive_session": newest,
                     "missed_sessions": market_hours.missed_sessions(newest),
                     "boards": out}

    if head == "board" and arg:
        t = tables.read(arg)
        if not t:
            return 404, {"error": f"no board {arg!r}"}
        if arg == "swing_master" and ("capital" in query or "risk" in query):
            capital = _float(query, "capital", 100_000.0)
            risk = _float(query, "risk", 1.0)
            if capital is None or risk is None:
                return 400, {"error": "capital and risk must be numbers"}
            if not 10_000 <= capital <= 100_000_000:
                return 400, {"error": "capital must be between 10,000 and 100,000,000"}
            if not 0.25 <= risk <= 5:
                return 400, {"error": "risk must be between 0.25 and 5 percent"}
            t = _resize(t, capital, risk)
        return 200, t

    if head == "symbols":
        from . import market
        return 200, {"symbols": _universe(), "indices": market.index_names()}

    if head == "swingquantam" and arg:
        # The board index is served by /api/board/swing_quantam like every other board.
        # This is the per-symbol detail behind one of its rows.
        # `tables` is imported at module level -- a local `from . import tables` here
        # would make the name local to route() and unbind it for every other branch.
        from swing_quantam import store
        from swing_quantam.loader import sessions as loader_sessions
        d = store.read_detail(arg.upper())
        if d is None:
            # Not an error -- the engine simply has not been run for this symbol. Say
            # which, so the page can tell "no such company" from "not built yet".
            known = arg.upper() in _universe()
            return 404, {"error": f"swing_quantam has not been built for {arg.upper()!r}"
                                  if known else f"no such symbol {arg.upper()!r}",
                         "known_symbol": known}
        newest = tables.newest_bar()
        # Judged against the newest FINISHED session, not the newest bar on disk -- the same
        # yardstick tables.read() uses for every other board. newest_bar() includes today's
        # partial bar from 11:00, so comparing against it marked this page stale from the
        # opening bell while /api/boards called the very same file fresh. Two routes reading one
        # board and disagreeing about its freshness is worse than either answer alone.
        benchmark = tables.newest_completed()
        # The newest session this SYMBOL actually has, which is not the same question as how old
        # the board is. 53 of 481 rows are 1-4 years behind simply because the company stopped
        # trading -- CBL's last floorsheet is 2023-02-23 and always will be. Telling that reader
        # to "rebuild" is advice that cannot work, so the page needs to tell the two apart:
        # a board behind the SYMBOL's own data is stale and fixable; a board level with it is
        # current, and the symbol is what ended.
        sessions = loader_sessions(arg.upper())
        symbol_last = sessions[-1] if sessions else None
        # The newest floorsheet session any symbol has, taken from the board rather than by
        # scanning 593 directories. Cached by tables.read().
        board_session = (tables.read("swing_quantam") or {}).get("session")

        # The broker the 30D window is actually about, lifted out of section 11 so the page can
        # chart it without opening a panel. Named explicitly rather than left for the frontend to
        # scrape out of a row label -- a label is prose and changes; this is a field.
        top_broker = None
        for _s in d.sections:
            if _s.n == 11:
                for _r in _s.rows:
                    if _r.metric == "30D top NET buyer":
                        top_broker = str(_r.value).strip() or None
                break
        return 200, {
            "symbol": d.symbol,
            "session": d.session,
            "archive_session": newest,
            "symbol_last_session": symbol_last,
            "top_broker": top_broker,
            # "Ended" means this symbol stopped trading while others carried on -- NOT that the
            # archive is a day behind. The yardstick is therefore the board's own session, which
            # is the newest floorsheet date ANY symbol has, not the newest price bar: between the
            # open and the 15:15 fetch every symbol's floorsheet trails today's bar, and comparing
            # against the bar told every reader their stock had stopped trading.
            "symbol_ended": bool(symbol_last and d.session and d.session >= symbol_last
                                 and board_session and symbol_last < board_session),
            "stale": bool(d.session and benchmark and d.session < benchmark),
            # `stale` is False when the session is UNKNOWN as well as when it is current, and
            # those are not the same fact. Without this flag a detail file that lost its session
            # line takes the fresh branch and the page reports week-old numbers as today's --
            # the exact collapse of "cannot tell" into "current" that /api/boards carries a flag
            # to prevent.
            "session_unknown": not d.session,
            "signal": d.signal,
            "score": d.score,
            "confidence": d.confidence,
            "reasons": list(d.reasons),
            "warnings": list(d.warnings),
            "sections": [
                {"n": s.n, "title": s.title, "note": s.note,
                 # tables._num is the same coercion every board goes through, so a number
                 # reaches the frontend as a number and it never parses strings itself.
                 "rows": [{"metric": r.metric, "value": tables._num(r.value), "note": r.note}
                          for r in s.rows]}
                for s in d.sections
            ],
        }

    if head == "bars" and arg:
        from prices import bars as adjusted
        from . import market
        n = _int(query, "limit", 500)
        if n is None:
            return 400, {"error": "limit must be a whole number"}
        # ?ema=20,50,200 — moving averages computed HERE, by indicators.ema, on the same closes
        # the chart draws. The frontend must not compute an indicator: a second implementation of
        # a seeded EMA is a second answer, and the one on screen would be the untested one.
        want_ema = [int(x) for x in query.get("ema", [""])[0].split(",") if x.strip().isdigit()]
        want_ema = sorted({p for p in want_ema if 2 <= p <= 400})[:4]

        def with_ema(payload, closes):
            if want_ema:
                from indicators import ema
                payload["ema"] = {str(p): ema(closes, p)[-len(payload["bars"]):]
                                  for p in want_ema}
            return payload

        b = adjusted(arg.upper())
        if b:
            d, o, h, l, c, v = b
            s = slice(-n, None) if n > 0 else slice(None)
            return 200, with_ema(
                {"symbol": arg.upper(), "kind": "stock", "adjusted": True,
                 "bars": [{"date": a, "open": b_, "high": c_, "low": d_, "close": e_,
                           "volume": f_}
                          for a, b_, c_, d_, e_, f_ in
                          zip(d[s], o[s], h[s], l[s], c[s], v[s])]},
                c)
        # Stocks first, then indices. An index carries `adjusted: false` and means it — see
        # market.index_bars for why running one through the adjuster would be wrong, not just
        # unnecessary.
        rows = market.index_bars(arg, n)
        if rows:
            full = market.index_bars(arg, 0) or rows
            return 200, with_ema(
                {"symbol": arg.upper(), "kind": "index", "adjusted": False, "bars": rows},
                [r["close"] for r in full])
        return 404, {"error": f"no bars for {arg!r}"}

    if head == "floorsheet" and arg:
        from . import market
        dates = market.sessions(arg)
        if not dates:
            return 404, {"error": f"no floorsheet on file for {arg.upper()!r}"}
        want = query.get("date", [None])[0]
        if not want:
            return 200, {"symbol": arg.upper(), "sessions": dates, "latest": dates[0]}
        if want not in dates:
            return 404, {"error": f"{arg.upper()} has no floorsheet for {want}",
                         "sessions": dates[:20]}
        return 200, market.floorsheet(arg, want)

    if head == "brokerflow" and arg:
        from . import market
        n = _int(query, "n", 20)
        if n is None:
            return 400, {"error": "n must be a whole number"}
        # An unknown symbol used to come back 200 with empty lists, and this was the ONLY
        # symbol-scoped route that did. A typo or a delisted ticker then rendered as "no broker
        # has touched this stock" -- a real, quiet fact about a real company -- instead of "no
        # such company". 404 like floorsheet and bars already do.
        if arg.upper() not in _universe():
            return 404, {"error": f"no such symbol {arg.upper()!r}"}
        return 200, market.broker_flow(arg, max(1, min(500, n)))

    if head == "bar" and arg:
        # TODAY's bar, live off the socket snapshot — small enough to poll every second, where
        # /api/bars is five thousand candles and is not.
        #
        # Built by live_1d.row(), the SAME function that writes this row into 1D.txt. That is the
        # point: the screen and the archive cannot disagree about today, and row()'s refusals come
        # with it — no LTP or no Open returns null rather than a hollow bar at price 0, and a quote
        # whose own timestamp is not today is refused outright, because the socket snapshot
        # survives past the close and would otherwise print yesterday as today.
        import feed_snap
        import live_1d
        snap = feed_snap.read()
        q = snap["quotes"].get(live_1d.feed_name(arg.upper()))
        out = {"symbol": arg.upper(), "age": snap["age"], "fresh": snap["fresh"],
               "session": market_hours.session_now()[0], "bar": None}
        if q:
            # back to the feed's own field names, which is what live_1d.row() reads
            line = live_1d.row({"LTP": q.get("ltp"), "Open": q.get("open"),
                                "High": q.get("high"), "Low": q.get("low"),
                                "Close": q.get("close"), "TTQ": q.get("volume"),
                                "TTV": q.get("ttv"), "WeightedAverage": q.get("avg_price"),
                                "_t": q.get("stamp")}, live_1d.today())
            if line:
                f = line.split("\t")
                keys = ("date", "open", "high", "low", "close", "change", "pct", "volume", "amount")
                out["bar"] = {k: (v if k == "date" else (float(v) if v else None))
                              for k, v in zip(keys, f)}
        return 200, out

    if head == "quotes":
        # Many instruments, three numbers each. The rail shows fourteen; sending the whole 347-row
        # snapshot at 1s would be ~40 KB a second for the sake of a price and a percentage.
        import feed_snap
        import live_1d
        want = [w.strip().upper() for w in (query.get("symbols", [""])[0] or "").split(",")
                if w.strip()]
        if not want:
            return 400, {"error": "symbols is required, e.g. /api/quotes?symbols=NEPSE,NABIL"}
        if len(want) > 400:
            return 400, {"error": "at most 400 symbols per call"}
        snap = feed_snap.read()
        # The feed stamps DD/MM/YYYY. The publisher keeps writing while the day is a trading day,
        # so after the close the snapshot stays seconds-old with the CLOSING quotes in it — and
        # tomorrow morning those are yesterday's numbers under a fresh written_at. /api/bar has
        # always refused that (live_1d.row checks the quote's own stamp); this did not.
        today = live_1d.today()
        stamp_today = "%s/%s/%s" % (today[8:10], today[5:7], today[:4]) if today else None
        out = {}
        for name in want:
            q = snap["quotes"].get(live_1d.feed_name(name))
            if not q or q.get("ltp") is None:
                continue
            if stamp_today and q.get("stamp") and not str(q["stamp"]).startswith(stamp_today):
                continue
            prev = q.get("close")
            out[name] = {"ltp": q["ltp"], "close": prev,
                         "pct": round((q["ltp"] - prev) / prev * 100, 2) if prev else None}
        return 200, {"age": snap["age"], "fresh": snap["fresh"],
                     "session": market_hours.session_now()[0], "quotes": out}

    if head == "depth":
        # The live book, read from the snapshot the socket-holding process publishes. `age` and
        # `fresh` travel with it and are not optional: a quote from Thursday's close renders
        # exactly like a live one, so a reader that cannot see the age cannot tell them apart.
        import feed_snap
        import live_1d
        snap = feed_snap.read()
        # The session state travels with the quote. "No ticks" means something different at 09:00
        # than it does at 12:00: the first is a closed market, the second is a broken feed, and a
        # panel that cannot tell them apart sends the reader hunting a problem they do not have.
        state, mins = market_hours.session_now()
        out = {"age": snap["age"], "fresh": snap["fresh"], "written_at": snap["written_at"],
               "instruments": len(snap["quotes"]),
               "session": state, "minutes_to_next": mins}
        if arg:
            # The snapshot is keyed the way the FEED names things, and eight sector indices are
            # abbreviated differently there.
            out["symbol"] = arg.upper()
            out["quote"] = snap["quotes"].get(live_1d.feed_name(arg.upper()))
        return 200, out

    if head == "ledger" and arg:
        # One broker's day-by-day bought / sold / net in one stock. This is the row-level evidence
        # under "broker 92 is the dominant net buyer": a claim about twenty sessions should be
        # checkable session by session, not taken on the strength of its own summary.
        broker = (query.get("broker", [""])[0] or "").strip()
        if not broker:
            return 400, {"error": "broker is required, e.g. /api/ledger/MEN?broker=92"}
        days = _int(query, "days", 30)
        if days is None or not 1 <= days <= 400:
            return 400, {"error": "days must be a whole number between 1 and 400"}
        path = MASTER / "broker_flow" / (arg.upper().replace("/", "-") + ".txt")
        if not path.exists():
            return 404, {"error": f"no broker flow on file for {arg.upper()!r}"}
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines()[1:]:
            f = line.split("\t")
            if len(f) < 5 or f[1] != broker:
                continue
            try:
                rows.append({"date": f[0], "bought": float(f[2]), "sold": float(f[3]),
                             "net": float(f[4])})
            except ValueError:
                continue          # a malformed row is skipped, never guessed at
        rows.sort(key=lambda r: r["date"])
        rows = rows[-days:]
        # vs_prev is computed here so the table and any chart of it cannot disagree.
        for i, r in enumerate(rows):
            r["vs_prev"] = None if i == 0 else round(r["net"] - rows[i - 1]["net"], 2)
        if not rows:
            return 404, {"error": f"broker {broker} has no sessions on file for {arg.upper()}"}
        return 200, {"symbol": arg.upper(), "broker": broker, "sessions": rows,
                     "totals": {"bought": round(sum(r["bought"] for r in rows), 2),
                                "sold": round(sum(r["sold"] for r in rows), 2),
                                "net": round(sum(r["net"] for r in rows), 2)}}

    if head == "setup" and arg:
        # trade_setup.setup() -- the A+ score, the entry timing and the levels. Computed live and
        # cheap (~20ms); it carries its own `date`, so the screen can say which session it read.
        import trade_setup
        if arg.upper() not in _universe():
            return 404, {"error": f"no such symbol {arg.upper()!r}"}
        out = trade_setup.setup(arg.upper())
        if not out:
            return 404, {"error": f"{arg.upper()}: not enough history for a setup"}
        return 200, out

    if head == "zones" and arg:
        import supply_demand
        from prices import bars as adjusted
        b = adjusted(arg.upper())
        if not b or len(b[4]) < 60:
            return 404, {"error": f"{arg.upper()}: needs at least 60 daily bars for zones"}
        n = _int(query, "bars", 180)
        if n is None or n < 20:
            return 400, {"error": "bars must be a whole number of at least 20"}
        d, o, h, l, c = (x[-supply_demand.MAX_BARS:] for x in (b[0], b[1], b[2], b[3], b[4]))
        start = max(0, len(c) - n)
        row = supply_demand.dashboard(o, h, l, c)
        out = []
        for z in supply_demand.zones(o, h, l, c):
            # Only zones BORN inside the window. One that formed earlier is still live, but its
            # box would start off the left edge and read as covering the whole chart.
            if z["i"] < start:
                continue
            state = supply_demand.classify(z, h, l, c)[0]
            out.append({"from": d[z["i"]], "lo": round(z["lo"], 4), "hi": round(z["hi"], 4),
                        "kind": z["kind"], "state": state})
        return 200, {
            "symbol": arg.upper(),
            "bars": [{"date": d[i], "open": o[i], "high": h[i], "low": l[i], "close": c[i]}
                     for i in range(start, len(c))],
            "zones": out,
            "levels": None if not row else {
                "entry": round(row["entry"], 2), "sl": round(row["sl"], 2),
                "tp": round(row["tp"], 2), "signal": row["signal"],
                "kind": row["kind"], "in_zone": bool(row["in_zone"]),
            },
        }

    if head == "timeframes" and arg:
        # One symbol across 5m/15m/30m/1h/1D/1W/1M. Computed on demand rather than stored: it is
        # ~1s for one scrip and would be ~6 minutes for the whole market, which is why the board
        # holds the daily row only and this answers the "and the other frames?" question.
        import supply_demand
        if arg.upper() not in _universe():
            return 404, {"error": f"no such symbol {arg.upper()!r}"}
        rows = supply_demand.scan_timeframes(arg.upper())
        if not rows:
            return 404, {"error": f"not enough history for {arg.upper()} on any timeframe"}
        return 200, {
            "symbol": arg.upper(),
            "timeframes": [
                {"timeframe": r["symbol"], "direction": r["direction"], "state": r["state"],
                 "age": r["age"], "signal": r["signal"], "close": round(r["close"], 2),
                 "entry": round(r["entry"], 2), "sl": round(r["sl"], 2), "tp": round(r["tp"], 2),
                 "risk_pct": round(r["risk_pct"], 2), "dist_pct": round(r["dist_pct"], 2),
                 "in_zone": bool(r["in_zone"])}
                for r in rows
            ],
            # The agreement verdict is computed HERE so both surfaces say the same thing about
            # the same rows, rather than each deciding what "agree" means.
            "agree": len({r["direction"] for r in rows}) == 1,
        }

    if head == "stores":
        from . import stores
        newest = tables.newest_bar()
        # The stores are the raw archive under the boards. Every board can agree with every other
        # and still be built on a store that stopped updating a week ago, so this is the same
        # question as missed_sessions asked one layer further down.
        return 200, {"stores": stores.state(), "archive_session": newest,
                     "missed_sessions": market_hours.missed_sessions(newest)}

    if head == "rebuild":
        # Read-only status. Starting a rebuild is a POST; a GET here can never begin one, which
        # matters because anything that runs on a GET runs on a link, a prefetch and a crawler.
        return 200, jobs.status()

    if head == "heatmap":
        from . import market
        return 200, {**market.heatmap(), "archive_session": tables.newest_bar()}

    if head == "indices":
        from . import market
        return 200, market.indices()

    if head == "auth":
        # Status of the two saved broker logins. Read-only by construction — see api/auth.py for
        # why there is no sign-in route here. ?probe=1 actually calls the broker; without it this
        # reports what is on disk, because a NAASA probe is a full OAuth login and would make
        # every page load wait on it.
        from . import auth
        return 200, auth.status(probe=query.get("probe", ["0"])[0] not in ("0", "", "false"))

    if head == "account":
        from . import account
        if not account.configured():
            return 503, {"error": "No NAASA login is saved on the server.",
                         "detail": "Sign in on the Streamlit app under 'NAASA account' with "
                                   "Remember me. The session lives on the box, not the browser.",
                         "configured": False}
        fn = {"holdings": account.holdings, "orderbook": account.orderbook,
              "collateral": account.collateral}.get(arg)
        if not fn:
            return 404, {"error": "account/holdings, account/orderbook or account/collateral"}
        try:
            return 200, {"configured": True, **fn()}
        except account.UpstreamChanged as e:
            # 503, not 500: the server is fine, the dependency is gone. `upstream` lets the page
            # say so plainly instead of showing a stack-flavoured 500 that reads like our bug.
            return 503, {"error": str(e), "configured": True, "upstream": "naasa"}

    if head in ("report", "scorecard", "questions") and arg:
        import swing_pro
        f = swing_pro.analyse(arg.upper(), swing_pro._fundamentals(), _calendar())
        if not f:
            return 404, {"error": f"{arg.upper()}: not enough daily history "
                                  "(the 200 EMA needs ~210 sessions and is never fabricated)"}
        if head == "scorecard":
            return 200, {"symbol": arg.upper(), "total": f["score"], "grade": f["grade"],
                         "parts": [{"name": n, "got": g, "max": m}
                                   for n, g, m in swing_pro.scorecard(f)]}
        if head == "questions":
            return 200, {"symbol": arg.upper(),
                         "warning_questions": sorted(swing_pro.WARNING_QUESTIONS),
                         "answers": [{"n": i, "question": q, "answer": a,
                                      "kind": "text" if isinstance(a, str) else "bool"}
                                     for i, (q, a) in enumerate(swing_pro.answers(f), 1)]}
        return 200, {"symbol": arg.upper(), "text": swing_pro.report(f),
                     "fields": {k: v for k, v in f.items()
                                if isinstance(v, (str, int, float, bool, type(None), list))}}

    return 404, {"error": "not found"}


_cal = {"value": None}


def _calendar():
    """swing_pro needs the market's own session calendar for Section 14 — without it trade_freq
    is None and the report silently drops a line. Built once per process."""
    if _cal["value"] is None:
        import swing_pro
        _cal["value"] = swing_pro.market_calendar(_universe())
    return _cal["value"]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, status, payload):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", _ALLOW)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        try:
            status, payload = route(u.path, parse_qs(u.query))
        except Exception as e:
            traceback.print_exc()
            status, payload = 500, {"error": f"{type(e).__name__}: {e}"}
        try:
            self._send(status, payload)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            # The client went away mid-response — a cancelled TanStack query, a closed tab, a
            # curl that stopped reading. Routine, and not worth a traceback that makes a healthy
            # log look broken.
            pass

    def do_POST(self):
        """The only write verb, and it serves exactly two paths.

        `POST /api/rebuild/<board>` starts that board's script in the background. The board name
        is a KEY into jobs.SCRIPTS -- never a path, never a command -- so the worst a caller can
        ask for is one of the analysis scripts this project already runs on a timer.

        409 rather than a queue when something is already running: two concurrent rebuilds write
        the same .txt files, which is the exact collision jobs.py's lock exists to stop, and
        silently queueing would hide it from whoever pressed the button.

        `POST /api/access-request` appends one landing-page form submission. It is PUBLIC, and
        it is the only route in this project a stranger can reach, so it is written out in full
        here rather than hidden behind a helper: the checks are the point, and a test that reads
        this function's source can only see what this function contains.
        """
        u = urlparse(self.path)
        parts = [p for p in u.path.strip("/").split("/") if p]

        if parts == ["api", "access-request"]:
            # Cap BEFORE reading. rfile.read(n) with an attacker-chosen n is how a form becomes
            # a memory exhaustion bug; 8 KB is ~40x the largest legitimate submission.
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length <= 0 or length > 8192:
                # Close, do not keep alive. protocol_version is HTTP/1.1, so the connection is
                # reused — and we are about to answer WITHOUT draining Content-Length bytes.
                # Leaving them in the socket makes the next request on that connection start
                # mid-body, which reads as a random parse failure somewhere else entirely.
                self.close_connection = True
                return self._send(413, {"error": "request too large"})
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                return self._send(400, {"error": "expected a JSON body"})

            # X-Real-IP, and ONLY that. nginx now runs set_real_ip_from over Cloudflare's
            # ranges with real_ip_header CF-Connecting-IP for this vhost, so $remote_addr —
            # which is what fills this header — is the true visitor when the request came
            # through Cloudflare, and the actual peer when it did not.
            #
            # Reading CF-Connecting-IP here directly, as this did, was a hole rather than a
            # nicety: the origin answers on 443 without Cloudflare in front of it, so anyone
            # could POST straight to the box with a DIFFERENT forged value on every request and
            # land each one in a fresh rate-limit bucket. Measured bypassed; measured closed.
            ip = (self.headers.get("X-Real-IP") or self.client_address[0] or "").strip()
            if not access.allowed(ip):
                return self._send(429, {"error": "Too many requests. Please try again later."})
            clean, why = access.validate(payload)
            if why:
                return self._send(400, {"error": why})
            try:
                access.record(clean, ip)
            except Exception:
                # The message stays GENERIC because this caller is the public. The other 500s
                # in this file echo the exception, which is right for a walled route and wrong
                # here: once the disk fills, the OSError text carries the absolute server path
                # and an errno straight to a stranger. The detail goes to the log instead.
                traceback.print_exc()
                return self._send(500, {"error": "Could not save that just now. "
                                                 "Please try again shortly."})
            return self._send(201, {"ok": True,
                                    "message": "Sent successfully. You will be contacted soon."})

        if len(parts) != 3 or parts[0] != "api" or parts[1] != "rebuild":
            return self._send(404, {"error": "write routes are POST /api/rebuild/<board> and "
                                             "POST /api/access-request"})
        board = unquote(parts[2])
        if board not in jobs.SCRIPTS:
            return self._send(404, {"error": "no rebuild for %r" % board,
                                    "boards": sorted(jobs.SCRIPTS)})
        try:
            out = jobs.start(board)
        except Exception as e:
            traceback.print_exc()
            return self._send(500, {"error": "%s: %s" % (type(e).__name__, e)})
        return self._send(202 if out.get("started") else 409, out)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", _ALLOW)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def log_message(self, fmt, *a):        # one tidy line, not apache's
        sys.stderr.write("  %s\n" % (fmt % a))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8600)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--allow-origin", default=_ALLOW,
                    help="CORS origin for the dev frontend (production is same-origin)")
    a = ap.parse_args()
    globals()["_ALLOW"] = a.allow_origin
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"chukul api on http://{a.host}:{a.port}  ·  archive {tables.newest_bar()}")
    print(f"  CORS allows {a.allow_origin}")
    print(f"  {len(tables.BOARDS)} boards · {len(_universe())} symbols · read-only")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
