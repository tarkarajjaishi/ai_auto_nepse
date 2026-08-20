"""OPERATOR TEST — a formal yes/no per stock, from the floorsheet, with the arithmetic shown.

Four independent tests. Each one alone has an innocent explanation; passing all four at once does
not, and that is the whole design.

  1 CONCENTRATION  the broker's share of THIS stock vs its own share of every OTHER stock, same
                   sessions. This is the load-bearing test. A broker doing 1.2% of the market
                   everywhere but 44% here cannot be explained by "it is a big broker" — its own
                   client base is the control group.
                   PASS: share in-stock >= 20% AND >= 5x its share elsewhere

  2 SIZE           median clip and block rate vs the same stock's other participants. Retail in
                   NEPSE trades 20-80 share lots; institutional tickets are 1000+.
                   PASS: median clip >= 1.5x the rest of the tape OR block rate >= 3x

  3 PERSISTENCE    % of the last 20 sessions it was net on that side. A campaign shows up day
                   after day; a block is one session wearing a big number.
                   PASS: >= 60% of sessions

  4 BREADTH        distinct counterparties. Absorbing from a crowd is accumulation; trading with
                   one desk is a cross and means nothing.
                   PASS: >= 10 counterparties

  4/4 = OPERATOR ENGAGED     3/4 = LIKELY     else = NO

WHAT THE VERDICT MEANS, EXACTLY: one decision-maker is behind the flow. That much the arithmetic
does establish — hundreds of independent retail clients cannot produce a 35x concentration in one
broker while trading 10x the market's block rate. What it does NOT establish is MOTIVE: a
manipulator cornering float, a mutual fund building a position, and a proprietary desk look
identical in this data. "Operator engaged" here means a single large accumulator, not a verdict
on their intent, which the floorsheet cannot see.

    python operator_verdict.py
    python operator_verdict.py --symbol NLICL --show
"""
import argparse
import statistics
from collections import defaultdict
from pathlib import Path

FLOOR = Path(__file__).parent / "Master_data" / "floorsheet"
OUT = Path(__file__).parent / "Master_data" / "operator_verdict.txt"
WINDOW = 20
MIN_TURNOVER = 500_000
HEADER = ("symbol\tverdict\tpasses\tside\tbroker\tshare_in\tshare_out\tratio\tclip\tclip_mkt"
          "\tblock_pct\tblock_mkt\tpersist_pct\tcounterparties\tnet_shares\tvalue_rs"
          # LAST on purpose: without a `date` column api/tables.read() reports
          # session=None, and its stale check turns that into "not stale" — the board
          # then claims to be current forever.
          "\tdate")


def read_day(path):
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
        f = line.rstrip("\r").split("\t")
        if len(f) < 5:
            continue
        try:
            out.append((f[0], f[1], int(float(f[2])), float(f[4])))
        except ValueError:
            continue
    return out


def load(dates):
    """{symbol: [days]} plus each broker's market-wide buy volume — the null model."""
    data, mkt_buy, mkt_tot = {}, defaultdict(int), 0
    for d in FLOOR.iterdir():
        if not d.is_dir():
            continue
        days = []
        for dt in dates:
            p = d / f"{dt}.txt"
            days.append(read_day(p) if p.exists() else [])
        if not any(days):
            continue
        data[d.name] = days
        for day in days:
            for b, s, q, _a in day:
                if b != s:
                    mkt_buy[b] += q
                    mkt_tot += q
    return data, mkt_buy, mkt_tot


def test(symbol, days, mkt_buy, mkt_tot):
    flat = [t for d in days for t in d if t[0] != t[1]]
    if not flat:
        return None
    turn = [sum(t[3] for t in d) for d in days if d]
    if not turn or statistics.median(turn) < MIN_TURNOVER:
        return None

    net, gross_buy = defaultdict(int), defaultdict(int)
    for b, s, q, _a in flat:
        net[b] += q
        net[s] -= q
        gross_buy[b] += q
    vol = sum(t[2] for t in flat)
    if vol <= 0:
        return None
    side = "BUY"
    who = max(net, key=net.get)
    if abs(min(net.values())) > net[who]:
        side, who = "SELL", min(net, key=net.get)

    # 1 CONCENTRATION — its share here vs its share everywhere else
    x1 = gross_buy[who] if side == "BUY" else sum(q for b, s, q, _a in flat if s == who)
    share_in = 100 * x1 / vol
    out_vol = mkt_tot - vol
    share_out = 100 * (mkt_buy[who] - gross_buy[who]) / out_vol if out_vol > 0 else 0
    ratio = share_in / share_out if share_out > 0.01 else 999.0
    t1 = share_in >= 20 and ratio >= 5

    # 2 SIZE — its clips vs the rest of this stock's tape
    mine = [q for b, s, q, _a in flat if (b == who if side == "BUY" else s == who)]
    rest = [q for b, s, q, _a in flat if (b != who if side == "BUY" else s != who)]
    if not mine or not rest:
        return None
    clip, clip_mkt = statistics.median(mine), statistics.median(rest)
    block = 100 * sum(1 for q in mine if q >= 1000) / len(mine)
    block_mkt = 100 * sum(1 for q in rest if q >= 1000) / len(rest)
    t2 = (clip >= 1.5 * clip_mkt) or (block >= 3 * max(block_mkt, 0.5))

    # 3 PERSISTENCE — how many sessions it was actually on that side
    active = 0
    for d in days:
        if not d:
            continue
        dn = defaultdict(int)
        for b, s, q, _a in d:
            if b != s:
                dn[b] += q
                dn[s] -= q
        n = dn.get(who, 0)
        if (side == "BUY" and n > 0) or (side == "SELL" and n < 0):
            active += 1
    traded = sum(1 for d in days if d)
    persist = 100 * active / max(traded, 1)
    t3 = persist >= 60

    # 4 BREADTH — distinct counterparties
    ctp = len({s if side == "BUY" else b for b, s, q, _a in flat
               if (b == who if side == "BUY" else s == who) and b != s})
    t4 = ctp >= 10

    passes = sum((t1, t2, t3, t4))
    return {"symbol": symbol, "side": side, "broker": who,
            "share_in": share_in, "share_out": share_out, "ratio": ratio,
            "clip": clip, "clip_mkt": clip_mkt, "block": block, "block_mkt": block_mkt,
            "persist": persist, "ctp": ctp, "net": abs(net[who]),
            "value": abs(net[who]) * (sum(t[3] for t in flat) / vol),
            "tests": (t1, t2, t3, t4), "passes": passes,
            "verdict": "OPERATOR ENGAGED" if passes == 4 else "LIKELY" if passes == 3 else "NO"}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol")
    ap.add_argument("--show", action="store_true", help="show the four tests in full")
    a = ap.parse_args()

    dates = [p.stem for p in sorted((FLOOR / "NABIL").glob("*.txt"))[-WINDOW:]]
    print(f"sessions {dates[0]} .. {dates[-1]}   loading tape ...", flush=True)
    data, mkt_buy, mkt_tot = load(dates)
    print(f"{len(data)} symbols · {mkt_tot:,} shares · {len(mkt_buy)} brokers\n")

    names = [a.symbol.upper()] if a.symbol else sorted(data)
    rows = [r for r in (test(s, data[s], mkt_buy, mkt_tot) for s in names if s in data) if r]
    rows.sort(key=lambda r: (r["passes"], r["ratio"]), reverse=True)

    OUT.write_text("\n".join([HEADER] + ["\t".join(str(x) for x in (
        r["symbol"], r["verdict"], r["passes"], r["side"], r["broker"],
        round(r["share_in"], 1), round(r["share_out"], 2), round(r["ratio"], 1),
        int(r["clip"]), int(r["clip_mkt"]), round(r["block"], 1), round(r["block_mkt"], 1),
        round(r["persist"]), r["ctp"], r["net"], round(r["value"]), dates[-1])) for r in rows]) + "\n",
        encoding="utf-8")

    engaged = [r for r in rows if r["passes"] == 4]
    likely = [r for r in rows if r["passes"] == 3]
    print(f"OPERATOR ENGAGED (4/4): {len(engaged)}    LIKELY (3/4): {len(likely)}    "
          f"of {len(rows)} liquid symbols\n")

    if a.show or a.symbol:
        for r in rows[:12]:
            t1, t2, t3, t4 = r["tests"]
            m = lambda ok: "PASS" if ok else "fail"
            print(f"=== {r['symbol']}  broker {r['broker']}  {r['side']}  -> {r['verdict']} "
                  f"({r['passes']}/4) ===")
            print(f"  1 concentration  {m(t1)}  {r['share_in']:.1f}% of this stock vs "
                  f"{r['share_out']:.2f}% of everything else = {r['ratio']:.0f}x")
            print(f"  2 size           {m(t2)}  median clip {r['clip']:.0f} vs {r['clip_mkt']:.0f} "
                  f"| blocks {r['block']:.0f}% vs {r['block_mkt']:.0f}%")
            print(f"  3 persistence    {m(t3)}  net on that side {r['persist']:.0f}% of sessions")
            print(f"  4 breadth        {m(t4)}  {r['ctp']} distinct counterparties")
            print(f"  position: {r['net']:,} shares  ~Rs {r['value']:,.0f}\n")
        return 0

    print(f"{'symbol':<10}{'verdict':<18}{'side':>5}{'brkr':>6}{'share':>8}{'vs else':>9}"
          f"{'ratio':>8}{'clip':>7}{'blk%':>6}{'days':>6}{'ctp':>5}")
    for r in engaged + likely[:12]:
        print(f"{r['symbol']:<10}{r['verdict']:<18}{r['side']:>5}{r['broker']:>6}"
              f"{r['share_in']:>7.1f}%{r['share_out']:>8.2f}%{r['ratio']:>7.0f}x"
              f"{r['clip']:>7.0f}{r['block']:>6.0f}{r['persist']:>5.0f}%{r['ctp']:>5}")
    print(f"\nfull table -> {OUT}")
    print("\nVerdict = ONE DECISION-MAKER behind the flow. Motive is not visible here: a")
    print("manipulator, a fund building a position, and a prop desk are indistinguishable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
