"""One row per BROKER, market-wide — the input behind the console's "Strong Brokers" tab.

Everything else in this package is per-symbol: 118 sections about one stock. This
module answers the other question — which member firms are actually pushing money
into the market right now, across all 593 symbols at once.

Three things the repo has already measured shape what is and is not written here.

**A big net is usually just a big broker.** The top accumulator on a symbol is
net-positive on ~100% of symbol-days, so ranking brokers by raw net rupees returns
the biggest brokers in size order and calls it a finding. ``conviction``
(net / gross) is the size-free reading, and the board carries both plus the gross
so a reader can see which one they are looking at.

No size floor is applied to conviction, and that is a measured decision rather than
an omission. Section 67 needs one because a broker with a single 10-share fill scores
exactly ±1 there. Here the unit is a member firm's whole 30-session book: the thinnest
broker on this board still traded 11 symbols, the median traded 217, and mean
|conviction| is HIGHER among the above-median brokers (0.147) than below (0.069), with
the extreme (-0.807) held by a firm well above median gross. Filtering by size would
remove the strongest readings, not the noise. ``_demo`` pins that so it cannot quietly
reverse.

**There is no "share of market" column, either kind.** Share of market NET is 0/0:
every share bought is sold, so the market's net is exactly zero (``_demo`` asserts it).
Share of market GROSS is real but useless here — the denominator is one constant for
the whole file, so it is ``gross_amt`` rescaled, ranks brokers in identical order, and
is recoverable by summing the column beside it. A reader who wants it can divide.

**The window must be the market's calendar, not each symbol's.** A thin symbol's
last 30 traded sessions can reach back two years. The dates are taken from the whole
archive and intersected, so every symbol contributes the same 30 sessions or nothing.

Reads ``Master_data/broker_flow/<SYMBOL>.txt`` — the prebuilt per-symbol broker
aggregate, date-ascending — never the raw floorsheet. ~30 s for the universe.
Writes ``Master_data/swing_quantam/brokers.txt``.
"""

from __future__ import annotations

import io
import os
import time
from typing import NamedTuple

from .loader import OUT, ROOT

FLOW = os.path.join(ROOT, "Master_data", "broker_flow")

#: Sessions in the window. 30 to match the board's own decision window.
WINDOW = 30

#: How many lines of each file's tail the calendar pass reads. The calendar is the UNION
#: over every file's tail, so it does not need any single symbol to cover the window —
#: which is just as well, because a busy name prints 28-84 broker rows a session and 400
#: lines buys it only ~7 sessions. The thin instruments carry the far end: their 400 lines
#: reach back years. Measured margin on this archive: the last-30 window is still exact at
#: _TAIL=10 and first breaks at 5, so 400 is ~40x headroom.
_TAIL = 400

COLUMNS = [
    "broker", "net_amt", "gross_amt", "conviction", "net_qty", "gross_qty",
    "trades", "sessions", "symbols", "symbols_net_buy", "breadth",
    "top_symbol", "top_symbol_amt", "top_symbol_share", "date",
]


class BrokerRow(NamedTuple):
    """One member firm's market-wide 30-session footprint."""

    broker: str
    net_amt: float  # buy - sell, in rupees, summed over every symbol
    gross_amt: float  # buy + sell
    conviction: float  # net_amt / gross_amt, in [-1, 1] — the size-free reading
    net_qty: int
    gross_qty: int
    trades: int
    sessions: int  # of the window's sessions this broker traded on at all
    symbols: int
    symbols_net_buy: int
    breadth: float  # symbols_net_buy / symbols
    top_symbol: str  # where the most net money went (or came from)
    top_symbol_amt: float
    top_symbol_share: float  # |top| / sum |net per symbol| — 1.0 means a one-name book


def calendar(files: list[str] | None = None) -> list[str]:
    """The market's session dates, newest last, from the tail of every flow file."""
    out: set[str] = set()
    for f in files if files is not None else _files():
        with io.open(os.path.join(FLOW, f), encoding="utf-8") as fh:
            tail = fh.read().splitlines()[-_TAIL:]
        out.update(ln.split("\t", 1)[0] for ln in tail)
    out.discard("date")
    return sorted(out)


def _files() -> list[str]:
    """The per-symbol flow files, minus the instruments this engine does not analyse.

    Reads the directory rather than ``loader.symbols()`` (the flow archive and the
    floorsheet archive are not always the same set), so the exclusion has to be applied
    here too — otherwise a mutual fund's broker flow would still reach the market-wide
    totals and every broker's shares would be computed over a different universe from
    the one the symbol board shows.
    """
    from .loader import excluded

    skip = excluded()
    return sorted(f for f in os.listdir(FLOW)
                  if f.endswith(".txt") and f[:-4] not in skip)


def scan(window: int = WINDOW, upto: str | None = None) -> tuple[list[BrokerRow], str, int]:
    """Aggregate every broker over the market's last ``window`` sessions.

    ``upto`` truncates the calendar, so a point-in-time rebuild reads only sessions at
    or before that date. Without it a ``--upto 2024-06-30`` run wrote a brokers.txt from
    the whole archive and stamped it with today's session — a look-ahead board sitting
    beside a correctly-cut board.txt, which is the failure this repo keeps finding.

    Returns ``(rows, session, symbols_read)``. ``session`` is the newest date in the
    window and is what the board's mandatory ``date`` column carries.
    """
    files = _files()
    cal = calendar(files)
    if upto:
        cal = [d for d in cal if d <= upto]
    if not cal or window < 1:
        return [], "", 0
    win = set(cal[-window:])
    session = cal[-1]

    # broker -> [net_qty, gross_qty, net_amt, gross_amt, trades, {date}, {symbol: net_amt}]
    agg: dict[str, list] = {}
    for f in files:
        sym = f[:-4]
        with io.open(os.path.join(FLOW, f), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        # Walk backwards and stop at the first row older than the window. Rows are
        # date-ascending, so everything before that point is older too — reading the
        # whole 11.5M-row archive to keep 30 sessions is the slow way round.
        for ln in reversed(lines):
            p = ln.split("\t")
            if len(p) < 8 or p[0] == "date":
                continue
            if p[0] < min(win):
                break
            if p[0] not in win:
                continue
            b = agg.get(p[1])
            if b is None:
                b = agg[p[1]] = [0, 0, 0.0, 0.0, 0, set(), {}]
            bq, sq = int(p[2]), int(p[3])
            ba, sa = float(p[5]), float(p[6])
            b[0] += bq - sq
            b[1] += bq + sq
            b[2] += ba - sa
            b[3] += ba + sa
            b[4] += int(p[7])
            b[5].add(p[0])
            b[6][sym] = b[6].get(sym, 0.0) + (ba - sa)

    rows: list[BrokerRow] = []
    for broker, (nq, gq, na, ga, tr, days, bysym) in agg.items():
        top, top_amt = max(bysym.items(), key=lambda kv: abs(kv[1]), default=("", 0.0))
        spread = sum(abs(v) for v in bysym.values())
        rows.append(BrokerRow(
            broker=broker,
            net_amt=na,
            gross_amt=ga,
            conviction=na / ga if ga else 0.0,
            net_qty=nq,
            gross_qty=gq,
            trades=tr,
            sessions=len(days),
            symbols=len(bysym),
            symbols_net_buy=sum(1 for v in bysym.values() if v > 0),
            breadth=sum(1 for v in bysym.values() if v > 0) / len(bysym) if bysym else 0.0,
            top_symbol=top,
            top_symbol_amt=top_amt,
            top_symbol_share=abs(top_amt) / spread if spread else 0.0,
        ))
    rows.sort(key=lambda r: -r.net_amt)
    return rows, session, len(files)


def write(rows: list[BrokerRow], session: str) -> str:
    """``Master_data/swing_quantam/brokers.txt``, tab-separated with ``date`` last.

    Not ``store.write_board`` — that one owns ``board.txt`` by name and carries a
    shrink guard keyed to the symbol universe. This file is 93 rows, not 481, and a
    shrink here means a broker stopped trading, which is news rather than a bug.
    """
    os.makedirs(OUT, exist_ok=True)
    from . import store

    lines = ["\t".join(COLUMNS)]
    for r in rows:
        d = r._asdict()
        d["date"] = session
        lines.append("\t".join(store._esc(store._fmt(d[c])) for c in COLUMNS))
    path = os.path.join(OUT, "brokers.txt")
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def _demo() -> None:
    t0 = time.time()
    cal = calendar()
    assert len(cal) > WINDOW, f"only {len(cal)} session dates in the whole archive"
    # _TAIL only has to be big enough for the UNION to reach back WINDOW sessions, not for
    # any one file to. Pin the margin: a much smaller tail must still find the same window.
    assert calendar()[-WINDOW:] == cal[-WINDOW:]
    _tail_was = globals()["_TAIL"]
    try:
        globals()["_TAIL"] = 40
        assert calendar()[-WINDOW:] == cal[-WINDOW:], (
            "the calendar window depends on _TAIL — the union no longer has headroom")
    finally:
        globals()["_TAIL"] = _tail_was
    assert cal == sorted(cal) and len(set(cal)) == len(cal)

    rows, session, n = scan()
    assert rows, "no broker rows — the flow archive is empty or the calendar missed"
    assert session == cal[-1]

    # The conservation law, market-wide: every rupee bought is a rupee sold, so the
    # net across ALL brokers is 0. If this ever fails the window filter is leaking one
    # side of a trade, which is exactly the D01 dealer-row bug this repo has hit.
    net = sum(r.net_amt for r in rows)
    gross = sum(r.gross_amt for r in rows)
    assert abs(net) / gross < 1e-9, f"market net is {net:,.0f}, not 0 — one side is being dropped"

    for r in rows:
        assert -1.0 <= r.conviction <= 1.0
        assert 0.0 <= r.breadth <= 1.0 and 0.0 <= r.top_symbol_share <= 1.0
        assert r.symbols_net_buy <= r.symbols
        assert r.sessions <= WINDOW, f"broker {r.broker} traded on {r.sessions} of {WINDOW}"
        assert abs(r.net_qty) <= r.gross_qty

    # Why conviction is ranked WITHOUT a size floor here, unlike section 67. If this
    # reverses, the floor has to come back — so it is measured every run, not assumed.
    import statistics
    med_g = sorted(r.gross_amt for r in rows)[len(rows) // 2]
    small = [r for r in rows if r.gross_amt < med_g]
    big = [r for r in rows if r.gross_amt >= med_g]
    assert min(r.symbols for r in rows) >= 10, (
        "a broker traded fewer than 10 symbols in 30 sessions — conviction is a small-sample "
        "artefact for it and the ranking needs a floor again")
    assert (statistics.fmean(abs(r.conviction) for r in big)
            > statistics.fmean(abs(r.conviction) for r in small)), (
        "conviction is now driven by the SMALL brokers — reinstate the median-gross floor")

    print(f"broker board ok — {len(rows)} brokers over {WINDOW} sessions to {session}, "
          f"{n} symbols, {time.time() - t0:.1f}s")
    med = sorted(r.gross_amt for r in rows)[len(rows) // 2]
    big = [r for r in rows if r.gross_amt >= med]
    for r in sorted(big, key=lambda r: -r.conviction)[:5]:
        print(f"  broker {r.broker:>3}  conviction {r.conviction:+.3f}  "
              f"net Rs {r.net_amt / 1e6:>8,.1f}m of Rs {r.gross_amt / 1e6:>9,.1f}m gross  "
              f"{r.symbols_net_buy}/{r.symbols} symbols  top {r.top_symbol} "
              f"({r.top_symbol_share:.0%} of its book)")


if __name__ == "__main__":
    _demo()
