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

import json
import string
import urllib.error
import urllib.parse
import urllib.request

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


# ---------------------------------------------------------------- account access
#
# Credentials are never stored by this module. `login()` takes them as arguments, exchanges
# them for a token, and returns it — the caller keeps the token in memory for the session.
# Nothing here writes to disk, and no order-placing endpoint is implemented on purpose.

AUTH = "https://auth.naasasecurities.com.np/realms/naasa/protocol/openid-connect/token"
CLIENT_ID = "blaze"


USERINFO = "https://auth.naasasecurities.com.np/realms/naasa/protocol/openid-connect/userinfo"


def whoami(token):
    """Validate a pasted token against Keycloak's userinfo endpoint.

    This is how the app checks a token is real and unexpired without ever seeing a
    password: userinfo answers 200 with the account's profile, or 401 once it lapses.
    """
    return authed(USERINFO, token)


def login(username, password):
    """Keycloak direct-grant — NOT available on this realm.

    Kept for reference: the `blaze` client answers 401 unauthorized_client, so the only
    way in is the browser's authorization-code flow, whose redirect URI is whitelisted to
    NAASA's own domain. A token therefore has to be copied out of a signed-in session.
    """
    body = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "grant_type": "password",
        "scope": "openid profile",
        "username": username,
        "password": password,
    }).encode()
    req = urllib.request.Request(AUTH, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"login rejected ({e.code}): {detail}") from None


def authed(url, token, timeout=30):
    """GET an endpoint that needs the session token. Read-only by construction."""
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


if __name__ == "__main__":
    main()
