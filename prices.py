"""Bonus / rights adjusted price loader — the layer every calculation should read through.

NEPSE prices in the archive are RAW: on a bonus or rights ex-date the close resets, printing a
-20% to -80% gap that never traded. NEPSE's daily circuit is +/-10%, so any move beyond that is
arithmetic, and that is exactly how we detect one without needing a ratio feed.

Adjustment is applied ON READ, never written back into 1D.txt — `fetch_last_session.py` rewrites
those rows every day, so a stored adjustment would be wiped and, worse, re-applied on top of
itself. Raw stays raw; this module is the single place that splices it.

Back-adjustment: at an ex-date with factor f = close_after / close_before, every bar BEFORE the
ex-date is multiplied by f. Today's price is left untouched (so levels still match the live
market), and history becomes continuous, which is what indicators and stops need.

    python prices.py            # report what would be adjusted
"""
from fetch_ohlc import MASTER

CIRCUIT_DROP = -0.105        # beyond the -10% circuit (small tolerance) = corporate action
MIN_FACTOR = 0.2             # a >5x split is implausible in this market; treat as bad data


def raw_bars(symbol):
    """(dates, o, h, l, c, v) straight off disk, oldest first — unadjusted."""
    p = MASTER / "symbols" / symbol.replace("/", "-") / "1D.txt"
    if not p.exists():
        return None
    d, o, h, l, c, v = [], [], [], [], [], []
    for line in p.read_text(encoding="utf-8").splitlines()[1:]:
        f = line.split("\t")
        if len(f) > 7 and f[4] not in ("", "None") and f[7] not in ("", "None"):
            try:
                d.append(f[0]); o.append(float(f[1])); h.append(float(f[2]))
                l.append(float(f[3])); c.append(float(f[4])); v.append(float(f[7]))
            except ValueError:
                continue
    return (d, o, h, l, c, v) if c else None


def ex_dates(dates, closes):
    """[(index, factor)] for every bar whose drop is too big to be a real trade."""
    out = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if prev <= 0:
            continue
        f = closes[i] / prev
        if (f - 1) <= CIRCUIT_DROP and f >= MIN_FACTOR:
            out.append((i, f))
    return out


def bars(symbol, adjust=True):
    """Adjusted (dates, o, h, l, c, v). Prices before each ex-date are scaled so the series is
    continuous; volume is left alone (share counts change, but volume is not a price)."""
    b = raw_bars(symbol)
    if not b or not adjust:
        return b
    d, o, h, l, c, v = b
    evs = ex_dates(d, c)
    if not evs:
        return b
    o, h, l, c = o[:], h[:], l[:], c[:]
    for i, f in reversed(evs):                 # apply oldest-last so factors compound correctly
        for k in range(i):
            o[k] *= f; h[k] *= f; l[k] *= f; c[k] *= f
    return (d, o, h, l, c, v)


def main():
    names = (MASTER / "symbols.txt").read_text(encoding="utf-8").split()
    total, affected, biggest = 0, 0, []
    for s in names:
        b = raw_bars(s)
        if not b or len(b[4]) < 30:
            continue
        evs = ex_dates(b[0], b[4])
        if evs:
            affected += 1
            total += len(evs)
            for i, f in evs:
                biggest.append(((f - 1) * 100, s, b[0][i]))
    print(f"{affected} of {len(names)} symbols carry corporate-action gaps · {total} gaps total")
    biggest.sort()
    print("\nlargest splices (these were being read as -50%+ trading losses):")
    for pct, s, date in biggest[:10]:
        print(f"  {s:<10} {date}  {pct:>7.1f}%  ->  prior history scaled by {1 + pct/100:.3f}")
    print("\nAdjustment is applied on read via prices.bars(symbol); 1D.txt stays raw.")


if __name__ == "__main__":
    main()
