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
    /api/heatmap                      every equity under its sector, from the archive
    /api/indices                      the last bar of every index
    /api/account/holdings|orderbook|collateral      NAASA, read-only
    /api/auth?probe=1                 whether the saved NAASA / SWP logins still work

GET only, and that is load-bearing rather than incidental: `do_POST` does not exist, so no
route can mutate anything even by accident. The account routes reach a live broker session but
only ever read from it — see api/account.py for why no order path is importable from here.

Nothing here computes. Every route calls a function that already exists and is already tested.
"""
import argparse
import json
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from fetch_ohlc import MASTER

import market_hours
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

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", _ALLOW)
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
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
