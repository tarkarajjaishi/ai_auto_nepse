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
HEADER = ("symbol\tscore\tbroker\tgrip20\tgrip15\tgrip7\tgrip3\ttighten\tpersist"
          "\tsellers\tnet20\tturnover\tvwap\tsessions")


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

    grip, leader, sellers_of_leader, net20 = {}, {}, 0, 0
    for w in WINDOWS:
        sub = days[-w:]
        net = defaultdict(int)
        vol = 0
        for d in sub:
            for b, s, q, _a in d:
                if b == s:                       # a broker crossing with itself moves no float
                    continue
                net[b] += q
                net[s] -= q
                vol += q
        if not net or vol <= 0:
            return None
        top = max(net, key=net.get)
        grip[w] = 100 * net[top] / vol           # % of window volume this broker net-absorbed
        leader[w] = top
        if w == 20:
            net20 = net[top]
            sellers_of_leader = len({s for d in sub for b, s, q, _a in d if b == top and s != top})

    return {
        "symbol": symbol,
        "broker": leader[20],
        "grip": grip,
        "tighten": grip[3] - grip[20],           # grip tightening into the recent sessions
        "persist": len({leader[w] for w in WINDOWS}),   # 1 = same broker all four windows
        "sellers": sellers_of_leader,
        "net20": net20,
        "turnover": statistics.median(turnovers),
        "vwap": amt_all / qty_all,
        "sessions": len(files),
        "last": files[-1].stem,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top", type=int, default=15)
    a = ap.parse_args()

    names = sorted(p.name for p in FLOOR.iterdir() if p.is_dir())
    print(f"scanning {len(names)} symbols over the last {max(WINDOWS)} sessions ...", flush=True)
    rows = [r for r in (profile(s) for s in names) if r]
    if not rows:
        print("no symbol had enough liquid floorsheet history")
        return 0

    # Recency: a thinly-traded name's "last 20 sessions" can reach months back, so its grip is not
    # comparable with a stock trading today. Rank only names current with the live tape.
    latest = max(r["last"] for r in rows)
    all_dates = sorted({r["last"] for r in rows}, reverse=True)
    cutoff = all_dates[min(MAX_STALE, len(all_dates) - 1)]
    fresh = [r for r in rows if r["last"] >= cutoff]
    # Breadth: buying from one desk is a cross, not accumulation of float.
    ranked = [r for r in fresh if r["sellers"] >= MIN_SELLERS]
    dropped_stale, dropped_block = len(rows) - len(fresh), len(fresh) - len(ranked)
    if not ranked:
        print("nothing passed the recency + counterparty-breadth filters")
        return 0
    rows = ranked

    # cross-sectional z on the SAME date — the whole point; a raw grip number means nothing
    # until you know what grip looks like everywhere else today.
    g20 = [r["grip"][20] for r in rows]
    tg = [r["tighten"] for r in rows]
    mu_g, sd_g = statistics.fmean(g20), (statistics.pstdev(g20) or 1)
    mu_t, sd_t = statistics.fmean(tg), (statistics.pstdev(tg) or 1)
    for r in rows:
        z_grip = (r["grip"][20] - mu_g) / sd_g
        z_tight = (r["tighten"] - mu_t) / sd_t
        # one identity across all four windows is worth something; four different leaders is noise
        persist_bonus = {1: 1.0, 2: 0.5, 3: 0.2, 4: 0.0}[r["persist"]]
        r["score"] = round(z_grip + 0.5 * z_tight + persist_bonus, 2)
    rows.sort(key=lambda r: r["score"], reverse=True)

    lines = [HEADER] + ["\t".join(str(x) for x in (
        r["symbol"], r["score"], r["broker"],
        round(r["grip"][20], 2), round(r["grip"][15], 2), round(r["grip"][7], 2),
        round(r["grip"][3], 2), round(r["tighten"], 2), r["persist"], r["sellers"],
        r["net20"], round(r["turnover"]), round(r["vwap"], 2), r["sessions"])) for r in rows]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\n=== ABNORMAL BROKER GRIP · tape to {latest} · {len(rows)} symbols ranked ===")
    print(f"filtered out: {dropped_stale} stale (last trade before {cutoff}) · "
          f"{dropped_block} block/cross (leader bought from <{MIN_SELLERS} sellers)")
    print(f"cross-sectional mean grip today: {mu_g:.1f}% of volume (sd {sd_g:.1f}) — "
          f"a score of +2 means two sd above THAT\n")
    print(f"{'symbol':<10}{'score':>6}{'brkr':>6}{'20d':>7}{'15d':>7}{'7d':>7}{'3d':>7}"
          f"{'tight':>7}{'ids':>5}{'sellers':>8}")
    for r in rows[:a.top]:
        print(f"{r['symbol']:<10}{r['score']:>6.2f}{r['broker']:>6}"
              f"{r['grip'][20]:>7.1f}{r['grip'][15]:>7.1f}{r['grip'][7]:>7.1f}{r['grip'][3]:>7.1f}"
              f"{r['tighten']:>+7.1f}{r['persist']:>5}{r['sellers']:>8}")
    print(f"\nfull ranking -> {OUT}")
    print("ids=1 means the SAME broker led all four windows; ids=4 means a different leader each")
    print("window (noise). sellers = distinct counterparties it bought from; 1-2 is a block/cross,")
    print("not accumulation. tight = 3d grip minus 20d grip, so + means tightening recently.")
    print("\nThis ranks how UNUSUAL today's concentration is. It is not a return forecast —")
    print("every predictive version of this tested dead, so no expected return is attached.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
