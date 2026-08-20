"""NAASA Securities market data — instrument types and live quotes.

Their charting backend is a public TradingView UDF datafeed:
`api-charts.naasasecurities.com.np/api/v1/datafeed/1/`. **No login is required** — every
endpoint below answers anonymously, so the app never handles a brokerage credential.

Two things it gives us that chukul does not:
  * the exchange's own instrument type (Stock / Bond / Mutual Fund / Index), which beats
    guessing fund and debenture tickers by their shape
  * a last-traded price with bid/ask, for marking open signals against the live market

    python naasa.py            # refresh Master_data/instruments.txt
"""

import base64
import html as html_lib
import http.cookiejar
import json
import re
import string
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

from fetch_ohlc import MASTER

BASE = "https://api-charts.naasasecurities.com.np/api/v1/datafeed/1"
INSTRUMENTS = MASTER / "instruments.txt"
HEADER = "symbol\ttype\tname"


def get(path, tries=3):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.load(r)
        except OSError:
            if attempt == tries - 1:
                raise


def instruments():
    """{symbol: (type, name)} for the whole exchange.

    The search endpoint rejects an empty query, so sweep the alphabet and merge — every
    instrument contains at least one letter, and duplicates collapse on the symbol.
    """
    found = {}
    for letter in string.ascii_uppercase:
        try:
            for row in get(f"/search?query={letter}&type=&exchange=&limit=500"):
                found[row["symbol"]] = (row.get("type", ""), row.get("fullName", ""))
        except (OSError, ValueError):
            continue
    return found


def quotes(symbols):
    """{symbol: {lp, ch, chp, open, high, low, prev_close, volume, bid, ask}}.

    NAASA accepts a comma-separated list; long lists are chunked to keep URLs sane.
    """
    out = {}
    symbols = [s.replace("/", "-") for s in symbols]
    for i in range(0, len(symbols), 40):
        chunk = ",".join(symbols[i:i + 40])
        try:
            payload = get(f"/quotes?symbols={chunk}")
        except (OSError, ValueError):
            continue
        for row in payload.get("d", []):
            v = row.get("v") or {}
            out[row.get("n")] = {
                "lp": v.get("lp"), "ch": v.get("ch"), "chp": v.get("chp"),
                "open": v.get("open_price"), "high": v.get("high_price"),
                "low": v.get("low_price"), "prev_close": v.get("prev_close_price"),
                "volume": v.get("volume"), "bid": v.get("bid"), "ask": v.get("ask"),
            }
    return out


RESOLUTIONS = {
    "1S": 1, "5S": 5, "1": 60, "5": 300, "15": 900, "30": 1800,
    "60": 3600, "120": 7200, "D": 86400, "W": 604800, "M": 2592000,
}


def history(symbol, resolution="D", bars=500):
    """Bars straight from NAASA, in the same column layout the archive readers return.

    Depth differs by resolution — seconds go back about a day, minutes a month, daily to
    2016 — so `bars` is a request, not a promise. Timestamps come back as UTC epochs and
    are rendered in Nepal time to match everything else in the app.
    """
    import time
    from datetime import datetime, timedelta, timezone

    npt = timezone(timedelta(hours=5, minutes=45))
    span = RESOLUTIONS.get(resolution, 86400) * bars
    now = int(time.time())
    payload = get(f"/history?symbol={symbol.replace('/', '-')}&resolution={resolution}"
                  f"&from={now - span * 3}&to={now}")   # ask wide; the feed trims to what it has
    if payload.get("s") != "ok" or not payload.get("t"):
        return None
    stamp = "%Y-%m-%d" if resolution in ("D", "W", "M") else "%Y-%m-%d %H:%M:%S"
    cols = {"when": [], "open": [], "high": [], "low": [], "close": [], "volume": [],
            "change": [], "pct": [], "amount": []}
    for i, t in enumerate(payload["t"][-bars:]):
        idx = len(payload["t"]) - min(bars, len(payload["t"])) + i
        cols["when"].append(datetime.fromtimestamp(t, npt).strftime(stamp))
        for key, src in (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c")):
            cols[key].append(float(payload[src][idx]))
        cols["volume"].append(float((payload.get("v") or [0] * len(payload["t"]))[idx] or 0))
        cols["amount"].append(0.0)
    closes = cols["close"]
    cols["change"] = [None] + [round(b - a, 2) for a, b in zip(closes, closes[1:])]
    cols["pct"] = [None] + [round((b - a) / a * 100, 2) if a else 0.0 for a, b in zip(closes, closes[1:])]
    return cols


def load_types():
    """{symbol: type} from disk — empty when the file has not been built yet."""
    if not INSTRUMENTS.exists():
        return {}
    rows = INSTRUMENTS.read_text(encoding="utf-8").splitlines()[1:]
    return {p[0]: p[1] for p in (line.split("\t") for line in rows) if len(p) >= 2}


def main():
    found = instruments()
    if not found:
        print("no instruments returned — datafeed unreachable")
        return
    lines = [HEADER] + [f"{sym}\t{kind}\t{name}" for sym, (kind, name) in sorted(found.items())]
    INSTRUMENTS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    counts = {}
    for kind, _ in found.values():
        counts[kind] = counts.get(kind, 0) + 1
    print(f"{len(found)} instruments -> {INSTRUMENTS}")
    print("  " + "   ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])))


# ---------------------------------------------------------------- account session
#
# The app API (nx.naasasecurities.com.np/api/*) authorizes with the browser's NextAuth
# session cookie — the Keycloak bearer token is rejected there (401). Direct-grant password
# login is disabled on the `blaze` realm, so there is no password flow to implement: the one
# durable credential is that cookie, copied once from a signed-in tab. We persist it to a
# .txt (the project's only storage) so the login survives restarts; the cookie itself lasts
# ~30 days and NextAuth slides it forward on each use. Only read endpoints are called here —
# nothing places or cancels an order on purpose.

NX = "https://nx.naasasecurities.com.np"
SESSION_FILE = MASTER / "naasa_session.txt"
CREDS_FILE = MASTER / "naasa_login.txt"
COOKIE_NAME = "__Secure-next-auth.session-token"


def _cookie_header(raw):
    """A Cookie header from whatever the user pasted — a bare value or a full name=value."""
    raw = (raw or "").strip()
    return raw if "=" in raw.split(";", 1)[0] else f"{COOKIE_NAME}={raw}"


def save_session(cookie):
    """Persist the session cookie so the login is permanent until it expires."""
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text((cookie or "").strip() + "\n", encoding="utf-8")


def load_session():
    """The saved cookie string, or "" if we have never signed in on this machine."""
    return SESSION_FILE.read_text(encoding="utf-8").strip() if SESSION_FILE.exists() else ""


def clear_session():
    SESSION_FILE.unlink(missing_ok=True)


def save_credentials(email, password):
    """Remember the login for silent auto sign-in. Plain text on disk — gitignored and local
    only; the user opts into this, and it is what lets the app re-authenticate on its own once
    the saved cookie lapses."""
    CREDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CREDS_FILE.write_text(f"{email}\n{password}\n", encoding="utf-8")


def load_credentials():
    """(email, password) if remembered, else (None, None)."""
    if not CREDS_FILE.exists():
        return None, None
    lines = CREDS_FILE.read_text(encoding="utf-8").splitlines()
    return (lines[0].strip(), lines[1]) if len(lines) >= 2 else (None, None)


def clear_credentials():
    CREDS_FILE.unlink(missing_ok=True)


def _nx(path, cookie, method="GET", body=None, timeout=30):
    """Call an nx app endpoint with the session cookie. Origin/Referer/UA mirror the browser
    so the request is indistinguishable from the SPA's own — read-only by construction."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(NX + path, data=data, method=method, headers={
        "Cookie": _cookie_header(cookie),
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": NX,
        "Referer": NX + "/",
        "User-Agent": "Mozilla/5.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{e.code}: {e.read().decode('utf-8', 'replace')[:150]}") from None


def session_info(cookie):
    """Validate the cookie and return the signed-in profile: {user, accessToken, expires}.

    NextAuth answers /api/auth/session with the user object while the cookie is live, and an
    empty body once it lapses — so a missing `user` means the session must be renewed.
    """
    info = _nx("/api/auth/session", cookie)
    if not info or not info.get("user"):
        raise RuntimeError("session expired or invalid — sign in again")
    return info


def login_password(email, password):
    """Log in with a NAASA email + password and return the session cookie string.

    There is no password API — the `blaze` realm disables direct grant — so this drives the
    same browser flow a human does: NextAuth hands out a Keycloak authorize URL, Keycloak shows
    a plain login form, we POST the credentials, and Keycloak redirects back through NextAuth's
    callback, which sets the session cookie. A single cookie jar carries the state/PKCE cookies
    across those hops. The password is used here and thrown away — only the cookie is returned
    (and only the cookie is ever persisted). Breaks the day NAASA adds MFA or a CAPTCHA.
    """
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [("User-Agent", "Mozilla/5.0"),
                     ("Accept", "text/html,application/json,*/*")]

    # 1) CSRF token, then ask NextAuth for the Keycloak authorize URL.
    csrf = json.load(op.open(NX + "/api/auth/csrf", timeout=30))["csrfToken"]
    signin = urllib.parse.urlencode({"csrfToken": csrf, "callbackUrl": NX + "/",
                                     "json": "true"}).encode()
    resp = op.open(urllib.request.Request(
        NX + "/api/auth/signin/keycloak", data=signin,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"}), timeout=30)
    raw = resp.read().decode("utf-8", "replace")
    try:
        auth_url = json.loads(raw)["url"]
    except (ValueError, KeyError):
        auth_url = resp.geturl()          # NextAuth already redirected us to Keycloak

    # 2) Load the Keycloak login form (the jar picks up AUTH_SESSION_ID / KC_RESTART).
    page = op.open(auth_url, timeout=30).read().decode("utf-8", "replace")
    m = re.search(r'action="([^"]*login-actions/authenticate[^"]*)"', page)
    if not m:
        raise RuntimeError("NAASA login form not found — the page changed, or MFA is now on")
    action = html_lib.unescape(m.group(1))

    # 3) POST the credentials; success 302s back through NextAuth's callback → session cookie.
    creds = urllib.parse.urlencode({"username": email, "password": password,
                                    "credentialId": ""}).encode()
    op.open(urllib.request.Request(action, data=creds, headers={
        "Content-Type": "application/x-www-form-urlencoded"}), timeout=30).read()

    # The session JWT is big (access + id token), so NextAuth splits it across chunk cookies
    # `…session-token.0`, `.1`, … — return every chunk, in order, or the token is only half sent.
    chunks = sorted((c for c in jar if "session-token" in c.name), key=lambda c: c.name)
    if chunks:
        return "; ".join(f"{c.name}={c.value}" for c in chunks)
    raise RuntimeError("login failed — wrong email/password, or NAASA blocked the sign-in")


def report(cookie, report_type):
    """Personal report by type: ORDERBOOK (your orders), HOLDINGREPORT (your holdings).

    The nx route reads clientCode/sessionNo from the session server-side (the browser sends
    neither), so we send exactly what it does. It answers 400 "Missing sessionNo or clientCode"
    when the session has been superseded by a newer NAASA login elsewhere — re-login to fix.
    """
    body = {"reportType": report_type}
    if report_type == "ORDERBOOK":
        body |= {"fromDate": "", "toDate": ""}
    return _nx("/api/report", cookie, "POST", body)


def dashboard(cookie):
    """Collateral and trade-summary block behind the account dashboard."""
    return _nx("/api/dashboard-details", cookie, "POST", {})


# ---------------------------------------------------------------- live market feed
#
# The order screen's live Top-5 depth and stock stats are NOT a websocket — they are GET calls
# to nx's market-feed proxy, `/api/feed/Services.<Name>`, that answer with the exact numbers on
# screen. Each wraps its payload as a JSON *string* in `data`, so it is parsed twice. Authed by
# the session cookie, same as the account endpoints (so the superseded-session 400 applies).

def _feed(cookie, service, **params):
    payload = _nx(f"/api/feed/Services.{service}?{urllib.parse.urlencode(params)}", cookie)
    raw = payload.get("data")
    return json.loads(raw) if isinstance(raw, str) else (raw or [])


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def market_depth(cookie, symbol, exchange="NEPSE"):
    """Live Top-5 order book: {time, levels:[{bid_price,bid_qty,bid_orders,ask_price,ask_qty,
    ask_orders}]}. Ladder is empty pre-open; the exact data NAASA's order screen shows."""
    rows = _feed(cookie, "GetMDepth", Tickers=symbol, Exchange=exchange)
    if not rows:
        return {"time": "", "levels": []}
    d = rows[0]
    levels = [{"bid_price": _f(l.get("BBR")), "bid_qty": _f(l.get("BBQ")), "bid_orders": _f(l.get("BO")),
               "ask_price": _f(l.get("BSR")), "ask_qty": _f(l.get("BSQ")), "ask_orders": _f(l.get("SO"))}
              for l in d.get("depth", [])]
    return {"time": d.get("DateTime", ""), "levels": levels}


QUOTE_COLUMNS = ("LTP", "Open", "High", "Low", "Close", "PreviousClose", "Volume",
                 "WeightedAverage", "Turnover", "TotalBuyQty", "TotalSellQty")


def market_quote(cookie, symbol, exchange="NEPSE"):
    """Live quote: {LTP, Open, High, Low, Close, PreviousClose, Volume, WeightedAverage (avg
    price), Turnover, TotalBuyQty, TotalSellQty} as floats. Same feed the screen reads."""
    rows = _feed(cookie, "GetSpecifiedQuote", Tickers=f"{exchange}.{symbol}",
                 Columns=",".join(QUOTE_COLUMNS))
    if not rows:
        return {}
    q = rows[0]
    return {k: _f(q.get(k)) for k in QUOTE_COLUMNS}


# ---------------------------------------------------------------- live WebSocket feed
#
# NAASA streams live ticks over a WebSocket (the same feed the order screen's Top-5 ladder
# uses). Frames are `102^` + base64(zlib-deflate(`type$seq$EXCH!SYMBOL$datetime$field^field…`)),
# the client subscribes with `ADD^<count>^25.1!SYMBOL…`, and the server pushes updates.
#
# The working endpoint is the OLD NAASA X app's socket, `wss://x.naasasecurities.com.np:8006/
# WebSocket/Connect`, authed by `?UserId=<hdnLogin>&Password=<hdnSession>&protocol=WSS&
# ClientIP=<ip>&Source=1`. hdnLogin/hdnSession are hidden fields the OLD app renders after an
# OAuth login — hdnSession is a feed token, NOT the account password. _x_login() logs in, scrapes
# them, and activates the session (see there); _client_ip() supplies the ClientIP, which must be
# the real one. (The nx app's `serverx` variant needs a routing key we can't reconstruct — 503.)
# Needs `websocket-client`.

X_APP = "https://x.naasasecurities.com.np"
FEED_URL = "wss://x.naasasecurities.com.np:8006/WebSocket/Connect"
X_AUTH = ("https://auth.naasasecurities.com.np/realms/naasa/protocol/openid-connect/auth"
          "?client_id=blaze&scope=openid%20profile&response_type=code"
          "&redirect_uri=https://x.naasasecurities.com.np/login")


def _inflate(b64):
    """base64 → raw bytes → inflate (zlib / raw-deflate / gzip). '' when it can't be inflated."""
    try:
        raw = base64.b64decode(b64)
    except (ValueError, TypeError):
        return ""
    for wbits in (15, -15, 47):
        try:
            return zlib.decompress(raw, wbits).decode("utf-8", "replace")
        except zlib.error:
            continue
    return ""


# Per message-type field layout, straight from the `101^` schema frame. 75/1 = stock quote,
# 77 = index quote, 76/2 = 5-level depth (repeats), the rest are summaries/circuit limits.
FEED_SCHEMA = {
    "2":  ["BestBuyRate", "BestBuyQty", "BuyOrders", "BestSellRate", "BestSellQty", "SellOrders"],
    "76": ["BestBuyRate", "BestBuyQty", "BuyOrders", "BestSellRate", "BestSellQty", "SellOrders"],
    "77": ["LTP", "High", "Low", "Open", "Close", "52WeekHigh", "52WeekLow", "TTQ", "TTV", "LTV",
           "TradedSymbolCount", "LastTradeTime"],
    "75": ["LTP", "LTQ", "LastTradeTime", "TTQ", "WeightedAverage", "BidPrice", "OfferPrice",
           "BidQty", "OfferQty", "TotalBuyQty", "TotalSellQty", "High", "Low", "Open", "Close"],
    "1":  ["LTP", "LTQ", "LastTradeTime", "TTQ", "WeightedAverage", "BidPrice", "OfferPrice",
           "BidQty", "OfferQty", "TotalBuyQty", "TotalSellQty", "High", "Low", "Open", "Close"],
    "73": ["Exchange", "TodayClose"],
    "78": ["LowerCKTLimit", "UpperCKTLimit", "52WeekHigh", "52WeekLow"],
    "74": ["TTQ", "TTV", "TradedSymbolCount", "LTP", "High", "Low", "Open", "Close"],
}


def decode_tick(message):
    """A `102^…` data frame → {type, symbol, fields}, or None. `fields` is the raw `$`-split of
    the FIRST record (kept for quick inspection); use parse_records() for the full mapped parse."""
    if not isinstance(message, str) or not message.startswith("102^"):
        return None
    body = _inflate(message.split("^", 1)[1])
    if not body:
        return None
    fields = body.split("$")
    symbol = next((f.split("!", 1)[1] for f in fields if "!" in f), "")
    return {"type": "102", "symbol": symbol, "fields": fields}


DEPTH_TYPES = {"2", "76"}
DEPTH_FIELDS = ("BuyRate", "BuyQty", "BuyOrders", "SellRate", "SellQty", "SellOrders")


def parse_records(message):
    """A `102^…` frame → list of {type, symbol, time, fields}. One frame can carry several
    newline-separated records, each `exch$type$25.X!SYMBOL$datetime$values`. Quote/index records
    (types 75/1/77…) map values by FEED_SCHEMA; depth records (76/2, key `25.2!`) carry a 5-level
    book — `|`-separated levels, each `BuyRate^BuyQty^BuyOrders^SellRate^SellQty^SellOrders` — and
    land as fields={"depth": [ {level dict} × up to 5 ]}."""
    if not isinstance(message, str) or not message.startswith("102^"):
        return []
    body = _inflate(message.split("^", 1)[1])
    out = []
    for rec in body.split("\n"):
        parts = rec.split("$")
        if len(parts) < 5:
            continue
        mtype, symkey, raw = parts[1], parts[2], parts[4]
        sym = symkey.split("!", 1)[1] if "!" in symkey else symkey
        if mtype in DEPTH_TYPES:
            levels = [dict(zip(DEPTH_FIELDS, lvl.split("^")))
                      for lvl in raw.split("|") if lvl.count("^") >= 5]
            fields = {"depth": levels}
        else:
            names, values = FEED_SCHEMA.get(mtype, []), raw.split("^")
            fields = {names[i]: values[i] for i in range(min(len(names), len(values)))}
        out.append({"type": mtype, "symbol": sym, "time": parts[3], "fields": fields})
    return out


# NEPSE headline + sector indices (from NAASA's own sectorMapMarketWatch). Streamed as type-77
# over the same socket; carry no depth book. The extras past the first 13 are subscribed too and
# simply return nothing if NEPSE doesn't publish them — the UI shows only indices that have data.
INDEX_SYMBOLS = (
    "NEPSE", "SENSIND", "FLOATIND", "SENFLOAT",
    "BANKSUBIND", "DEVBANKIND", "FININD", "HOTELIND", "HYDPOWIND",
    "INVIDX", "LIFINSIND", "MANPROCIND", "MICRFININD", "NONLIFIND",
    "OTHERSIND", "TRADIND", "MUTUALIND",
)
_INDEX_KEYS = set(INDEX_SYMBOLS)


def subscribe_frame(symbols, depth=()):
    """`ADD^<count>^25.1!SYM…^25.2!SYM…` — the quote feed (`25.1!`) for every symbol/index, plus
    the 5-level depth book (`25.2!`) for the stocks (NAASA's own order screen builds the depth key
    by swapping `.1!`→`.2!`; indices carry no book so they're left out).

    Depth is opt-in because we subscribe the WHOLE market for the archive updater: asking for
    360 order books as well would double the key count and stream ladders nobody reads. Only the
    scrip actually on screen needs one. Verified live: 360 quote keys in one ADD is accepted."""
    keys = ["25.1!%s" % s for s in symbols]
    keys += ["25.2!%s" % s for s in depth if s not in _INDEX_KEYS]
    return "ADD^%d^%s" % (len(keys), "^".join(keys))


_X_TTL = 1800         # re-login on a schedule only every 30 min: each one re-mints the
                      # session and so interrupts the socket. Staleness is caught on demand.
_X_RELOGIN_GAP = 20   # ...but never mint two sessions within this many seconds
_x_sess = {"op": None, "user_id": None, "session": None, "ts": 0.0, "gen": 0,
           "lock": threading.Lock()}


def _validate_session(op):
    """POST Login/ValidateSessionNo -> the sessionNo to use for the NEXT socket connect.

    NOT idempotent, and that is the whole point: each call mints a number and invalidates the
    previous one, and the number only becomes usable once a socket connects with it. The order
    screen calls this immediately before EVERY WS_connect(), reconnects included — a session
    number is good for exactly ONE connection. Reuse it on a reconnect and NAASA accepts the
    socket and then never sends a single frame, which is indistinguishable from a quiet market.
    """
    req = urllib.request.Request(
        X_APP + "/Login/ValidateSessionNo", data=b"{}", method="POST",
        headers={"Content-Type": "application/json; charset=utf-8",
                 "X-Requested-With": "XMLHttpRequest"})
    return json.loads(op.open(req, timeout=30).read().decode("utf-8", "replace")).get("sessionNo")


def _x_login(email, password, force=False):
    """Authenticated opener for the OLD NAASA X app (x.naasasecurities.com.np), cached module-wide
    so the WSS feed AND the order-book/holdings/collateral report calls share ONE login — exactly
    like the browser's order screen, which holds the socket and calls the report API on the same
    session. Sharing one login is what stops the reports from fighting the live feed for NAASA's
    single-active-session-per-account. Returns the cached {op, user_id, session}; re-logs in on
    `force` or once the TTL lapses."""
    with _x_sess["lock"]:
        age = time.time() - _x_sess["ts"]
        if _x_sess["op"] and age < _X_TTL and not force:
            return _x_sess
        # A re-login mints a NEW session, and NAASA allows exactly ONE per account — so every
        # forced re-login EVICTS the live socket. Order book, holdings, collateral and the index
        # table all poll every 1-3s through x_report, which force-relogins on a stale session; a
        # burst of those mints sessions faster than the feed can adopt one, so the socket never
        # survives long enough to deliver a tick. The reports win, the feed starves, and the page
        # reads "connecting" forever while the thread sits healthy in recv(). Coalesce: honour at
        # most one re-login per _X_RELOGIN_GAP so a burst SHARES a session instead of racing.
        if force and _x_sess["op"] and age < _X_RELOGIN_GAP:
            return _x_sess
        jar = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        op.addheaders = [("User-Agent", "Mozilla/5.0")]
        page = op.open(X_AUTH, timeout=30).read().decode("utf-8", "replace")
        m = re.search(r'action="([^"]*login-actions/authenticate[^"]*)"', page)
        if m:                                          # not already SSO'd from a shared session
            creds = urllib.parse.urlencode({"username": email, "password": password,
                                            "credentialId": ""}).encode()
            op.open(urllib.request.Request(html_lib.unescape(m.group(1)), data=creds, headers={
                "Content-Type": "application/x-www-form-urlencoded"}), timeout=30).read()
        order = op.open(X_APP + "/MarketOrder/Order", timeout=30).read().decode("utf-8", "replace")

        def field(name):
            hit = (re.search(r'id=["\']%s["\'][^>]*value=["\']([^"\']*)' % name, order)
                   or re.search(r'name=["\']%s["\'][^>]*value=["\']([^"\']*)' % name, order))
            return hit.group(1) if hit else None
        user_id, session = field("hdnLogin"), field("hdnSession")
        if not (user_id and session):
            raise RuntimeError("X-app login failed — could not scrape hdnLogin/hdnSession")
        # The scraped hdnSession is NOT yet usable. The order screen always POSTs
        # Login/ValidateSessionNo and swaps the answer in before it opens the socket
        # ($("#hdnSession").val(response.sessionNo); WS_connect()). Skip that and the backend
        # rejects every account-scoped report with -1006001 "Invalid sessionid" while the WSS
        # still connects but never sends a tick — a live-looking feed with no data. Activate it
        # here, once, so the socket and the reports share one working session.
        try:
            session = _validate_session(op) or session
        except (OSError, ValueError):
            pass                       # keep the scraped value; account calls fail loudly anyway
        # `gen` bumps on every successful login. Login/ValidateSessionNo above is NOT
        # idempotent — the new session number it mints INVALIDATES the previous one — so
        # anything holding the old session (the live socket) must notice and re-establish.
        _x_sess.update(op=op, user_id=user_id, session=session, ts=time.time(),
                       gen=_x_sess["gen"] + 1)
        return _x_sess


def _client_ip(op):
    """The public IP the feed expects in the handshake, from the same place the order screen takes
    it (`myip` <- /IP/IP.aspx). Hand the socket any other IP and NAASA completes the handshake and
    then never sends a single frame — a connected, silent feed that reads as live."""
    req = urllib.request.Request(X_APP + "/IP/IP.aspx", data=b"", method="POST")
    return op.open(req, timeout=25).read().decode("utf-8", "replace").strip()


def _decode_report(data):
    """An X-app report `data` field is either plain JSON (a `[`/`{`-string, e.g. Indices) or
    base64(JSON) (the order-book/holdings grids), or an already-parsed value. Return the useful
    part — a list/dict of rows where possible, else the raw value."""
    if isinstance(data, dict) and "reportTable" in data:
        # Nested report envelope: {errorCode, message, reportName, isCompressed, reportTable}.
        # The rows live in `reportTable` (base64 JSON). Returning the envelope itself makes every
        # caller's `isinstance(rows, list)` test fail, so a populated order book reads as empty.
        rows = data.get("reportTable")
        if rows is None:
            code = data.get("errorCode")
            if code not in (0, -5001001, None):   # -5001001 = "No Record found" = an empty grid
                raise RuntimeError("NAASA report %s: %s"
                                   % (data.get("reportName") or "?", data.get("message") or code))
            return []
        return _decode_report(rows)
    if isinstance(data, str):
        s = data.strip()
        if not s:
            return []
        if s[0] in "[{":                                   # already JSON text
            try:
                return json.loads(s)
            except Exception:
                return s
        try:                                               # base64(JSON)
            return json.loads(base64.b64decode(s).decode("utf-8", "replace"))
        except Exception:
            try:
                return json.loads(s)
            except Exception:
                return s
    return data


def x_report(email, password, controller, action, body=None, retry=True):
    """POST `/<controller>/<action>` on the X app (shared feed session) → decoded report. The app's
    `ExecuteAPI(POST, action, controller, body)` helper: JSON body, `{errorCode, data}` envelope
    where `data` is base64(JSON). Re-logs in once on failure (stale session).

    `retry=False` disables that second attempt. Order placement MUST use it: a transport error can
    fire after the exchange already accepted the order, so a retry would place a duplicate."""
    err = None
    for attempt in ((0, 1) if retry else (0,)):
        s = _x_login(email, password, force=(attempt == 1))
        req = urllib.request.Request(
            X_APP + "/" + controller + "/" + action,
            data=json.dumps(body or {}).encode(), method="POST",
            headers={"Content-Type": "application/json; charset=utf-8",
                     "X-Requested-With": "XMLHttpRequest", "Accept": "application/json"})
        try:
            raw = s["op"].open(req, timeout=30).read().decode("utf-8", "replace")
        except Exception as e:
            err = e
            continue
        try:
            resp = json.loads(raw)
        except Exception:
            return raw
        if isinstance(resp, str):            # double-encoded body (Indices, DashboardDetails)
            try:
                resp = json.loads(resp)
            except Exception:
                return resp
        if isinstance(resp, dict):
            # NAASA reports an expired session as HTTP 200 with an in-body error, so the transport
            # `except` above never sees it. Left unhandled the caller gets Data:[] and renders it
            # as "no open orders" / "no holdings" — a dead session that reads like a flat account.
            note = str(resp.get("Message") or resp.get("message") or "")
            code = resp.get("ErrorCode", resp.get("errorCode"))
            stale = ("session" in note.lower() or "login again" in note.lower()
                     or code == -1006001)
            if stale:
                if attempt == 0 and retry:
                    err = RuntimeError(note or "session expired")
                    continue                 # re-login with force=True and try once more
                raise RuntimeError(f"NAASA session rejected: {note or 'session expired'}")
            if "data" in resp:               # lowercase 'data' = encoded rows (base64 or JSON text)
                return _decode_report(resp["data"])
            return resp                      # e.g. DashboardDetails {ServiceName, Data:[...groups...]}
        return resp
    raise err if err else RuntimeError("x_report failed")


def x_orderbook(email, password):
    """Open orders (order book) — list of row dicts. Cols incl Scrip, B/S, RemainingQty, Price,
    OrderStatus, BrokerOrderTime, BuySellType."""
    rows = x_report(email, password, "MarketOrder", "OrderBook")
    return rows if isinstance(rows, list) else []


def x_holdings(email, password):
    """Holdings — list of row dicts. Cols incl NEPSECode, AvailableQty, LastTradedPrice,
    ClosePrice, ValueAsOfLTP, DayGainLoss."""
    rows = x_report(email, password, "TradeBook", "HoldingDataReport")
    return rows if isinstance(rows, list) else []


def x_quotes(email, password, tickers):
    """Batch quote — POST /MarketOrder/SpecifiedQuote {ticker:"NEPSE.A,NEPSE.B,…"} → list of dicts
    (ticker, LTP, High, Low, Open, Close, %Change, Volume, TTQ, 52WeekHigh/Low). Works for stocks
    AND indices, and even when the market is CLOSED (returns the closing snapshot). Shared X-app
    session, so it coexists with the live socket."""
    q = ",".join("NEPSE." + t for t in tickers)
    rows = x_report(email, password, "MarketOrder", "SpecifiedQuote", {"ticker": q})
    return rows if isinstance(rows, list) else []


def x_indices(email, password):
    """NEPSE index watchlist (headline + all sector indices) — batched SpecifiedQuote over
    INDEX_SYMBOLS. Only indices that actually publish a price are returned. Works while closed."""
    return [r for r in x_quotes(email, password, INDEX_SYMBOLS) if r.get("LTP")]


def heatmap_sectors(email, password):
    """Stock→sector map for the heatmap — GET /HeatMap/SectorStock → [{stock, sector, stockName}]."""
    for attempt in (0, 1):
        s = _x_login(email, password, force=(attempt == 1))
        try:
            raw = s["op"].open(urllib.request.Request(
                X_APP + "/HeatMap/SectorStock",
                headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}),
                timeout=30).read().decode("utf-8", "replace")
        except Exception:
            if attempt == 0:
                continue
            return []
        try:
            resp = json.loads(raw)
        except Exception:
            return []
        rows = _decode_report(resp.get("data", resp.get("Data"))) if isinstance(resp, dict) else resp
        return rows if isinstance(rows, list) else []
    return []


def x_collateral(email, password):
    """Dashboard / collateral summary (Home/DashboardDetails). Returns the dict `{Data: [...]}`
    where Data is 5 positional one-row groups: [0] order summary, [1] trade summary, [2] holdings
    totals, [3] collateral (GrossAllocatedExposure / GrossUsedExposure / GrossAvalibleExposure),
    [4] client PII (do NOT render). The body is double-encoded JSON, so parse again if needed."""
    resp = x_report(email, password, "Home", "DashboardDetails")
    if isinstance(resp, str):
        try:
            resp = json.loads(resp)
        except Exception:
            return {}
    return resp if isinstance(resp, dict) else {}


# ------------------------------------------------------------------ orders (REAL money)
# Placement is POST /MarketOrder/Order — the SAME url that GETs the order screen (the app's
# ExecuteOrderRequest posts `Order` on the `MarketOrder` controller). Read off the order screen's
# own PlaceOrder()/Cancel_Orders() builders, so our payload is field-for-field what the broker's
# web client sends. Deliberately no modify: place and cancel cover the need without a third
# money path to keep correct.
ORDER_TERMS = ("DAY", "GTD", "GTC", "IOC", "FOK")

# The constant leg of every order object. The backend answers "-102 Wrong Request Object" when a
# key is missing, so these ride along even though they never vary.
_ORDER_FIXED = {"DeliveryTerms": "D", "MarketSegment": "RL", "OrderCategory": "NORMAL",
                "OrderType": "NORMAL", "AccRefCode": "SELF", "ProductType": "CASH",
                "DisclosedQuantity": ""}


def order_body(scrip, side, quantity, price, terms="DAY", term_validity="", exchange="NEPSE"):
    """Build and validate the payload for ONE new order. Pure and side-effect free on purpose —
    the field set is what decides whether the right trade happens, so it must be testable without
    sending anything.

    A `price` of 0 is the screen's MARKET-order flag (it flips `Market` to "1"); any other price
    is a limit. Quantity rules are the screen's own ValidatePlaceOrder(): a whole number, 1..1e8-1.
    """
    side = str(side).upper().strip()
    if side not in ("BUY", "SELL"):
        raise ValueError("side must be BUY or SELL, got %r" % (side,))
    if float(quantity) != int(float(quantity)):
        raise ValueError("quantity must be a whole number, got %r" % (quantity,))
    qty = int(float(quantity))
    if not 1 <= qty <= 99999999:
        raise ValueError("quantity must be 1..99999999, got %r" % (quantity,))
    px = float(price)
    if px < 0:
        raise ValueError("price cannot be negative, got %r" % (price,))
    if terms not in ORDER_TERMS:
        raise ValueError("terms must be one of %s, got %r" % (ORDER_TERMS, terms))
    if terms == "GTD" and not term_validity:
        raise ValueError("a GTD order needs a term_validity date")
    body = dict(_ORDER_FIXED)
    body.update({
        "TradingAccount": "CNC", "Exchange": exchange, "Scrip": str(scrip).upper().strip(),
        "Quantity": str(qty), "Price": "0" if px == 0 else ("%g" % px),
        "Market": "1" if px == 0 else "0",
        "OrderTerms": terms, "TermValidity": term_validity,
        "BuySellIndicator": "B" if side == "BUY" else "S",
        "BuySellType": "Buy" if side == "BUY" else "Sell",
        "isSquareOff": 0,
    })
    return body


# Substrings, not exact matches: the status vocabulary is only partly known (REJECTED is the one
# the app's own JS tests for) and NAASA writes both CANCELLED and CANCELED in different places.
_FINISHED = ("TRADED", "COMPLETE", "CANCEL", "REJECT", "EXPIR")


def order_is_working(row):
    """Is this order still live in the market — i.e. can it still be cancelled?

    `MarketOrder/OrderBook` is really the PendingOrder report and returns TODAY's orders,
    completed ones included, so "everything in the book is open" is simply wrong. Two signals,
    because either alone has a hole: RemainingQty is the semantic truth (a filled order has
    nothing left to pull) but is blank on some rows, while the status vocabulary is incomplete.
    An UNRECOGNISED status counts as working — hiding a cancellable order is worse than offering
    one the broker will refuse, and the broker gets the final say either way.
    """
    status = str(row.get("OrderStatus") or "").upper()
    if any(done in status for done in _FINISHED):
        return False
    try:
        return float(row.get("RemainingQty")) > 0
    except (TypeError, ValueError):
        return True              # no usable quantity; the status did not say finished


def cancel_body(row):
    """Payload to cancel ONE open order, built from its order-book row. The screen rebuilds the
    whole order object and adds OrderId/TranId (both = BrokerTranID); note TradingAccount is BLANK
    here, unlike a new order."""
    tran = row.get("BrokerTranID") or row.get("LatestOrderID")
    if not tran:
        raise ValueError("order row carries no BrokerTranID — nothing to cancel")
    if not order_is_working(row):
        raise ValueError("order %s is %s — there is nothing left to cancel"
                         % (tran, row.get("OrderStatus") or "already finished"))
    side = str(row.get("B/S") or row.get("BuySellIndicator") or row.get("BuySellType") or "").upper()
    if not side.startswith(("B", "S")):
        raise ValueError("order row has no readable buy/sell side: %r" % (side,))
    body = dict(_ORDER_FIXED)
    body.update({
        "TradingAccount": "", "Exchange": row.get("Exchange") or "NEPSE",
        "Scrip": row.get("Scrip"), "Quantity": row.get("RemainingQty"),
        "Price": row.get("Price"), "Market": "0", "OrderTerms": "", "TermValidity": "",
        "BuySellIndicator": "B" if side.startswith("B") else "S",
        "BuySellType": "Buy" if side.startswith("B") else "Sell",
        "OrderId": tran, "TranId": tran, "AMOBulkIndicator": row.get("OrderStatus"),
    })
    return body


def modify_body(row, quantity, price, terms="DAY", term_validity=""):
    """Payload to change a working order's quantity/price (POST /MarketOrder/ModifyOrder).

    The screen rebuilds the whole order object and adds the identifiers, so this is `order_body`
    plus OrderId/TranId (both = BrokerTranID) and **OriginalRemainingQty**, which is what the
    exchange matches the amendment against — send the new quantity there and the change is
    rejected or, worse, applied to the wrong residue.
    """
    tran = row.get("BrokerTranID") or row.get("LatestOrderID")
    if not tran:
        raise ValueError("order row carries no BrokerTranID — nothing to modify")
    if not order_is_working(row):
        raise ValueError("order %s is %s — there is nothing left to modify"
                         % (tran, row.get("OrderStatus") or "already finished"))
    side = str(row.get("B/S") or row.get("BuySellIndicator") or row.get("BuySellType") or "")
    body = order_body(row.get("Scrip"), "BUY" if side.upper().startswith("B") else "SELL",
                      quantity, price, terms, term_validity,
                      exchange=row.get("Exchange") or "NEPSE")
    body.update({"OrderId": tran, "TranId": tran,
                 "OriginalRemainingQty": row.get("RemainingQty"),
                 "AMOBulkIndicator": row.get("OrderStatus")})
    return body


def x_modify_order(email, password, row, quantity, price, terms="DAY", term_validity=""):
    """Amend a working order. No auto-retry, same reason as x_place_order."""
    return x_report(email, password, "MarketOrder", "ModifyOrder",
                    modify_body(row, quantity, price, terms, term_validity), retry=False)


def x_place_order(email, password, scrip, side, quantity, price, terms="DAY", term_validity=""):
    """PLACE A REAL ORDER on the signed-in NAASA account. This commits real money on NEPSE.

    Returns the broker's envelope — `ErrorCode` 0 means accepted. Never auto-retries (see
    x_report's `retry`): a duplicate order is far worse than a failed one the caller can repeat.
    """
    body = order_body(scrip, side, quantity, price, terms, term_validity)
    return x_report(email, password, "MarketOrder", "Order", body, retry=False)


def x_cancel_order(email, password, row):
    """Cancel one open order, given its order-book row. No auto-retry, as for x_place_order."""
    return x_report(email, password, "MarketOrder", "CancelOrder", cancel_body(row), retry=False)


def feed_ws_url(user_id, session, client_ip):
    q = urllib.parse.quote
    return (FEED_URL + "?UserId=" + q(user_id) + "&Password=" + q(session)
            + "&protocol=WSS&ClientIP=" + q(client_ip) + "&Source=1")


FEED_POLL = 5      # socket read timeout — how often the loop re-checks stop() and the session
FEED_SILENCE = 45  # ...and how long a connected-but-silent feed is tolerated before reconnecting


def stream_ticks(email, password, symbols, on_tick, stop=None, depth=()):
    """Full live feed: log in to NAASA X, open the socket, subscribe to `symbols`, call
    on_tick(decoded) for each frame. Blocks — run in a thread. One NAASA session per account is
    active at a time, so this evicts a browser tab logged in as the same account (and vice versa)."""
    import ssl
    import websocket
    s = _x_login(email, password)
    gen = s["gen"]
    # A session number is single-use (see _validate_session), so mint a fresh one for THIS
    # connection. Without it every reconnect produced a socket that was accepted and then
    # starved. Reports read the same value, so publish it before connecting.
    fresh = _validate_session(s["op"])
    if fresh:
        _x_sess["session"] = fresh
    ws = websocket.create_connection(
        feed_ws_url(s["user_id"], _x_sess["session"], _client_ip(s["op"])), timeout=FEED_POLL,
        sslopt={"cert_reqs": ssl.CERT_NONE})
    try:
        ws.send(subscribe_frame(symbols, depth))
        last = time.time()
        while (stop is None or not stop()) and _x_sess["gen"] == gen:
            try:
                frame = ws.recv()
            except websocket.WebSocketTimeoutException:
                # NAASA only pushes on change, so a quiet book really does go seconds without a
                # frame. Treating that as death is what made the feed reconnect in a loop. Keep
                # waiting, but give up if the silence outlasts FEED_SILENCE: a session superseded
                # elsewhere goes quiet with no error at all, and only a reconnect recovers it.
                if time.time() - last > FEED_SILENCE:
                    break
                continue
            last = time.time()
            for record in parse_records(frame):
                on_tick(record)
    finally:
        ws.close()


if __name__ == "__main__":
    main()
