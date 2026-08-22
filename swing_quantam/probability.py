"""How often a target this far away is reached before a stop this far away.

This answers ONE question, and it is worth being exact about which, because the
obvious reading is not the one the data supports.

**It does not say the signal predicts anything.** The shipped backtest digest on
every symbol page reports that this engine's own buy zone is *significantly
negative* over 6,964 stock-days, that its score orders outcomes slightly
backwards (rank IC -0.018), and that 1 of 15 rule families clears the p<0.0033
bar 15 tests require — and that one is a price-momentum feature, not a floorsheet
one. Conditioning a probability on today's signal would therefore attach a number
to a claim that has been measured and rejected.

**What it does say** is a question about geometry and this stock's own volatility:
*given a target `t` percent above the entry and a stop `s` percent below it, how
often in this symbol's own history did price touch the target first, within 20
sessions?* That is a first-passage frequency. It is unconditional by design — it
scans every bar with a full forward window, not the bars the engine happened to
flag — so it is a BASE RATE, and a base rate is exactly what a reader needs in
order to judge a zone ladder that has no demonstrated edge.

Three outcomes, always reported together: target first, stop first, and neither
within the horizon. A "probability of going up" that omits the third is not a
probability; on a 20-session horizon "neither" is routinely the largest of the
three, and hiding it inflates both other numbers.

The barrier test uses each bar's HIGH and LOW, so it is a real touch rather than a
close-to-close approximation. Daily bars cannot order two touches inside the same
session, so a bar that reaches both is counted separately as ``ambiguous`` and
resolved to the STOP — the pessimistic reading, disclosed rather than buried.

Reads ``Master_data/symbols/<SYM>/1D.txt``, which exists for 350 of the board's
481 rows. The rest return None: no bar history is no answer, not a 50/50.
"""

from __future__ import annotations

import io
import os
from typing import NamedTuple, Sequence

from .loader import ROOT

BARS = os.path.join(ROOT, "Master_data", "symbols")

#: Sessions ahead the barrier test is allowed. Matches backtest.PRIMARY so this
#: number and the shipped edge study answer over the same window.
HORIZON = 20

#: Fewest historical windows that may back a printed probability. Below this the
#: figure moves several points per observation and reads as precision it lacks.
MIN_WINDOWS = 100

#: The SYMMETRIC barrier, as a fraction. Same distance up and down, identical for every
#: symbol — which is the only way a "most likely to rise" ranking means anything across
#: symbols. Each stock's own ladder has its own geometry (target 1 sits anywhere from
#: 0.3% to 50% away, the stop from 0.1% to 36%), so ranking symbols by their LADDER's
#: p_up ranks them by how near their target happens to be: the top of that list came back
#: with reward:risk 0.26-0.51, and the downside list with stops 0.26-0.67% from price,
#: which is arithmetic about the level, not a reading about the stock. Fix the distance
#: and the number becomes a property of the stock: how often it travels +5% before -5%.
SYMMETRIC = 0.05

#: Round-trip cost, percent. Taken from this repo's own backtest (Master_data/
#: backtest.txt `cost_pct`), so the gross number here and the shipped edge study
#: charge the same commission. A gross expectancy is not a result: the median
#: symbol on this board clears +0.35% over 20 sessions, which does not pay 0.8%.
COST_PCT = 0.8


class Bar(NamedTuple):
    date: str
    high: float
    low: float
    close: float


class Passage(NamedTuple):
    """First-passage frequencies for one (target, stop) geometry."""

    n: int  # windows with a full HORIZON ahead
    up: int  # touched +target% first
    down: int  # touched -stop% first
    neither: int  # neither barrier inside the horizon
    ambiguous: int  # one bar touched BOTH — counted in `down`, reported here
    p_up: float
    p_down: float
    p_neither: float
    #: Mean close-to-close return of the windows that touched NEITHER barrier, in
    #: percent. Measured, not assumed — see `expectancy`.
    open_return: float
    #: p_up*target% + p_down*(-stop%) + p_neither*open_return, in percent of entry.
    #:
    #: The third term is load-bearing and was missing in the first version of this
    #: file. Scoring an untouched window at 0 makes a WIDE stop look free: the stop
    #: is almost never hit, so p_down collapses toward 0 and the whole expression
    #: reduces to p_up * target, which cannot be negative. Measured on the shipped
    #: board that bug put 84.8% of symbols in positive expectancy on a market whose
    #: median 20-session return is negative. A position still open at the horizon
    #: has a real P&L; this uses its actual mean.
    expectancy: float

    @property
    def decided(self) -> int:
        return self.up + self.down


def bars(symbol: str) -> list[Bar]:
    """The daily archive, oldest first. Empty when the symbol has no bar file."""
    path = os.path.join(BARS, symbol.upper(), "1D.txt")
    if not os.path.exists(path):
        return []
    out: list[Bar] = []
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        next(fh, None)  # header
        for line in fh:
            p = line.rstrip("\n").split("\t")
            if len(p) < 5:
                continue
            try:
                h, lo, c = float(p[2]), float(p[3]), float(p[4])
            except ValueError:
                continue
            if h <= 0 or lo <= 0 or c <= 0:
                continue
            out.append(Bar(p[0], h, lo, c))
    return out


def first_passage(hist: Sequence[Bar], up_pct: float, down_pct: float,
                  horizon: int = HORIZON) -> Passage | None:
    """Scan every bar with a full forward window; return the three frequencies.

    ``up_pct`` and ``down_pct`` are POSITIVE fractions of the entry price (0.04 =
    4%). Returns None when the geometry is undefined — a target at or below the
    entry, or a stop at or above it, which is what an inverted ladder produces and
    is not a probability question at all.
    """
    if up_pct <= 0 or down_pct <= 0:
        return None
    n = len(hist) - horizon
    if n < MIN_WINDOWS:
        return None

    up = down = neither = ambig = 0
    open_rets: list[float] = []
    for i in range(n):
        entry = hist[i].close
        hi_bar = entry * (1.0 + up_pct)
        lo_bar = entry * (1.0 - down_pct)
        for b in hist[i + 1: i + 1 + horizon]:
            hit_up = b.high >= hi_bar
            hit_dn = b.low <= lo_bar
            if hit_up and hit_dn:
                # Both barriers inside one session. A daily bar records no order,
                # so this is genuinely unknown; resolve to the stop and count it.
                ambig += 1
                down += 1
                break
            if hit_up:
                up += 1
                break
            if hit_dn:
                down += 1
                break
        else:
            neither += 1
            # The position is still open at the horizon and has a real P&L. Record
            # it; scoring it 0 is what made a wide stop look free.
            open_rets.append((hist[i + horizon].close - entry) / entry * 100.0)

    p_up_, p_dn_, p_no_ = up / n, down / n, neither / n
    open_ret = (sum(open_rets) / len(open_rets)) if open_rets else 0.0
    return Passage(
        n=n, up=up, down=down, neither=neither, ambiguous=ambig,
        p_up=p_up_, p_down=p_dn_, p_neither=p_no_, open_return=open_ret,
        expectancy=p_up_ * up_pct * 100.0 - p_dn_ * down_pct * 100.0 + p_no_ * open_ret,
    )


class Odds(NamedTuple):
    """One symbol's ladder, priced by its own history."""

    symbol: str
    entry: float
    target1: float
    target2: float
    stop: float
    up1_pct: float  # target 1 distance, percent of entry
    up2_pct: float
    down_pct: float  # stop distance
    rr1: float  # reward:risk to target 1
    rr2: float
    t1: Passage
    t2: Passage | None
    #: The same stock against a SYMMETRIC +-SYMMETRIC barrier, so it can be compared with
    #: other stocks. None when the history is too short.
    sym: Passage | None


def odds(symbol: str, entry: float, target1: float, target2: float, stop: float,
         horizon: int = HORIZON) -> Odds | None:
    """Price one symbol's zone ladder against its own bar history.

    ``entry`` is the reference the distances are measured from — the zone midpoint
    where the board gives one, else the last close. Returns None when the symbol
    has no usable bar history or the ladder is inverted.
    """
    hist = bars(symbol)
    if not hist or entry <= 0:
        return None
    up1 = (target1 - entry) / entry
    dn = (entry - stop) / entry
    p1 = first_passage(hist, up1, dn, horizon)
    if p1 is None:
        return None
    up2 = (target2 - entry) / entry if target2 else 0.0
    p2 = first_passage(hist, up2, dn, horizon) if up2 > up1 else None
    return Odds(
        symbol=symbol.upper(), entry=entry, target1=target1, target2=target2,
        stop=stop, up1_pct=up1 * 100.0, up2_pct=up2 * 100.0, down_pct=dn * 100.0,
        rr1=up1 / dn if dn else 0.0, rr2=(up2 / dn) if (dn and up2 > 0) else 0.0,
        t1=p1, t2=p2,
        sym=first_passage(hist, SYMMETRIC, SYMMETRIC, horizon),
    )


# ---------------------------------------------------------------------------
# The board
# ---------------------------------------------------------------------------

COLUMNS = [
    "symbol", "signal", "price", "entry", "dist_to_entry", "target1", "target2",
    "stop", "up1_pct", "up2_pct", "down_pct", "rr1", "rr2",
    "p_up", "p_down", "p_none", "open_return", "expectancy", "net_edge",
    "p_up2", "sym_up", "sym_down", "sym_none", "windows", "date",
]

#: Rows of the detail file the ladder is read from. Cheaper than parsing the whole
#: 130 KB report, and each name is the metric exactly as sections 84-88 emit it.
_LADDER = ("entry low", "entry high", "profit 1 low", "profit 2 low", "invalidation high")


def _ladder(symbol: str) -> dict[str, float]:
    """The five zone edges for one symbol, straight from its detail file."""
    from . import store

    path = store.detail_path(symbol)
    if not os.path.exists(path):
        return {}
    want = set(_LADDER)
    out: dict[str, float] = {}
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            k, _, rest = line.partition("	")
            if k in want:
                try:
                    out[k] = float(rest.split("	")[0])
                except ValueError:
                    pass
                want.discard(k)
                if not want:
                    break
    return out


def scan(symbols: Sequence[str] | None = None,
         horizon: int = HORIZON) -> tuple[list[dict], str]:
    """One row per board symbol. Returns ``(rows, session)``.

    Reads the detail files for the ladder and the daily archive for the barrier
    test — no floorsheet pass, so this is seconds rather than the board's minutes
    and can be rebuilt on its own whenever the zones move.
    """
    from . import store

    # board.txt directly: store owns the writer, and a second reader here would be a
    # second definition of the format. It is a tab file with a header — nothing more.
    bpath = store.board_path()
    if not os.path.exists(bpath):
        return [], ""
    with io.open(bpath, encoding="utf-8", errors="replace") as fh:
        head = fh.readline().rstrip("\n").split("\t")
        board = [dict(zip(head, l.rstrip("\n").split("\t"))) for l in fh]

    rows: list[dict] = []
    session = ""
    for b in board:
        sym = str(b.get("symbol", ""))
        if not sym or (symbols and sym not in symbols):
            continue
        session = max(session, str(b.get("date", "")))
        lad = _ladder(sym)
        entry_lo, entry_hi = lad.get("entry low"), lad.get("entry high")
        t1, t2, st = lad.get("profit 1 low"), lad.get("profit 2 low"), lad.get("invalidation high")
        price = _num(b.get("vwap"))
        # The reference is the ZONE the ladder describes, not today's price: these
        # levels are a plan to enter there, and measuring the distances from
        # anywhere else prices a different plan. `dist_to_entry` says how far the
        # plan is from being live, so a reader can see when it is not.
        entry = ((entry_lo + entry_hi) / 2.0) if (entry_lo and entry_hi) else None
        row = {
            "symbol": sym, "signal": b.get("signal"), "price": price, "entry": entry,
            "dist_to_entry": ((price - entry) / entry * 100.0) if (entry and price) else None,
            "target1": t1, "target2": t2, "stop": st, "date": b.get("date"),
        }
        o = odds(sym, entry, t1 or 0.0, t2 or 0.0, st or 0.0, horizon) if (entry and t1 and st) else None
        if o:
            row.update({
                "up1_pct": o.up1_pct, "up2_pct": o.up2_pct or None, "down_pct": o.down_pct,
                "rr1": o.rr1, "rr2": o.rr2 or None,
                "p_up": o.t1.p_up, "p_down": o.t1.p_down, "p_none": o.t1.p_neither,
                "open_return": o.t1.open_return, "expectancy": o.t1.expectancy,
                "net_edge": o.t1.expectancy - COST_PCT,
                "p_up2": o.t2.p_up if o.t2 else None,
                # geometry-free, comparable across symbols — see SYMMETRIC
                "sym_up": o.sym.p_up if o.sym else None,
                "sym_down": o.sym.p_down if o.sym else None,
                "sym_none": o.sym.p_neither if o.sym else None,
                "windows": o.t1.n,
            })
        rows.append(row)
    # Sorted by NET EDGE, never by p_up. Measured on this board p_up correlates
    # -0.497 with reward:risk, so ranking on it is ranking by how near the target is:
    # a 0.9% target against a 4.3% stop is touched first 86% of the time and is not
    # thereby a good trade. Net edge prices the distances AND the commission.
    rows.sort(key=lambda r: (r.get("net_edge") is None, -(r.get("net_edge") or 0.0)))
    return rows, session


def _num(v: object) -> float | None:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def write(rows: list[dict], session: str) -> str:
    """``Master_data/swing_quantam/probability.txt``, date last like every board."""
    from . import store
    from .loader import OUT

    os.makedirs(OUT, exist_ok=True)
    lines = ["	".join(COLUMNS)]
    for r in rows:
        d = dict(r)
        d.setdefault("date", session)
        lines.append("	".join(store._esc(store._fmt(d.get(c))) for c in COLUMNS))
    path = os.path.join(OUT, "probability.txt")
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def _demo() -> None:
    import time

    t0 = time.time()
    hist = bars("NABIL")
    assert len(hist) > 1000, f"NABIL has only {len(hist)} bars"
    assert hist == sorted(hist, key=lambda b: b.date), "bars must be oldest-first"
    assert all(b.low <= b.high for b in hist), "a bar's low cannot exceed its high"

    # The three outcomes are a partition. If they ever stop summing to 1 the loop
    # has a path that neither breaks nor falls through to `else`.
    p = first_passage(hist, 0.05, 0.05)
    assert p and p.up + p.down + p.neither == p.n
    assert abs(p.p_up + p.p_down + p.p_neither - 1.0) < 1e-9
    assert p.ambiguous <= p.down, "an ambiguous bar is resolved to the stop"

    # A SYMMETRIC ladder on a market whose median 20-day return is negative must not
    # come back better than even. This is the guard against an optimism bug: any
    # sign error, or counting `neither` as an up, shows up here first.
    assert p.p_up <= p.p_down + 0.15, (
        f"a 5%/5% ladder scores p_up {p.p_up:.3f} vs p_down {p.p_down:.3f} — "
        "suspiciously bullish for this archive")

    # A stop so wide it is never reached must NOT read as free money. This is the
    # exact bug the open_return term exists for: with the third bucket scored at 0,
    # a 3% target against a 40% stop came back at +2.9% expectancy on a falling
    # market. The honest answer is bounded by the target.
    wide = first_passage(hist, 0.03, 0.40)
    assert wide and wide.p_down < 0.02, "pick a wider stop — this one is reachable"
    assert wide.expectancy < 0.03 * 100.0 * wide.p_up, (
        f"a never-touched stop yields {wide.expectancy:+.2f}% — the open position "
        "is being scored at zero again")

    # Monotonicity: a FARTHER target must be reached less often, and a wider stop
    # must be hit less often. Both are true of any correct barrier count.
    near, far = first_passage(hist, 0.03, 0.05), first_passage(hist, 0.12, 0.05)
    assert near and far and near.p_up > far.p_up
    tight, wide = first_passage(hist, 0.05, 0.03), first_passage(hist, 0.05, 0.12)
    assert tight and wide and tight.p_down > wide.p_down

    # Undefined geometry is None, never a number.
    assert first_passage(hist, 0.0, 0.05) is None
    assert first_passage(hist, 0.05, -0.01) is None
    assert first_passage(hist[:50], 0.05, 0.05) is None, "too few windows must refuse"
    assert bars("__NOT_A_SYMBOL__") == []
    assert odds("__NOT_A_SYMBOL__", 100, 110, 120, 90) is None

    o = odds("NABIL", 550.0, 570.0, 590.0, 520.0)
    assert o and o.rr1 > 0 and o.t2 and o.t2.p_up < o.t1.p_up

    # The symmetric reading must not depend on the ladder at all: it is a property of the
    # stock, and two different ladders on the same symbol must agree on it exactly. That is
    # the whole reason it exists, so it is pinned rather than assumed.
    other = odds("NABIL", 480.0, 500.0, 505.0, 470.0)
    assert other and other.sym and o.sym
    assert other.sym == o.sym, "the symmetric barrier moved with the ladder"
    assert abs(o.sym.p_up + o.sym.p_down + o.sym.p_neither - 1.0) < 1e-9

    print(f"probability ok — NABIL {len(hist)} bars, {HORIZON}-session barrier, "
          f"{time.time() - t0:.1f}s")
    print(f"  symmetric 5%/5%: up {p.p_up:.1%} / down {p.p_down:.1%} / "
          f"neither {p.p_neither:.1%} over {p.n:,} windows "
          f"({p.ambiguous} same-bar ties resolved to the stop)")
    print(f"  ladder 550 -> T1 570 (+{o.up1_pct:.1f}%) T2 590 (+{o.up2_pct:.1f}%) "
          f"stop 520 (-{o.down_pct:.1f}%), RR {o.rr1:.2f}")
    print(f"    T1 first {o.t1.p_up:.1%}, stop first {o.t1.p_down:.1%}, "
          f"neither {o.t1.p_neither:.1%} -> expectancy {o.t1.expectancy:+.2f}% of entry")
    print(f"    T2 first {o.t2.p_up:.1%}, stop first {o.t2.p_down:.1%}, "
          f"neither {o.t2.p_neither:.1%} -> expectancy {o.t2.expectancy:+.2f}%")
    print(f"    net of the {COST_PCT}% round trip this repo's backtest charges: "
          f"T1 {o.t1.expectancy - COST_PCT:+.2f}%, T2 {o.t2.expectancy - COST_PCT:+.2f}%")
    print(f"  symmetric +-{SYMMETRIC:.0%} (ladder-free, comparable across symbols): "
          f"up {o.sym.p_up:.1%} / down {o.sym.p_down:.1%} / neither {o.sym.p_neither:.1%}")


if __name__ == "__main__":
    _demo()
