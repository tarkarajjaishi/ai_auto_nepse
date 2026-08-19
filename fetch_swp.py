"""Fetch the SmartWealthPro data the operator scan needs, into plain .txt (upsert = full rewrite):

  Master_data/corporate_actions.txt  — latest book-closure per company (operator book-close skip)
  Master_data/lockin.txt             — currently-locked IPO promoter shares (operator supply-shock flag)

Uses the saved SWP login (swp_login.txt); re-logs in if the cookie lapsed. Run by the Cron page's
master pipeline. Idempotent.
"""
import concurrent.futures as cf
import sys

import swp
from fetch_ohlc import MASTER

CA_FILE = MASTER / "corporate_actions.txt"
LK_FILE = MASTER / "lockin.txt"
FUND_FILE = MASTER / "fundamentals.txt"
CA_HEADER = "symbol\tfiscal_year\tbonus\tcash\ttotal\tbook_close\tdistribution"
LK_HEADER = "symbol\tallotment\tlockin_expiry\tpromoter_shares\tpublic_shares"


def _session():
    """A working SWP cookie — reuse the saved one, else re-login from saved creds."""
    ck = swp.load_session()
    try:
        swp.session_info(ck)
        return ck
    except Exception:
        pass
    em, pw = swp.load_credentials()
    if not (em and pw):
        sys.exit("SWP not logged in — sign in on the app sidebar (💼 SmartWealthPro) first.")
    ck = swp.login_password(em, pw)
    swp.save_session(ck)
    return ck


def _latest_book_close(rows):
    """The company's newest dividend row that actually has a book-closure date (ISO, sortable)."""
    dated = [r for r in rows if (r.get("bookClosureDateAD") or "").strip()]
    return max(dated, key=lambda r: r["bookClosureDateAD"]) if dated else None


def corporate_actions(ck):
    comps = [(c["stockSymbol"], c["companyId"]) for c in swp.companies(ck)
             if c.get("stockSymbol") and c.get("companyId")]

    def one(sym_id):
        sym, cid = sym_id
        try:
            r = _latest_book_close(swp.dividends_for(ck, cid))
        except Exception:
            return None
        if not r:
            return None
        return "\t".join(str(x) for x in (
            sym, r.get("fiscalYearBS", ""), r.get("bonus", ""), r.get("cash", ""),
            r.get("totalDividend", ""), (r.get("bookClosureDateAD") or "")[:10],
            (r.get("distributionDateAD") or "")[:10]))

    rows = []
    with cf.ThreadPoolExecutor(max_workers=12) as ex:      # 579 quick GETs, I/O-bound
        for line in ex.map(one, comps):
            if line:
                rows.append(line)
    rows.sort()
    CA_FILE.write_text(CA_HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    print("  corporate_actions.txt  %d companies with a book close (of %d)" % (len(rows), len(comps)))


def lockins(ck):
    rows = []
    for r in swp.lockin(ck):
        if not r.get("stockSymbol"):
            continue
        rows.append("\t".join(str(x) for x in (
            r.get("stockSymbol", ""), (r.get("allotmentDateAD") or "")[:10],
            (r.get("lockInPeriodDateAD") or "")[:10], r.get("promoterShares", ""),
            r.get("publicShares", ""))))
    rows.sort()
    LK_FILE.write_text(LK_HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    print("  lockin.txt             %d IPO(s) still in promoter lock-in" % len(rows))


def refresh_float(ck):
    """Update public_share / promoter_share / float_pct (cols 4-6) in fundamentals.txt from live
    GetOwnershipStructure, keeping every other column (close, market_cap, paid_up, pe, bvps, roe,
    dividends) as-is. Only touches rows with a sane publicPercent — leaves the rest untouched."""
    if not FUND_FILE.exists():
        print("  fundamentals.txt       missing — skipped float refresh")
        return
    lines = FUND_FILE.read_text(encoding="utf-8").splitlines()
    header, rows = lines[0], [ln.split("\t") for ln in lines[1:]]
    comps = {c["stockSymbol"]: c["companyId"] for c in swp.companies(ck)
             if c.get("stockSymbol") and c.get("companyId")}

    def own(sym):
        cid = comps.get(sym)
        if not cid:
            return None
        try:
            o = swp.ownership(ck, cid)
            pub, pro, fl = float(o["publicShareCount"]), float(o["promoterShareCount"]), float(o["publicPercent"])
            return (pub, pro, fl) if pub > 0 and 0 < fl <= 100 else None
        except Exception:
            return None

    syms = [r[0] for r in rows]
    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        got = dict(zip(syms, ex.map(own, syms)))
    updated = 0
    for r in rows:
        o = got.get(r[0])
        if o and len(r) > 6:
            r[4], r[5], r[6] = str(int(o[0])), str(int(o[1])), "%.2f" % o[2]
            updated += 1
    FUND_FILE.write_text(header + "\n" + "\n".join("\t".join(r) for r in rows) + "\n", encoding="utf-8")
    print("  fundamentals.txt       refreshed live float for %d/%d companies" % (updated, len(rows)))


def main():
    ck = _session()
    corporate_actions(ck)
    lockins(ck)
    refresh_float(ck)
    print("SWP fetch ok")


if __name__ == "__main__":
    main()
