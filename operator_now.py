"""Where is one broker's grip on a stock statistically ABNORMAL right now — floorsheet only.

This is a DESCRIPTIVE screen, and the distinction is the whole point. The research run proved a
predictive operator signal is not in this data: across 135,626 liquid symbol-days the top
accumulator's 20-session net was positive on 100% of them, so "an operator is accumulating" is an
identity, not a condition, and six families built on it all died out of sample.

So this does not ask "who is the top buyer" (always somebody). It asks:

    is this broker's grip on this stock extreme compared with every OTHER stock today?

Everything is cross-sectionally z-scored ON THE SAME DATE. That is the repo's pre-registered kill
rule, and it matters: the wash family's entire apparent edge was the gap between an obs-weighted
view (-3.01pp) and a per-date view (-0.28pp) — a date-mix artifact. Ranking within a single day
cannot have that bug.

Four windows, as asked: 20 / 15 / 7 / 3 sessions.

  grip      top accumulator's net buying as a share of window volume
  persist   is it the SAME broker leading all four windows (1 identity) or rotating (4)
  tighten   is the grip tightening into the short windows (3d grip - 20d grip)
  breadth   how many distinct sellers it is absorbing from (1 = a block/cross, not accumulation)

The score is the date-z of grip, plus persistence, plus tightening. High score = unusual, and
unusual is all it means. It is a place to look, not a forecast — quoted returns are not attached
on purpose, because the ones this would have quoted did not survive testing.

    python operator_now.py                 # today
    python operator_now.py --top 25        # show more
"""
import argparse
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

FLOOR = Path(__file__).parent / "Master_data" / "floorsheet"
OUT = Path(__file__).parent / "Master_data" / "operator_now.txt"
WINDOWS = (20, 15, 7, 3)
MIN_TURNOVER = 500_000          # Rs median daily turnover over the 20-session window
MIN_SELLERS = 5                 # the leader must be absorbing from a real crowd, not one desk
MAX_STALE = 5                   # its last session must be within N sessions of the archive's latest
MIN_BROKERS = 10                # a day with a handful of brokers is a block market, not a tape
HEADER = ("side\tsymbol\tscore\tbroker\tregular\tgrip20\tgrip15\tgrip7\tgrip3\ttighten\tpersist"
          "\tcounterparties\tnet20\tturnover\tvwap\tsessions")


def sessions(symbol, n):
    """The last n session files for a symbol, oldest first."""
    d = FLOOR / symbol
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.txt"))[-n:]


def read_day(path):
    """[(buyer, seller, qty, amount)] for one session.

    Parse gotcha found the hard way: old files are CRLF and write quantity as `50.0`, so a bare
    int() raises and silently drops the ENTIRE day — one probe read 227 of 1,056 sessions and
    never errored. int(float(x)) is mandatory here."""
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


def profile(symbol):
    """Per-window grip for the dominant accumulator, or None if too thin / no data."""
    files = sessions(symbol, max(WINDOWS))
    if len(files) < max(WINDOWS):
        return None
    days = [read_day(p) for p in files]
    if not any(days):
        return None

    turnovers = [sum(t[3] for t in d) for d in days]
    if statistics.median(turnovers) < MIN_TURNOVER:
        return None
    # A promoter share or debenture trades as occasional negotiated blocks between a couple of
    # desks. One block then reads as "100% grip", which is arithmetic, not accumulation. Demand a
    # real multi-broker tape — this is floorsheet-native, no instrument list needed.
    brokers_per_day = [len({t[0] for t in d} | {t[1] for t in d}) for d in days if d]
    if not brokers_per_day or statistics.median(brokers_per_day) < MIN_BROKERS:
        return None
    qty_all = sum(t[2] for d in days for t in d)
    amt_all = sum(t[3] for d in days for t in d)
    if qty_all <= 0:
        return None

    # Per-window net per broker, computed once and reused for both sides.
    per_window = {}
    for w in WINDOWS:
        net = defaultdict(int)
        vol = 0
        for d in days[-w:]:
            for b, s, q, _a in d:
                if b == s:                       # a broker crossing with itself moves no float
                    continue
                net[b] += q
                net[s] -= q
                vol += q
        if not net or vol <= 0:
            return None
        per_window[w] = (net, vol)

    def side_profile(side):
        """The dominant net BUYER or net SELLER, and how regularly it actually shows up.

        `regular` is the point of the whole thing: a broker can end a window with a big net from
        ONE session, which is a block, not a campaign. This counts on how many of the 20 sessions
        it was genuinely on that side — daily behaviour, not the window total."""
        grip, leader = {}, {}
        for w in WINDOWS:
            net, vol = per_window[w]
            top = max(net, key=net.get) if side == "BUY" else min(net, key=net.get)
            grip[w] = 100 * abs(net[top]) / vol
            leader[w] = top
        who = leader[20]

        # how many sessions was this broker net on that side, and who did it face
        active, counterparties = 0, set()
        for d in days[-20:]:
            day_net = defaultdict(int)
            for b, s, q, _a in d:
                if b == s:
                    continue
                day_net[b] += q
                day_net[s] -= q
                if side == "BUY" and b == who and s != who:
                    counterparties.add(s)
                elif side == "SELL" and s == who and b != who:
                    counterparties.add(b)
            n = day_net.get(who, 0)
            if (side == "BUY" and n > 0) or (side == "SELL" and n < 0):
                active += 1
        traded_days = sum(1 for d in days[-20:] if d)
        return {
            "side": side, "symbol": symbol, "broker": who, "grip": grip,
            "regular": round(100 * active / max(traded_days, 1)),   # % of sessions on that side
            "tighten": grip[3] - grip[20],
            "persist": len({leader[w] for w in WINDOWS}),
            "counterparties": len(counterparties),
            "net20": abs(per_window[20][0][who]),
            "turnover": statistics.median(turnovers),
            "vwap": amt_all / qty_all,
            "sessions": len(files),
            "last": files[-1].stem,
        }

    return [side_profile("BUY"), side_profile("SELL")]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top", type=int, default=15)
    a = ap.parse_args()

    names = sorted(p.name for p in FLOOR.iterdir() if p.is_dir())
    print(f"scanning {len(names)} symbols over the last {max(WINDOWS)} sessions ...", flush=True)
    got = [r for r in (profile(s) for s in names) if r]
    if not got:
        print("no symbol had enough liquid floorsheet history")
        return 0
    rows = [side for pair in got for side in pair]

    # Recency: a thin name's "last 20 sessions" can reach months back, so its grip is not
    # comparable with a stock trading today. Rank only names current with the live tape.
    latest = max(r["last"] for r in rows)
    cutoff = sorted({r["last"] for r in rows}, reverse=True)[
        min(MAX_STALE, len({r["last"] for r in rows}) - 1)]
    fresh = [r for r in rows if r["last"] >= cutoff]
    # Breadth: trading against one desk is a cross, not accumulation or distribution of float.
    ranked = [r for r in fresh if r["counterparties"] >= MIN_SELLERS]
    dropped_stale, dropped_block = len(rows) - len(fresh), len(fresh) - len(ranked)
    if not ranked:
        print("nothing passed the recency + counterparty-breadth filters")
        return 0

    # Score each SIDE against its own cross-section on the same date — a 40% buy-grip and a 40%
    # sell-grip are not comparable, so they get separate baselines.
    out_lines, printed = [HEADER], {}
    for side in ("BUY", "SELL"):
        sel = [r for r in ranked if r["side"] == side]
        if not sel:
            continue
        g20 = [r["grip"][20] for r in sel]
        tg = [r["tighten"] for r in sel]
        mu_g, sd_g = statistics.fmean(g20), (statistics.pstdev(g20) or 1)
        mu_t, sd_t = statistics.fmean(tg), (statistics.pstdev(tg) or 1)
        for r in sel:
            persist_bonus = {1: 1.0, 2: 0.5, 3: 0.2, 4: 0.0}[r["persist"]]
            # regularity gates the score: a big net done in 3 of 20 sessions is a block, and
            # should not outrank a broker that showed up day after day.
            reg = r["regular"] / 100
            r["score"] = round(((r["grip"][20] - mu_g) / sd_g
                                + 0.5 * (r["tighten"] - mu_t) / sd_t
                                + persist_bonus) * (0.4 + 0.6 * reg), 2)
        sel.sort(key=lambda r: r["score"], reverse=True)
        printed[side] = (sel, mu_g, sd_g)
        out_lines += ["\t".join(str(x) for x in (
            r["side"], r["symbol"], r["score"], r["broker"], r["regular"],
            round(r["grip"][20], 2), round(r["grip"][15], 2), round(r["grip"][7], 2),
            round(r["grip"][3], 2), round(r["tighten"], 2), r["persist"],
            r["counterparties"], r["net20"], round(r["turnover"]), round(r["vwap"], 2),
            r["sessions"])) for r in sel]
    OUT.write_text("\n".join(out_lines) + "\n", encoding="utf-8")

    print(f"\n=== DOMINANT BROKERS · tape to {latest} ===")
    print(f"filtered: {dropped_stale} stale (last trade before {cutoff}) · "
          f"{dropped_block} block/cross (<{MIN_SELLERS} counterparties)")
    for side, title in (("BUY", "ACCUMULATING  — one broker is the dominant NET BUYER"),
                        ("SELL", "DISTRIBUTING  — one broker is the dominant NET SELLER")):
        if side not in printed:
            continue
        sel, mu_g, sd_g = printed[side]
        print(f"\n--- {title} ({len(sel)} symbols, mean grip {mu_g:.1f}% sd {sd_g:.1f}) ---")
        print(f"{'symbol':<10}{'score':>6}{'brkr':>6}{'regular':>9}{'20d':>7}{'15d':>7}"
              f"{'7d':>7}{'3d':>7}{'tight':>7}{'ids':>5}{'ctp':>5}")
        for r in sel[:a.top]:
            print(f"{r['symbol']:<10}{r['score']:>6.2f}{r['broker']:>6}"
                  f"{str(r['regular'])+'%':>9}"
                  f"{r['grip'][20]:>7.1f}{r['grip'][15]:>7.1f}{r['grip'][7]:>7.1f}"
                  f"{r['grip'][3]:>7.1f}{r['tighten']:>+7.1f}{r['persist']:>5}"
                  f"{r['counterparties']:>5}")
    print(f"\nfull ranking -> {OUT}")
    print("regular = % of the 20 sessions that broker was actually net on that side. THIS is the")
    print("  'are they buying regularly' answer: 85% is a daily campaign, 20% is one block.")
    print("ids=1 = same broker led all four windows.  ctp = distinct counterparties faced.")
    print("tight = 3d grip minus 20d grip, so + means the grip is tightening lately.")
    print("\nRanks how UNUSUAL today's concentration is — not a return forecast. Every predictive")
    print("version tested dead, so no expected return is attached to either side.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
