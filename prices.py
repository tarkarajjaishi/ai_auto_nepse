"""Bonus / rights adjusted price loader — the layer every calculation should read through.

NEPSE prices in the archive are RAW: on a bonus or rights ex-date the close resets, printing a
-20% to -80% gap that never traded. We have no ratio feed, so the ex-date has to be detected
from the tape — and the thing that identifies one is not the SIZE of the fall but WHERE it
happened. An ex-date is arithmetic: the price is restated overnight, so the session already
OPENS at the adjusted level and nothing trades through the gap. A limit-down is the opposite —
it opens near the previous close and falls during the session.

Adjustment is applied ON READ, never written back into 1D.txt — `fetch_last_session.py` rewrites
those rows every day, so a stored adjustment would be wiped and, worse, re-applied on top of
itself. Raw stays raw; this module is the single place that splices it.

Back-adjustment: at an ex-date with factor f = close_after / close_before, every bar BEFORE the
ex-date is multiplied by f. Today's price is left untouched (so levels still match the live
market), and history becomes continuous, which is what indicators and stops need.

    python prices.py            # report what would be adjusted
"""
from fetch_ohlc import MASTER

CIRCUIT_DROP = -0.105        # a gap this big AT THE OPEN did not trade; it was restated
MIN_FACTOR = 0.2             # a >5x split is implausible in this market; treat as bad data
STALE_DAYS = 60              # a restatement is overnight; two trades months apart are not one


def _days_apart(dates, i):
    """Calendar days between bar i and the one before it. A big number means the instrument
    simply did not trade in between, so any step in its price is elapsed time, not an ex-date."""
    try:
        a, b = dates[i - 1], dates[i]
        ay, am, ad = int(a[:4]), int(a[5:7]), int(a[8:10])
        by, bm, bd = int(b[:4]), int(b[5:7]), int(b[8:10])
    except (ValueError, IndexError, TypeError):
        return 0                                  # unparseable date: do not veto on it
    return (by - ay) * 365 + (bm - am) * 30 + (bd - ad)


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


def ex_dates(dates, closes, opens):
    """[(index, factor)] for every bar whose fall was restated overnight rather than traded.

    The gate is on the OPEN. Testing the close instead conflated two different events, because
    NEPSE's circuit is no longer the +/-10% this module was written against — it is +/-15%, and
    the tape proves it: 150 sessions close at >= +14.5%, and in 2026 the largest up move in the
    entire market is exactly 15.00%. So every ordinary limit-down of -10.5% to -15% was being
    read as a corporate action and the symbol's whole prior history rescaled behind it.

    67 of the 139 events this used to flag were false positives, and they are not subtle: they
    OPEN at +5.00% and close at -15.00%, which is a session that traded, not a price that was
    restated. 48 symbols had their entire pre-event history multiplied by a fabricated factor —
    NIL by 0.34, CORBL by 0.49, ULHC by 0.62 — and every consumer of bars() saw the invention.

    BOTH conditions are required, and each rejects a different impostor. Without the open test,
    an ordinary limit-down looks like a restatement. Without the close test, a panic gap-down
    that gets bought back during the session looks like one too — the price opened low but the
    market disagreed and it did not stay there, so nothing was restated.

    A third condition rejects a third impostor: a restatement happens OVERNIGHT, so the previous
    bar has to be a recent session. Some instruments here trade a handful of times a year in
    single blocks — MDBPO printed 311, 379, 359, 318 with open=high=low=close on bars 4 to 12
    MONTHS apart — and the step between two such trades is the passage of time, not a corporate
    action. STALE_DAYS is deliberately generous rather than tight: H8020 and NIBLGF both sit 13
    days after their previous bar and both are REAL, corroborated by a book-closure date in
    corporate_actions.txt, because trading halts around a book close. Only gaps of months are
    rejected.

    The factor is still measured close-to-close, so the genuine adjustments are unchanged.
    Sanity-checked against the archive's own witness: of the 22 events inside the last 250 bars,
    16 match a published book close in the same month and several match it to the exact day.
    """
    out = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if prev <= 0:
            continue
        f = closes[i] / prev
        gap = opens[i] / prev - 1              # what was already gone before a share changed hands
        if gap <= CIRCUIT_DROP and (f - 1) <= CIRCUIT_DROP and f >= MIN_FACTOR \
                and _days_apart(dates, i) <= STALE_DAYS:
            out.append((i, f))
    return out


def bars(symbol, adjust=True):
    """Adjusted (dates, o, h, l, c, v). Prices before each ex-date are scaled so the series is
    continuous; volume is left alone (share counts change, but volume is not a price)."""
    b = raw_bars(symbol)
    if not b or not adjust:
        return b
    d, o, h, l, c, v = b
    evs = ex_dates(d, c, o)
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
        evs = ex_dates(b[0], b[4], b[1])
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
