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

from . import tables

_ALLOW = "http://127.0.0.1:3000"          # the Next.js dev server; nginx serves same-origin


def _universe():
    p = MASTER / "symbols.txt"
    return p.read_text(encoding="utf-8").split() if p.exists() else []


def route(path, query):
    """(status, payload). Raising is fine — the handler turns it into a 500 with the message."""
    parts = [p for p in path.strip("/").split("/") if p]
    if not parts or parts[0] != "api":
        return 404, {"error": "not found"}
    parts = parts[1:]
    head = parts[0] if parts else ""
    arg = unquote(parts[1]) if len(parts) > 1 else None

    if head == "health":
        return 200, {"ok": True, "archive_session": tables.newest_bar(),
                     "symbols": len(_universe())}

    if head == "boards":
        out = {}
        for name in tables.BOARDS:
            t = tables.read(name)
            out[name] = {"rows": len(t["rows"]), "session": t["session"],
                         "stale": t["stale"], "missing": t.get("missing", False)}
        return 200, {"archive_session": tables.newest_bar(), "boards": out}

    if head == "board" and arg:
        t = tables.read(arg)
        return (200, t) if t else (404, {"error": f"no board {arg!r}"})

    if head == "symbols":
        from . import market
        return 200, {"symbols": _universe(), "indices": market.index_names()}

    if head == "bars" and arg:
        from prices import bars as adjusted
        from . import market
        n = int(query.get("limit", ["500"])[0])
        b = adjusted(arg.upper())
        if b:
            d, o, h, l, c, v = b
            s = slice(-n, None) if n > 0 else slice(None)
            return 200, {"symbol": arg.upper(), "kind": "stock", "adjusted": True,
                         "bars": [{"date": a, "open": b_, "high": c_, "low": d_, "close": e_,
                                   "volume": f_}
                                  for a, b_, c_, d_, e_, f_ in
                                  zip(d[s], o[s], h[s], l[s], c[s], v[s])]}
        # Stocks first, then indices. An index carries `adjusted: false` and means it — see
        # market.index_bars for why running one through the adjuster would be wrong, not just
        # unnecessary.
        rows = market.index_bars(arg, n)
        if rows:
            return 200, {"symbol": arg.upper(), "kind": "index", "adjusted": False, "bars": rows}
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
        n = max(1, min(500, int(query.get("n", ["20"])[0])))
        return 200, market.broker_flow(arg, n)

    if head == "heatmap":
        from . import market
        return 200, {**market.heatmap(), "archive_session": tables.newest_bar()}

    if head == "indices":
        from . import market
        return 200, market.indices()

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
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"chukul api on http://{a.host}:{a.port}  ·  archive {tables.newest_bar()}")
    print(f"  {len(tables.BOARDS)} boards · {len(_universe())} symbols · read-only")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
