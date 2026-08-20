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

    Re-measured 2026-08-20 22:53 NPT, because the earlier diagnosis here was wrong in a way that
    sends you to the wrong place. What is actually true:

      * `/MarketOrder/Order` is **307**, not 404 — it redirects to the new SPA login. The page is
        still served; it just no longer renders the hidden hdnLogin/hdnSession inputs.
      * The break is one step earlier, at Keycloak: the authorize URL answers **400 "Invalid
        parameter: redirect_uri"** for client `blaze` with redirect_uri
        `https://x.naasasecurities.com.np/login`. That client/redirect pair is no longer
        registered.
      * x.naasasecurities.com.np is now a Next.js SPA and its login logic lives in JS bundles, so
        recovering this means re-reading how the new app authenticates.

    Worth knowing: this integration was working earlier the SAME day — a real order was placed
    through it at 12:52 NPT — so the cutover happened mid-session. Do not assume "dead upstream"
    is permanent without re-probing; and do not assume it is alive because it worked this morning.

    Without this, the page renders a bare "HTTP Error 400: Bad Request", which reads like OUR bug
    and sends the next person debugging our code instead of theirs.
    """
    try:
        return fn()
    except urllib.error.HTTPError as e:
        if e.code in (400, 404) and "naasasecurities" in str(getattr(e, "url", "") or ""):
            raise UpstreamChanged(
                "NAASA replaced the X app with a Next.js single-page app, and the Keycloak client "
                "this integration logs in with is no longer registered: their authorize URL "
                "answers 400 'Invalid parameter: redirect_uri' for client 'blaze'. The old order "
                "screen still responds (307 to the new login), so the break is the login flow, "
                "not the page. This hits the Streamlit NAASA page and the live socket feed "
                "identically — it is not specific to this screen — and fixing it means re-reading "
                "how their new app authenticates.") from e
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
