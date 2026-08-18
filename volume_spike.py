"""Screen for unusual volume backed by broker-level flow — the "who is working this stock" list.

Writes Master_data/volume_spike.txt, one row per (window, symbol), for three windows:
3 sessions, 2 weeks (10) and 1 month (20).

WHAT IT MEASURES, and what the research actually supports (see the caveats at the bottom —
they are not decoration, they decide how this list may be read):

  spike_z      log-volume z-score of the window against a 60-session baseline that ENDS
               before the window opens. Ranking on z rather than a raw volume ratio is what
               lets one cutoff mean the same thing for NABIL and for a thin small-cap:
               measured per-symbol p99 spread is 1.9x on z against 3.1x on the ratio.
  net_churn    sum of every broker's positive net, over total bought. The share of the
               window's volume that actually changed hands BETWEEN brokers instead of being
               round-tripped inside them. LOW is the interesting reading: it is stock going
               round in circles, which is the classic operator-churn footprint.
  buy_hhi      Herfindahl of gross buying. A tie-breaker only, and note the sign: concentrated
               buying in a liquid name has predicted UNDER-performance, not accumulation.
  top_buyer /  the broker code with the largest positive and largest negative net over the
  top_seller   window, with their net in shares — "who is on the heavy side".

Of these, net_churn is the only one with out-of-sample support: sorted into deciles across
the liquid universe its forward 5-day return is monotone over all ten, D1 -0.40% to D10
+0.35%, a 0.75% spread. Everything else here is descriptive.

    python volume_spike.py            # rebuild the screen from what is on disk
"""

import math
from pathlib import Path

from fetch_ohlc import MASTER

OUT = MASTER / "volume_spike.txt"
FLOW = MASTER / "broker_flow"

WINDOWS = {"3d": 3, "2w": 10, "1m": 20}
BASE = 60                    # baseline sessions for the volume z-score
FLOOR = 1_000_000            # Rs median daily turnover; below this, concentration is noise
Z_CUT = {3: 2.5, 10: 2.0, 20: 2.0}      # ~1 spike episode per symbol per year
CHURN_CUT = {3: 0.36, 10: 0.24, 20: 0.18}   # 10th percentile of the liquid universe

HEADER = ("window\tsymbol\tdate\tspike_z\tspike_ratio\tturnover\tnet_churn\tbuy_hhi"
          "\ttop_buyer\tbuyer_net\ttop_seller\tseller_net\tprior30\twindow_pct\trel_move"
          "\tkind\tflags")


def tail_lines(path, kb=512):
    """Last rows of a file. broker_flow files run to 60k+ rows and we only need 20 sessions."""
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - kb * 1024))
        chunk = f.read().decode("utf-8", "replace").splitlines()
    # big file: the read started mid-line, so the first fragment is partial — drop it.
    # small file: drop the header row instead.
    return chunk[1:] if size <= kb * 1024 else chunk[1:]


def daily(symbol):
    """[(date, close, volume)] oldest last, only the tail we need."""
    path = MASTER / "symbols" / symbol / "1D.txt"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        f = line.split("\t")
        if len(f) > 7 and f[4] not in ("", "None") and f[7] not in ("", "None"):
            rows.append((f[0], float(f[4]), float(f[7])))
    return rows[-(BASE + max(WINDOWS.values()) + 35):]


def broker_window(symbol, dates):
    """Aggregate broker_flow over exactly `dates`. Returns None when that session set is absent."""
    path = FLOW / f"{symbol}.txt"
    if not path.exists():
        return None
    want = set(dates)
    agg = {}
    for line in tail_lines(path):
        f = line.split("\t")
        if len(f) < 8 or f[0] not in want:
            continue
        b = agg.setdefault(f[1], [0.0, 0.0])
        b[0] += float(f[2])          # bought
        b[1] += float(f[3])          # sold
    if not agg:
        return None

    bought = sum(v[0] for v in agg.values())
    if bought <= 0:
        return None
    nets = {k: v[0] - v[1] for k, v in agg.items()}
    pos = sum(n for n in nets.values() if n > 0)
    top_buy = max(nets.items(), key=lambda kv: kv[1])
    top_sell = min(nets.items(), key=lambda kv: kv[1])
    return {
        "net_churn": pos / bought,
        "buy_hhi": sum((v[0] / bought) ** 2 for v in agg.values()),
        "top_buyer": top_buy[0], "buyer_net": top_buy[1],
        "top_seller": top_sell[0], "seller_net": top_sell[1],
    }


def median(xs):
    s = sorted(xs)
    n = len(s)
    return 0.0 if not n else (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)


def spike(bars, w):
    """(z, ratio, turnover, window_pct, prior30) for the newest `w` sessions, or None."""
    if len(bars) < BASE + w + 1:
        return None
    vols = [b[2] for b in bars]
    closes = [b[1] for b in bars]

    window, base = vols[-w:], vols[-(BASE + w):-w]
    lw = [math.log1p(v) for v in window]
    lb = [math.log1p(v) for v in base]
    mean_b = sum(lb) / len(lb)
    var = sum((v - mean_b) ** 2 for v in lb) / (len(lb) - 1)
    sd = var ** 0.5
    if sd == 0:
        return None
    z = (sum(lw) / w - mean_b) / sd

    med_b = median(base)
    ratio = (sum(window) / w) / med_b if med_b else 0.0
    turnover = median([bars[i][1] * bars[i][2] for i in range(-(BASE + w), -w)])

    start = closes[-w - 1]
    window_pct = (closes[-1] / start - 1) * 100 if start else 0.0
    prior = closes[-w - 31] if len(closes) > w + 31 else closes[0]
    prior30 = (closes[-w - 1] / prior - 1) * 100 if prior else 0.0
    return z, ratio, turnover, window_pct, prior30


def main():
    names = (MASTER / "symbols.txt").read_text(encoding="utf-8").split()
    rows, skipped = [], 0

    per_window = {label: [] for label in WINDOWS}
    for name in names:
        safe = name.replace("/", "-")
        bars = daily(safe)
        if len(bars) < BASE + 21:
            skipped += 1
            continue
        for label, w in WINDOWS.items():
            s = spike(bars, w)
            if not s:
                continue
            z, ratio, turnover, window_pct, prior30 = s
            if turnover < FLOOR:          # debentures, closed-end funds, promoter lines
                continue
            flow = broker_window(safe, [b[0] for b in bars[-w:]])
            if not flow:
                continue
            per_window[label].append({
                "symbol": name, "date": bars[-1][0], "z": z, "ratio": ratio,
                "turnover": turnover, "window_pct": window_pct, "prior30": prior30, **flow,
            })

    # "kind" is judged against the market's own move that window, not against zero — in a
    # falling market every stock is red and an absolute cutoff would call everything markdown.
    for label, items in per_window.items():
        w = WINDOWS[label]
        mkt = median([i["window_pct"] for i in items]) if items else 0.0
        band = {3: 2.0, 10: 3.0, 20: 5.0}[w]
        for i in items:
            rel = i["window_pct"] - mkt
            kind = "churn" if abs(rel) <= band else ("markup" if rel > 0 else "markdown")
            flags = []
            if i["z"] >= Z_CUT[w]:
                flags.append("SPIKE")
            if i["net_churn"] <= CHURN_CUT[w]:
                flags.append("CHURN")
            if i["buyer_net"] > 0 and i["seller_net"] < 0:
                flags.append("BUY" if i["buyer_net"] >= -i["seller_net"] else "SELL")
            rows.append("\t".join(str(v) for v in (
                label, i["symbol"], i["date"], f"{i['z']:.2f}", f"{i['ratio']:.2f}",
                f"{i['turnover']:.0f}", f"{i['net_churn']:.3f}", f"{i['buy_hhi']:.3f}",
                i["top_buyer"], f"{i['buyer_net']:.0f}", i["top_seller"], f"{i['seller_net']:.0f}",
                f"{i['prior30']:.2f}", f"{i['window_pct']:.2f}", f"{rel:.2f}", kind,
                ",".join(flags) or "-",
            )))

    # spikes first, biggest first, so the head of each window block is what to look at
    order = {"3d": 0, "2w": 1, "1m": 2}
    rows.sort(key=lambda r: (order[r.split("\t")[0]], -float(r.split("\t")[3])))
    OUT.write_text(HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")

    print(f"{len(rows)} symbol-windows -> {OUT}   ({skipped} symbols too short)")
    for label in WINDOWS:
        block = [r for r in rows if r.startswith(label + "\t")]
        spikes = [r for r in block if "SPIKE" in r.split("\t")[16]]
        churn = [r for r in block if "CHURN" in r.split("\t")[16]]
        both = [r for r in block if "SPIKE" in r.split("\t")[16] and "CHURN" in r.split("\t")[16]]
        print(f"  {label:3} {len(block):>4} screened   SPIKE {len(spikes):>3}   "
              f"CHURN {len(churn):>3}   both {len(both):>3}")
        for r in block[:3]:
            f = r.split("\t")
            print(f"        {f[1]:10} z={f[3]:>5}  x{f[4]:<6} churn={f[6]}  "
                  f"buyer {f[8]} {f[9]:>9}  prior30 {f[12]:>7}%  {f[15]}")


if __name__ == "__main__":
    main()
