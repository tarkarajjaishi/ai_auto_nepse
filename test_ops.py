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

    python test_ops.py
"""
import ast
import re
import sys
import tempfile
from pathlib import Path

import backtest
import fetch_swp
import master_signal
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
    print("  fetch_swp._rewrite  refuses a shrunken overwrite, allows a real one, seeds a new file")


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
    d = ["d1", "d2"]

    def ex(open2, close2, close1=100.0):
        return prices.ex_dates(d, [close1, close2], [close1, open2])

    # a bonus ex-date: restated overnight, so the gap is already in the open and it stays
    assert ex(50.0, 50.0), "a genuine restatement must be detected"
    # an ordinary limit-down: opens at/above the prior close, then trades down to the band
    assert not ex(105.0, 85.0), "a limit-down that opened UP is not a corporate action"
    assert not ex(97.34, 85.02), "ULHC 2026-08-12 opened -2.7% and traded down — not an ex-date"
    # a panic gap that the market buys back: opened low, did not stay there
    assert not ex(80.0, 98.0), "a gap-down that recovered was not a restatement"
    # bad data guard still holds
    assert not ex(5.0, 5.0), "a >5x drop is bad data, not a splice"
    print("  prices.ex_dates     restatement yes; limit-down, bought-back gap and bad data no")


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


def _ui_src():
    return (Path(__file__).parent / "ui.py").read_text(encoding="utf-8")


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


def main():
    print("ops safety:")
    test_rewrite()
    test_deploy_health()
    test_ex_date_detection()
    test_position_never_exceeds_the_book()
    test_edge_is_read_not_transcribed()
    test_no_nested_expanders()
    test_every_page_has_a_body()
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
