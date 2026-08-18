"""Fetch only the newest trading session and upsert it into the archive.

Every other fetcher re-walks history; this one touches a single date. For each store the
session's rows are **replaced if already present and appended if not**, so it is safe to
run repeatedly — during the session to refresh a partial day, or after the close for the
final one.

    python fetch_last_session.py              # newest session the feed offers
    python fetch_last_session.py 2026-08-17   # a specific date

Order matters: bars first (they name the session), then the floorsheet for that date, then
the minute candles and broker-flow rows that are built from it.
"""

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from build_broker_flow import HEADER as FLOW_HEADER
from fetch_ohlc import HEADER, MASTER, RECENT, folder, get

HERE = Path(__file__).parent
FLOORSHEET = MASTER / "floorsheet"
FLOW = MASTER / "broker_flow"


def recent(symbol):
    """{date: [open, high, low, close, volume, amount]} for roughly the last three months."""
    return {r["date"]: [r["open"], r["high"], r["low"], r["close"],
                        r.get("volume", ""), r.get("amount", "")]
            for r in get(RECENT.format(symbol))}


def upsert_daily(path, date, row):
    """Replace or append one dated row in a 1D.txt, re-deriving change against the prior close."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else [HEADER]
    head = lines[0] if lines else HEADER
    keep = sorted((ln for ln in lines[1:] if ln.strip() and ln.split("\t")[0] != date),
                  key=lambda ln: ln.split("\t")[0])

    prev = None
    for ln in keep:
        f = ln.split("\t")
        if f[0] >= date:
            break
        if len(f) > 4 and f[4] not in ("", "None"):
            prev = float(f[4])

    o, h, l, c, vol, amt = row
    change = pct = ""
    if prev is not None and c not in (None, ""):
        change = round(float(c) - prev, 2)
        pct = round(change / prev * 100, 2) if prev else ""

    keep.append("\t".join("" if v is None else str(v)
                          for v in (date, o, h, l, c, change, pct, vol, amt)))
    keep.sort(key=lambda ln: ln.split("\t")[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(head + "\n" + "\n".join(keep) + "\n", encoding="utf-8")


def upsert_flow(symbol, date):
    """Aggregate that one session's trades and swap them into the symbol's broker-flow file."""
    src = FLOORSHEET / symbol / f"{date}.txt"
    if not src.exists():
        return 0

    agg = {}
    for line in src.read_text(encoding="utf-8").splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        buyer, seller, qty, _rate, amount = parts[:5]
        q, a = float(qty), float(amount)
        b = agg.setdefault(buyer, [0.0, 0.0, 0.0, 0.0, 0])
        b[0] += q
        b[2] += a
        b[4] += 1
        s = agg.setdefault(seller, [0.0, 0.0, 0.0, 0.0, 0])
        s[1] += q
        s[3] += a
        s[4] += 1

    fresh = [f"{date}\t{broker}\t{bought:.0f}\t{sold:.0f}\t{bought - sold:.0f}"
             f"\t{buy_amt:.1f}\t{sell_amt:.1f}\t{trades}"
             for broker, (bought, sold, buy_amt, sell_amt, trades) in agg.items()]

    out = FLOW / f"{symbol}.txt"
    old = out.read_text(encoding="utf-8").splitlines()[1:] if out.exists() else []
    keep = [ln for ln in old if ln.strip() and ln.split("\t")[0] != date] + fresh
    keep.sort(key=lambda ln: (ln.split("\t")[0], -float(ln.split("\t")[4])))
    FLOW.mkdir(parents=True, exist_ok=True)
    out.write_text(FLOW_HEADER + "\n" + "\n".join(keep) + "\n", encoding="utf-8")
    return len(fresh)


def main():
    wanted = sys.argv[1] if len(sys.argv) > 1 else None
    names = {kind: (MASTER / f"{kind}.txt").read_text(encoding="utf-8").split()
             for kind in ("symbols", "indices")}

    print("reading the recent feed ...", flush=True)
    feeds, failed = {}, []
    with ThreadPoolExecutor(8) as pool:
        jobs = {(kind, name): pool.submit(recent, name)
                for kind, group in names.items() for name in group}
        for (kind, name), job in jobs.items():
            try:
                feeds[(kind, name)] = job.result()
            except (OSError, KeyError, TypeError, ValueError) as e:
                failed.append(f"{name}: {type(e).__name__}")

    session = wanted or max((max(rows, default="") for rows in feeds.values()), default="")
    if not session:
        print("no session found — the feed returned nothing")
        return 1
    print(f"session {session}  ({len(feeds)} instruments read, {len(failed)} unreadable)", flush=True)

    wrote = 0
    for (kind, name), rows in feeds.items():
        if session in rows:
            upsert_daily(folder(kind, name) / "1D.txt", session, rows[session])
            wrote += 1
    print(f"daily bars: {wrote} symbols carry {session}", flush=True)

    print("floorsheet ...", flush=True)
    p = subprocess.run([sys.executable, "fetch_floorsheet_merolagani.py", session, "--force"],
                       cwd=HERE, capture_output=True, text=True, timeout=7200)
    print((p.stdout or p.stderr).strip().splitlines()[-1] if (p.stdout or p.stderr) else "", flush=True)

    print("minute bars ...", flush=True)
    p = subprocess.run([sys.executable, "fetch_minutes_today.py", session],
                       cwd=HERE, capture_output=True, text=True, timeout=7200)
    out = (p.stdout or p.stderr).strip().splitlines()
    exact = sum(1 for ln in out if "exact minutes" in ln)
    print(f"minute bars: {exact} symbols rebuilt for {session}", flush=True)

    rows = symbols = 0
    for d in sorted(FLOORSHEET.iterdir()):
        if d.is_dir():
            n = upsert_flow(d.name, session)
            rows += n
            symbols += bool(n)
    print(f"broker flow: {rows:,} broker-days across {symbols} symbols for {session}")
    if failed:
        print(f"unreadable: {' '.join(failed[:10])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
