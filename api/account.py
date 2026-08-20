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


def _creds():
    email, password = naasa.load_credentials()
    if not (email and password):
        raise RuntimeError(
            "No NAASA login saved on the server. Sign in on the Streamlit app under "
            "'NAASA account' with Remember me — the session lives on the box, not in the browser.")
    return email, password


def holdings():
    rows = naasa.x_holdings(*_creds())
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
    rows = naasa.x_orderbook(*_creds())
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
    payload = naasa.x_collateral(*_creds())
    flat = {}
    for group in (payload.get("Data") or []):
        row = group[0] if isinstance(group, list) and group else group
        if isinstance(row, dict):
            flat.update(row)
    return {"fields": {k: _num(flat.get(k)) for k in _MONEY if k in flat}}
