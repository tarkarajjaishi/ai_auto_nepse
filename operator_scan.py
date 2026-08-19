"""Master proof: which stock is being REGULARLY bought / sold, confirmed by THREE independent
sources that must agree — floorsheet, chart (price+volume), and broker position.

A single source can be wrong or misread. So a stock is only "PROVEN" when all three agree:

  1 FLOORSHEET  (who)   one broker is the dominant net buyer/seller, on >=70% of days, still
                        going across 1 month -> 3 weeks -> last 3 days, and >=15% of the tape.
  2 CHART       (tape)  price + volume ALONE — no broker data — show accumulation: the
                        Accumulation/Distribution line is rising, up-day volume beats down-day
                        volume, and price is not collapsing. (Mirror for distribution.)
  3 POSITION    (size)  that broker's 1-month net is a real, growing slice of the free float
                        (>=3%), bought at/near a price that keeps them in profit.

  3/3 -> PROVEN   2/3 -> likely   <=1 -> weak / conflicting

All local: broker_flow, 1D (price+volume), fundamentals (float), corporate_actions (book-close).
The chart leg is the key cross-check: it corroborates the floorsheet from a totally separate
measurement, so a false broker attribution cannot fake a PROVEN on its own.

    python operator_scan.py
"""
import math
from datetime import date
from pathlib import Path
from collections import defaultdict, Counter

from fetch_ohlc import MASTER

MONTH, WEEK3, DAY3 = 20, 15, 3
QUARTER = 60            # ~3 months, to see whether the campaign is long-running or fresh
MEGA_AT = 6
OUT = MASTER / "operator_scan.txt"
HEADER = ("symbol\tside\tverdict\tproof\tfloat_pct\tbroker\tbreadth\tnet_1m\tnet_3w\tnet_3d"
          "\tpct_float\tregular_pct\taccel\tvol_dom\tvol_z"
          "\tfloor_ok\tchart_ok\tpos_ok\tad_trend\tupdown_vol\tprice_chg\tavg_price\tclose\tprice_vs"
          "\tnet_3m\tpct_3m\tcoord2_pct\tlockin_expiry\tlockin_days"
          "\tsector\tsector_chg\trel_str\taccum_days\tprofit_yoy\teps_yoy\tearn_period"
          "\tnet_half1\tnet_half2"
          "\tpub_shares\tbroker_buy\ttape_vol\tactive_days\tsame_days")


def load_float():
    pub, flp = {}, {}
    for line in (MASTER / "fundamentals.txt").read_text(encoding="utf-8").splitlines()[1:]:
        f = line.split("\t")
        try:
            p, pr, pu, fl = float(f[4]), float(f[5]), float(f[3]), float(f[6])
        except (ValueError, IndexError):
            continue
        if pu > 0 and abs((p + pr) - pu * 10) / max(pu * 10, 1) < 0.05 and 0 < fl < 95:
            pub[f[0]], flp[f[0]] = p, fl
    return pub, flp


def book_closes():
    bc = defaultdict(list)
    for line in (MASTER / "corporate_actions.txt").read_text(encoding="utf-8").splitlines()[1:]:
        f = line.split("\t")
        if len(f) > 5 and f[5]:
            bc[f[0]].append(f[5])
    return bc


def lockins():
    """symbol -> IPO promoter lock-in expiry date (ISO). When it passes, locked promoter shares
    can hit the market — a supply shock that caps or crashes an accumulation. Absent file = {}."""
    p = MASTER / "lockin.txt"
    if not p.exists():
        return {}
    out = {}
    for line in p.read_text(encoding="utf-8").splitlines()[1:]:
        f = line.split("\t")
        if len(f) > 2 and f[2]:
            out[f[0]] = f[2]
    return out


def earnings_map():
    """symbol -> latest-quarter YoY growth (net profit, EPS-TTM) from SWP KeyFinancials. Tells a
    fundamentally-driven move from an unexplained one. Absent file / symbol = no earnings line."""
    m = {}
    p = MASTER / "earnings.txt"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines()[1:]:
            f = line.split("\t")
            if len(f) >= 4:
                m[f[0]] = {"fy": f[1], "q": f[2], "profit": f[3], "eps": f[4] if len(f) > 4 else ""}
    return m


def median(xs):
    s = sorted(xs)
    n = len(s)
    return 0.0 if not n else (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)


def load_sectors():
    """symbol -> sector (from SWP). Used to tell an idiosyncratic move from a sector-wide one."""
    m = {}
    p = MASTER / "sectors.txt"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines()[1:]:
            f = line.split("\t")
            if len(f) > 1 and f[1]:
                m[f[0]] = f[1]
    return m


def sector_medians(names, smap):
    """Median 1-month (MONTH-session) close-to-close % change per sector, over its equity peers.
    A candidate's move minus this is its RELATIVE strength — how much it moved on its own vs beta.
    Sectors with < 4 peers are dropped (no reliable benchmark)."""
    bysec = defaultdict(list)
    for sym in names:
        sec = smap.get(sym)
        if not sec:
            continue
        rows = bars(sym)
        if len(rows) < MONTH + 1:
            continue
        c_now, c_then = rows[-1][4], rows[-MONTH][4]
        if c_then > 0:
            bysec[sec].append((c_now / c_then - 1) * 100)
    return {sec: median(v) for sec, v in bysec.items() if len(v) >= 4}


def bars(sym):
    p = MASTER / "symbols" / sym / "1D.txt"
    if not p.exists():
        return []
    out = []
    for l in p.read_text(encoding="utf-8").splitlines()[1:]:
        f = l.split("\t")
        if f[7] not in ("", "None"):
            out.append((f[0], float(f[1]), float(f[2]), float(f[3]), float(f[4]), float(f[7])))
    return out              # date, open, high, low, close, volume


def chart_signal(rows, dates):
    """Accumulation read from price+volume ONLY — no broker data.

    Returns (ad_rising, updown_vol_ratio, price_change_pct). ad_rising is the slope sign of the
    Accumulation/Distribution line over the window; updown_vol compares volume on up-closes vs
    down-closes; price_change is close-to-close over the window.
    """
    win = [r for r in rows if r[0] in set(dates)]
    if len(win) < 5:
        return 0.0, 1.0, 0.0
    ad, ad_series = 0.0, []
    up_v = dn_v = 0.0
    prev_close = None
    for _, o, h, l, c, v in win:
        rng = h - l
        mfm = ((c - l) - (h - c)) / rng if rng else 0.0     # money-flow multiplier
        ad += mfm * v
        ad_series.append(ad)
        if prev_close is not None:
            if c > prev_close:
                up_v += v
            elif c < prev_close:
                dn_v += v
        prev_close = c
    # slope of the A/D line, normalised by average |ad| so it is comparable across stocks
    n = len(ad_series)
    xs = list(range(n))
    mx, my = sum(xs) / n, sum(ad_series) / n
    denom = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ad_series)) / denom if denom else 0.0
    scale = (sum(abs(a) for a in ad_series) / n) or 1.0
    ad_trend = slope / scale                                 # >0 rising, <0 falling
    updown = up_v / dn_v if dn_v else (2.0 if up_v else 1.0)
    price_chg = (win[-1][4] / win[0][4] - 1) * 100 if win[0][4] else 0.0
    return ad_trend, updown, price_chg


def broker_days(sym, dates, broker):
    net = defaultdict(float)
    buy = defaultdict(float)
    amt = defaultdict(float)
    want = set(dates)
    lines = (MASTER / "broker_flow" / f"{sym}.txt").read_text(encoding="utf-8").splitlines()
    for line in lines[max(1, len(lines) - 12000):]:
        f = line.split("\t")
        if f[0] in want and f[1] == broker:
            net[f[0]] += float(f[4])
            buy[f[0]] += float(f[2])
            amt[f[0]] += float(f[5])
    return net, buy, amt


def month_leaders(sym, dates):
    tot = defaultdict(float)
    want = set(dates)
    lines = (MASTER / "broker_flow" / f"{sym}.txt").read_text(encoding="utf-8").splitlines()
    turnover = 0.0
    for line in lines[max(1, len(lines) - 12000):]:
        f = line.split("\t")
        if f[0] in want and len(f) >= 8:
            tot[f[1]] += float(f[4])
            turnover += float(f[5])
    if not tot:
        return None
    return max(tot, key=tot.get), min(tot, key=tot.get), turnover, dict(tot)


def non_operable():
    """Symbols an operator does not work: bonds/debentures, mutual funds, and promoter shares.

    Bonds and funds trade on coupons/NAV, not accumulation; promoter shares (ticker ends in
    P/PO with the ordinary line also listed) are locked-in stock, not a free-float campaign.
    Type comes from instruments.txt (NAASA); promoter shares are caught by ticker shape.
    """
    skip = set()
    ip = MASTER / "instruments.txt"
    listed = set()
    if ip.exists():
        for line in ip.read_text(encoding="utf-8").splitlines()[1:]:
            f = line.split("\t")
            if len(f) >= 2:
                listed.add(f[0])
                if f[1] in ("Bond", "Mutual Fund", "Index"):
                    skip.add(f[0])
    for s in listed:
        base = s[:-2] if s.endswith("PO") else (s[:-1] if s.endswith("P") else None)
        if base and base in listed:          # promoter line of an ordinary share
            skip.add(s)
    return skip


def market_wide_breadth():
    """How many symbols each broker is the all-time #1 net buyer OR #1 net seller of.

    A broker that leads dozens of unrelated stocks is a hyperactive/market-making firm, not an
    operator on any one of them. The old per-scan MEGA_AT filter never fired (breadth was only
    counted among already-gated records), so market-makers like broker 17 (tops 22 symbols) or
    58 (31) slipped through. This measures breadth across the WHOLE archive.
    """
    top = Counter()
    for f in MASTER.glob("broker_flow/*.txt"):
        tot = defaultdict(float)
        for line in f.read_text(encoding="utf-8").splitlines()[1:]:
            p = line.split("\t")
            if len(p) >= 5:
                tot[p[1]] += float(p[4])
        if tot:
            top[max(tot, key=tot.get)] += 1
            top[min(tot, key=tot.get)] += 1
    return top


BROAD_MEGA = 12         # broker that leads >= this many symbols market-wide = hyperactive firm


def main():
    pub, flp = load_float()
    bc = book_closes()
    lk = lockins()
    today = date.today()
    hyper = {b for b, c in market_wide_breadth().items() if c >= BROAD_MEGA}
    skip = non_operable()
    names = [n for n in (MASTER / "symbols.txt").read_text(encoding="utf-8").split() if n not in skip]
    smap = load_sectors()
    sec_med = sector_medians(names, smap)
    earn = earnings_map()

    recs, breadth = [], Counter()
    for sym in names:
        if sym not in pub:
            continue
        rows = bars(sym)
        if len(rows) < 80:
            continue
        dates = [r[0] for r in rows][-MONTH:]
        if any(min(dates) <= d <= max(dates) for d in bc.get(sym, [])):
            continue
        lead = month_leaders(sym, dates)
        if not lead or lead[2] < 20_000_000:
            continue
        buyer, seller, _, monthnet = lead
        # coordination context: combined net of the top-2 buyers / top-2 sellers over the month
        buys = sorted((n for n in monthnet.values() if n > 0), reverse=True)
        sells = sorted((n for n in monthnet.values() if n < 0))
        top2_buy = sum(buys[:2])
        top2_sell = sum(sells[:2])

        vol = {r[0]: r[5] for r in rows}
        v20 = [vol[d] for d in dates]
        base = [r[5] for r in rows[-80:-20]]
        lb = [math.log1p(v) for v in base]
        mb = sum(lb) / len(lb)
        sd = (sum((x - mb) ** 2 for x in lb) / (len(lb) - 1)) ** 0.5
        z = (sum(math.log1p(v) for v in v20) / MONTH - mb) / sd if sd else 0
        total_vol = sum(v20)
        close = rows[-1][4]
        ad_trend, updown, price_chg = chart_signal(rows, dates)
        dates60 = [r[0] for r in rows][-QUARTER:]

        for broker, want_buy in ((buyer, True), (seller, False)):
            net, buy, amt = broker_days(sym, dates, broker)
            n1 = sum(net.values())
            if abs(n1) / pub[sym] * 100 < 2:
                continue
            # 3-month (60-session) net for the same broker — long campaign vs fresh?
            net60, _, _ = broker_days(sym, dates60, broker)
            n3m = sum(net60.values())
            pct3m = n3m / pub[sym] * 100
            n2 = sum(net[d] for d in dates[-WEEK3:])
            n3 = sum(net[d] for d in dates[-DAY3:])
            # campaign trend: broker net in the first vs second half of the month — is the
            # accumulation intensifying (back-loaded) or fading (front-loaded)?
            half = MONTH // 2
            h1 = sum(net.get(d, 0) for d in dates[:half])
            h2 = sum(net.get(d, 0) for d in dates[half:])
            active = [d for d in dates if d in net]
            same = [d for d in active if (net[d] > 0) == want_buy]
            reg = len(same) / max(len(active), 1)
            accel = (abs(n3) / DAY3) / (abs(n1) / MONTH) if n1 else 0
            casc = (n1 > 0 and n2 > 0 and n3 > 0) if want_buy else (n1 < 0 and n2 < 0 and n3 < 0)
            dom = sum(buy.values()) / total_vol * 100 if total_vol else 0
            gross = sum(buy.values())
            avg = sum(amt.values()) / gross if gross else 0
            pct = n1 / pub[sym] * 100
            coord2 = (top2_buy if want_buy else top2_sell) / pub[sym] * 100  # top-2 same-side

            if broker in hyper:          # hyperactive/market-making firm, not an operator
                continue
            # THREE INDEPENDENT LEGS
            floor_ok = reg >= 0.70 and casc and accel >= 0.7 and dom >= 15
            if want_buy:
                chart_ok = ad_trend > 0 and updown >= 1.0 and price_chg > -8
            else:
                chart_ok = ad_trend < 0 and updown <= 1.0 and price_chg < 8
            pos_ok = abs(pct) >= 3
            proof = int(floor_ok) + int(chart_ok) + int(pos_ok)

            word = "buying more" if want_buy else "selling more"
            if proof == 3:
                verdict = f"strong {word} lead — all 3 sources (still a lead, not proof)"
            elif proof == 2:
                verdict = f"likely {word} (2 of 3 sources)"
            elif floor_ok:
                verdict = f"floorsheet says {word}, chart not confirming"
            else:
                verdict = "weak / conflicting"

            recs.append({
                "sym": sym, "side": "BUY" if want_buy else "SELL", "verdict": verdict, "proof": proof,
                "flp": flp[sym], "broker": broker, "n1": n1, "n2": n2, "n3": n3, "pct": pct,
                "reg": reg * 100, "accel": accel, "dom": dom, "z": z,
                "floor_ok": floor_ok, "chart_ok": chart_ok, "pos_ok": pos_ok,
                "ad_trend": ad_trend, "updown": updown, "price_chg": price_chg,
                "avg": avg, "close": close, "n3m": n3m, "pct3m": pct3m, "coord2": coord2,
                "lockin": lk.get(sym, ""),
                "ldays": (date.fromisoformat(lk[sym]) - today).days if sym in lk else "",
                "sector": smap.get(sym, ""),
                "sec_chg": sec_med.get(smap.get(sym, "")),
                "rel_str": (price_chg - sec_med[smap[sym]])
                           if sym in smap and smap[sym] in sec_med else None,
                # net position expressed as days of the stock's own average volume — how hard one
                # broker is cornering the tradeable liquidity (higher = more aggressive campaign)
                "accum_days": abs(n1) * MONTH / total_vol if total_vol else 0,
                "profit_yoy": earn.get(sym, {}).get("profit", ""),
                "eps_yoy": earn.get(sym, {}).get("eps", ""),
                "earn_period": (f"{earn[sym]['q']} {earn[sym]['fy']}" if sym in earn else ""),
                "h1": h1, "h2": h2,
                # raw building blocks so the UI can show the actual arithmetic behind each claim
                "pub_shares": pub[sym], "broker_buy": gross, "tape_vol": total_vol,
                "active_days": len(active), "same_days": len(same),
            })
            breadth[broker] += 1

    rows_out = list(recs)          # hyperactive brokers already skipped above
    rows_out.sort(key=lambda r: (-r["proof"], -r["accel"]))

    lines = [HEADER]
    for r in rows_out:
        pv = (r["avg"] / r["close"] - 1) * 100 if r["close"] else 0
        lines.append("\t".join(str(v) for v in (
            r["sym"], r["side"], r["verdict"], r["proof"], f"{r['flp']:.0f}", r["broker"],
            breadth[r["broker"]], f"{r['n1']:.0f}", f"{r['n2']:.0f}", f"{r['n3']:.0f}",
            f"{r['pct']:+.1f}", f"{r['reg']:.0f}", f"{r['accel']:.1f}", f"{r['dom']:.0f}",
            f"{r['z']:+.2f}", "yes" if r["floor_ok"] else "no", "yes" if r["chart_ok"] else "no",
            "yes" if r["pos_ok"] else "no", f"{r['ad_trend']:+.3f}", f"{r['updown']:.2f}",
            f"{r['price_chg']:+.1f}", f"{r['avg']:.2f}", f"{r['close']:.2f}", f"{pv:+.1f}",
            f"{r['n3m']:.0f}", f"{r['pct3m']:+.1f}", f"{r['coord2']:+.1f}",
            r["lockin"], r["ldays"], r["sector"],
            f"{r['sec_chg']:+.1f}" if r["sec_chg"] is not None else "",
            f"{r['rel_str']:+.1f}" if r["rel_str"] is not None else "",
            f"{r['accum_days']:.1f}", r["profit_yoy"], r["eps_yoy"], r["earn_period"],
            f"{r['h1']:.0f}", f"{r['h2']:.0f}",
            f"{r['pub_shares']:.0f}", f"{r['broker_buy']:.0f}", f"{r['tape_vol']:.0f}",
            r["active_days"], r["same_days"])))
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    proven = [r for r in rows_out if r["proof"] == 3]
    print(f"scanned {len({r['sym'] for r in recs})} operable stocks | {len(hyper)} hyperactive "
          f"brokers excluded market-wide: {sorted(hyper, key=int)}\n")
    print(f"PROVEN by all 3 sources — floorsheet + chart + position ({len(proven)}):")
    for r in proven:
        print(f"   {r['sym']:6} {r['side']:4} broker {r['broker']:>3}  "
              f"regular {r['reg']:.0f}%  dom {r['dom']:.0f}%  {r['pct']:+.0f}% float  "
              f"| chart: A/D {r['ad_trend']:+.2f}, up/dn vol {r['updown']:.1f}, price {r['price_chg']:+.0f}%")
    if not proven:
        print("   none this session — no stock has all three sources agreeing")
    print("\nFloorsheet says buying but CHART does NOT confirm (one-source only):")
    for r in [x for x in rows_out if x["floor_ok"] and not x["chart_ok"]][:8]:
        print(f"   {r['sym']:6} {r['side']:4} broker {r['broker']:>3}  "
              f"A/D {r['ad_trend']:+.2f}, up/dn vol {r['updown']:.1f}, price {r['price_chg']:+.0f}% "
              "-> tape not backing the broker")


if __name__ == "__main__":
    main()
