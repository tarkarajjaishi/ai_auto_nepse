"""Participation, rotation, the broker-pair network and anomaly flags — spec 33-49.

This is the *relational* layer. :mod:`brokers` answers "how much did broker 58
buy"; this module answers "who was in the room, who left, who traded with whom,
and does today look like the last three months".

READ THIS BEFORE USING ANY OF SECTIONS 40-44 OR 48 AS A SIGNAL
--------------------------------------------------------------
Everything that follows is **descriptive and forensic**. The pair/network family
has already been tested in this codebase and killed as alpha. The findings are
not opinions, they are measurements, and the code below is shaped by them:

* **Pair "wash reciprocity"** — ``min(a->b, b->a)`` — is a trade-count proxy
  (r = +0.78) and the apparent spread is a date effect. :class:`Pair` therefore
  reports ``qty_share`` and ``trade_share`` side by side so the confound is
  visible in the same row, and ``reciprocal_qty`` is never returned on its own.
* **Counterparty breadth** (who the top accumulator buys from) is a turnover
  proxy; its sign inverts under a turnover double-sort. Degree is reported here
  as a network fact, never as an edge.
* **Coordinated / handoff / wash multi-broker patterns** added ~0 new symbols
  over the single-broker core. Brokers are not one-sided.

So: sections 40-44 and 48 produce *anomaly candidates*. Section 47 flags a
self-trade — which is routine broker business, a firm crossing two of its own
clients — and section 48 finds circular structures. Neither is proof of
improper activity or manipulation, and no function here says otherwise.

Section 49's :class:`Anomaly` always ships its :class:`Component` list. A bare
number is forbidden by the spec and there is no code path that produces one.

Cost discipline
---------------
Sections 37-39 are broker x stock across the whole market: 593 symbols x up to
1055 sessions. Nothing here loads the market implicitly. The three functions
that touch more than one symbol — :func:`broker_totals`, :func:`stock_rotation`
— take an **explicit symbol list** and a small window, and say in their
docstrings what they will read. Everything else takes an already-sliced
``list[Session]`` and is point-in-time by construction.

Degenerate metrics that were measured and cut are named in comments where they
would otherwise be written. See the note above :func:`participation` and the one
inside :func:`_metrics`.

Pure stdlib: this runs on a RAM-starved VPS beside the stdlib-only API.
"""

from __future__ import annotations

import statistics
import time
from collections import Counter, defaultdict
from typing import Iterable, NamedTuple

from . import brokers, loader
from .loader import WINDOWS, Session, Trade

__all__ = [
    "Participation", "participation",
    "Churn", "churn",
    "Rotation", "rotation",
    "RankStability", "rank_stability",
    "BrokerStock", "broker_stock",
    "Affinity", "AffinityShift", "broker_totals", "affinity", "affinity_shift",
    "StockRotation", "stock_rotation",
    "Pair", "PairShift", "pairs", "pair_shift", "pair_concentration",
    "Network", "network", "NetworkDrift", "centrality_drift",
    "Repeat", "repeats",
    "SequenceStats", "ordered_trades", "sequence",
    "SelfTrades", "self_trades",
    "Cycle", "cycles",
    "Component", "Anomaly", "anomaly",
]


# --------------------------------------------------------------------------
# shared primitives
# --------------------------------------------------------------------------

def _hhi(values: Iterable[float]) -> float:
    """Herfindahl index of a set of magnitudes. 1/n = perfectly even, 1 = one actor."""
    vals = [abs(v) for v in values if v]
    total = sum(vals)
    if not total:
        return 0.0
    return sum((v / total) ** 2 for v in vals)


def _rosters(sessions: Iterable[Session]) -> tuple[set[int], set[int]]:
    """(buyers, sellers) seen anywhere in these sessions."""
    buyers: set[int] = set()
    sellers: set[int] = set()
    for s in sessions:
        for t in s.trades:
            buyers.add(t.buyer)
            sellers.add(t.seller)
    return buyers, sellers


def _ranks(agg: dict[int, brokers.BrokerDay]) -> dict[int, int]:
    """Broker -> rank by gross quantity, 1 = biggest. Ties broken by broker id."""
    order = sorted(agg.values(), key=lambda b: (-b.gross_qty, b.broker))
    return {b.broker: i + 1 for i, b in enumerate(order)}


def _z(value: float, sample: list[float]) -> tuple[float, float]:
    """(z, baseline mean). z is 0 when the baseline is flat — never a divide by zero."""
    if len(sample) < 2:
        return 0.0, (sample[0] if sample else 0.0)
    mu = statistics.fmean(sample)
    sd = statistics.pstdev(sample)
    if sd <= 0:
        return 0.0, mu
    return (value - mu) / sd, mu


# --------------------------------------------------------------------------
# 33. BROKER PARTICIPATION
# --------------------------------------------------------------------------

class Participation(NamedTuple):
    """Section 33 — who was in the room, over one window.

    ``participation_pct`` is ``None`` unless the caller supplies a broker
    universe. Deriving the universe from the same sessions would pin it to
    1.000 on every stock and every day — the same conservation trap that killed
    stock-level buy/sell imbalance in :mod:`brokers` — so it is left unset
    rather than shipped as a column of ones.
    """

    sessions: int
    active: int  # unique participants across the window
    buyers: int
    sellers: int
    both_sides: int  # brokers that both bought and sold at some point
    per_session_mean: float
    per_session_min: int
    per_session_max: int
    breadth: float  # mean daily participants / window roster; 1.0 = the same faces daily
    participation_pct: float | None  # active / universe
    growth: float  # 2nd-half mean participants vs 1st-half, as a ratio - 1
    expansion: int  # brokers in the 2nd half that were absent from the 1st
    contraction: int  # brokers in the 1st half that were absent from the 2nd


def participation(sessions: list[Session], universe: int | None = None) -> Participation:
    """Section 33. ``universe`` is the number of brokers licensed/active market-wide."""
    n = len(sessions)
    if not n:
        return Participation(0, 0, 0, 0, 0, 0.0, 0, 0, 0.0, None, 0.0, 0, 0)

    buyers, sellers = _rosters(sessions)
    roster = buyers | sellers
    daily = [len({t.buyer for t in s.trades} | {t.seller for t in s.trades}) for s in sessions]

    half = max(1, n // 2)
    early, late = sessions[:half], sessions[half:] or sessions[-1:]
    eb, es = _rosters(early)
    lb, ls = _rosters(late)
    early_set, late_set = eb | es, lb | ls
    e_mean = statistics.fmean(daily[:half]) if daily[:half] else 0.0
    l_mean = statistics.fmean(daily[half:]) if daily[half:] else e_mean

    mean_daily = statistics.fmean(daily) if daily else 0.0
    return Participation(
        sessions=n,
        active=len(roster),
        buyers=len(buyers),
        sellers=len(sellers),
        both_sides=len(buyers & sellers),
        per_session_mean=mean_daily,
        per_session_min=min(daily) if daily else 0,
        per_session_max=max(daily) if daily else 0,
        breadth=mean_daily / len(roster) if roster else 0.0,
        participation_pct=(len(roster) / universe) if universe else None,
        growth=(l_mean / e_mean - 1.0) if e_mean else 0.0,
        expansion=len(late_set - early_set),
        contraction=len(early_set - late_set),
    )


# --------------------------------------------------------------------------
# 34. NEW / RETURNING / EXITING BROKERS
# --------------------------------------------------------------------------

class Churn(NamedTuple):
    """Section 34 — roster accounting between two adjacent windows.

    The accounting closes exactly, and :func:`_demo` asserts it::

        curr_active == new + returning + continuing
        prev_active == continuing + exiting

    "New" means never seen, which needs ``history``. Without history everything
    unseen in ``prev`` is counted as new and ``returning`` is 0 — honest, but
    weaker, so pass history when you have it.
    """

    prev_active: int
    curr_active: int
    new: int  # in curr, in neither prev nor history
    returning: int  # in curr, absent from prev, present in history
    continuing: int
    exiting: int
    new_pct: float
    lost_pct: float
    expansion: int  # inflow: new + returning
    net_change: int  # expansion - exiting
    acceleration: float | None  # 2nd difference of roster size; needs history


def churn(prev: list[Session], curr: list[Session], history: list[Session] | None = None) -> Churn:
    """Section 34. ``history`` is everything older than ``prev`` you want counted."""
    pb, ps = _rosters(prev)
    cb, cs = _rosters(curr)
    prev_set, curr_set = pb | ps, cb | cs
    hist_set: set[int] = set()
    if history:
        hb, hs = _rosters(history)
        hist_set = hb | hs

    continuing = curr_set & prev_set
    fresh = curr_set - prev_set
    returning = fresh & hist_set
    new = fresh - hist_set
    exiting = prev_set - curr_set

    accel = None
    if history:
        accel = float((len(curr_set) - len(prev_set)) - (len(prev_set) - len(hist_set)))

    return Churn(
        prev_active=len(prev_set),
        curr_active=len(curr_set),
        new=len(new),
        returning=len(returning),
        continuing=len(continuing),
        exiting=len(exiting),
        new_pct=len(new) / len(curr_set) if curr_set else 0.0,
        lost_pct=len(exiting) / len(prev_set) if prev_set else 0.0,
        expansion=len(fresh),
        net_change=len(fresh) - len(exiting),
        acceleration=accel,
    )


# --------------------------------------------------------------------------
# 35. BROKER ROTATION
# --------------------------------------------------------------------------

class Rotation(NamedTuple):
    """Section 35 — how much the cast changed, and how much of the tape they carry.

    ``rotation`` is the Jaccard distance of the two rosters, so it is a fraction
    of the *cast*. ``intensity`` is the fraction of the *volume* the new faces
    actually traded — a roster can churn heavily at the bottom and mean nothing,
    and those two numbers are what tells the difference.
    """

    rotation: float  # |symmetric difference| / |union|
    # No `velocity`. It was rotation / len(curr), and len(curr) is the window — 30 on every
    # symbol — so the column was the row above it divided by a constant: a rescale, not a
    # second measurement. A real velocity needs a varying denominator (e.g. ses[-10:-5] vs
    # ses[-5:] against the 30D roster), which nothing asks for yet.
    new_active: int
    disappearing: int
    increasing: int  # continuing brokers whose share of gross volume rose
    decreasing: int
    intensity: float  # share of current volume traded by brokers absent from prev
    rank_changes: int  # continuing brokers whose rank moved at all
    mean_abs_rank_change: float
    dominance_change: float  # curr top-1 gross share - prev top-1 gross share
    top_broker_changed: bool


def rotation(prev: list[Session], curr: list[Session]) -> Rotation:
    """Section 35. Both arguments are already-sliced windows, oldest-first."""
    pa, ca = brokers.window(prev), brokers.window(curr)
    prev_set, curr_set = set(pa), set(ca)
    union = prev_set | curr_set
    if not union:
        return Rotation(0.0, 0, 0, 0, 0, 0.0, 0, 0.0, 0.0, False)

    fresh = curr_set - prev_set
    gone = prev_set - curr_set
    pg = sum(b.gross_qty for b in pa.values()) or 1
    cg = sum(b.gross_qty for b in ca.values()) or 1

    inc = dec = 0
    for b in curr_set & prev_set:
        if ca[b].gross_qty / cg > pa[b].gross_qty / pg:
            inc += 1
        elif ca[b].gross_qty / cg < pa[b].gross_qty / pg:
            dec += 1

    pr, cr = _ranks(pa), _ranks(ca)
    moves = [abs(cr[b] - pr[b]) for b in curr_set & prev_set]

    p_top = max(pa.values(), key=lambda b: b.gross_qty, default=None)
    c_top = max(ca.values(), key=lambda b: b.gross_qty, default=None)

    return Rotation(
        rotation=len(fresh | gone) / len(union),
        new_active=len(fresh),
        disappearing=len(gone),
        increasing=inc,
        decreasing=dec,
        intensity=sum(ca[b].gross_qty for b in fresh) / cg,
        rank_changes=sum(1 for m in moves if m),
        mean_abs_rank_change=statistics.fmean(moves) if moves else 0.0,
        dominance_change=(c_top.gross_qty / cg if c_top else 0.0) - (p_top.gross_qty / pg if p_top else 0.0),
        top_broker_changed=bool(p_top and c_top and p_top.broker != c_top.broker),
    )


# --------------------------------------------------------------------------
# 36. BROKER RANK STABILITY
# --------------------------------------------------------------------------

class RankStability(NamedTuple):
    """Section 36 — a stable #1 is a different animal from a rotating #1.

    That distinction is the whole point of the section, so it is measured three
    ways rather than one: ``top1_persistence`` (how often the modal leader
    actually led), ``top1_changes`` (how often the leader flipped between
    consecutive sessions), and ``rank_volatility`` (how much the regulars move
    around underneath). A stock can score high on the first and still be
    churning below the surface.

    Rank is by gross quantity, 1 = largest. Brokers absent from a session are
    unranked for that session rather than parked at the bottom — padding absent
    brokers to rank N+1 would manufacture volatility out of non-participation.
    """

    sessions: int
    ranked: int  # brokers ranked at least once
    top1: int | None  # modal #1
    top1_persistence: float  # sessions the modal #1 held #1 / sessions
    top1_changes: int  # consecutive-session leader flips
    top5_persistence: float  # mean |A n B| / top over consecutive sessions
    rank_volatility: float  # mean per-broker stdev of rank, regulars only
    mean_abs_rank_change: float  # mean |rank(t) - rank(t-1)| for regulars
    dominant: int | None  # broker in the top-N on the most sessions
    dominant_persistence: float
    mean_rank: dict[int, float]


def rank_stability(sessions: list[Session], top: int = 5) -> RankStability:
    """Section 36. ``top`` sets the top-N set used for top-5/dominant persistence."""
    n = len(sessions)
    if not n:
        return RankStability(0, 0, None, 0.0, 0, 0.0, 0.0, 0.0, None, 0.0, {})

    per_day = [_ranks(brokers.day(s)) for s in sessions]
    leaders = [min(r, key=r.get) if r else None for r in per_day]
    top_sets = [{b for b, rk in r.items() if rk <= top} for r in per_day]

    seen: dict[int, list[int]] = defaultdict(list)
    for r in per_day:
        for b, rk in r.items():
            seen[b].append(rk)

    # "Regulars" = present on at least half the sessions. Rank volatility over a
    # broker that showed up twice is noise, not a property of the stock.
    regulars = {b: rks for b, rks in seen.items() if len(rks) >= max(2, n // 2)}
    vols = [statistics.pstdev(rks) for rks in regulars.values() if len(rks) > 1]

    moves: list[int] = []
    for a, b in zip(per_day, per_day[1:]):
        for br in set(a) & set(b) & set(regulars):
            moves.append(abs(b[br] - a[br]))

    lead_counts = Counter(x for x in leaders if x is not None)
    modal, modal_n = (lead_counts.most_common(1) or [(None, 0)])[0]
    flips = sum(1 for a, b in zip(leaders, leaders[1:]) if a is not None and b is not None and a != b)

    overlaps = [len(a & b) / top for a, b in zip(top_sets, top_sets[1:]) if a and b]
    top_counts = Counter(b for st in top_sets for b in st)
    dom, dom_n = (top_counts.most_common(1) or [(None, 0)])[0]

    return RankStability(
        sessions=n,
        ranked=len(seen),
        top1=modal,
        top1_persistence=modal_n / n,
        top1_changes=flips,
        top5_persistence=statistics.fmean(overlaps) if overlaps else 0.0,
        rank_volatility=statistics.fmean(vols) if vols else 0.0,
        mean_abs_rank_change=statistics.fmean(moves) if moves else 0.0,
        dominant=dom,
        dominant_persistence=dom_n / n,
        mean_rank={b: statistics.fmean(rks) for b, rks in seen.items()},
    )


# --------------------------------------------------------------------------
# 37. BROKER x STOCK MATRIX
# --------------------------------------------------------------------------

class BrokerStock(NamedTuple):
    """Section 37 — one cell of the broker x stock matrix, for one window.

    The matrix itself is the caller's loop over symbols; this builds one column
    from an already-sliced window so nothing loads the market by accident.
    """

    broker: int
    symbol: str
    buy_qty: int
    sell_qty: int
    net_qty: int
    buy_amt: float
    sell_amt: float
    net_amt: float
    buy_trades: int
    sell_trades: int
    trades: int
    # None when the broker never traded that side — 0.0 is a PRICE and read as
    # "bought at zero rupees". See brokers.BrokerDay.buy_vwap.
    buy_vwap: float | None
    sell_vwap: float | None
    largest_buy: int
    largest_sell: int
    volume_pct: float  # gross qty / 2*stock volume — every share has two sides
    turnover_pct: float
    accumulation: int  # sessions net-positive
    distribution: int  # sessions net-negative
    cum_net: int  # net flow over ``hist_net``'s span; == net_qty when none is supplied
    persistence: float  # sessions active / sessions in window
    consistency: float  # |acc - dist| / (acc + dist); 1 = one-directional
    affinity: float | None  # section 38; None unless market totals supplied


def broker_stock(
    sessions: list[Session],
    totals: dict[int, int] | None = None,
    hist_net: dict[int, int] | None = None,
) -> dict[int, BrokerStock]:
    """Section 37 for one symbol. ``totals`` from :func:`broker_totals` fills affinity.

    ``hist_net`` is per-broker net quantity over a span LONGER than ``sessions`` — the
    spec's "historical cumulative flow", which is a separate item from "net quantity".
    Without it ``cum_net`` degenerates to ``net_qty``: the cumulative net over a window
    IS that window's net, so the two rows carried one number on 2,405/2,405 shipped
    cells and the sign was the WINDOW's, not history's (ADBL broker 65: -48,183 over
    30 sessions, +214,038 over 120). Callers that have the longer span must pass it;
    ``__main__`` gets it free off ``flow.all_series``, which already scans it."""
    if not sessions:
        return {}
    symbol = sessions[0].symbol
    agg = brokers.window(sessions)
    flow = brokers.stock_flow(agg)

    acc: Counter[int] = Counter()
    dist: Counter[int] = Counter()
    active: Counter[int] = Counter()
    for s in sessions:
        for b, bd in brokers.day(s).items():
            active[b] += 1
            if bd.net_qty > 0:
                acc[b] += 1
            elif bd.net_qty < 0:
                dist[b] += 1

    n = len(sessions)
    out: dict[int, BrokerStock] = {}
    for b, bd in agg.items():
        a, d = acc[b], dist[b]
        directional = a + d
        out[b] = BrokerStock(
            broker=b,
            symbol=symbol,
            buy_qty=bd.buy_qty,
            sell_qty=bd.sell_qty,
            net_qty=bd.net_qty,
            buy_amt=bd.buy_amt,
            sell_amt=bd.sell_amt,
            net_amt=bd.net_amt,
            buy_trades=bd.buy_trades,
            sell_trades=bd.sell_trades,
            trades=bd.trades,
            buy_vwap=bd.buy_vwap,
            sell_vwap=bd.sell_vwap,
            largest_buy=bd.buy_max,
            largest_sell=bd.sell_max,
            volume_pct=bd.gross_qty / (2 * flow.volume) if flow.volume else 0.0,
            turnover_pct=bd.gross_amt / (2 * flow.turnover) if flow.turnover else 0.0,
            accumulation=a,
            distribution=d,
            cum_net=hist_net.get(b, bd.net_qty) if hist_net else bd.net_qty,
            persistence=active[b] / n,
            consistency=abs(a - d) / directional if directional else 0.0,
            affinity=(bd.gross_qty / totals[b]) if totals and totals.get(b) else None,
        )
    return out


# --------------------------------------------------------------------------
# 38. BROKER-STOCK AFFINITY  (market-wide — opt-in, cost documented)
# --------------------------------------------------------------------------

class Affinity(NamedTuple):
    """Section 38 — how much of a broker's own activity lands in this stock.

    NOT capital allocation. Client-level holdings do not exist in this data, so
    a high affinity says the broker's *executions* concentrate here, nothing
    about who owns the shares or what anyone allocated.
    """

    broker: int
    symbol: str
    affinity: float  # broker gross qty in this symbol / broker gross qty everywhere
    rank: int  # this symbol's position in the broker's own book, 1 = biggest
    percentile: float  # affinity percentile among brokers active in this symbol


class AffinityShift(NamedTuple):
    broker: int
    symbol: str
    prev: float
    curr: float
    change: float
    status: str  # new | lost | up | down | flat


def broker_totals(
    symbols: list[str], n: int = 7, upto: str | None = None
) -> tuple[dict[int, int], dict[int, dict[str, int]]]:
    """Market-wide gross quantity per broker, and per broker-stock.

    **COST — read before calling.** This reads ``n`` floorsheet sessions for
    EVERY symbol in ``symbols``. On this archive one session parses in roughly
    4 ms, so ``len(symbols) * n * 4 ms``: 20 symbols x 7 sessions is under a
    second, the full 593-symbol universe x 30 sessions is well over a minute and
    is not something to put behind a page load. ``symbols`` is required and the
    default ``n`` is deliberately small — there is no "whole market" default and
    there should not be one.

    Returns ``(total_gross_per_broker, {broker: {symbol: gross}})``.
    """
    total: Counter[int] = Counter()
    book: dict[int, Counter[str]] = defaultdict(Counter)
    for sym in symbols:
        ses = loader.load_last(sym, n, upto)
        if not ses:
            continue
        for b, bd in brokers.window(ses).items():
            total[b] += bd.gross_qty
            book[b][sym] += bd.gross_qty
    return dict(total), {b: dict(c) for b, c in book.items()}


class MarketBook(NamedTuple):
    """One market-wide scan, sliced per window — the input sections 38 and 39 need.

    WHY THIS EXISTS. Both sections were implemented, demo-tested, and then shipped as
    ``computed: no`` on every symbol, because each needed its own full-market read:
    :func:`broker_totals` scans every symbol once, and :func:`stock_rotation` scans them
    again FOR EVERY BROKER. Called per symbol that is 593 scans; called per broker it is
    worse. So neither was ever called and two sections of the specification shipped empty.

    The fix is the pattern the market pass already uses: read once per BUILD, slice in
    memory, thread the result in. Each symbol's sessions are loaded a single time at the
    longest window and every shorter window is a slice of that same list, so the cost is
    one scan whatever ``windows`` holds — not one per window and not one per broker.
    """

    date: str | None  # `upto` as given, for the record
    windows: tuple[int, ...]
    symbols: int  # symbols that actually had sessions
    seconds: float
    #: window -> broker -> market-wide gross qty in that window
    total: dict[int, dict[int, int]]
    #: window -> broker -> symbol -> gross qty
    book: dict[int, dict[int, dict[str, int]]]

    #: symbol -> that symbol's Affinity rows at the LONGEST window, best first. Section 38
    #: wants one symbol; :func:`affinity` returns the whole market keyed by broker. Building
    #: it per symbol meant recomputing 93 brokers x ~500 names 593 times per build and cost
    #: more than the scan that produced the book.
    by_symbol: dict[str, tuple[Affinity, ...]]

    def totals(self, window: int) -> dict[int, int]:
        return self.total.get(window, {})

    def books(self, window: int) -> dict[int, dict[str, int]]:
        return self.book.get(window, {})

    def affinities(self, symbol: str) -> tuple[Affinity, ...]:
        return self.by_symbol.get(symbol, ())


def market_book(
    symbols: list[str], windows: tuple[int, ...] = WINDOWS, upto: str | None = None
) -> MarketBook:
    """ONE market-wide scan covering every window. Sections 38 and 39 read this.

    **COST.** ``max(windows)`` sessions for every symbol, once. On this archive a session
    parses in roughly 4 ms, so the 593-symbol universe at 30 sessions is a minute or two —
    the same order as the market pass, and like it this belongs in :func:`main` once per
    build, never behind a page load or inside a per-symbol call.
    """
    span = max(windows)
    total: dict[int, Counter[int]] = {w: Counter() for w in windows}
    book: dict[int, dict[int, Counter[str]]] = {w: defaultdict(Counter) for w in windows}
    seen = 0
    started = time.time()
    for sym in symbols:
        ses = loader.load_last(sym, span, upto)
        if not ses:
            continue
        seen += 1
        # ONE aggregation per session, added to every window that contains it — not one
        # aggregation per window. The windows nest (3 in 7 in 15 in 30), so the obvious
        # `for w: brokers.window(ses[-w:])` re-aggregates the newest three sessions four
        # times over and costs 3+7+15+30 = 55 session-aggregations where 30 will do.
        # Measured on 40 symbols: 1246 ms/symbol the naive way, and the whole 593-symbol
        # universe is the difference between ~12 and ~7 minutes of a once-per-build pass.
        for i, sess in enumerate(reversed(ses)):
            wins = [w for w in windows if i < w]
            if not wins:
                break
            for b, bd in brokers.window([sess]).items():
                g = bd.gross_qty
                if not g:
                    continue
                for w in wins:
                    total[w][b] += g
                    book[w][b][sym] += g
    tot = {w: dict(c) for w, c in total.items()}
    bk = {w: {b: dict(c) for b, c in d.items()} for w, d in book.items()}

    # ONE affinity pass over the longest window, inverted to symbol -> rows. Section 38
    # asks 593 times for one symbol's slice of a market-wide table; computing that table
    # per call made the section cost more than the scan above it.
    span_w = max(windows)
    by_sym: dict[str, list[Affinity]] = defaultdict(list)
    for rows in affinity(tot.get(span_w, {}), bk.get(span_w, {})).values():
        for a in rows:
            by_sym[a.symbol].append(a)
    return MarketBook(
        upto, tuple(windows), seen, time.time() - started, tot, bk,
        {s: tuple(sorted(r, key=lambda a: -a.affinity)) for s, r in by_sym.items()},
    )


def rotation_from_book(broker: int, mb: MarketBook, top: int = 5) -> StockRotation:
    """Section 39 for one broker with NO I/O — the arithmetic half of :func:`stock_rotation`.

    Same result as that function, computed from a :class:`MarketBook` that was already
    read. That is what makes the section affordable: the scan happens once per build
    rather than once per broker.
    """
    windows = mb.windows
    focus: dict[int, str | None] = {}
    shares: dict[int, float] = {}
    books: dict[int, dict[str, float]] = {}
    for w in windows:
        syms = mb.books(w).get(broker, {})
        tot = sum(syms.values())
        books[w] = {s: q / tot for s, q in syms.items()} if tot else {}
        if syms:
            sym, q = max(syms.items(), key=lambda kv: kv[1])
            focus[w], shares[w] = sym, q / tot
        else:
            focus[w], shares[w] = None, 0.0

    short, long = min(windows), max(windows)
    s_book, l_book = books[short], books[long]
    s_top = {s for s, _ in sorted(s_book.items(), key=lambda kv: -kv[1])[:top]}
    l_top = {s for s, _ in sorted(l_book.items(), key=lambda kv: -kv[1])[:top]}
    return StockRotation(
        broker=broker,
        focus=focus,
        shares=shares,
        rotated=bool(focus[short] and focus[long] and focus[short] != focus[long]),
        new_focus=tuple(sorted(s_top - l_top)),
        abandoned=tuple(sorted(l_top - s_top)),
        increasing=tuple(sorted(s for s in s_book if s_book[s] > l_book.get(s, 0.0) + 1e-9)),
        decreasing=tuple(sorted(s for s in l_book if l_book[s] > s_book.get(s, 0.0) + 1e-9)),
    )


def affinity(
    total: dict[int, int], book: dict[int, dict[str, int]], min_affinity: float = 0.0
) -> dict[int, list[Affinity]]:
    """Section 38 from an already-loaded :func:`broker_totals` result. No I/O.

    The expensive part stays in one clearly-labelled function; this is pure
    arithmetic over what it returned, so it is cheap to re-slice.
    """
    raw: dict[int, list[tuple[str, float, int]]] = {}
    for b, syms in book.items():
        tot = total.get(b, 0)
        if not tot:
            continue
        rows = sorted(((s, q / tot) for s, q in syms.items()), key=lambda x: -x[1])
        raw[b] = [(s, a, i + 1) for i, (s, a) in enumerate(rows)]

    # Percentile is across the brokers active in the SAME symbol — an affinity of
    # 0.4 means different things in a name three brokers touch and one 60 do.
    by_symbol: dict[str, list[float]] = defaultdict(list)
    for rows in raw.values():
        for s, a, _ in rows:
            by_symbol[s].append(a)
    for s in by_symbol:
        by_symbol[s].sort()

    out: dict[int, list[Affinity]] = {}
    for b, rows in raw.items():
        keep = []
        for s, a, rk in rows:
            if a < min_affinity:
                continue
            peers = by_symbol[s]
            below = sum(1 for x in peers if x < a)
            keep.append(Affinity(b, s, a, rk, below / len(peers) if peers else 0.0))
        if keep:
            out[b] = keep
    return out


def affinity_shift(
    prev: dict[int, list[Affinity]], curr: dict[int, list[Affinity]], min_affinity: float = 0.05
) -> list[AffinityShift]:
    """Section 38 — affinity change, new affinity, lost affinity. No I/O."""
    out: list[AffinityShift] = []
    for b in set(prev) | set(curr):
        p = {a.symbol: a.affinity for a in prev.get(b, ())}
        c = {a.symbol: a.affinity for a in curr.get(b, ())}
        for s in set(p) | set(c):
            pv, cv = p.get(s, 0.0), c.get(s, 0.0)
            if max(pv, cv) < min_affinity:
                continue
            if pv < min_affinity <= cv:
                status = "new"
            elif cv < min_affinity <= pv:
                status = "lost"
            elif cv > pv:
                status = "up"
            elif cv < pv:
                status = "down"
            else:
                status = "flat"
            out.append(AffinityShift(b, s, pv, cv, cv - pv, status))
    out.sort(key=lambda x: -abs(x.change))
    return out


# --------------------------------------------------------------------------
# 39. BROKER STOCK ROTATION  (market-wide — opt-in, cost documented)
# --------------------------------------------------------------------------

class StockRotation(NamedTuple):
    """Section 39 — where one broker's *activity* sits as the window shortens.

    Spec wording, kept deliberately: this is **broker activity rotation**, not
    capital rotation. Nothing here knows about capital.
    """

    broker: int
    focus: dict[int, str | None]  # window -> the broker's biggest symbol by gross qty
    shares: dict[int, float]  # window -> that symbol's share of the broker's activity
    rotated: bool  # shortest-window focus differs from longest
    new_focus: tuple[str, ...]  # in the short-window book, absent from the long
    abandoned: tuple[str, ...]
    increasing: tuple[str, ...]  # share rises as the window shortens
    decreasing: tuple[str, ...]


def stock_rotation(
    broker: int,
    symbols: list[str],
    windows: tuple[int, ...] = WINDOWS,
    upto: str | None = None,
    top: int = 5,
) -> StockRotation:
    """Section 39 for one broker across a symbol list.

    **COST.** Reads ``max(windows)`` sessions for every symbol given — one load
    per symbol, then sliced per window, so it is ``len(symbols) * 30`` sessions
    at the default. ``symbols`` is required; pass the shortlist you actually
    care about, not :func:`loader.symbols`.
    """
    span = max(windows)
    per_window: dict[int, Counter[str]] = {w: Counter() for w in windows}
    for sym in symbols:
        ses = loader.load_last(sym, span, upto)
        if not ses:
            continue
        for w in windows:
            bd = brokers.window(ses[-w:]).get(broker)
            if bd and bd.gross_qty:
                per_window[w][sym] = bd.gross_qty

    focus: dict[int, str | None] = {}
    shares: dict[int, float] = {}
    books: dict[int, dict[str, float]] = {}
    for w in windows:
        c = per_window[w]
        tot = sum(c.values())
        books[w] = {s: q / tot for s, q in c.items()} if tot else {}
        if c:
            sym, q = c.most_common(1)[0]
            focus[w], shares[w] = sym, q / tot
        else:
            focus[w], shares[w] = None, 0.0

    short, long = min(windows), max(windows)
    s_book, l_book = books[short], books[long]
    s_top = {s for s, _ in sorted(s_book.items(), key=lambda x: -x[1])[:top]}
    l_top = {s for s, _ in sorted(l_book.items(), key=lambda x: -x[1])[:top]}

    inc = tuple(sorted(s for s in s_book if s_book[s] > l_book.get(s, 0.0)))
    dec = tuple(sorted(s for s in l_book if l_book[s] > s_book.get(s, 0.0)))

    return StockRotation(
        broker=broker,
        focus=focus,
        shares=shares,
        rotated=bool(focus[short] and focus[long] and focus[short] != focus[long]),
        new_focus=tuple(sorted(s_top - l_top)),
        abandoned=tuple(sorted(l_top - s_top)),
        increasing=inc,
        decreasing=dec,
    )


# --------------------------------------------------------------------------
# 40 + 43. BROKER x BROKER / PAIR PERSISTENCE
# --------------------------------------------------------------------------

class Pair(NamedTuple):
    """Sections 40 and 43 — one directed buyer -> seller edge over a window.

    ``qty_share`` and ``trade_share`` are both here on purpose. Pair flow is a
    trade-count proxy (r = +0.78 against trade count in this codebase's own
    tests), so a heavy pair that is also a busy pair is telling you nothing you
    did not already know from turnover. Read them together, always.

    ``reciprocal_qty`` is the ``min(a->b, b->a)`` "wash reciprocity" quantity.
    It is kept because section 40 asks for it and it is a fine forensic
    descriptor. It is **not** alpha: as a ranking factor it was measured and
    killed, and the spread it appeared to have was a date effect.
    """

    buyer: int
    seller: int
    trades: int
    qty: int
    turnover: float
    avg_rate: float  # unweighted mean of trade rates
    vwap: float  # quantity-weighted
    largest: int
    days: int  # distinct sessions the pair traded in
    stocks: int  # distinct symbols (1 unless the caller concatenated symbols)
    persistence: float  # days / sessions in the window
    qty_share: float  # pair qty / window volume
    trade_share: float  # pair trades / window trades  <- the confound, side by side
    reciprocal_qty: int  # min(a->b, b->a); see the class docstring
    recurrence: float  # days / days both brokers were simultaneously active


def pairs(sessions: list[Session], min_trades: int = 1) -> dict[tuple[int, int], Pair]:
    """Sections 40/43. Key is the directed ``(buyer, seller)`` tuple.

    Sessions may span symbols if the caller concatenates them — ``stocks`` picks
    that up for free. Self-trades (buyer == seller) are included as their own
    edge; see :func:`self_trades` for the section 47 treatment.
    """
    n = len(sessions)
    if not n:
        return {}

    qty: Counter[tuple[int, int]] = Counter()
    amt: dict[tuple[int, int], float] = defaultdict(float)
    cnt: Counter[tuple[int, int]] = Counter()
    rate_sum: dict[tuple[int, int], float] = defaultdict(float)
    largest: Counter[tuple[int, int]] = Counter()
    days: Counter[tuple[int, int]] = Counter()
    syms: dict[tuple[int, int], set[str]] = defaultdict(set)
    # ponytail: days counted by "the session key changed", not by a per-pair set of
    # dates. A set per pair is ~5M ints on NABIL's full history and this box is
    # RAM-starved. Correct as long as sessions are grouped by symbol and ordered by
    # date, which is what loader hands out. Upgrade to sets only if that stops holding.
    last_key: dict[tuple[int, int], tuple[str, str]] = {}
    active_days: Counter[int] = Counter()

    for s in sessions:
        key = (s.symbol, s.date)
        present: set[int] = set()
        for t in s.trades:
            e = (t.buyer, t.seller)
            qty[e] += t.quantity
            amt[e] += t.amount
            cnt[e] += 1
            rate_sum[e] += t.rate
            if t.quantity > largest[e]:
                largest[e] = t.quantity
            syms[e].add(s.symbol)
            if last_key.get(e) != key:
                last_key[e] = key
                days[e] += 1
            present.add(t.buyer)
            present.add(t.seller)
        for b in present:
            active_days[b] += 1

    volume = sum(s.volume for s in sessions) or 1
    total_trades = sum(len(s.trades) for s in sessions) or 1

    out: dict[tuple[int, int], Pair] = {}
    for e, c in cnt.items():
        if c < min_trades:
            continue
        a, b = e
        both = min(active_days[a], active_days[b]) or 1
        out[e] = Pair(
            buyer=a,
            seller=b,
            trades=c,
            qty=qty[e],
            turnover=amt[e],
            avg_rate=rate_sum[e] / c,
            vwap=amt[e] / qty[e] if qty[e] else 0.0,
            largest=largest[e],
            days=days[e],
            stocks=len(syms[e]),
            persistence=days[e] / n,
            qty_share=qty[e] / volume,
            trade_share=c / total_trades,
            reciprocal_qty=min(qty[e], qty.get((b, a), 0)),
            recurrence=days[e] / both,
        )
    return out


def pair_concentration(pair_map: dict[tuple[int, int], Pair]) -> float:
    """Sections 40/43 — HHI of pair quantity. Same number as ``network().concentration``."""
    return _hhi(p.qty for p in pair_map.values())


class PairShift(NamedTuple):
    appeared: int
    disappeared: int
    continuing: int
    turnover_rate: float  # |symmetric difference| / |union| of the edge sets
    top_appeared: tuple[tuple[int, int], ...]
    top_disappeared: tuple[tuple[int, int], ...]


def pair_shift(
    prev: dict[tuple[int, int], Pair], curr: dict[tuple[int, int], Pair], top: int = 5
) -> PairShift:
    """Sections 40/43 — pair appearance and disappearance between two windows."""
    ps, cs = set(prev), set(curr)
    new, gone = cs - ps, ps - cs
    union = ps | cs
    return PairShift(
        appeared=len(new),
        disappeared=len(gone),
        continuing=len(ps & cs),
        turnover_rate=len(new | gone) / len(union) if union else 0.0,
        top_appeared=tuple(sorted(new, key=lambda e: -curr[e].qty)[:top]),
        top_disappeared=tuple(sorted(gone, key=lambda e: -prev[e].qty)[:top]),
    )


# --------------------------------------------------------------------------
# 41 + 42. BROKER NETWORK / CENTRALITY OVER TIME
# --------------------------------------------------------------------------

class Network(NamedTuple):
    """Section 41 — the buyer -> seller execution graph over a window.

    Written by hand because networkx is not on the target box and these metrics
    are five lines each on a ~70-node graph.

    A caution that matters for section 48: over any window longer than a few
    sessions this graph is close to complete. Most brokers trade with most
    brokers. Structure only becomes informative once you filter to heavy edges,
    which is why :func:`cycles` takes a ``min_share``.
    """

    nodes: int
    edges: int  # distinct directed buyer -> seller edges
    undirected_edges: int
    density: float  # edges / (n * (n-1)) for the directed graph, in [0, 1]
    mean_degree: float  # mean counterparty count
    max_degree: int
    concentration: float  # HHI of edge quantity
    clustering: float  # mean local clustering on the undirected projection
    # ``busiest``/``busiest_share`` were called ``central``/``central_share`` and were
    # presented as the network's centrality. They are not a graph measure at all. The
    # weighted degree adds every edge's quantity to BOTH endpoints, so a broker's
    # weighted degree is exactly its gross traded quantity and ``busiest_share`` is
    # exactly its share of twice the volume — which is what section 24 already reports
    # as ``broker top1`` and section 9 already names as ``broker #1``. Measured on the
    # shipped board: ``41 central`` == ``9 broker #1 broker`` on 481 of 481 symbols and
    # ``41 central share`` == ``24 30D broker top1`` on 481 of 481. Renamed to say what
    # it is, and section 41 no longer prints it — it points at 24 instead.
    #
    # NOT replaced with betweenness or eigenvector centrality, deliberately. Eigenvector
    # centrality on a weighted graph this dense (measured density: median 0.133, max
    # 0.840, mean degree 17 on a median 75 nodes) tracks weighted degree closely enough
    # to land right back here, and Brandes betweenness is O(V*E) per symbol per build
    # for a factor with no out-of-sample support — this repo has already retired six
    # floorsheet operator families and the whole counterparty-breadth family on exactly
    # that evidence. If a real centrality is ever wanted it needs a hypothesis first.
    busiest: int | None  # broker with the largest weighted degree == largest gross quantity
    busiest_share: float  # its share of total weighted degree == its gross share of 2x volume
    degree: dict[int, int]  # broker -> distinct counterparties
    weighted_degree: dict[int, int]  # broker -> gross quantity through it
    centrality: dict[int, float]  # degree / (n - 1)
    edge_qty: dict[tuple[int, int], int]


def network(sessions: list[Session], pair_map: dict[tuple[int, int], Pair] | None = None) -> Network:
    """Section 41. Pass ``pair_map`` to avoid re-scanning trades you already paired."""
    pm = pair_map if pair_map is not None else pairs(sessions)
    if not pm:
        return Network(0, 0, 0, 0.0, 0.0, 0, 0.0, 0.0, None, 0.0, {}, {}, {}, {})

    edge_qty = {e: p.qty for e, p in pm.items()}
    nbrs: dict[int, set[int]] = defaultdict(set)
    wdeg: Counter[int] = Counter()
    undirected: set[tuple[int, int]] = set()
    for (a, b), q in edge_qty.items():
        wdeg[a] += q
        wdeg[b] += q
        if a != b:  # a self-loop is not a counterparty relationship
            nbrs[a].add(b)
            nbrs[b].add(a)
            undirected.add((a, b) if a < b else (b, a))
        else:
            nbrs.setdefault(a, set())

    nodes = set(nbrs) | set(wdeg)
    n = len(nodes)
    deg = {v: len(nbrs.get(v, ())) for v in nodes}

    # Local clustering on the undirected projection: links among v's neighbours
    # over the links there could be. O(n * k^2); n is ~70 here, so it is free.
    local = []
    for v in nodes:
        ns = nbrs.get(v, set())
        k = len(ns)
        if k < 2:
            local.append(0.0)
            continue
        links = sum(1 for i, x in enumerate(sorted(ns)) for y in sorted(ns)[i + 1:] if y in nbrs.get(x, ()))
        local.append(2.0 * links / (k * (k - 1)))

    total_w = sum(wdeg.values()) or 1
    top = max(wdeg, key=wdeg.get) if wdeg else None

    # Density counts only edges between DIFFERENT brokers, because n*(n-1) is the number of
    # ordered pairs of different brokers -- a self-loop has no slot in that denominator. Counting
    # them in the numerator (as this did) inflates density by the self-trade rate, and self-trades
    # are not rare here: they appear in 72% of real (symbol, window) combinations and on 100% of
    # NABIL's sessions. Measured worst case before the fix, BOKD86KA over 26 brokers with 6
    # self-loops: 0.0738 reported against a true 0.0646, +12.5%. Real broker graphs are sparse
    # enough that it never printed >1.0, so nothing raised -- it was simply wrong by a few percent
    # on most windows. Self-trades are not lost: section 47 counts and reports them on their own.
    cross_edges = sum(1 for (a, b) in edge_qty if a != b)

    return Network(
        nodes=n,
        edges=len(edge_qty),
        undirected_edges=len(undirected),
        density=cross_edges / (n * (n - 1)) if n > 1 else 0.0,
        mean_degree=statistics.fmean(deg.values()) if deg else 0.0,
        max_degree=max(deg.values()) if deg else 0,
        concentration=_hhi(edge_qty.values()),
        clustering=statistics.fmean(local) if local else 0.0,
        busiest=top,
        busiest_share=wdeg[top] / total_w if top is not None else 0.0,
        degree=deg,
        weighted_degree=dict(wdeg),
        centrality={v: (deg[v] / (n - 1) if n > 1 else 0.0) for v in nodes},
        edge_qty=edge_qty,
    )


class NetworkDrift(NamedTuple):
    """Section 42 — the same network at 30D / 15D / 7D / 3D, and what moved."""

    by_window: dict[int, Network]
    central_changed: bool
    degree_centrality_change: float  # the long window's busiest broker: short degree centrality - long
    weighted_centrality_change: float
    # The two rows below compare the last `short` sessions against the `short` BEFORE them —
    # never the nested 3D-inside-30D pair the rows above use. Nested, the short window's edge
    # set is a subset of the long one's, so a broker's degree can only shrink: `contraction`
    # counted essentially every node (it equalled section 41's `nodes` on 220 of 481 symbols,
    # r = 0.999) and its expansion twin was 0 on 481 of 481. On equal-length adjacent blocks
    # both move: net spans [-41, +57], median 0, positive on 49% of symbols.
    #
    # Jaccard(short edges, long edges) is gone for the same reason — nested, it is identically
    # |E_short| / |E_long| and could only say a 3-day window holds fewer edges than a 30-day
    # one. The genuine appearance/disappearance measure is section 43's
    # ``pair_shift(pairs(prev), pm).turnover_rate``, prior 30D against current 30D.
    counterparty_contraction: int  # brokers with fewer counterparties than in the block before
    counterparty_expansion: int  # …and with more
    concentration_change: float


def centrality_drift(sessions: list[Session], windows: tuple[int, ...] = WINDOWS) -> NetworkDrift:
    """Section 42. Slices one already-loaded window list — no extra I/O."""
    nets = {w: network(sessions[-w:]) for w in windows if sessions}
    if not nets:
        return NetworkDrift({}, False, 0.0, 0.0, 0, 0, 0.0)

    short, long = min(nets), max(nets)
    ns, nl = nets[short], nets[long]

    ref = nl.busiest
    dcc = (ns.centrality.get(ref, 0.0) - nl.centrality.get(ref, 0.0)) if ref is not None else 0.0
    ws = sum(ns.weighted_degree.values()) or 1
    wl = sum(nl.weighted_degree.values()) or 1
    wcc = (ns.weighted_degree.get(ref, 0) / ws - nl.weighted_degree.get(ref, 0) / wl) if ref is not None else 0.0

    # Adjacent equal-length blocks, both already inside `sessions` — no extra I/O.
    if len(sessions) >= 2 * short:
        prev_n, curr_n = network(sessions[-2 * short:-short]), network(sessions[-short:])
        con = sum(1 for v, d in prev_n.degree.items() if d > curr_n.degree.get(v, 0))
        exp = sum(1 for v, d in curr_n.degree.items() if d > prev_n.degree.get(v, 0))
    else:
        con = exp = 0

    return NetworkDrift(
        by_window=nets,
        central_changed=ns.busiest != nl.busiest,
        degree_centrality_change=dcc,
        weighted_centrality_change=wcc,
        counterparty_contraction=con,
        counterparty_expansion=exp,
        concentration_change=ns.concentration - nl.concentration,
    )


# --------------------------------------------------------------------------
# 44. REPEATED TRANSACTION PATTERNS
# --------------------------------------------------------------------------

class Repeat(NamedTuple):
    """Section 44 — one repeated structure, with the reason it was flagged."""

    kind: str  # pair | price | price_volume | accumulator | distributor | large
    key: str
    count: int
    share: float  # of trades, sessions or volume, per ``detail``
    detail: str


def repeats(sessions: list[Session], top: int = 3, large_q: float = 0.99) -> list[Repeat]:
    """Section 44 — recurring pairs, prices, price-volume combos and one-way brokers.

    Forensic descriptors. A repeated price is usually a round number and a
    repeated pair is usually the two busiest brokers in the name; both are
    reported with the denominator that makes that visible.
    """
    if not sessions:
        return []

    n = len(sessions)
    trades = [t for s in sessions for t in s.trades]
    total = len(trades) or 1
    volume = sum(s.volume for s in sessions) or 1
    out: list[Repeat] = []

    pm = pairs(sessions)
    for p in sorted(pm.values(), key=lambda p: (-p.days, -p.qty))[:top]:
        out.append(Repeat(
            "pair", f"{p.buyer}->{p.seller}", p.days, p.persistence,
            f"traded on {p.days}/{n} sessions; {p.qty_share:.2%} of volume on "
            f"{p.trade_share:.2%} of trades (trade count is the known confound)",
        ))

    rates = Counter(t.rate for t in trades)
    for rate, c in rates.most_common(top):
        out.append(Repeat("price", f"{rate:g}", c, c / total, f"{c} of {total} trades printed at {rate:g}"))

    combos = Counter((t.rate, t.quantity) for t in trades)
    for (rate, q), c in combos.most_common(top):
        if c < 2:
            break
        out.append(Repeat("price_volume", f"{q}@{rate:g}", c, c / total, f"{q} shares at {rate:g}, {c} times"))

    acc: Counter[int] = Counter()
    dist: Counter[int] = Counter()
    for s in sessions:
        for b, bd in brokers.day(s).items():
            if bd.net_qty > 0:
                acc[b] += 1
            elif bd.net_qty < 0:
                dist[b] += 1
    for b, c in acc.most_common(top):
        out.append(Repeat("accumulator", str(b), c, c / n, f"net buyer on {c}/{n} sessions"))
    for b, c in dist.most_common(top):
        out.append(Repeat("distributor", str(b), c, c / n, f"net seller on {c}/{n} sessions"))

    qtys = sorted(t.quantity for t in trades)
    cut = qtys[min(len(qtys) - 1, int(len(qtys) * large_q))]
    big = Counter(t.buyer for t in trades if t.quantity >= cut)
    for b, c in big.most_common(top):
        out.append(Repeat("large", str(b), c, c / max(1, sum(big.values())),
                          f"bought {c} of the trades at or above {cut:,} shares (p{large_q:.0%})"))

    return out


# --------------------------------------------------------------------------
# 45 + 46. SEQUENCE / CONTRACT ANALYSIS
# --------------------------------------------------------------------------

def ordered_trades(session: Session) -> tuple[Trade, ...]:
    """Section 46 — transaction order, from the contract number. Sorting is required.

    The archive files are written **newest contract first**, so ``session.trades``
    is in reverse execution order. Every sequence statistic below is meaningless
    without this call. There is no timestamp in this data and the spec forbids
    inventing one, so the contract number is the only ordering key there is.

    Sorted by ``(len, contract)`` so a short/legacy contract string never sorts
    above a longer one by accident.
    """
    return tuple(sorted(session.trades, key=lambda t: (len(t.contract), t.contract)))


class SequenceStats(NamedTuple):
    """Sections 45/46 — the shape of one session's transaction sequence."""

    trades: int
    reordered: int  # trades that moved when sorted into contract order
    consecutive_buyer: int  # adjacent trades sharing a buyer
    consecutive_seller: int
    buyer_switch: float  # 1 - consecutive_buyer / (n-1)
    seller_switch: float
    both_switch: float  # fraction of steps where BOTH sides changed
    longest_buyer_run: int
    longest_seller_run: int
    up_steps: int
    down_steps: int
    flat_steps: int
    price_drift: float  # last rate - first rate, in contract order
    size_early: float  # mean trade size, 1st half of the sequence
    size_late: float
    size_trend: float  # size_late / size_early - 1
    top_bigram: tuple[tuple[int, int], tuple[int, int]] | None  # most repeated adjacent pair-of-edges
    top_bigram_count: int
    repeated_bigrams: int  # distinct adjacent edge-pairs seen more than once


def sequence(session: Session) -> SequenceStats:
    """Sections 45/46 for one session. Cross-day adjacency is meaningless, so one day."""
    ts = ordered_trades(session)
    n = len(ts)
    if n < 2:
        return SequenceStats(n, 0, 0, 0, 0.0, 0.0, 0.0, n, n, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, None, 0, 0)

    reordered = sum(1 for a, b in zip(ts, session.trades) if a.contract != b.contract)

    cb = cs = both = 0
    up = down = flat = 0
    run_b = run_s = best_b = best_s = 1
    bigrams: Counter[tuple[tuple[int, int], tuple[int, int]]] = Counter()
    for a, b in zip(ts, ts[1:]):
        same_b = a.buyer == b.buyer
        same_s = a.seller == b.seller
        cb += same_b
        cs += same_s
        both += (not same_b) and (not same_s)
        run_b = run_b + 1 if same_b else 1
        run_s = run_s + 1 if same_s else 1
        best_b, best_s = max(best_b, run_b), max(best_s, run_s)
        if b.rate > a.rate:
            up += 1
        elif b.rate < a.rate:
            down += 1
        else:
            flat += 1
        bigrams[((a.buyer, a.seller), (b.buyer, b.seller))] += 1

    steps = n - 1
    half = n // 2
    early = statistics.fmean(t.quantity for t in ts[:half]) if half else 0.0
    late = statistics.fmean(t.quantity for t in ts[half:]) if n - half else 0.0
    bg, bgc = (bigrams.most_common(1) or [(None, 0)])[0]

    return SequenceStats(
        trades=n,
        reordered=reordered,
        consecutive_buyer=cb,
        consecutive_seller=cs,
        buyer_switch=1.0 - cb / steps,
        seller_switch=1.0 - cs / steps,
        both_switch=both / steps,
        longest_buyer_run=best_b,
        longest_seller_run=best_s,
        up_steps=up,
        down_steps=down,
        flat_steps=flat,
        price_drift=ts[-1].rate - ts[0].rate,
        size_early=early,
        size_late=late,
        size_trend=(late / early - 1.0) if early else 0.0,
        top_bigram=bg,
        top_bigram_count=bgc,
        repeated_bigrams=sum(1 for c in bigrams.values() if c > 1),
    )


# --------------------------------------------------------------------------
# 47. SAME-BROKER SIDE FLAG
# --------------------------------------------------------------------------

class SelfTrades(NamedTuple):
    """Section 47 — ``buyer == seller`` rows, flagged for review.

    **This is not proof of improper activity.** A broker crossing two of its own
    clients books exactly like this and is ordinary business. The spec asks for
    the flag and the percentages; it does not ask for a verdict, and this module
    does not offer one.
    """

    count: int
    quantity: int
    amount: float
    pct_transactions: float
    pct_volume: float
    pct_turnover: float
    sessions_with: int
    session_pct: float  # historical frequency: sessions containing one / sessions
    brokers: tuple[tuple[int, int], ...]  # (broker, count), most frequent first
    symbols: tuple[str, ...]


def self_trades(sessions: list[Session], top: int = 5) -> SelfTrades:
    """Section 47."""
    n = len(sessions)
    if not n:
        return SelfTrades(0, 0, 0.0, 0.0, 0.0, 0.0, 0, 0.0, (), ())

    count = qty = 0
    amt = 0.0
    with_any = 0
    who: Counter[int] = Counter()
    syms: set[str] = set()
    for s in sessions:
        hit = False
        for t in s.trades:
            if t.buyer == t.seller:
                count += 1
                qty += t.quantity
                amt += t.amount
                who[t.buyer] += 1
                syms.add(s.symbol)
                hit = True
        with_any += hit

    trades = sum(len(s.trades) for s in sessions) or 1
    volume = sum(s.volume for s in sessions) or 1
    turnover = sum(s.turnover for s in sessions) or 1.0

    return SelfTrades(
        count=count,
        quantity=qty,
        amount=amt,
        pct_transactions=count / trades,
        pct_volume=qty / volume,
        pct_turnover=amt / turnover,
        sessions_with=with_any,
        session_pct=with_any / n,
        brokers=tuple(who.most_common(top)),
        symbols=tuple(sorted(syms)),
    )


# --------------------------------------------------------------------------
# 48. CIRCULAR-PATTERN CANDIDATES
# --------------------------------------------------------------------------

class Cycle(NamedTuple):
    """Section 48 — a closed A -> B -> C -> A loop among heavy edges.

    **Anomaly candidates, not proof of manipulation.** Read :class:`Network`'s
    docstring first: the unfiltered graph is close to complete, so *every*
    triangle exists and an unfiltered cycle count is both meaningless and
    expensive. What can be informative is a loop whose edges are all large and
    roughly the same size — hence ``min_share`` and ``balance``.

    ``min_share`` sits on a cliff and the default was chosen by measuring it,
    not by taste. NABIL, 30D window, cycles found as the threshold drops::

        1.0%   2 heavy edges     0 cycles
        0.5%   8 heavy edges     0 cycles
        0.2%  73 heavy edges     1 cycle      <- the default
        0.1% 204 heavy edges    50+ (capped)

    Two decimal places of threshold is the difference between "nothing" and
    "everything". Anything at or below 0.1% is just enumerating a dense graph.
    """

    brokers: tuple[int, ...]
    length: int
    min_qty: int
    max_qty: int
    balance: float  # min edge / max edge around the loop; 1.0 = perfectly even
    qty_share: float  # the loop's binding edge as a share of window volume
    days: int  # fewest sessions any edge in the loop was active


def cycles(
    sessions: list[Session],
    lengths: tuple[int, ...] = (3, 4),
    min_share: float = 0.002,
    cap: int = 50,
    pair_map: dict[tuple[int, int], Pair] | None = None,
) -> list[Cycle]:
    """Section 48 — bounded search, lengths 3 and 4 only, capped at ``cap`` results.

    Depth-first from each node, keeping only cycles whose smallest member is the
    start node so each loop is enumerated once. Bounded by construction: the
    heavy-edge subgraph is tiny and the depth never exceeds ``max(lengths)``.
    """
    pm = pair_map if pair_map is not None else pairs(sessions)
    volume = sum(s.volume for s in sessions) or 1
    keep = {e: p for e, p in pm.items() if e[0] != e[1] and p.qty / volume >= min_share}
    if not keep:
        return []

    adj: dict[int, list[int]] = defaultdict(list)
    for a, b in keep:
        adj[a].append(b)

    max_len = max(lengths)
    found: list[Cycle] = []
    seen: set[tuple[int, ...]] = set()

    def walk(start: int, node: int, path: list[int]) -> None:
        if len(found) >= cap:
            return
        for nxt in adj.get(node, ()):
            # Re-test per EDGE, not once on entry: a frame that entered under the cap kept
            # appending down its own neighbour list and five symbols shipped 51 of a cap of 50.
            if len(found) >= cap:
                return
            if nxt == start and len(path) in lengths:
                loop = tuple(path)
                rot = min(range(len(loop)), key=lambda i: loop[i])
                canon = loop[rot:] + loop[:rot]
                if canon in seen:
                    continue
                seen.add(canon)
                edges = [keep[(path[i], path[(i + 1) % len(path)])] for i in range(len(path))]
                qs = [e.qty for e in edges]
                found.append(Cycle(
                    brokers=canon,
                    length=len(canon),
                    min_qty=min(qs),
                    max_qty=max(qs),
                    balance=min(qs) / max(qs),
                    qty_share=min(qs) / volume,
                    days=min(e.days for e in edges),
                ))
                if len(found) >= cap:
                    return
            elif nxt > start and nxt not in path and len(path) < max_len:
                walk(start, nxt, path + [nxt])

    for start in sorted(adj):
        walk(start, start, [start])
        if len(found) >= cap:
            break

    found.sort(key=lambda c: (-c.qty_share, -c.balance))
    return found


# --------------------------------------------------------------------------
# 49. ANOMALY DETECTION
# --------------------------------------------------------------------------

class Component(NamedTuple):
    """One reason inside an :class:`Anomaly`. Never aggregated away."""

    name: str
    value: float
    baseline: float
    z: float  # signed
    contribution: float  # min(|z|, 5) — what this reason added to the score
    reason: str


class Anomaly(NamedTuple):
    """Section 49 — an AnomalyScore that always carries its reasons.

    The spec is explicit: "retain component reasons", "never output only a
    black-box score". ``components`` holds every metric that was tested, not
    only the ones that fired, so a low score is as explainable as a high one.
    Reading ``score`` without ``components`` is using this class wrong.

    Score is the mean clipped |z| across components, so it lives in [0, 5].
    """

    symbol: str
    date: str
    score: float
    components: tuple[Component, ...]  # every metric, worst first
    flagged: tuple[str, ...]  # names with |z| >= z_flag
    baseline_sessions: int


def _metrics(session: Session, dominant: int | None = None) -> dict[str, float]:
    """The section 49 metric vector for one session.

    Two things the spec lists are deliberately absent:

    * *Stock-level buy/sell imbalance* — identically 0.000 on every stock every
      day, because every share bought is sold. Already cut in :mod:`brokers`
      and it does not come back here.
    * *Unique-broker imbalance* (buyers minus sellers as a count) — measured on
      this archive and it moves almost not at all, because nearly every broker
      active in a name is active on both sides. ``fragmentation`` and
      ``participation`` carry the same information without the flat column.
    """
    trades = session.trades
    if not trades:
        return {}
    agg = brokers.day(session)
    flow = brokers.stock_flow(agg)
    pm = pairs([session])
    rates = Counter(t.rate for t in trades)

    m = {
        "trade_size": flow.volume / len(trades),
        "volume": float(flow.volume),
        "turnover": flow.turnover,
        "broker_concentration": _hhi(b.gross_qty for b in agg.values()),
        "buyer_concentration": _hhi(b.buy_qty for b in agg.values()),
        "seller_concentration": _hhi(b.sell_qty for b in agg.values()),
        "pair_concentration": _hhi(p.qty for p in pm.values()),
        "flow_quality": flow.flow_quality,
        "price_concentration": max(rates.values()) / len(trades),
        "trade_frequency": float(len(trades)),
        "participation": float(flow.brokers),
        "fragmentation": len(trades) / flow.brokers if flow.brokers else 0.0,
        "self_trade_pct": sum(t.quantity for t in trades if t.buyer == t.seller) / flow.volume if flow.volume else 0.0,
        "top_pair_share": max((p.qty for p in pm.values()), default=0) / flow.volume if flow.volume else 0.0,
    }
    if dominant is not None:
        # Flow reversal: the baseline's most one-directional broker, measured on
        # THIS session. A large negative z is that broker turning around.
        bd = agg.get(dominant)
        m["dominant_flow"] = (bd.net_qty / flow.volume) if bd and flow.volume else 0.0
    return m


_REASONS = {
    "trade_size": "mean trade size",
    "volume": "session volume",
    "turnover": "session turnover",
    "broker_concentration": "how few brokers carry the gross volume",
    "buyer_concentration": "how few brokers carry the buying",
    "seller_concentration": "how few brokers carry the selling",
    "pair_concentration": "how few buyer-seller pairs carry the volume",
    "flow_quality": "net shares changing hands / volume",
    "price_concentration": "share of trades printing at one rate",
    "trade_frequency": "transaction count",
    "participation": "brokers active",
    "fragmentation": "trades per active broker",
    "self_trade_pct": "volume where buyer == seller",
    "top_pair_share": "volume in the single heaviest broker pair",
    "dominant_flow": "the baseline's dominant broker's net share today",
}


def anomaly(
    session: Session, baseline: list[Session], z_flag: float = 2.0, min_baseline: int = 10
) -> Anomaly:
    """Section 49. ``baseline`` is history strictly before ``session`` — point-in-time.

    Anything in ``baseline`` dated on or after ``session`` is dropped rather than
    trusted, so a caller that hands over a whole archive still gets an honest
    answer.
    """
    base = [s for s in baseline if s.date < session.date and s.trades]
    if len(base) < min_baseline or not session.trades:
        return Anomaly(session.symbol, session.date, 0.0, (), (), len(base))

    agg_base = brokers.window(base)
    dom = max(agg_base.values(), key=lambda b: abs(b.net_qty)).broker if agg_base else None

    hist = [_metrics(s, dom) for s in base]
    now = _metrics(session, dom)

    comps: list[Component] = []
    for name, value in now.items():
        sample = [h[name] for h in hist if name in h]
        z, mu = _z(value, sample)
        contrib = min(abs(z), 5.0)
        direction = "above" if z >= 0 else "below"
        comps.append(Component(
            name=name,
            value=value,
            baseline=mu,
            z=z,
            contribution=contrib,
            reason=f"{_REASONS.get(name, name)} {value:,.4g} is {abs(z):.1f} sd {direction} "
                   f"its {len(sample)}-session baseline of {mu:,.4g}",
        ))

    comps.sort(key=lambda c: -c.contribution)
    return Anomaly(
        symbol=session.symbol,
        date=session.date,
        score=statistics.fmean(c.contribution for c in comps),
        components=tuple(comps),
        flagged=tuple(c.name for c in comps if abs(c.z) >= z_flag),
        baseline_sessions=len(base),
    )


# --------------------------------------------------------------------------
# self-check
# --------------------------------------------------------------------------

def _demo() -> None:
    """Real archive, real invariants. Every assert below has failed at least once."""
    syms = loader.symbols()
    assert syms, "no floorsheet archive"
    sym = "NABIL" if "NABIL" in syms else syms[0]

    ses = loader.load_last(sym, 1055)
    assert len(ses) > 200, f"{sym}: only {len(ses)} sessions"
    last = ses[-1]
    w30, w15, w7, w3 = ses[-30:], ses[-15:], ses[-7:], ses[-3:]

    # -- 33: participation must agree with the broker aggregate ---------------
    p = participation(w30, universe=90)
    assert p.active == len(brokers.window(w30)), "participation roster != broker aggregate"
    assert p.buyers <= p.active and p.sellers <= p.active
    assert p.both_sides <= min(p.buyers, p.sellers)
    assert p.per_session_min <= p.per_session_mean <= p.per_session_max
    assert 0.0 < p.breadth <= 1.0, p.breadth
    assert p.participation_pct is not None
    assert participation(w30).participation_pct is None, "no universe must mean no fake 1.000"

    # -- 34: the roster accounting has to close exactly ----------------------
    ch = churn(prev=ses[-60:-30], curr=w30, history=ses[:-60])
    assert ch.new + ch.returning + ch.continuing == ch.curr_active, "curr roster does not close"
    assert ch.continuing + ch.exiting == ch.prev_active, "prev roster does not close"
    assert ch.expansion == ch.new + ch.returning
    assert ch.net_change == ch.expansion - ch.exiting
    assert 0.0 <= ch.new_pct <= 1.0 and 0.0 <= ch.lost_pct <= 1.0
    assert ch.acceleration is not None

    # -- 35/36 ---------------------------------------------------------------
    rot = rotation(ses[-60:-30], w30)
    assert 0.0 <= rot.rotation <= 1.0 and 0.0 <= rot.intensity <= 1.0
    assert rot.new_active == ch.new + ch.returning, "rotation and churn disagree on new faces"
    assert -1.0 <= rot.dominance_change <= 1.0

    rs = rank_stability(w30)
    assert rs.sessions == len(w30)
    assert 0.0 <= rs.top1_persistence <= 1.0
    assert 0.0 <= rs.top5_persistence <= 1.0
    assert 0.0 <= rs.dominant_persistence <= 1.0
    assert rs.top1_changes <= rs.sessions
    assert rs.rank_volatility >= 0.0
    assert rs.top1 is not None and rs.dominant is not None
    assert min(rs.mean_rank.values()) >= 1.0

    # -- 37 ------------------------------------------------------------------
    bs = broker_stock(w30)
    assert set(bs) == set(brokers.window(w30)), "broker x stock cell set != aggregate"
    assert all(0.0 <= c.persistence <= 1.0 for c in bs.values())
    assert all(0.0 <= c.consistency <= 1.0 for c in bs.values())
    assert all(0.0 <= c.volume_pct <= 1.0 for c in bs.values())
    assert abs(sum(c.volume_pct for c in bs.values()) - 1.0) < 1e-6, "volume shares must sum to 1"
    assert sum(c.net_qty for c in bs.values()) == 0, "net flow must cancel"
    assert all(c.affinity is None for c in bs.values()), "affinity needs market totals, not a guess"
    # cum_net must be HISTORY's net, not the window's. Without hist_net the two are the
    # same number by construction, which is the defect; with it they must disagree
    # somewhere, or the longer span was not actually threaded through.
    hist = {b: bd.net_qty for b, bd in brokers.window(ses).items()}
    bs_h = broker_stock(w30, hist_net=hist)
    assert all(c.cum_net == c.net_qty for c in bs.values()), "no hist_net: cum_net degenerates to net_qty"
    assert any(c.cum_net != c.net_qty for c in bs_h.values()), "hist_net ignored — cum_net still copies net_qty"
    assert all(c.cum_net == hist[b] for b, c in bs_h.items()), "cum_net must be the historical net"

    # -- 38: market-wide, opt-in, deliberately tiny --------------------------
    shortlist = [s for s in ("NABIL", "NICA", "HBL", "SCB", "EBL", "ADBL") if s in syms][:6]
    total, book = broker_totals(shortlist, n=7)
    assert total and book
    aff = affinity(total, book)
    for rows in aff.values():
        assert abs(sum(r.affinity for r in rows) - 1.0) < 1e-6, "a broker's affinities must sum to 1"
        assert all(0.0 <= r.percentile <= 1.0 for r in rows)
        assert [r.rank for r in rows] == sorted(r.rank for r in rows)
    prev_total, prev_book = broker_totals(shortlist, n=7, upto=ses[-8].date)
    shifts = affinity_shift(affinity(prev_total, prev_book), aff)
    assert all(s.status in ("new", "lost", "up", "down", "flat") for s in shifts)

    bs_aff = broker_stock(loader.load_last(sym, 7), totals=total)
    assert any(c.affinity is not None for c in bs_aff.values())

    # -- 39 ------------------------------------------------------------------
    busiest = max(total, key=total.get)
    sr = stock_rotation(busiest, shortlist, windows=(3, 7, 15, 30))
    assert set(sr.focus) == {3, 7, 15, 30}
    assert all(0.0 <= v <= 1.0 for v in sr.shares.values())
    assert not (set(sr.new_focus) & set(sr.abandoned))

    # -- 40/43/44 ------------------------------------------------------------
    pm = pairs(w30)
    assert pm, "no pairs"
    assert sum(p.qty for p in pm.values()) == sum(s.volume for s in w30), "pair volume != window volume"
    assert abs(sum(p.qty_share for p in pm.values()) - 1.0) < 1e-9
    assert abs(sum(p.trade_share for p in pm.values()) - 1.0) < 1e-9
    assert all(0.0 <= p.persistence <= 1.0 for p in pm.values())
    assert all(p.days <= len(w30) for p in pm.values())
    assert all(p.reciprocal_qty <= p.qty for p in pm.values())
    assert all(p.stocks == 1 for p in pm.values()), "single-symbol window must report one stock"
    assert 0.0 < pair_concentration(pm) <= 1.0
    multi = pairs(w7 + loader.load_last(shortlist[1] if len(shortlist) > 1 else sym, 7))
    assert any(p.stocks > 1 for p in multi.values()), "cross-symbol pairs must be detected"

    ps = pair_shift(pairs(ses[-60:-30]), pm)
    assert ps.appeared + ps.continuing == len(pm)
    assert 0.0 <= ps.turnover_rate <= 1.0

    reps = repeats(w30)
    assert reps and all(0.0 <= r.share <= 1.0 for r in reps)
    assert {r.kind for r in reps} >= {"pair", "price", "accumulator", "distributor", "large"}

    # -- 41/42: the graph invariants the task named --------------------------
    net = network(w30, pair_map=pm)
    assert net.nodes == len(brokers.window(w30)), "network nodes != broker count"
    assert net.max_degree <= net.nodes, "degree cannot exceed the broker count"
    assert all(d <= net.nodes - 1 for d in net.degree.values()), "degree exceeds nodes-1"
    assert 0.0 <= net.density <= 1.0, net.density
    assert 0.0 <= net.clustering <= 1.0

    # Density must exclude self-loops, and a bound check on REAL data cannot prove it: broker
    # graphs are sparse, so counting self-loops in the numerator inflated density by a few
    # percent and still landed under 1.0 on every real window. It took a constructed graph to
    # make it fail, so the constructed graph lives here.
    _t = lambda b, s: Trade(b, s, 100, 10.0, 1000.0, f"{b}{s}", 1000.0)
    loopy = Session("SYNTH", "2026-01-01", (_t(1, 2), _t(2, 1), _t(1, 1), _t(2, 2)), None)
    ln = network([loopy])
    assert ln.nodes == 2, ln.nodes
    # 2 ordered pairs of DIFFERENT brokers exist and both traded -> exactly 1.0, not 2.0.
    assert ln.density == 1.0, f"self-loops are in the density numerator again: {ln.density}"
    assert sum(1 for (a, b) in ln.edge_qty if a == b) == 2, "the self-loops must still be counted"
    assert 0.0 < net.concentration <= 1.0
    assert all(0.0 <= c <= 1.0 for c in net.centrality.values())
    assert net.busiest is not None and 0.0 < net.busiest_share <= 1.0
    assert net.undirected_edges <= net.edges

    drift = centrality_drift(ses, windows=WINDOWS)
    assert set(drift.by_window) == set(WINDOWS)
    # `and`, not `or`: the nesting bug produces exp=0 con=89, and `0 or 89` is truthy —
    # the guard would have passed under the exact failure its message names.
    assert drift.counterparty_expansion and drift.counterparty_contraction, (
        "adjacent blocks must move in BOTH directions - a one-sided pair is the nesting bug")
    assert drift.by_window[3].nodes <= drift.by_window[30].nodes

    # The windows NEST, which is the whole reason NetworkDrift has no counterparty_expansion
    # field: sessions[-3:] is a subset of sessions[-30:], so the short edge set is a subset of
    # the long one and no broker's degree can grow. Assert it on real data — if this ever
    # fails the windows have stopped nesting and an expansion column would become meaningful.
    n3, n30 = drift.by_window[3], drift.by_window[30]
    assert set(n3.edge_qty) <= set(n30.edge_qty), "short window must be a subset of the long one"
    grew = [v for v, d in n3.degree.items() if d > n30.degree.get(v, 0)]
    assert not grew, f"nested windows cannot grow a degree, but {grew} did"
    assert "stability" not in NetworkDrift._fields, "a nested Jaccard came back"

    # -- 45/46: contract order is not file order -----------------------------
    ot = ordered_trades(last)
    assert len(ot) == len(last.trades)
    assert [t.contract for t in ot] == sorted(t.contract for t in ot)
    sq = sequence(last)
    assert sq.reordered > 0, "archive writes newest-first; sorting must actually move rows"
    assert sq.up_steps + sq.down_steps + sq.flat_steps == sq.trades - 1
    assert 0.0 <= sq.buyer_switch <= 1.0 and 0.0 <= sq.seller_switch <= 1.0
    assert sq.consecutive_buyer <= sq.trades - 1
    assert sq.longest_buyer_run <= sq.trades

    # -- 47 ------------------------------------------------------------------
    st = self_trades(ses)
    assert st.count > 0, "NABIL's history contains self-trades; finding none means a parse bug"
    assert 0.0 <= st.pct_transactions <= 1.0
    assert 0.0 <= st.pct_volume <= 1.0
    assert 0.0 <= st.session_pct <= 1.0
    assert st.sessions_with <= len(ses)
    assert st.symbols == (sym,)

    # -- 48 ------------------------------------------------------------------
    cyc = cycles(w30, pair_map=pm)
    assert cyc, "the default threshold must actually find candidates or the search is untested"
    for c in cyc:
        assert c.length in (3, 4)
        assert len(set(c.brokers)) == c.length, "a cycle must not repeat a broker"
        assert 0.0 < c.balance <= 1.0
        assert c.brokers[0] == min(c.brokers), "cycles must be canonicalised"
        assert all((c.brokers[i], c.brokers[(i + 1) % c.length]) in pm for i in range(c.length))
    # The cliff documented on the class, re-measured every run so it cannot rot.
    assert len(cycles(w30, pair_map=pm, min_share=0.01)) < len(cyc), "threshold does nothing"
    # Sweep, not one lucky constant: the old cap=20 assert passed while five symbols
    # shipped 51 candidates at cap=50, because the overrun depends on where the last
    # frame happened to be when the count crossed.
    assert all(len(cycles(w30, pair_map=pm, min_share=0.001, cap=n)) == n
               for n in range(1, 40)), "the cap must be EXACT at every value, not eventual"

    # -- 49: the score must never arrive without its reasons ------------------
    an = anomaly(last, ses[:-1])
    assert an.components, "AnomalyScore with no components is exactly what the spec forbids"
    assert an.baseline_sessions > 100
    assert abs(an.score - statistics.fmean(c.contribution for c in an.components)) < 1e-9
    assert 0.0 <= an.score <= 5.0
    assert all(c.reason for c in an.components), "every component must explain itself"
    assert all(abs(c.z) >= 2.0 for c in an.components if c.name in an.flagged)
    assert list(an.components) == sorted(an.components, key=lambda c: -c.contribution)
    assert "dominant_flow" in {c.name for c in an.components}
    # Point-in-time: a baseline containing the session itself must not leak in.
    assert anomaly(last, ses).baseline_sessions == an.baseline_sessions

    # -- nothing shipped as a constant ---------------------------------------
    # Rule: a metric pinned to a constant is worse than no metric, because it
    # reads as a real column. Stock-level buy/sell imbalance died this way; these
    # four are the survivors most at risk of it, so they are re-measured on real
    # data every run rather than trusted.
    for name in ("flow_quality", "broker_concentration", "fragmentation", "price_concentration"):
        vals = {round(_metrics(s).get(name, 0.0), 6) for s in ses[-40:]}
        assert len(vals) > 5, f"anomaly metric {name} barely varies ({len(vals)} distinct in 40 sessions)"

    # The score itself must discriminate: a detector that returns the same number
    # every day has 15 components and no information.
    recent = [anomaly(s, [x for x in ses if x.date < s.date]) for s in ses[-40:]]
    spread = [a.score for a in recent]
    assert max(spread) - min(spread) > 0.5, f"AnomalyScore barely moves: {min(spread):.2f}-{max(spread):.2f}"
    assert any(a.flagged for a in recent), "nothing ever flags — the z threshold is unreachable"
    assert not all(a.flagged for a in recent), "everything flags — the threshold is meaningless"
    worst = max(recent, key=lambda a: a.score)

    top = max(pm.values(), key=lambda p: p.qty)
    print(f"network ok — {sym} {last.date}, {len(ses)} sessions")
    print(f"  33/34/35: {p.active} brokers over 30D (breadth {p.breadth:.2f}), "
          f"{ch.new} new / {ch.returning} returning / {ch.exiting} exiting vs the prior 30D, "
          f"rotation {rot.rotation:.2f} carrying {rot.intensity:.1%} of volume")
    print(f"  36: #1 is broker {rs.top1} on {rs.top1_persistence:.0%} of sessions "
          f"({rs.top1_changes} leader flips), top-5 persistence {rs.top5_persistence:.2f}, "
          f"rank volatility {rs.rank_volatility:.1f}")
    print(f"  37/38/39: {len(bs)} broker-stock cells; broker {busiest} 30D focus "
          f"{sr.focus[30]} -> 3D focus {sr.focus[3]} across {len(shortlist)} symbols "
          f"(rotated={sr.rotated})")
    print(f"  40/43: {len(pm)} pairs, heaviest {top.buyer}->{top.seller} "
          f"{top.qty_share:.1%} of volume on {top.trade_share:.1%} of trades "
          f"(reciprocal {top.reciprocal_qty:,} — trade-count confound, not a signal)")
    print(f"  41/42: {net.nodes} nodes, {net.edges} edges, density {net.density:.3f}, "
          f"clustering {net.clustering:.3f}, busiest broker {net.busiest} "
          f"({net.busiest_share:.1%} of gross); counterparties "
          f"+{drift.counterparty_expansion}/-{drift.counterparty_contraction}")
    print(f"  45/46: {sq.trades} trades, {sq.reordered} needed reordering by contract, "
          f"buyer switch {sq.buyer_switch:.2f}, {sq.repeated_bigrams} repeated adjacent edges")
    print(f"  47: {st.count:,} self-trades, {st.pct_volume:.2%} of volume, on "
          f"{st.session_pct:.0%} of sessions — flagged for review, not a verdict")
    print(f"  48: {len(cyc)} circular candidates at the 0.2% default, "
          f"{len(cycles(w30, pair_map=pm, min_share=0.001))} at 0.1% "
          f"(anomaly candidates, not proof of manipulation)")
    print(f"  49: AnomalyScore {an.score:.2f} from {len(an.components)} components, "
          f"flagged {list(an.flagged) or 'nothing'}; over 40 sessions the score ran "
          f"{min(spread):.2f}-{max(spread):.2f}, worst {worst.date} ({len(worst.flagged)} flags)")
    for c in worst.components[:3]:
        print(f"      - {c.name}: {c.reason}")

    # -- 38 + 39: the market book, and that its cheap path equals the honest one -----
    # Both sections shipped "computed: no" on every symbol until market_book existed, so the
    # invariant that matters is that the once-per-build scan gives the SAME answer as the
    # per-broker function it replaces — otherwise the cheap path is a different metric
    # wearing the same name.
    probe = syms[:12]
    mb = market_book(probe)
    assert mb.symbols > 0, "market book read nothing"
    assert set(mb.windows) == set(WINDOWS)
    aff = affinity(mb.totals(30), mb.books(30))
    assert aff, "no affinity rows from a non-empty book"
    for rows in list(aff.values())[:5]:
        # a broker's shares of its own activity are a partition: they sum to 1 over the
        # symbols scanned, and never above it
        assert abs(sum(r.affinity for r in rows) - 1.0) < 1e-9, "affinity is not a partition"
        assert all(0.0 <= r.percentile <= 1.0 for r in rows)
    # The index must BE the table, not a second computation of it — otherwise section 38
    # silently ships a different number from the one this demo just proved is a partition.
    flat = sorted((a for rows in aff.values() for a in rows), key=lambda a: (a.symbol, a.broker))
    idx = sorted((a for rows in mb.by_symbol.values() for a in rows),
                 key=lambda a: (a.symbol, a.broker))
    assert flat == idx, "MarketBook.by_symbol disagrees with affinity() over the same book"
    for sym, rows in list(mb.by_symbol.items())[:5]:
        assert list(rows) == sorted(rows, key=lambda a: -a.affinity), "by_symbol is not sorted"
        assert all(a.symbol == sym for a in rows)

    busiest = max(mb.totals(30), key=lambda b: mb.totals(30)[b])
    fast, slow = rotation_from_book(busiest, mb), stock_rotation(busiest, list(probe))
    assert fast.focus == slow.focus, (fast.focus, slow.focus)
    assert fast.shares == slow.shares
    assert fast.rotated == slow.rotated
    assert fast.new_focus == slow.new_focus and fast.abandoned == slow.abandoned
    print(f"  38: affinity for {len(aff)} brokers over {mb.symbols} symbols in "
          f"{mb.seconds:.0f}s — each broker's shares sum to 1.0")
    print(f"  39: rotation for broker {busiest} {list(fast.focus.values())}, "
          f"rotated={fast.rotated} — matches the per-broker function exactly")


if __name__ == "__main__":
    _demo()
