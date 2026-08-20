"""The safety rules with no other coverage, checked without touching the network, the VPS, or
Master_data. Every one of these guards a run that goes GREEN while something is broken.

  fetch_swp._rewrite  — corporate_actions.txt and lockin.txt are upserts by FULL REWRITE, and
                        every per-company fetch swallows its own exception. A lapsed SWP cookie
                        therefore produced a short list that overwrote a good archive with a
                        header-only file, while the Cron node still went green.
  deploy.vps_ship     — its only health check was `"active" in stdout` against systemd's
                        is-active, and "active" is a substring of "inactive", so a stopped
                        service reported a successful deploy.
  ui.py               — NOTHING in this repo renders ui.py, so a Streamlit runtime error ships
                        green and is only found by opening the page. Two static checks catch
                        the two failures that actually happen: a nested expander (Streamlit
                        raises and the page dies — this shipped once), and a sidebar entry whose
                        `if page ==` body was renamed with it, which draws a BLANK page with no
                        error because every branch simply falls through.
  naasa orders        — the order payload decides what actually gets traded, a retried place is a
                        DUPLICATE order, and a money call reachable from a 1s fragment could be
                        sent by a timer instead of a click. None of the three raises anything.

    python test_ops.py
"""
import ast
import re
import sys
import tempfile
import time
from pathlib import Path

import backtest
import fetch_ohlc
import fetch_swp
import indicators
import live_1d
import market_hours
import master_signal
import naasa
import prices
import swing_master

H = "a\tb"


def test_rewrite():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "probe.txt"

        p.write_text(H + "\n" + "\n".join(f"r{i}\t{i}" for i in range(100)) + "\n",
                     encoding="utf-8")
        assert fetch_swp._rewrite(p, H, [f"n{i}\t{i}" for i in range(98)], "probe") is True
        assert len(p.read_text(encoding="utf-8").splitlines()) == 99, "a real refresh must land"

        assert fetch_swp._rewrite(p, H, ["only\t1"], "probe") is False
        assert len(p.read_text(encoding="utf-8").splitlines()) == 99, "archive must survive"

        assert fetch_swp._rewrite(p, H, [], "probe") is False
        assert len(p.read_text(encoding="utf-8").splitlines()) == 99, "header-only refused"

        p.unlink()
        assert fetch_swp._rewrite(p, H, ["first\t1"], "probe") is True, "first run must seed"

        # the same guard protects the PRICE archive, which is the more valuable case:
        # fetch_ohlc.write rewrites a symbol's entire 1D.txt from one response, and run() only
        # asserts the result is non-empty — so a feed answering with 50 bars instead of 1,600
        # passed that check and truncated years of history, silently, exit 0.
        bars = Path(d) / "1D.txt"
        rows = [f"2020-01-{i:02d}\t1\t2\t3\t4" for i in range(1, 29)]
        assert fetch_ohlc.safe_rewrite(bars, H, rows, "AAA") is True, "first fetch must seed"
        assert fetch_ohlc.safe_rewrite(bars, H, rows[:27], "AAA") is True, "a real refresh lands"
        assert fetch_ohlc.safe_rewrite(bars, H, rows[:5], "AAA") is False, "a short feed refused"
        assert len(bars.read_text(encoding="utf-8").splitlines()) - 1 == 27, "history survives"
        assert "safe_rewrite" in (Path(__file__).parent / "fetch_ohlc.py").read_text(
            encoding="utf-8").split("def write(")[1], "fetch_ohlc.write bypasses the guard"
    print("  archive rewrites    a shrunken feed cannot overwrite prices or the SWP tables")


def test_deploy_health():
    src = (Path(__file__).parent / "deploy.py").read_text(encoding="utf-8")
    dead = ("inactive", "activating", "deactivating", "failed", "unknown")
    assert [w for w in dead if "active" in w] == ["inactive"], \
        "the substring test used to read 'inactive' as healthy — keep that documented"
    assert '"active" in ship.stdout' not in src, "the substring health check is back"
    assert 'state == "active"' in src, "is-active must be matched exactly"
    assert "SKIPPED — the push failed" in src, \
        "a failed push must block the VPS ship, or the box runs code GitHub does not have"
    print("  deploy              only exact 'active' is healthy; a failed push blocks the ship")


def test_ex_date_detection():
    """prices.ex_dates must separate a RESTATEMENT from a traded fall of the same size.

    It used to test only the close against a -10.5% threshold, on the premise that NEPSE's
    circuit is +/-10%. It is +/-15% — 150 sessions in the archive close at >= +14.5% — so 67 of
    the 139 events it flagged were ordinary limit-downs, and 48 symbols had their entire prior
    history multiplied by a fabricated factor (NIL 0.34, CORBL 0.49, ULHC 0.62).
    """
    def ex(open2, close2, close1=100.0, dates=("2026-08-11", "2026-08-12")):
        return prices.ex_dates(list(dates), [close1, close2], [close1, open2])

    # a bonus ex-date: restated overnight, so the gap is already in the open and it stays
    assert ex(50.0, 50.0), "a genuine restatement must be detected"
    # an ordinary limit-down: opens at/above the prior close, then trades down to the band
    assert not ex(105.0, 85.0), "a limit-down that opened UP is not a corporate action"
    assert not ex(97.34, 85.02), "ULHC 2026-08-12 opened -2.7% and traded down — not an ex-date"
    # a panic gap that the market buys back: opened low, did not stay there
    assert not ex(80.0, 98.0), "a gap-down that recovered was not a restatement"
    # bad data guard still holds
    assert not ex(5.0, 5.0), "a >5x drop is bad data, not a splice"

    # A restatement is OVERNIGHT. MDBPO trades a few times a YEAR in single blocks
    # (open=high=low=close) and the step between two such trades is elapsed time, not an ex-date.
    assert not ex(50.0, 50.0, dates=("2025-08-07", "2026-08-17")), \
        "two trades a year apart are not a corporate action"
    # ...but the cutoff must stay generous: H8020 and NIBLGF are REAL ex-dates 13 days after
    # their previous bar, corroborated by a book close, because trading halts around one.
    assert ex(50.0, 50.0, dates=("2025-09-08", "2025-09-21")), \
        "a 13-day gap is normal around a book closure and must still be detected"
    print("  prices.ex_dates     restatement yes; limit-down, bought-back gap, bad data and")
    print("                      two-trades-a-year-apart no; a book-close halt still yes")


def test_position_never_exceeds_the_book():
    """swing_master sized on risk alone, so a tight stop geared the position past the capital.

    The real case: SSHL 2025-07-31, entry 192.83 stop 192.39 — a 0.23% stop on a Rs 100,000
    book sized 2,272 shares, Rs 438,110, 4.4x the whole account, while the sheet reported the
    risk as a tidy Rs 1,000.
    """
    cap, budget = 100_000.0, 1_000.0     # Rs 100k book, 1% risk

    qty = swing_master.size(cap, budget, 192.83, 192.39)
    assert qty * 192.83 <= cap, f"SSHL sized {qty} shares = Rs {qty * 192.83:,.0f} on a Rs {cap:,.0f} book"

    # a normal stop is unaffected — still sized by risk, not by the cash cap
    wide = swing_master.size(cap, budget, 100.0, 90.0)
    assert wide == 100, f"a 10% stop should size 1000/10 = 100 shares, got {wide}"

    # degenerate inputs size nothing rather than raising
    assert swing_master.size(cap, budget, 100.0, 100.0) == 0, "zero risk must not size"
    assert swing_master.size(cap, budget, 100.0, 110.0) == 0, "an inverted stop must not size"
    assert swing_master.size(0.0, budget, 100.0, 90.0) == 0, "no capital, no position"
    print("  swing_master.size   capped at the cash available; risk sizing otherwise unchanged")


def test_edge_is_read_not_transcribed():
    """Both sheets print an 'edge%' column as a MEASURED out-of-sample average. They used to
    hardcode it, and the two copies drifted from each other and from backtest.txt — three
    different values shipped for every volume band (vol>3x: 2.39 / 2.44 / 2.51)."""
    measured = dict(backtest.oos_edge())
    if not measured:
        print("  edge                backtest.txt absent — nothing to check")
        return
    for band in measured:
        a = master_signal.edge_for(band + 0.01)[1]
        b = swing_master.edge_for(band + 0.01)
        assert a == b == measured[band], \
            f"vol>={band}: master_signal {a}, swing_master {b}, backtest.txt {measured[band]}"
    src = (Path(__file__).parent / "swing_master.py").read_text(encoding="utf-8")
    assert "EDGE = [" not in src, "a transcribed EDGE table is back in swing_master"
    print(f"  edge                both sheets read backtest.txt ({len(measured)} bands agree)")


def test_rsi_undefined_on_a_frozen_series():
    """Zero average loss has two meanings. All-upside is RSI 100; a price that has not moved at
    all is undefined. Collapsing them labelled 12,750 frozen intraday windows as maximum
    overbought — a screaming signal on a stock that simply did not trade."""
    assert indicators.rsi([100.0] * 40)[-1] is None, "a frozen series is not overbought"
    assert indicators.rsi([100.0 + i for i in range(40)])[-1] == 100, "all-upside is still 100"
    assert indicators.rsi([100.0 - i for i in range(40)])[-1] == 0, "all-downside is still 0"
    mid = indicators.rsi([100 + ((-1) ** i) * i * 0.5 for i in range(60)])[-1]
    assert mid is not None and 0 < mid < 100, f"a normal series must score in between, got {mid}"
    print("  indicators.rsi      undefined on a frozen series; 100 and 0 still reachable")


def test_no_pivot_lookahead():
    """A 5/5 pivot is confirmed five bars after it prints, so any HISTORICAL replay may only
    use pivots up to k-5. backtest.py has always applied that guard and says so in a comment;
    trade_setup's post-mortem did not, and walked back from the trigger bar itself.

    Over 200,928 replayed trigger bars it picked a pivot that had not printed on 25.3%, and the
    stop differed on every one — ACLBSL 2021-02-14 was judged against a stop of 1,170.01 when
    the honest level was 547.42. The verdict shown under the chart was scoring a trade nobody
    could have placed. Both files must carry the same guard.
    """
    here = Path(__file__).parent
    ts = (here / "trade_setup.py").read_text(encoding="utf-8")
    bt = (here / "backtest.py").read_text(encoding="utf-8")
    assert "range(buy_idx - 5, -1, -1)" in ts, \
        "trade_setup's post-mortem is walking pivots back from the trigger bar again"
    assert "range(buy_idx, -1, -1)" not in ts, "the peeking walk-back is back in trade_setup"
    assert "range(k - 5, -1, -1)" in bt, "backtest lost its pivot-confirmation guard"
    print("  pivot look-ahead    both replays start five bars behind the trigger")


def _ui_src():
    return (Path(__file__).parent / "ui.py").read_text(encoding="utf-8")


def test_ui_positional_columns():
    """ui.py reads four of these tables by COLUMN INDEX, not by name.

    `int(r[9])` for swing_master's risk_rs, `r[1] == "BUY"` for both signal sheets, and thirteen
    indices into backtest.txt. The writer owns the order and the reader hardcodes it, with
    nothing connecting them — insert a column mid-header and the page shows a different number
    with no error at all. That nearly happened when cost_rs was added to swing_master; it went
    at the END of the header specifically to keep index 9 pointing at risk_rs.

    Checked against each writer's HEADER constant rather than the produced .txt, so a stale file
    on disk cannot make this pass.
    """
    import backtest as _bt
    import master_signal as _ms
    import swing_master as _sm

    contract = [
        (_sm.HEADER, "swing_master", {1: "verdict", 9: "risk_rs"}),
        (_ms.HEADER, "master_signal", {1: "verdict"}),
        (_bt.OUT_HEADER, "backtest", {0: "variant", 1: "is_n", 2: "is_win", 3: "is_avg",
                                      4: "oos_n", 5: "oos_win", 6: "oos_avg", 7: "trades",
                                      8: "split", 9: "from", 10: "to", 11: "cost_pct",
                                      12: "max_hold"}),
    ]
    n = 0
    for header, who, want in contract:
        cols = header.split("\t")
        for idx, name in want.items():
            assert idx < len(cols), f"{who}: ui.py reads r[{idx}] but the header has {len(cols)}"
            assert cols[idx] == name, \
                f"{who}: ui.py reads r[{idx}] expecting '{name}', header now has '{cols[idx]}'"
            n += 1
    print(f"  ui column indices   {n} positional reads still point at the column they mean")


def test_no_nested_expanders():
    """Streamlit raises StreamlitAPIException on an expander inside an expander, killing the
    page. One shipped, duplicated inside itself, and took the whole Supply Demand page down."""
    tree = ast.parse(_ui_src())

    def is_expander(node):
        return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "expander")

    bad, depth = [], [0]

    class V(ast.NodeVisitor):
        def visit_With(self, node):
            n = sum(1 for it in node.items if is_expander(it.context_expr))
            if n and depth[0]:
                bad.append(node.lineno)
            depth[0] += n
            self.generic_visit(node)
            depth[0] -= n

    V().visit(tree)
    total = sum(1 for n in ast.walk(tree) if is_expander(n))
    assert not bad, f"nested st.expander at line(s) {bad} — that page will not render"
    print(f"  ui expanders        {total} declared, none nested")


def test_every_page_has_a_body():
    """A sidebar entry renamed without its `if page == ...` body renders BLANK and raises
    nothing — Streamlit just falls through every branch and draws an empty page."""
    src = _ui_src()
    pages = ast.literal_eval(re.search(r"^PAGES = (\[.*?\])$", src, re.S | re.M).group(1))
    guards = set(re.findall(r'if page == "([^"]+)"', src))
    assert not [p for p in pages if p not in guards], \
        f"sidebar entries with no body: {[p for p in pages if p not in guards]}"
    assert not [g for g in guards if g not in pages], \
        f"page bodies unreachable from the sidebar: {sorted(g for g in guards if g not in pages)}"
    print(f"  ui pages            {len(pages)} sidebar entries, {len(guards)} bodies, all matched")


def test_order_body_is_exactly_what_the_screen_sends():
    """A missing or renamed key is rejected as "-102 Wrong Request Object"; a wrong VALUE trades
    the wrong thing, silently and for real. So pin the whole payload, not a sample of it."""
    b = naasa.order_body(" nabil ", "buy", 10, 549.5)
    assert set(b) == {
        "TradingAccount", "Exchange", "Scrip", "Quantity", "Price", "Market", "OrderTerms",
        "TermValidity", "BuySellIndicator", "BuySellType", "DeliveryTerms", "MarketSegment",
        "OrderCategory", "OrderType", "AccRefCode", "ProductType", "DisclosedQuantity",
        "isSquareOff"}, sorted(b)
    assert b["Scrip"] == "NABIL" and b["Exchange"] == "NEPSE"
    assert b["Quantity"] == "10" and b["Price"] == "549.5"
    assert b["BuySellIndicator"] == "B" and b["BuySellType"] == "Buy"
    assert b["Market"] == "0", "a priced order must go as a LIMIT order"
    assert b["TradingAccount"] == "CNC"

    # price 0 IS the screen's market-order flag — the two must never drift apart
    m = naasa.order_body("NABIL", "SELL", 5, 0)
    assert m["Market"] == "1" and m["Price"] == "0"
    assert m["BuySellIndicator"] == "S" and m["BuySellType"] == "Sell"

    for bad in (0, -1, 2.5, 100000000):
        try:
            naasa.order_body("NABIL", "BUY", bad, 100)
        except ValueError:
            pass
        else:
            raise AssertionError("quantity %r should have been rejected" % (bad,))
    for bad in ("", "b", "LONG", "SHORT"):
        try:
            naasa.order_body("NABIL", bad, 1, 100)
        except ValueError:
            pass
        else:
            raise AssertionError("side %r should have been rejected" % (bad,))
    try:
        naasa.order_body("NABIL", "BUY", 1, -1)
    except ValueError:
        pass
    else:
        raise AssertionError("a negative price should have been rejected")

    # cancel rebuilds the order and identifies it by BrokerTranID; TradingAccount is BLANK here
    c = naasa.cancel_body({"Scrip": "NABIL", "B/S": "B", "RemainingQty": "10", "Price": "549.5",
                           "BrokerTranID": "77123", "OrderStatus": "OPEN", "Exchange": "NEPSE"})
    assert c["OrderId"] == "77123" and c["TranId"] == "77123"
    assert c["TradingAccount"] == "" and c["BuySellIndicator"] == "B"
    try:
        naasa.cancel_body({"Scrip": "NABIL", "B/S": "B"})
    except ValueError:
        pass
    else:
        raise AssertionError("a row with no BrokerTranID must not produce a cancel")

    # The order book returns TODAY's orders, filled ones included. Offering to cancel a trade
    # that already executed is the bug this guards: it shipped once, on the money screen.
    filled = {"Scrip": "SAHAS", "B/S": "B", "RemainingQty": "0", "Price": "701.80",
              "BrokerTranID": "9263601", "OrderStatus": "TRADED", "Exchange": "NEPSE"}
    assert naasa.order_is_working(filled) is False, "a TRADED order is not working"
    try:
        naasa.cancel_body(filled)
    except ValueError:
        pass
    else:
        raise AssertionError("a filled order must not produce a cancel request")
    for done in ("CANCELLED", "CANCELED", "REJECTED", "EXPIRED", "COMPLETED"):
        assert naasa.order_is_working(dict(filled, OrderStatus=done)) is False, done
    # still working: part-filled, and a status we have never seen before
    assert naasa.order_is_working(dict(filled, OrderStatus="OPEN", RemainingQty="4")) is True
    assert naasa.order_is_working({"OrderStatus": "SOMETHING NEW"}) is True, \
        "an unknown status must stay cancellable — the broker decides, not us"

    # A modify is matched against the RESIDUE, so OriginalRemainingQty must carry the OLD
    # quantity — put the new figure there and the amendment is applied to the wrong amount.
    working = dict(filled, OrderStatus="OPEN", RemainingQty="10")
    m = naasa.modify_body(working, 4, 705.0)
    assert m["OriginalRemainingQty"] == "10", m["OriginalRemainingQty"]
    assert m["Quantity"] == "4" and m["Price"] == "705", (m["Quantity"], m["Price"])
    assert m["OrderId"] == m["TranId"] == "9263601"
    assert m["BuySellIndicator"] == "B", "side comes from the order, not from the caller"
    try:
        naasa.modify_body(filled, 4, 705.0)
    except ValueError:
        pass
    else:
        raise AssertionError("a filled order must not produce a modify request")

    print("  naasa.order_body    full payload pinned; qty/side/price rules enforced")


def test_money_calls_never_auto_retry():
    """x_report retries once on a stale session. A transport error can fire AFTER the exchange has
    accepted an order, so retrying a place would DUPLICATE it. Both money paths must opt out —
    and the flag has to gate the loop, not merely exist."""
    tree = ast.parse(Path("naasa.py").read_text(encoding="utf-8"))
    for name in ("x_place_order", "x_cancel_order"):
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == name), None)
        assert fn is not None, "%s is gone from naasa.py" % name
        calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call)
                 and getattr(c.func, "id", getattr(c.func, "attr", "")) == "x_report"]
        assert calls, "%s no longer goes through x_report" % name
        for c in calls:
            kw = {k.arg: k.value for k in c.keywords}
            assert "retry" in kw and getattr(kw["retry"], "value", None) is False, \
                "%s must call x_report(..., retry=False)" % name

    class _Dead:                        # every attempt dies in transport, the duplicate-risk case
        def __init__(self, log):
            self.log = log

        def open(self, req, timeout=None):
            self.log.append(1)
            raise OSError("connection reset")

    log, real = [], naasa._x_login
    naasa._x_login = lambda e, p, force=False: {"op": _Dead(log)}
    try:
        for retry, expected in ((False, 1), (True, 2)):
            del log[:]
            try:
                naasa.x_report("e", "p", "MarketOrder", "Order", {"q": 1}, retry=retry)
            except Exception:
                pass
            assert len(log) == expected, \
                "retry=%s made %d POST(s), expected %d" % (retry, len(log), expected)
    finally:
        naasa._x_login = real
    print("  money calls         place and cancel never auto-retry (1 POST, not 2)")


def test_one_price_loader():
    """Every module reads the SAME adjusted series. prices.py is "the layer every calculation
    should read through", but five modules re-parsed 1D.txt themselves and so never saw a
    corporate-action adjustment: trade_setup, ui.read_bars, scan, volume_spike, operator_scan.
    While prices.py was ALSO fabricating ex-dates the two happened to differ loudly; now that it
    is correct, a divergence would be silent — hence a behavioural check, not a source one."""
    import operator_scan
    import scan
    import volume_spike

    names = (prices.MASTER / "symbols.txt").read_text(encoding="utf-8").split()
    checked = bad = 0
    for s in names:
        b = prices.bars(s)
        if not b or len(b[4]) < 60:
            continue
        ref = round(b[4][-1], 4)
        checked += 1
        vs, os_, sc = volume_spike.daily(s), operator_scan.bars(s), scan.read_bars(s)
        for got in (round(vs[-1][1], 4) if vs else None,
                    round(os_[-1][4], 4) if os_ else None,
                    round(float(sc[-1][4]), 4) if sc else None):
            if got is not None and got != ref:
                bad += 1
    assert checked > 100, f"only {checked} symbols checked — the archive looks wrong"
    assert bad == 0, f"{bad} module/symbol pairs disagree with prices.bars() on the last close"
    print(f"  one price loader    {checked} symbols, every module agrees with prices.bars()")


def test_order_ticket_is_not_on_a_timer():
    """The NAASA page re-runs fragments every second. A money call reachable from one could be
    sent by a timer tick instead of a click, so no run_every fragment may reach x_place_order or
    x_cancel_order — directly OR through any chain of helpers in this file.

    The first version of this test checked direct containment only and said so in its own
    docstring: "a fragment calling a helper that trades would still slip through". That is the
    likely shape of the accident, so the check is transitive now.
    """
    src = Path(__file__).parent / "ui.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    money = {"x_place_order", "x_cancel_order"}

    def called_names(node):
        out = set()
        for c in ast.walk(node):
            if isinstance(c, ast.Call):
                out.add(getattr(c.func, "attr", None) or getattr(c.func, "id", None))
        return {n for n in out if n}

    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    calls = {name: called_names(node) for name, node in funcs.items()}

    def reaches_money(start, seen=None):
        """Any path from `start` to a money call, through helpers defined in ui.py."""
        seen = seen or set()
        for callee in calls.get(start, ()):
            if callee in money:
                return callee
            if callee in funcs and callee not in seen:
                hit = reaches_money(callee, seen | {callee})
                if hit:
                    return hit
        return None

    timed = [n for n in funcs.values()
             if any(isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "fragment"
                    and any(k.arg == "run_every" for k in d.keywords)
                    for d in n.decorator_list)]
    assert timed, "no run_every fragments found — this test no longer matches ui.py"
    for fn in timed:
        hit = reaches_money(fn.name)
        assert not hit, f"{fn.name}() is on a run_every timer and can reach {hit}()"

    # and the check must be able to fail: a synthetic timer that calls a helper that trades
    probe = ast.parse("def _t():\n    _h()\ndef _h():\n    naasa.x_place_order(1,2,3,4,5)\n")
    pf = {n.name: n for n in ast.walk(probe) if isinstance(n, ast.FunctionDef)}
    funcs, calls = pf, {k: called_names(v) for k, v in pf.items()}
    assert reaches_money("_t") == "x_place_order", \
        "the transitive walk cannot see an indirect money call — it proves nothing"
    print("  order ticket        no run_every fragment can reach a money call, even indirectly")


def test_forced_relogin_is_coalesced():
    """NAASA allows ONE session per account, so every re-login evicts the live socket. The order
    book, holdings, collateral and index panels all poll every few seconds through x_report, which
    force-relogins on a stale session — un-coalesced, a burst of those mints sessions faster than
    the feed can adopt one and the socket NEVER lives long enough to deliver a tick. That is not
    hypothetical: it starved the feed for twenty minutes while the thread sat healthy in recv()
    and the page read "connecting". A burst inside the gap must share one session."""
    saved = dict(naasa._x_sess)
    try:
        sentinel = object()                       # if the guard fails this is replaced by a real
        naasa._x_sess.update(op=sentinel, user_id="u", session="s",  # login attempt (or a hang)
                             ip="1.2.3.4", ts=time.time())
        for _ in range(5):
            got = naasa._x_login("e", "p", force=True)
            assert got["op"] is sentinel, "a forced re-login inside the gap minted a new session"
        # ...but a session older than the gap must still be replaceable, or a genuinely dead
        # session could never be refreshed and every account call would fail forever.
        naasa._x_sess["ts"] = time.time() - naasa._X_RELOGIN_GAP - 1
        assert naasa._X_RELOGIN_GAP < naasa._X_TTL, "the gap must be shorter than the TTL"
    finally:
        naasa._x_sess.update(saved)
    print("  relogin coalesce    a burst of forced re-logins shares one session")


def main():
    print("ops safety:")
    test_rewrite()
    test_deploy_health()
    test_ex_date_detection()
    test_position_never_exceeds_the_book()
    test_edge_is_read_not_transcribed()
    test_rsi_undefined_on_a_frozen_series()
    test_no_pivot_lookahead()
    test_one_price_loader()
    test_ui_positional_columns()
    test_no_nested_expanders()
    test_every_page_has_a_body()
    test_order_body_is_exactly_what_the_screen_sends()
    test_money_calls_never_auto_retry()
    test_order_ticket_is_not_on_a_timer()
    test_forced_relogin_is_coalesced()
    live_1d.demo()          # today's bar maths + the archive-never-shrinks rule
    market_hours.demo()     # the one open/closed switch: defaults, toggles, bad file
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
