"""The NAASA account, READ ONLY. Holdings, open orders, collateral. Nothing that trades.

This is the only module in the API that touches an authenticated broker session, so two rules
hold it in place and both are enforced here rather than trusted to the caller:

1. **No write path exists.** naasa.py can place, modify and cancel orders. None of those three
   is imported, called, or reachable from this file, and `test_ops.py` walks the call graph to
   prove it. The Streamlit page has been display-only since it was written; putting a Place
   button on a web surface is a decision for a human, not a side effect of porting a page.

2. **Fields are allow-listed, never blocked.** `Home/DashboardDetails` returns five positional
   groups and group [4] is the client's personal details — name, address, document numbers.
   ui.py flattens all five into one dict and then happens to only render safe keys, which is one
   careless `st.json(f)` away from publishing PII. Here the flatten result is filtered through
   `_MONEY` and everything unrecognised is dropped, so a new field NAASA adds tomorrow is
   invisible by default instead of exposed by default.

The credentials live on the box in Master_data/naasa_login.txt and never leave it; this module
returns numbers, not the session.
"""
import urllib.error

import naasa

# Group [3] is collateral, [2] holdings totals, [1] trade summary, [0] order summary. Only these
# names are ever returned. Anything else in the payload — including all of group [4] — is dropped.
_MONEY = (
    "GrossAvalibleExposure",      # NAASA's spelling, not a typo on our side
    "GrossUsedExposure",
    "GrossAllocatedExposure",
    "TotalHoldingAmount",
    "HoldingStockCount",
    "TotalBuyAmount",
    "TotalSellAmount",
    "TotalOrderCount",
)


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() and abs(f) < 2 ** 53 else f


def configured():
    """Are credentials saved on this box at all? Never returns the credentials themselves."""
    try:
        email, password = naasa.load_credentials()
    except Exception:
        return False
    return bool(email and password)


class UpstreamChanged(RuntimeError):
    """NAASA's own app changed shape — nothing on our side can be signed in to."""


def _creds():
    email, password = naasa.load_credentials()
    if not (email and password):
        raise RuntimeError(
            "No NAASA login saved on the server. Sign in on the Streamlit app under "
            "'NAASA account' with Remember me — the session lives on the box, not in the browser.")
    return email, password


def _guard(fn):
    """Run a NAASA call, and name the failure when it is theirs rather than ours.

    Verified on 2026-08-20: x.naasasecurities.com.np is now a Next.js SPA. /MarketOrder/Order —
    the page every account call scrapes hdnLogin/hdnSession from — returns 404, the login page no
    longer mentions Keycloak at all, and the old authorize URL answers 400 "Invalid parameter:
    redirect_uri" for the `blaze` client on every redirect_uri we can form. So the whole X-app
    integration is dead upstream: login, order book, holdings, collateral, batch quotes, indices
    and the WebSocket feed.

    Without this, the page renders a bare "HTTP Error 400: Bad Request", which reads like OUR bug
    and sends the next person debugging our code instead of theirs.
    """
    try:
        return fn()
    except urllib.error.HTTPError as e:
        if e.code in (400, 404) and "naasasecurities" in str(getattr(e, "url", "") or ""):
            raise UpstreamChanged(
                "NAASA replaced the X app with a new single-page app, so the login flow this "
                "integration uses no longer exists. Their authorize URL rejects our redirect_uri "
                "and /MarketOrder/Order — the page holdings and orders are scraped from — is now "
                "a 404. This affects the Streamlit NAASA page and the live socket feed too; it is "
                "not specific to this screen, and nothing here can fix it without re-reading how "
                "their new app authenticates.") from e
        raise


def holdings():
    rows = _guard(lambda: naasa.x_holdings(*_creds()))
    out = []
    for r in rows:
        out.append({
            "symbol": r.get("NEPSECode") or r.get("Scrip"),
            "quantity": _num(r.get("AvailableQty")),
            "ltp": _num(r.get("LastTradedPrice")),
            "close": _num(r.get("ClosePrice")),
            "value": _num(r.get("ValueAsOfLTP")),
            "day_change": _num(r.get("DayGainLoss")),
            "wacc": _num(r.get("WACCValue") or r.get("WACC")),
        })
    return {"rows": [r for r in out if r["symbol"]], "count": len(out)}


def orderbook():
    rows = _guard(lambda: naasa.x_orderbook(*_creds()))
    out = []
    for r in rows:
        out.append({
            "symbol": r.get("Scrip"),
            "side": r.get("BuySellType") or r.get("B/S"),
            "quantity": _num(r.get("Quantity") or r.get("OrderQty")),
            "remaining": _num(r.get("RemainingQty")),
            "price": _num(r.get("Price")),
            "status": r.get("OrderStatus"),
            "time": r.get("BrokerOrderTime"),
        })
    return {"rows": [r for r in out if r["symbol"]], "count": len(out)}


def collateral():
    """The dashboard money figures, allow-listed. Group [4] (client PII) can never come through."""
    payload = _guard(lambda: naasa.x_collateral(*_creds()))
    flat = {}
    for group in (payload.get("Data") or []):
        row = group[0] if isinstance(group, list) and group else group
        if isinstance(row, dict):
            flat.update(row)
    return {"fields": {k: _num(flat.get(k)) for k in _MONEY if k in flat}}
