"""The safety rules with no other coverage, checked without touching the network, the VPS, or
Master_data. Every one of these guards a run that goes GREEN while something is broken.

  fetch_swp._rewrite  — corporate_actions.txt and lockin.txt are upserts by FULL REWRITE, and
                        every per-company fetch swallows its own exception. A lapsed SWP cookie
                        therefore produced a short list that overwrote a good archive with a
                        header-only file, while the Cron node still went green.
  deploy.vps_ship     — its only health check was `"active" in stdout` against systemd's
                        is-active, and "active" is a substring of "inactive", so a stopped
                        service reported a successful deploy.
  naasa orders        — the order payload decides what actually gets traded, a retried place is a
                        DUPLICATE order, and a money call reachable from a 1s fragment could be
                        sent by a timer instead of a click. None of the three raises anything.

    python test_ops.py
"""
import ast
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import backtest
import fetch_ohlc
import fetch_swp
import feed_publisher
import feed_snap
import indicators
import jobs
import live_1d
import market_hours
from api import access
from api import stores
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


def test_no_detail_file_survives_for_an_unanalysed_symbol():
    """A symbol the engine no longer analyses must not still have a report on disk.

    Narrowing the universe (mutual funds, indices, promoter shares) left the previous
    build's 69 reports in place, and `/api/swingquantam/NABILP` served a full 103-section
    analysis from a build that will never run again -- a stale read presented as current,
    which is this project's named failure mode.

    The check is on the SHIPPED directory, not on the code, because that is where the
    symptom lives: a correct pruner that nobody calls looks identical from the source.
    """
    from swing_quantam import loader, store

    out = Path(store.OUT)
    if not out.is_dir():
        print("  swing_quantam       detail pruning (board not built yet)")
        return
    ex = loader.excluded()
    boards = {"board.txt", "brokers.txt", "probability.txt"}
    orphans = sorted(
        f.stem for f in out.glob("*.txt") if f.name not in boards and f.stem in ex
    )
    assert not orphans, (
        f"{len(orphans)} detail file(s) remain for symbols the engine no longer analyses, "
        f"e.g. {orphans[:5]} -- a full run must call store.prune_excluded()")

    # and the builder must actually invoke the pruner, or the next narrowing repeats this
    src = (Path(__file__).parent / "swing_quantam" / "__main__.py").read_text(encoding="utf-8")
    assert "store.prune_excluded(loader.excluded())" in src, (
        "the full run no longer prunes detail files for excluded symbols")

    print(f"  swing_quantam       no detail file for any of the {len(ex)} unanalysed symbols")


def test_promoter_and_fund_exclusion_uses_the_name_not_the_suffix():
    """swing_quantam must ignore mutual funds, indices and promoter shares -- by NAME.

    The ticker suffix looks like the obvious rule and is wrong in both directions.
    `NADEP` and `RRHP` are ordinary companies whose tickers merely end in P, and the
    exchange spells the real promoter lines four ways -- "Promoter Share", "PROMOTER
    SHARE", "Promotor Share" (sic) and lowercase "promotor" -- so an exact-case
    "Promoter" test misses 8 of the 121, NABILP among them.

    Pinned because both failure modes are silent: a wrong rule does not error, it just
    quietly analyses the wrong universe, and every board downstream inherits it.
    """
    from swing_quantam import loader

    ex = loader.excluded()
    assert ex, "the exclusion set is empty -- instruments.txt unreadable?"

    for s in ("NABILP", "AKBSLP", "ICFCPO", "KBLPO", "MBLPO", "SJLICP", "PMLIP"):
        assert s in ex, f"{s} is a promoter share and must be excluded"
    for s in ("C30MF", "CMF1", "NIBSF2"):
        assert s in ex, f"{s} is a mutual fund and must be excluded"
    for s in ("NADEP", "RRHP", "NABIL", "ADBL", "NTC"):
        assert s not in ex, (
            f"{s} is an ordinary company and must NOT be excluded -- a ticker-suffix "
            "rule has replaced the name rule")

    # and the universe must actually shrink, or the filter is not wired to the callers
    universe = loader.symbols()
    assert universe and not (set(universe) & ex), "excluded symbols reached loader.symbols()"

    print(f"  universe            {len(universe)} analysable; {len(ex)} funds/indices/"
          f"promoters excluded by name")


def test_deploy_ships_paths_with_spaces():
    """The ship must send every tracked file, including paths containing a space.

    `git ls-files` piped through `.split()` shredded `quantam_nepse_landing page/...` into
    the two nonexistent names `quantam_nepse_landing` and `page/...`. tar errored on each
    fragment, ALL 111 files under that directory silently never reached the box (measured:
    the tree was absent there), the printed count was 379 tokens for 268 real files, and
    every deploy still said "deploy ok" because only `systemctl is-active` was consulted.

    Pinned three ways, because each defect is independently re-introducible.
    """
    root = Path(__file__).parent
    src = (root / "deploy.py").read_text(encoding="utf-8")

    # 1. the path list must be NUL-separated, never whitespace-split
    assert 'run(["git", "ls-files", "-z"]' in src, (
        "deploy.py must ask git for NUL-separated paths")
    # narrowly: the GIT call, not every .split() in the file — `ship.stdout.split()`
    # splits systemctl's one-word answer and is fine.
    git_lines = [l for l in src.splitlines() if "ls-files" in l]
    assert git_lines, "deploy.py no longer asks git for the file list"
    assert all("-z" in l for l in git_lines), "the ls-files call must pass -z"
    assert not any(".split()" in l for l in git_lines), (
        "a bare .split() on git output shreds any path containing a space")

    # 2. and the real list must round-trip: every entry an existing file
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root,
                         capture_output=True, text=True).stdout
    files = [f for f in out.split(chr(0)) if f and not f.startswith("Master_data/")]
    missing = [f for f in files if not (root / f).exists()]
    assert not missing, f"{len(missing)} shipped paths do not exist, e.g. {missing[:3]}"
    spaced = [f for f in files if " " in f]
    assert spaced, "no space-containing tracked path — this test has stopped proving anything"

    # 3. tar's exit code must gate the deploy verdict
    assert "tar_rc = tar.wait()" in src and "return tar_rc == 0" in src, (
        "tar can fail while systemctl reports active; its exit code must reach the verdict")

    print(f"  deploy paths        {len(files)} tracked files ship, {len(spaced)} contain a space")


def test_deploy_health():
    src = (Path(__file__).parent / "deploy.py").read_text(encoding="utf-8")
    dead = ("inactive", "activating", "deactivating", "failed", "unknown")
    assert [w for w in dead if "active" in w] == ["inactive"], \
        "the substring test used to read 'inactive' as healthy — keep that documented"
    assert '"active" in ship.stdout' not in src, "the substring health check is back"
    assert 'state == "active"' in src, "is-active must be matched exactly"
    assert 'all(s == "active" for s in states)' in src, \
        "every shipped unit must be checked, not just the first"
    assert "SKIPPED — the push failed" in src, \
        "a failed push must block the VPS ship, or the box runs code GitHub does not have"
    print("  deploy              only exact 'active' is healthy; a failed push blocks the ship")


def test_deploy_ships_all_three_services():
    """Both units this deploy ships must restart, or it is a silent half-deploy.

    nginx routes / and /blogs to the landing page, /admin to the Next terminal and /api to the
    Python API. deploy.py ships the last two; the landing page is a separate bundle (see
    DEPLOYMENT.md) and `chukul` — Streamlit — was removed with ui.py.

    The API imports the SAME modules the terminal's numbers come from, so shipping Python
    without restarting it leaves the terminal answering from a process that loaded yesterday's
    swing_pro.py — the pages render, every number is real, and every number is stale. Nothing
    goes red.
    """
    src = (Path(__file__).parent / "deploy.py").read_text(encoding="utf-8")
    restart = src.split("systemctl restart")[1:]
    assert restart, "deploy no longer restarts anything"
    assert any("{API_SERVICE}" in r[:60] for r in restart), \
        "the python source ships without restarting the API — it will serve stale modules"
    assert any("{WEB_SERVICE}" in r[:60] for r in restart), "the frontend is never restarted"
    print("  deploy services     ui, api and web all restart — no silent half-deploy")


def test_web_bundle_cannot_ship_symlinks():
    """The frontend bundle must contain real files, never pnpm's symlinks.

    pnpm's default node_modules is a symlink farm pointing into .pnpm, and on Windows those links
    carry MSYS paths (/c/Tarkaproject/...). `next build` copies that shape into .next/standalone,
    so the bundle runs on the machine that built it and dies on the box with "Cannot find module
    'next'". Dereferencing at ship time does not save you: tar -h cannot follow an MSYS path and
    SKIPS what it cannot follow, which produced a bundle quietly missing @swc/helpers instead.

    Two things have to hold, and this checks both, because either alone fails open.
    """
    root = Path(__file__).parent
    src = (root / "deploy.py").read_text(encoding="utf-8")
    assert "find . -type l | wc -l" in src, \
        "the ship no longer refuses a symlinked bundle — it will fail after the swap, not before"

    ws = root / "web" / "pnpm-workspace.yaml"
    assert ws.exists(), "web/pnpm-workspace.yaml is gone"
    # Line-anchored, not `"nodeLinker: hoisted" in text` — commenting the key OUT leaves that
    # substring intact, so the obvious spelling of this assertion passes on the exact edit it
    # exists to catch. Same shape as the "active" in "inactive" bug two tests up.
    keys = [ln.split(":", 1) for ln in ws.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#") and ":" in ln]
    linker = [v.strip() for k, v in keys if k.strip() == "nodeLinker"]
    assert linker == ["hoisted"], \
        f"nodeLinker must be an active key set to hoisted, found {linker or 'nothing'} — without " \
        "it pnpm rebuilds the symlink farm and every deploy ships links that die on the box"
    # It belongs HERE and not in .npmrc: pnpm 11 ignores node-linker in .npmrc without a word,
    # and `pnpm config get node-linker` answers undefined while the install looks fine.
    npmrc = root / "web" / ".npmrc"
    assert not (npmrc.exists() and "node-linker" in npmrc.read_text(encoding="utf-8")), \
        ".npmrc node-linker is silently ignored by pnpm 11 — it must live in pnpm-workspace.yaml"
    print("  web bundle          hoisted node_modules; a symlinked bundle is refused before swap")


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


def test_order_body_is_exactly_what_the_screen_sends():
    """A missing or renamed key is rejected as "-102 Wrong Request Object"; a wrong VALUE trades
    the wrong thing, silently and for real. So pin the whole payload, not a sample of it."""
    b = naasa.order_body(" nabil ", "buy", 10, 549.5)
    assert set(b) == {"BuySellType", "DeliveryFlag", "OrderTerms", "OrderType", "Price",
                      "Quantity", "Scrip"}, sorted(b)
    assert b["Scrip"] == "NABIL"
    assert b["Quantity"] == 10 and b["Price"] == "549.5"
    assert b["BuySellType"] == "Buy"
    assert b["OrderType"] == "NORMAL", "a priced order must go as a LIMIT order"
    assert b["DeliveryFlag"] == "DEL"

    # price 0 IS the screen's market-order flag — the two must never drift apart, and on this
    # API that is OrderType MKT rather than the old Market="1" field.
    m = naasa.order_body("NABIL", "SELL", 5, 0)
    assert m["OrderType"] == "MKT" and m["Price"] == "0"
    assert m["BuySellType"] == "Sell"
    # A sell delivers from the demat, a buy does not: the flag is asymmetric and getting it
    # backwards is rejected at the broker, not by us.
    assert m["DeliveryFlag"] == "AUTO"

    # GTD is the only validity that carries a date, and it goes up as DD-MON-YY.
    g = naasa.order_body("NABIL", "BUY", 1, 100, "GTD", "2026-08-29")
    assert g["ValidTill"] == "29-AUG-26", g["ValidTill"]
    assert "ValidTill" not in b, "a DAY order must not carry a validity date"
    try:
        naasa.order_body("NABIL", "BUY", 1, 100, "GTD")
    except ValueError:
        pass
    else:
        raise AssertionError("GTD with no date should have been rejected")

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
    # A REAL order-book row, copied from the account (2026-08-20 SAHAS), part-filled here so it
    # is still cancellable. Note what it does NOT contain: no DeliveryFlag, and OrderType reads
    # "Add" — a value the request side would reject, which is why the body hardcodes NORMAL.
    live = {"LatestOrderID": 9263601, "ExchangeOrderNo": "2026082005015954",
            "ClientCode": "22607070507", "BrokerTranID": 9263601, "Exchange": "NEPSE",
            "Scrip": "SAHAS", "B/S": "B", "RemainingQty": 4, "Price": "701.80",
            "OrderTerms": "DAY", "TermValidity": "", "OrderStatus": "OPEN",
            "TradingAccount": "CNC", "Quantity": 10, "MarketOrder": "1", "ErrorCode": 0,
            "OrderCategory": "MARKET", "TradeType": "DEL", "OrderType": "Add",
            "BuySellType": "Buy", "TotalQuantity": 10}
    c = naasa.cancel_body(live)
    assert c["TranId"] == "9263601" and c["OrderId"] == "9263601", (c["TranId"], c["OrderId"])
    assert c["Quantity"] == 4, "a cancel names the RESIDUE, not the original size"
    assert c["OrderType"] == "NORMAL", "the row's OrderType 'Add' must never be echoed back"
    assert c["DeliveryFlag"] == "DEL", "absent DeliveryFlag falls back to TradeType"
    assert naasa.cancel_body(dict(live, TradeType="AUTO"))["DeliveryFlag"] == "AUTO",         "a sell must not be cancelled under the buy flag"
    try:
        naasa.cancel_body({"Scrip": "NABIL", "B/S": "B"})
    except ValueError:
        pass
    else:
        raise AssertionError("a row with no BrokerTranID must not produce a cancel")

    # The order book returns TODAY's orders, filled ones included. Offering to cancel a trade
    # that already executed is the bug this guards: it shipped once, on the money screen.
    filled = {"Scrip": "SAHAS", "B/S": "B", "RemainingQty": "0", "Price": "701.80",
              "BrokerTranID": "9263601", "OrderStatus": "TRADED", "BuySellType": "Buy"}
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
    assert m["Quantity"] == 4 and m["Price"] == "705", (m["Quantity"], m["Price"])
    assert m["OrderId"] == m["TranId"] == "9263601"
    assert m["BuySellType"] == "Buy", "side comes from the order, not from the caller"
    try:
        naasa.modify_body(filled, 4, 705.0)
    except ValueError:
        pass
    else:
        raise AssertionError("a filled order must not produce a modify request")

    # A cancel is a DELETE to the same path. Sent as a POST it reads as a NEW order at the
    # residue price — the single worst way for this to fail.
    src = Path("naasa.py").read_text(encoding="utf-8")
    fn = src[src.index("def x_cancel_order("):src.index("def ", src.index("def x_cancel_order(") + 5)]
    assert 'method="DELETE"' in fn, "x_cancel_order must send DELETE, not POST"

    print("  naasa.order_body    full payload pinned; qty/side/price rules enforced")


def test_freshness_is_never_reported_from_one_half():
    """A screen may not answer "is this current?" from only one of the two halves.

    There are two independent questions and each has produced a false green on its own:

      stale           - is the BOARD older than the archive?
      session_unknown - can we even tell?          (undated boards ticked green "current")
      missed_sessions - is the ARCHIVE older than the market?  (a whole-pipeline stall freezes
                        every store in lockstep, so they all still agree and the pill said
                        "Every board matches the archive" over week-old prices)

    Both bugs shipped. Both were one consumer forgetting one term, which is a thing careful
    reading does not reliably catch — so it is pinned here instead.
    """
    web = Path("web/src")
    consumers = sorted(f for f in web.rglob("*.tsx")
                       if ".stale" in f.read_text(encoding="utf-8"))
    assert consumers, "no consumer reads .stale any more - has the field been renamed?"
    for f in consumers:
        src = f.read_text(encoding="utf-8")
        assert "session_unknown" in src, (
            "%s branches on `stale` but never mentions `session_unknown`, so an undated board "
            "takes the fresh branch and is reported as current." % f.as_posix())

    # the calendar half has to reach the client at all
    api_src = Path("api/__main__.py").read_text(encoding="utf-8")
    assert "missed_sessions" in api_src, "the /api/boards payload no longer carries missed_sessions"
    assert "market_hours.missed_sessions" in api_src,         "missed_sessions must come from market_hours, not a second copy of the rule"
    nav = (web / "components/top-nav.tsx").read_text(encoding="utf-8")
    assert "missed_sessions" in nav,         "the header pill is on every route and must state the ARCHIVE's own age"

    # and there must be exactly ONE implementation of the trading-session count
    # this file names the rule in its own assertions, so exclude it or the guard flags itself
    impls = [f for f in Path(".").glob("*.py")
             if f.name != "test_ops.py"
             and ("def missed_sessions" in f.read_text(encoding="utf-8")
                  or "def _missed_sessions" in f.read_text(encoding="utf-8"))]
    assert [f.name for f in impls] == ["market_hours.py"],         "the session-age rule must live only in market_hours.py, found: %s" % [f.name for f in impls]

    # Every board either rebuilds itself overnight or is declared manual WITH A REASON. A
    # board nothing rebuilds still ticks green the day it is written and then ages out
    # silently -- which is how backtest.txt fell a session behind under a Cron page saying
    # rebuilds "happen on their own".
    from api import tables as _tables
    unclassified = [b for b in _tables.BOARDS
                    if not jobs.auto(b) and b not in jobs.MANUAL]
    assert not unclassified, (
        "these boards are in neither the nightly chain nor jobs.MANUAL, so nothing says "
        "whether they refresh: %s" % unclassified)
    for board, why in jobs.MANUAL.items():
        assert board in _tables.BOARDS, "jobs.MANUAL names %s, which is not a board" % board
        assert why and len(why) > 20, (
            "jobs.MANUAL[%r] needs a real reason -- the screen prints it" % board)
        assert not jobs.auto(board), (
            "%s is in the nightly chain AND listed as manual" % board)

    print("  freshness           both halves reach every consumer (%d screens, 1 rule); "
          "%d boards nightly, %d manual"
          % (len(consumers),
             sum(1 for b in _tables.BOARDS if jobs.auto(b)), len(jobs.MANUAL)))


def test_live_never_means_merely_recent():
    """LIVE must mean the market is OPEN, not that the snapshot is recent.

    Caught end-to-end at 15:01, one minute after the close: the chart still read LIVE. The tag
    keyed on freshness and the presence of a bar, and both stay true after the close -- the
    publisher keeps writing, so the snapshot is seconds old and the bar is today's. Neither says
    the market is open, and a tag reading LIVE over numbers that stopped moving is the same false
    claim as a stale board reporting itself current.

    The quote route had the matching hole. The publisher holds the socket for as long as the DAY
    is a trading day, so at 09:00 tomorrow the snapshot is seconds old with yesterday's closing
    quotes in it. /api/bar always refused that -- live_1d.row() checks the quote's own timestamp
    -- and /api/quotes did not, so every board overlay would have applied yesterday's close as
    today's price.
    """
    api_src = Path("api/__main__.py").read_text(encoding="utf-8")
    i = api_src.find("head == ")
    while i != -1 and "quotes" not in api_src[i:i + 40]:
        i = api_src.find("head == ", i + 1)
    assert i != -1, "the quotes route is gone"
    assert "stamp" in api_src[i:i + 1800], (
        "/api/quotes must check the quote's own timestamp, or the morning after a session it "
        "serves yesterday's close as today's price")

    chart = Path("web/src/app/admin/chart/page.tsx").read_text(encoding="utf-8")
    assert "const ticking = open &&" in chart, (
        "the chart's LIVE tag must require the market to be open, not merely a recent snapshot")

    board = Path("web/src/components/board-page.tsx").read_text(encoding="utf-8")
    assert "marketOpen" in board, (
        "the board's price tag must tell a live price from today's final one")

    print("  live means open     a recent file is not a running market")


def test_a_partial_bar_does_not_make_every_board_stale():
    """A board is not out of date for being older than a bar that is still being written.

    Making the system live had a side effect: live_1d keeps TODAY's partial bar in every 1D.txt
    from 11:00, so `newest_bar()` becomes today at the opening bell — and every board, all of them
    rebuilt after last night's close exactly as designed, started reporting itself stale. The
    header pill read "11 BEHIND" for the whole session.

    The boards were right; the yardstick was wrong. `newest_completed()` is today only once the
    market has closed, and the previous trading day while it is still trading.
    """
    from api import tables as _t

    real_newest, real_session, real_trading = _t.newest_bar, market_hours.session_now, \
        market_hours.is_trading_day
    try:
        # Mid-session, with the archive already carrying today's partial bar.
        # DERIVED from the clock, never hardcoded: newest_completed() calls
        # datetime.now(NPT) itself, so a pinned date only agrees with it on the one
        # day it was written. This test passed on 2026-08-21 and went red at midnight
        # for that reason alone, with nothing in the code having changed.
        _today = datetime.now(market_hours.NPT).date()   # must BE the real today, see above
        _prev = _today - timedelta(days=1)
        while _prev.weekday() > 4:
            _prev -= timedelta(days=1)
        today = _today.isoformat()
        _t.newest_bar = lambda: today
        market_hours.is_trading_day = lambda d: d.weekday() in (0, 1, 2, 3, 4)

        market_hours.session_now = lambda when=None: ("LIVE", 120)
        got = _t.newest_completed()
        assert got == _prev.isoformat(), (
            "while the market trades, the yardstick must be the previous session, not today's "
            "half-formed bar — got %r" % got)

        market_hours.session_now = lambda when=None: ("CLOSED", None)
        assert _t.newest_completed() == today, "after the close, today is a fair comparison"

        # A bar the archive has not reached yet. newest_completed compares newest_bar against
        # the REAL today, so a FUTURE date is the only way to pin this path open on every day
        # of the week — which must return the bar untouched.
        _ahead = (_today + timedelta(days=2)).isoformat()
        _t.newest_bar = lambda: _ahead
        market_hours.session_now = lambda when=None: ("LIVE", 120)
        assert _t.newest_completed() == _ahead

        # And an archive behind today is never adjusted, whatever the session says.
        _t.newest_bar = lambda: "2026-08-10"
        assert _t.newest_completed() == "2026-08-10"
        _t.newest_bar = lambda: None
        assert _t.newest_completed() is None, "no archive is not a date"
    finally:
        _t.newest_bar, market_hours.session_now, market_hours.is_trading_day = \
            real_newest, real_session, real_trading

    print("  partial bar         a forming bar does not mark every board stale")


def test_open_orders_never_counts_a_filled_one():
    """A tile labelled "Open orders" may not count a trade that already executed.

    The order book returns TODAY's orders with the filled ones in it. `count` was the only number
    the API sent, and the account page bound its "Open orders" tile straight to it — so a trade
    that executed at 12:52 was displayed as still working, on the money screen.

    `naasa.order_is_working` already decided this everywhere else; it simply was not imported
    under api/. This pins the split, and that the screen reads the right half of it.
    """
    src = Path("api/account.py").read_text(encoding="utf-8")
    assert "order_is_working" in src,         "api/account.py no longer asks whether an order is working"
    for key in ('"working"', '"done"'):
        assert key in src, "the orderbook response no longer carries %s" % key

    page = Path("web/src/app/admin/account/page.tsx").read_text(encoding="utf-8")
    i = page.find('label="Open orders"')
    assert i != -1, "the Open orders tile is gone from the account page"
    tile = page[i:i + 400]
    assert "orders.data?.working" in tile,         'the "Open orders" tile must read `working`, not `count` — count includes filled orders'

    print("  open orders         a filled trade is not an open order (api + tile agree)")


def test_money_calls_never_auto_retry():
    """x_report retries once on a stale session. A transport error can fire AFTER the exchange has
    accepted an order, so retrying a place would DUPLICATE it. Both money paths must opt out —
    and the flag has to gate the loop, not merely exist."""
    tree = ast.parse(Path("naasa.py").read_text(encoding="utf-8"))
    for name in ("x_place_order", "x_cancel_order", "x_modify_order"):
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == name), None)
        assert fn is not None, "%s is gone from naasa.py" % name
        names = {getattr(c.func, "id", getattr(c.func, "attr", "")) for c in ast.walk(fn)
                 if isinstance(c, ast.Call)}
        # Two legal states and nothing between them. Either the function is UNPORTED and
        # refuses rather than posting to an endpoint that no longer exists, or it is wired
        # and passes retry=False. NAASA rebuilt their app, so all three are currently the
        # first; this keeps the retry rule waiting for whoever wires them back up.
        if "_order_endpoint_gone" in names:
            continue
        calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call)
                 and getattr(c.func, "id", getattr(c.func, "attr", "")) in
                 ("x_report", "x_api", "_x_call")]
        assert calls, "%s reaches no transport and does not refuse either" % name
        for c in calls:
            kw = {k.arg: k.value for k in c.keywords}
            assert "retry" in kw and getattr(kw["retry"], "value", None) is False, \
                "%s must pass retry=False - a retried place is a duplicate order" % name

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


def test_api_can_never_place_an_order():
    """The web API reaches a live broker session. No path through it may reach a money call.

    The Account page tells the reader "there is no endpoint behind a Place button". That promise
    is only worth anything if something checks it, because the failure mode is a future edit that
    adds one helper — `api/account.py` importing a convenience wrapper that happens to call
    naasa.x_place_order is a two-line change nobody would flag in review.

    Transitive across every module in api/, for the same reason the ui.py version is: a direct
    containment check would miss exactly the shape the accident takes.
    """
    root = Path(__file__).parent / "api"
    money = {"x_place_order", "x_cancel_order", "x_modify_order"}

    def called_names(node):
        out = set()
        for c in ast.walk(node):
            if isinstance(c, ast.Call):
                out.add(getattr(c.func, "attr", None) or getattr(c.func, "id", None))
        return {n for n in out if n}

    funcs, calls = {}, {}
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef):
                key = f"{path.stem}.{n.name}"
                funcs[key] = n
                calls[key] = called_names(n)

    by_name = {}
    for key in funcs:
        by_name.setdefault(key.split(".", 1)[1], []).append(key)

    def reaches(start, seen=None):
        seen = seen or set()
        for callee in calls.get(start, ()):
            if callee in money:
                return callee
            for nxt in by_name.get(callee, ()):
                if nxt not in seen:
                    hit = reaches(nxt, seen | {nxt})
                    if hit:
                        return hit
        return None

    assert funcs, "no functions found under api/ — this test no longer matches the tree"
    for key in funcs:
        hit = reaches(key)
        assert not hit, f"api/{key.replace('.', '.py:')}() can reach {hit}()"

    # The API used to be GET-only, and the rule was "a handler that cannot receive a POST cannot
    # be talked into one". That was given up deliberately for the rebuild button, so the
    # replacement has to carry the same weight rather than simply being weaker.
    #
    # Checked against DEFINED METHOD NAMES, not `"do_POST" in source`. The naive spelling failed
    # the moment the module docstring mentioned do_POST — a test that a comment can turn red is a
    # test people learn to edit rather than believe.
    api_src = (root / "__main__.py").read_text(encoding="utf-8")
    api_tree = ast.parse(api_src)
    handlers = {n.name for n in ast.walk(api_tree)
                if isinstance(n, ast.FunctionDef) and n.name.startswith("do_")}
    assert handlers == {"do_GET", "do_POST", "do_OPTIONS"}, \
        f"the API answers a verb nobody designed: {sorted(handlers)}"

    # The one write handler must dispatch on a KEY, never on anything the client can shape into a
    # path or a command. If this lookup ever goes, the endpoint becomes a remote shell.
    post = next(n for n in ast.walk(api_tree)
                if isinstance(n, ast.FunctionDef) and n.name == "do_POST")
    post_src = ast.get_source_segment(api_src, post) or ""
    # Look for the GUARD, not a mention. `"jobs.SCRIPTS" in post_src` passed even when the
    # validation was replaced by `if False:`, because the name still appeared in the 404 body
    # underneath it -- a substring test cannot tell a validation from an error message.
    guarded = any(
        isinstance(n, ast.If)
        and any(getattr(d, "attr", None) == "SCRIPTS" for d in ast.walk(n.test))
        and any(isinstance(r, ast.Return) for r in ast.walk(n))
        for n in ast.walk(post))
    assert guarded, (
        "do_POST must REFUSE a board that is not in jobs.SCRIPTS, not merely mention the "
        "allow-list -- without that the board name reaches jobs.start() unchecked")
    for forbidden in ("subprocess", "os.system", "eval(", "exec(", "shell=True"):
        assert forbidden not in post_src, \
            "do_POST must not run anything itself (%s); it may only call jobs.start()" % forbidden
    # The write surface is TWO paths now, and "in post_src" cannot express that: the old
    # assertion passed for any handler that merely MENTIONED rebuild, so it would have stayed
    # green while a third route was added underneath it. Read the route literals out of the AST
    # and compare the SET — a new path cannot be introduced without this line failing.
    # Only the constants the handler COMPARES a path segment against. Collecting every string
    # in the function instead swept up dict keys and error-message words ("boards", "started"),
    # which is a set nobody can maintain — and an unmaintainable assertion gets deleted.
    literals = {c.value
                for cmp_ in ast.walk(post) if isinstance(cmp_, ast.Compare)
                for c in ast.walk(cmp_)
                if isinstance(c, ast.Constant) and isinstance(c.value, str)}
    assert literals == {"api", "rebuild", "access-request"}, (
        "do_POST's route literals changed — it now serves %s. Every path this handler answers "
        "must be listed here deliberately, because one of them is public." % sorted(literals))

    # The public route is the only one a stranger can reach. Four things make it safe, and each
    # is a line somebody could delete without any other test noticing.
    pub = next((n for n in ast.walk(post) if isinstance(n, ast.If)
                and "access-request" in ast.dump(n.test)), None)
    assert pub is not None, "the access-request branch is no longer an explicit path test"
    pub_src = ast.get_source_segment(api_src, pub) or ""

    # 1. the body is capped BEFORE it is read. rfile.read(n) with an attacker-chosen n is how a
    #    form becomes a memory-exhaustion bug, and the cap is invisible in a functional test
    #    because a well-behaved client never trips it.
    caps = [n.value for n in ast.walk(pub) if isinstance(n, ast.Constant)
            and isinstance(n.value, int) and n.value > 1000]
    assert caps and max(caps) <= 8192,         "the access-request branch must bound Content-Length by a literal <= 8192; found %s" % caps
    assert "Content-Length" in pub_src and "rfile.read" in pub_src,         "the access-request branch no longer reads a bounded body"

    # 2. it validates, and a failure RETURNS rather than falling through to the write
    assert "access.validate" in pub_src, "access-request does not call access.validate"
    # The GUARD, not the mention: an `if` on validate's error that actually returns. A substring
    # test cannot tell `if why: return` from `why = ...` sitting above an unconditional write —
    # the same trap the jobs.SCRIPTS check above was rewritten to avoid.
    returns_on_invalid = any(
        isinstance(n, ast.If)
        and any(isinstance(x, ast.Name) and x.id == "why" for x in ast.walk(n.test))
        and any(isinstance(r, ast.Return) for r in ast.walk(n))
        for n in ast.walk(pub))
    assert returns_on_invalid, \
        "a validation failure must return, not fall through to access.record"

    # 3. nothing in it builds a path or opens a file. The target is a module constant; if a
    #    filename ever gets derived from the payload, this is where it would happen.
    for forbidden in ("open(", "Path(", "os.path", "join(", "MASTER", "__file__"):
        assert forbidden not in pub_src,             "the public route must not name a file (%s) — access.PATH is a constant" % forbidden

    # 4. ...and that constant really is one, rooted in the archive
    assert access.PATH == fetch_ohlc.MASTER / "access_requests.txt",         "access.PATH moved: %s" % access.PATH
    # Checked against string constants that are NOT docstrings. `"access_requests.txt" not in
    # api_src` went red the moment the module docstring documented the route -- the same failure
    # the do_POST spelling check above already carries a note about. A test a comment can turn
    # red is a test people learn to edit rather than believe.
    docs = {ast.get_docstring(n, clean=False) for n in ast.walk(api_tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    code_strings = [n.value for n in ast.walk(api_tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)
                    and n.value not in docs]
    assert not [v for v in code_strings if "access_requests" in v],         "the API names the storage file in code -- it must only ever go through api/access.py"

    assert "rebuild" in post_src, "do_POST no longer serves /api/rebuild"

    # The allow-list itself: plain filenames that exist, and nothing that reaches the broker.
    import jobs as _jobs
    for board, scripts in _jobs.SCRIPTS.items():
        for script in scripts:
            assert script.endswith(".py"), (board, script)
            assert "/" not in script and "\\" not in script and ".." not in script, \
                "%s names a path, not a script in the project root: %r" % (board, script)
            assert (Path(__file__).parent / script).exists(), \
                "%s rebuilds with %s, which does not exist" % (board, script)
            assert script != "naasa.py", "a rebuild may never run the broker module"

    # ...and a rebuild must not be able to reach a money call either, by any route.
    jobs_src = (Path(__file__).parent / "jobs.py").read_text(encoding="utf-8")
    assert "import naasa" not in jobs_src and "from naasa" not in jobs_src, \
        "jobs.py imports naasa — a rebuild could then reach an order path"
    for name in money:
        assert name not in jobs_src, "jobs.py mentions %s" % name

    # ...and the walk must be able to fail, or it proves nothing.
    probe = ast.parse("def a():\n    b()\ndef b():\n    naasa.x_place_order(1,2,3,4,5)\n")
    pf = {f"p.{n.name}": n for n in ast.walk(probe) if isinstance(n, ast.FunctionDef)}
    funcs, calls = pf, {k: called_names(v) for k, v in pf.items()}
    by_name = {k.split(".", 1)[1]: [k] for k in pf}
    assert reaches("p.a") == "x_place_order", \
        "the transitive walk cannot see an indirect money call — it proves nothing"
    print("  api write surface   two POST routes, capped public body, no path to a money call")


def test_collateral_never_returns_client_pii():
    """Home/DashboardDetails group [4] is the client's personal details. It must not come out.

    ui.py flattens all five groups into one dict and is then careful to render only money fields
    — one `st.json(f)` away from publishing a name and document number. The API cannot rely on
    that kind of care, so account.collateral() allow-lists: unknown keys are dropped rather than
    passed through. This feeds it a payload shaped like the real one and checks what escapes.
    """
    from api import account

    pii = {"ClientName": "A Real Person", "ClientCode": "1301-XXXXXXXX",
           "Address": "Kathmandu", "MobileNumber": "98########", "Email": "x@y.z",
           "BOID": "1301060000######", "PAN": "#########"}
    payload = {"Data": [
        [{"TotalOrderCount": "7"}],
        [{"TotalBuyAmount": "125000.5", "TotalSellAmount": "0"}],
        [{"TotalHoldingAmount": "980000", "HoldingStockCount": "12"}],
        [{"GrossAvalibleExposure": "45000", "GrossUsedExposure": "0",
          "GrossAllocatedExposure": "45000"}],
        [pii],
    ]}
    saved = account.naasa.x_collateral
    try:
        account.naasa.x_collateral = lambda *a, **k: payload
        account.naasa.load_credentials = lambda: ("probe@example.com", "probe")
        out = account.collateral()["fields"]
    finally:
        account.naasa.x_collateral = saved

    leaked = sorted(set(out) & set(pii))
    assert not leaked, f"collateral() leaked client PII: {leaked}"
    assert out["GrossAvalibleExposure"] == 45000, "the money fields must still come through"
    assert out["HoldingStockCount"] == 12, "numbers must arrive parsed, not as strings"
    # An allow-list, not a block-list: a field NAASA adds tomorrow is invisible by default.
    account.naasa.x_collateral = lambda *a, **k: {"Data": [[{"SomethingNew": "1", **pii}]]}
    try:
        assert account.collateral()["fields"] == {}, \
            "an unrecognised field came through — this is a block-list, and it will leak"
    finally:
        account.naasa.x_collateral = saved
    print("  account PII         collateral allow-lists money fields; group [4] cannot escape")


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


def test_board_writers_emit_exactly_their_header():
    """A board row with the wrong number of fields is dropped SILENTLY, every one of them.

    api/tables.py `read()` skips any line where `len(f) != len(cols)` — deliberately, because a
    short row is corrupt rather than blank. The consequence is that adding a column to a HEADER
    and forgetting the writer (or the reverse) does not raise, does not warn, and does not show a
    partial table: the board goes completely EMPTY while the file on disk looks fine and every
    script still exits 0. That is the whole failure class this file exists for.

    Counted from the source, not from the produced .txt, so a stale file cannot make it pass.
    """
    import ast as _ast

    def header_cols(tree, name):
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Assign) and getattr(node.targets[0], "id", "") == name:
                return len(_ast.literal_eval(node.value).split("\t"))
        raise AssertionError("no %s in this module" % name)

    here = Path(__file__).parent
    # writers that build a row as `"\t".join(str(x) for x in (...))`
    writers = (("master_signal.py", "HEADER"), ("swing_master.py", "HEADER"),
               ("operator_scan.py", "HEADER"), ("operator_now.py", "HEADER"),
               ("operator_verdict.py", "HEADER"), ("scan.py", "HEADER"))
    for fname, hname in writers:
        tree = _ast.parse((here / fname).read_text(encoding="utf-8"))
        cols = header_cols(tree, hname)
        tuples = [len(n.generators[0].iter.elts) for n in _ast.walk(tree)
                  if isinstance(n, _ast.GeneratorExp)
                  and isinstance(n.generators[0].iter, _ast.Tuple)]
        assert cols in tuples, \
            f"{fname}: {hname} has {cols} columns but the writer emits {tuples} fields — " \
            "every row would be dropped and the board would render empty"

    # And no writer may locate one of its own columns by a hand-counted position. scan.py read
    # its verdict as `row.rsplit(chr(9), 1)[-1]` -- "the last field" -- so appending the badge and
    # its trade levels made it tally risk percentages as if they were BUY/SELL. It raised instead
    # of lying, which was luck: the new last column happened not to be a valid key.
    for fname in ("scan.py",):
        src = (here / fname).read_text(encoding="utf-8")
        assert 'rsplit("	", 1)' not in src, (
            "%s locates a column by position; resolve it out of HEADER by name instead, or the "
            "next appended column silently moves it" % fname)

    # backtest builds its row from f-strings, not a tuple, so count separators across save()
    # as a whole: every field boundary is one tab and the tail splices into the head, so
    # fields = tabs + 1. Do NOT slice the tail out by parenthesis — len(trades) closes the
    # first bracket, so you count zero and the check passes while measuring nothing.
    bt = (here / "backtest.py").read_text(encoding="utf-8")
    cols = header_cols(_ast.parse(bt), "OUT_HEADER")
    save = next(n for n in _ast.walk(_ast.parse(bt))
                if isinstance(n, _ast.FunctionDef) and n.name == "save")
    fields = (_ast.get_source_segment(bt, save) or "").count("\\t") + 1
    assert fields == cols, \
        f"backtest.py: OUT_HEADER has {cols} columns but save() emits {fields} fields"

    print("  board writers       header width == fields written (%d boards), "
          "no positional column reads" % len(writers))


def test_every_registered_rebuild_script_exists():
    """A board whose rebuild script is not on disk fails only when somebody presses the button.

    `jobs.SCRIPTS` and `ui.CRON_JOBS` are both allow-lists of FILENAMES. Nothing imports them, so
    a typo, a rename, or a script that was planned and never written sits there indefinitely:
    every test passes, the Pipeline page renders the row, and the failure arrives at 15:15 on the
    box -- or the first time a human clicks Rebuild -- as a non-zero exit nobody is watching.

    swing_quantam is the live example: it is a PACKAGE, so its registered script has to be the
    `build_swing_quantam.py` shim (jobs runs `[python, <script>]` and cannot spell `-m`). Name the
    package directory there instead and it would import as a directory, fail, and be invisible
    until the cron fired.
    """
    import jobs
    here = Path(__file__).parent

    missing = []
    for board, scripts in jobs.SCRIPTS.items():
        for s in scripts:
            if not (here / s).is_file():
                missing.append(f"jobs.SCRIPTS[{board!r}] -> {s}")
    assert not missing, "registered rebuild scripts that do not exist: " + ", ".join(missing)

    # ui.py held a second copy of this registry and was checked here too. It is gone (2026-08-22);
    # the systemd unit chukul-update.service is now the only other place the chain is spelled out,
    # and it must mirror jobs.SCRIPTS["pipeline"]. Nothing in this repo can see that unit file, so
    # THAT pairing is unguarded by construction -- keep them in step by hand.
    # Every board the API will serve must be rebuildable, or the Pipeline page offers no way out
    # of a stale board. `pipeline` is the whole chain and is not itself a board.
    from api import tables
    unbuildable = [b for b in tables.BOARDS if b not in jobs.SCRIPTS]
    assert not unbuildable, "boards with no rebuild script: " + ", ".join(unbuildable)

    print("  rebuild scripts     %d jobs.SCRIPTS all exist, %d boards all rebuildable"
          % (len(jobs.SCRIPTS), len(tables.BOARDS)))


def test_expected_lag_is_an_allowance_not_an_exemption():
    """A board allowed to trail the archive must still report a MISSED run.

    `backtest` cannot be as current as the archive: it labels each trade by its outcome, so
    it cannot score a session until the forward window exists. It therefore sat one session
    back forever and the badge read a permanent "1 BEHIND", which trains a reader to ignore
    the one indicator that is supposed to mean something.

    The fix is an allowance subtracted from the benchmark, NOT an exemption from the check.
    The distinction is invisible on a healthy day -- both spellings print "fresh" -- and only
    shows up the day the script actually fails, which is the day it matters.

    This drives `tables.read()` ITSELF against a scratch board. The first version of this
    test re-implemented the `session < due` comparison inline, which made it pass happily
    against a mutated `read()` that exempted the board outright -- an assert that could not
    fail for the regression named in its own docstring. Mutation-tested: replacing the
    comparison with `False if board in EXPECTED_LAG` now fails this test.
    """
    import tempfile
    from pathlib import Path

    from api import tables

    # trading-session arithmetic, not calendar days: Monday minus one session is Friday
    assert tables._sessions_back("2026-08-24", 1) == "2026-08-21"
    assert tables._sessions_back("2026-08-24", 2) == "2026-08-20"
    assert tables._sessions_back("2026-08-21", 0) == "2026-08-21"
    assert tables._sessions_back(None, 1) is None
    assert tables._sessions_back("not-a-date", 1) == "not-a-date"

    lag = tables.EXPECTED_LAG["backtest"]
    assert lag >= 1, "an allowance of 0 would make the rest of this test vacuous"
    # ...and a CEILING, because the other way to defeat this check is to widen the
    # allowance until a broken run fits inside it. backtest needs exactly one session:
    # it cannot label the newest one until its outcome exists. Anything larger is a
    # different problem being absorbed, and should be diagnosed rather than allowed for.
    assert lag == 1, (
        f"backtest's allowance is {lag} sessions; only 1 is structural, and a wider one "
        "hides a missed run instead of reporting it")
    assert all(v <= 1 for v in tables.EXPECTED_LAG.values()), (
        "no board needs to trail by more than one session on this pipeline")

    bench = "2026-08-21"
    due = tables._sessions_back(bench, lag)
    late = tables._sessions_back(due, 1)

    # A scratch dir, never Master_data: a self-check that writes to production is a
    # scheduled data-loss bug, and the hourly health check runs these.
    tmp = Path(tempfile.mkdtemp(prefix="lag_selfcheck_"))
    real_master, real_bench, real_boards = tables.MASTER, tables.newest_completed, dict(tables.BOARDS)
    try:
        tables.MASTER = tmp
        tables.newest_completed = lambda: bench
        tables.BOARDS["__lag_probe__"] = "probe.txt"

        def verdict(session, board):
            (tmp / "probe.txt").write_text(f"symbol\tdate\nAAA\t{session}\n", encoding="utf-8")
            return tables.read(board)["stale"]

        # the SAME file, judged as a board with an allowance and as one without
        tables.EXPECTED_LAG["__lag_probe__"] = lag
        assert not verdict(due, "__lag_probe__"), "a board at its allowance must read fresh"
        assert not verdict(bench, "__lag_probe__"), "a board ahead of its allowance is fresh"
        assert verdict(late, "__lag_probe__"), (
            "a board one session past its allowance must STILL report stale -- the "
            "allowance has become an exemption and a failed run is now invisible")

        del tables.EXPECTED_LAG["__lag_probe__"]
        assert verdict(due, "__lag_probe__"), "without an allowance, trailing must be stale"
    finally:
        tables.MASTER, tables.newest_completed = real_master, real_bench
        tables.BOARDS.clear()
        tables.BOARDS.update(real_boards)
        tables.EXPECTED_LAG.pop("__lag_probe__", None)
        shutil.rmtree(tmp, ignore_errors=True)

    # and the allowance must not leak onto boards that have no reason to trail
    for board in ("scan", "swing_quantam", "master_signal"):
        assert tables.EXPECTED_LAG.get(board, 0) == 0, (
            f"{board} was given a lag allowance -- only a board that CANNOT cover the "
            "newest session may have one, and the reason belongs in EXPECTED_LAG's comment")

    print(f"  freshness          backtest may trail {lag} session; {lag + 1} is still stale")


def test_swing_quantam_board_carries_its_date():
    """A board with no `date` column reports itself fresh forever.

    api/tables.read() sets `stale = session and newest and session < newest`, so a board whose rows
    carry no date gets session=None and stale=False -- indistinguishable from current. Four boards
    have already shipped that way here. `store.write_board` refuses a dateless row; this pins that
    the refusal is real, because a writer that silently stopped refusing would look identical.
    """
    from swing_quantam import store

    try:
        store.write_board(["symbol", "date"], [{"symbol": "X"}])
    except ValueError:
        pass
    else:
        raise AssertionError("write_board accepted a row with no date -- the board would claim "
                             "to be current forever")

    # date must be LAST, so appending a column later cannot push it out of the position readers
    # of the older boards hand-count.
    path = Path(store.board_path())
    if path.is_file():
        cols = path.read_text(encoding="utf-8").splitlines()[0].split("\t")
        assert cols[-1] == "date", f"swing_quantam board.txt columns end {cols[-3:]}, not 'date'"
        print("  swing_quantam       board.txt has date last; dateless rows refused")
    else:
        print("  swing_quantam       dateless rows refused (board not built yet)")


def test_swing_quantam_open_ended_zones_stay_open():
    """Section 89 is the spec's final deliverable, and two of its seven rungs are one-sided.

    `Invalidation` is open below and `Distribution Risk` is open above -- the spec prints them
    `<690` and `770+`. Both still carry a real `low` and `high` so every consumer can treat every
    zone alike, so a renderer that reaches for those two floats prints a CLOSED band and silently
    denies the openness. 481 of 481 shipped blocks did exactly that. Nothing crashes when this
    breaks; the numbers are all correct. Only the shipped text shows it.
    """
    from swing_quantam import store

    d = Path(store.detail_path("ADBL")).parent
    files = sorted(d.glob("*.txt")) if d.is_dir() else []
    if not files:
        print("  swing_quantam       open-ended zones (details not built yet)")
        return
    bad = []
    for f in files:
        m = re.search(r"^## 89	.*?(?=^## |\Z)", f.read_text(encoding="utf-8", errors="replace"),
                      re.M | re.S)
        if not m:
            continue
        for line in m.group(0).splitlines():
            metric, _, rest = line.partition("	")
            if metric == "invalidation band" and not rest.startswith("<"):
                bad.append(f"{f.stem} {line.strip()}")
            if metric == "distribution band" and "+" not in rest.split("	")[0]:
                bad.append(f"{f.stem} {line.strip()}")
    assert not bad, (f"{len(bad)} section-89 rows print an open-ended zone as a closed band, "
                     f"e.g. {bad[0]}")
    print(f"  swing_quantam       section 89 keeps both one-sided zones open ({len(files)} files)")


def main():
    print("ops safety:")
    test_rewrite()
    test_promoter_and_fund_exclusion_uses_the_name_not_the_suffix()
    test_no_detail_file_survives_for_an_unanalysed_symbol()
    test_deploy_ships_paths_with_spaces()
    test_deploy_health()
    test_deploy_ships_all_three_services()
    test_web_bundle_cannot_ship_symlinks()
    test_ex_date_detection()
    test_position_never_exceeds_the_book()
    test_edge_is_read_not_transcribed()
    test_rsi_undefined_on_a_frozen_series()
    test_no_pivot_lookahead()
    test_one_price_loader()
    test_ui_positional_columns()
    test_order_body_is_exactly_what_the_screen_sends()
    test_freshness_is_never_reported_from_one_half()
    test_live_never_means_merely_recent()
    test_a_partial_bar_does_not_make_every_board_stale()
    test_open_orders_never_counts_a_filled_one()
    test_money_calls_never_auto_retry()
    test_board_writers_emit_exactly_their_header()
    test_api_can_never_place_an_order()
    test_collateral_never_returns_client_pii()
    test_forced_relogin_is_coalesced()
    test_every_registered_rebuild_script_exists()
    test_expected_lag_is_an_allowance_not_an_exemption()
    test_swing_quantam_board_carries_its_date()
    test_swing_quantam_open_ended_zones_stay_open()
    live_1d.demo()          # today's bar maths + the archive-never-shrinks rule
    market_hours.demo()     # the one open/closed switch: defaults, toggles, bad file
    jobs.demo()             # the cross-process rebuild lock: exclusive, breakable, safe
    stores.demo()           # per-store freshness: dates compared as dates, columns by name
    feed_snap.demo()        # the live snapshot: round-trip, missing != zero, stale says so
    feed_publisher.demo()   # the socket owner: switches gate it, snapshots are copied
    access.demo()           # the public form: validation, tab-safe rows, per-IP rate limit
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
