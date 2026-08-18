"""Does broker net flow predict forward returns? An out-of-sample test.

The claim being tested: some brokers' net buying systematically precedes price gains, so
their flow is a usable signal. The honest way to check is to pick the "smart" brokers on
an early slice of history and see whether they still work on a later slice they had no
part in selecting — in-sample winners appear by chance no matter what.

    python analyze_broker_flow.py

Reads Master_data/broker_flow/*.txt and the daily bars; writes a report to
Master_data/broker_flow_report.txt. Nothing here is trading advice — it is a measurement.
"""

from pathlib import Path

import numpy as np
import pandas as pd

from fetch_ohlc import MASTER

FLOW = MASTER / "broker_flow"
REPORT = MASTER / "broker_flow_report.txt"

HORIZONS = (5, 10)          # forward return windows, in sessions
MIN_TURNOVER = 500_000      # rupees; below this a symbol-day is noise
MIN_OBS = 300               # broker-days needed before a broker gets an opinion
TRAIN_FRACTION = 0.7        # first 70% of dates select the cohort, last 30% judge it
TOP_BROKERS = 20

out = []


def say(line=""):
    print(line, flush=True)
    out.append(str(line))


def load_prices():
    """{(symbol, date): forward return} for each horizon, from adjusted daily closes."""
    frames = []
    for kind in ("symbols", "indices"):
        for path in (MASTER / kind).glob("*/1D.txt"):
            rows = [l.split("\t") for l in path.read_text(encoding="utf-8").splitlines()[1:]]
            rows = [(r[0], float(r[4])) for r in rows if r[4] not in ("", "None")]
            if len(rows) < max(HORIZONS) + 2:
                continue
            df = pd.DataFrame(rows, columns=["date", "close"])
            df["symbol"] = path.parent.name
            for h in HORIZONS:
                df[f"fwd{h}"] = df["close"].shift(-h) / df["close"] - 1.0
            frames.append(df)
    return pd.concat(frames, ignore_index=True)


def load_flow():
    frames = []
    for path in sorted(FLOW.glob("*.txt")):
        df = pd.read_csv(path, sep="\t", dtype={"broker": str})  # broker codes are labels, not numbers
        df["symbol"] = path.stem
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def spearman(a, b):
    """Rank correlation without scipy. Drops pairs where either side is null —
    fwd10 is undefined for the last 10 sessions, and one nan poisons the whole corrcoef."""
    pair = pd.DataFrame({"a": np.asarray(a, dtype=float), "b": np.asarray(b, dtype=float)}).dropna()
    if len(pair) < 30:
        return np.nan
    ra, rb = pair.a.rank(), pair.b.rank()
    if ra.std() == 0 or rb.std() == 0:
        return np.nan
    return float(np.corrcoef(ra, rb)[0, 1])


say("loading daily bars ...")
prices = load_prices()
say(f"  {len(prices):,} symbol-days of prices")

say("loading broker flow ...")
flow = load_flow()
say(f"  {len(flow):,} broker-days across {flow.symbol.nunique()} symbols")

# a broker's footprint only means something relative to how much traded that session
session = flow.groupby(["symbol", "date"]).agg(
    day_qty=("bought", "sum"), day_amount=("buy_amount", "sum")
).reset_index()
flow = flow.merge(session, on=["symbol", "date"])
flow = flow[flow.day_amount >= MIN_TURNOVER]
flow["share"] = flow["net"] / flow["day_qty"].where(flow["day_qty"] > 0)

data = flow.merge(prices[["symbol", "date"] + [f"fwd{h}" for h in HORIZONS]], on=["symbol", "date"])
data = data.dropna(subset=["share", f"fwd{HORIZONS[0]}"])
say(f"  {len(data):,} usable broker-days after liquidity filter (>= Rs {MIN_TURNOVER:,})")

dates = np.sort(data.date.unique())
cut = dates[int(len(dates) * TRAIN_FRACTION)]
train, test = data[data.date < cut], data[data.date >= cut]
say(f"\ntrain: {train.date.min()} → {train.date.max()}  ({len(train):,} rows)")
say(f"test : {test.date.min()} → {test.date.max()}  ({len(test):,} rows)")

say("\n=== 1. baseline: does ANY broker's flow correlate with forward returns?")
for h in HORIZONS:
    say(f"  all brokers pooled, {h}-day IC : {spearman(data['share'], data[f'fwd{h}']):+.4f}")

say(f"\n=== 2. select the cohort on TRAIN only (min {MIN_OBS} broker-days)")
rows = []
for broker, g in train.groupby("broker"):
    if len(g) < MIN_OBS:
        continue
    rows.append({"broker": broker, "obs": len(g),
                 **{f"ic{h}": spearman(g["share"], g[f"fwd{h}"]) for h in HORIZONS}})
ranked = pd.DataFrame(rows).dropna().sort_values("ic5", ascending=False)
say(f"  {len(ranked)} brokers qualified")
say(f"  best train IC5 : {ranked.ic5.max():+.4f}   worst: {ranked.ic5.min():+.4f}")
say("\n  top 10 by train IC5:")
for _, r in ranked.head(10).iterrows():
    say(f"    broker {str(r.broker):>4}  obs={int(r.obs):>6,}  IC5={r.ic5:+.4f}  IC10={r.ic10:+.4f}")

say(f"\n=== 3. do those same brokers still work OUT OF SAMPLE?")
smart = set(ranked.head(TOP_BROKERS).broker)
dumb = set(ranked.tail(TOP_BROKERS).broker)
for label, cohort in (("top", smart), ("bottom", dumb)):
    sub = test[test.broker.isin(cohort)]
    line = f"  {label}-{TOP_BROKERS} cohort on test ({len(sub):,} rows):"
    for h in HORIZONS:
        line += f"  IC{h}={spearman(sub['share'], sub[f'fwd{h}']):+.4f}"
    say(line)

say("\n=== 4. tradeable form: rank symbols each day by smart-broker net share")
sig = (test[test.broker.isin(smart)]
       .groupby(["symbol", "date"])
       .agg(signal=("share", "sum"), fwd5=("fwd5", "first"), fwd10=("fwd10", "first"))
       .reset_index()
       .dropna())
say(f"  {len(sig):,} symbol-days carry a smart-broker signal")
if len(sig) > 200:
    sig["bucket"] = pd.qcut(sig["signal"].rank(method="first"), 5, labels=["Q1 sell", "Q2", "Q3", "Q4", "Q5 buy"])
    table = sig.groupby("bucket", observed=True)[["fwd5", "fwd10"]].mean() * 100
    say("\n  mean forward return by signal quintile (%, gross of costs):")
    for bucket, r in table.iterrows():
        say(f"    {bucket:9} 5d={r.fwd5:+.2f}%   10d={r.fwd10:+.2f}%")
    spread5 = table.fwd5.iloc[-1] - table.fwd5.iloc[0]
    spread10 = table.fwd10.iloc[-1] - table.fwd10.iloc[0]
    say(f"\n  top-minus-bottom quintile: 5d={spread5:+.2f}%   10d={spread10:+.2f}%")
    say("  round-trip cost in NEPSE is roughly 1%+ — the spread must clear that to be real.")

REPORT.write_text("\n".join(out) + "\n", encoding="utf-8")
say(f"\nreport written to {REPORT}")
