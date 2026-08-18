"""Round two: do richer floorsheet features predict returns, at longer horizons?

Round one tested a broker's raw net share of daily volume and found nothing that survives
costs. This tests the features that actually encode an accumulation *campaign* rather than
a single session, and pushes the horizon out — a campaign in an illiquid market plays out
over weeks, not days.

Features, all computed per symbol-day from Master_data/broker_flow:
  persist_k   net share held by brokers on a >=k-session net-buying streak
  streak_max  longest current net-buying streak among that day's buyers
  herfindahl  concentration of the buy side (1.0 = a single buyer took everything)
  top_share   largest single broker's net, as a share of the session's volume
  buyer_ratio unique sellers / unique buyers (few buyers absorbing many sellers > 1)

Protocol is the same discipline as round one: features are judged on a later slice of
history, and everything is reported — including the features that fail.

    python analyze_flow_features.py
"""

from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from fetch_ohlc import MASTER

FLOW = MASTER / "broker_flow"
REPORT = MASTER / "flow_features_report.txt"

HORIZONS = (5, 10, 20)      # out-of-sample testable given ~225 sessions of floorsheet
EXPLORATORY = (40, 60)      # reported on the full sample only — the test slice is too short
MIN_TURNOVER = 500_000
STREAK_K = 3
TRAIN_FRACTION = 0.7

out = []


def say(line=""):
    print(line, flush=True)
    out.append(str(line))


def forward_returns():
    frames = []
    for kind in ("symbols", "indices"):
        for path in (MASTER / kind).glob("*/1D.txt"):
            rows = [l.split("\t") for l in path.read_text(encoding="utf-8").splitlines()[1:]]
            rows = [(r[0], float(r[4])) for r in rows if r[4] not in ("", "None")]
            if len(rows) < 70:
                continue
            df = pd.DataFrame(rows, columns=["date", "close"])
            df["symbol"] = path.parent.name
            for h in HORIZONS + EXPLORATORY:
                df[f"fwd{h}"] = df["close"].shift(-h) / df["close"] - 1.0
            frames.append(df)
    return pd.concat(frames, ignore_index=True)


def features_for(path):
    """One row per session for a symbol, built strictly from that session and earlier."""
    df = pd.read_csv(path, sep="\t", dtype={"broker": str})
    if df.empty:
        return None
    symbol = path.stem
    rows, streak = [], defaultdict(int)

    for date, day in df.groupby("date", sort=True):
        volume = day["bought"].sum()
        turnover = day["buy_amount"].sum()
        if volume <= 0 or turnover < MIN_TURNOVER:
            # streaks still have to advance or they would leak across illiquid gaps
            for broker in day.loc[day.net <= 0, "broker"]:
                streak[broker] = 0
            continue

        buyers = day[day.net > 0]
        # persistence uses the streak as it stood BEFORE this session — no lookahead
        persist = buyers.loc[buyers.broker.map(lambda b: streak[b] >= STREAK_K), "net"].sum() / volume
        streak_max = max((streak[b] for b in buyers.broker), default=0)

        shares = day.loc[day.bought > 0, "bought"] / volume
        rows.append({
            "symbol": symbol, "date": date,
            "persist_k": persist,
            "streak_max": streak_max,
            "herfindahl": float((shares ** 2).sum()),
            "top_share": float(day.net.max() / volume),
            "buyer_ratio": float((day.sold > 0).sum() / max((day.bought > 0).sum(), 1)),
        })

        for broker, net in zip(day.broker, day.net):
            streak[broker] = streak[broker] + 1 if net > 0 else 0
    return pd.DataFrame(rows)


def spearman(a, b):
    pair = pd.DataFrame({"a": np.asarray(a, float), "b": np.asarray(b, float)}).dropna()
    if len(pair) < 50:
        return np.nan
    ra, rb = pair.a.rank(), pair.b.rank()
    if ra.std() == 0 or rb.std() == 0:
        return np.nan
    return float(np.corrcoef(ra, rb)[0, 1])


say("building per-symbol-day features ...")
frames = [f for f in (features_for(p) for p in sorted(FLOW.glob("*.txt"))) if f is not None and len(f)]
feat = pd.concat(frames, ignore_index=True)
say(f"  {len(feat):,} symbol-days across {feat.symbol.nunique()} symbols")

data = feat.merge(forward_returns(), on=["symbol", "date"]).dropna(subset=["fwd5"])
say(f"  {len(data):,} after joining forward returns")

COLS = ["persist_k", "streak_max", "herfindahl", "top_share", "buyer_ratio"]
dates = np.sort(data.date.unique())
cut = dates[int(len(dates) * TRAIN_FRACTION)]
train, test = data[data.date < cut], data[data.date >= cut]
say(f"\ntrain: {train.date.min()} → {train.date.max()} ({len(train):,})")
say(f"test : {test.date.min()} → {test.date.max()} ({len(test):,})")

say("\n=== information coefficient by feature (train | test)")
say(f"  {'feature':12} " + "  ".join(f"{'IC' + str(h):>16}" for h in HORIZONS))
for col in COLS:
    line = f"  {col:12} "
    for h in HORIZONS:
        line += f"  {spearman(train[col], train[f'fwd{h}']):+.4f} | {spearman(test[col], test[f'fwd{h}']):+.4f}"
    say(line)

say("\n=== exploratory long horizons (FULL sample — not out of sample, treat as a hint)")
say(f"  {'feature':12} " + "  ".join(f"{'IC' + str(h):>9}" for h in EXPLORATORY))
for col in COLS:
    say(f"  {col:12} " + "  ".join(f"   {spearman(data[col], data[f'fwd{h}']):+.4f}" for h in EXPLORATORY))

# best feature on test, by |IC| at the longest testable horizon
best = max(COLS, key=lambda c: abs(spearman(test[c], test[f"fwd{HORIZONS[-1]}"]) or 0))
say(f"\n=== quintiles for '{best}' on the test slice (%, gross of costs)")
sub = test.dropna(subset=[best, f"fwd{HORIZONS[-1]}"]).copy()
sub["bucket"] = pd.qcut(sub[best].rank(method="first"), 5, labels=["Q1", "Q2", "Q3", "Q4", "Q5"])
table = sub.groupby("bucket", observed=True)[[f"fwd{h}" for h in HORIZONS]].mean() * 100
for bucket, r in table.iterrows():
    say(f"  {bucket}  " + "  ".join(f"{h}d={r[f'fwd{h}']:+.2f}%" for h in HORIZONS))
for h in HORIZONS:
    say(f"  Q5-Q1 spread {h}d: {table[f'fwd{h}'].iloc[-1] - table[f'fwd{h}'].iloc[0]:+.2f}%")

say("\n=== regime split: does the signal behave differently in up vs down markets?")
nepse = [l.split("\t") for l in (MASTER / "indices" / "NEPSE" / "1D.txt").read_text(encoding="utf-8").splitlines()[1:]]
idx = pd.DataFrame([(r[0], float(r[4])) for r in nepse], columns=["date", "nepse"])
idx["ma50"] = idx.nepse.rolling(50).mean()
idx["up"] = idx.nepse > idx.ma50
merged = test.merge(idx[["date", "up"]], on="date", how="left")
for label, sub in (("index above 50d", merged[merged.up]), ("index below 50d", merged[~merged.up.astype(bool)])):
    if len(sub) < 100:
        continue
    say(f"  {label:16} ({len(sub):>6,} rows)  " +
        "  ".join(f"{c}:{spearman(sub[c], sub['fwd20']):+.4f}" for c in COLS))

REPORT.write_text("\n".join(out) + "\n", encoding="utf-8")
say(f"\nreport written to {REPORT}")
