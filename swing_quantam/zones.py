"""Buy/sell zones, entry and exit types, and the three scores — spec sections 82-92, 110-113.

This is the module that turns everything the package measured into an actionable
map: *where* is the stock worth buying, *where* is it worth taking profit, and
*below what price does the floorsheet story stop making sense*. Sections 89 and
113 are the two rules that shape every line of code here.

**Section 113 — a zone is a RANGE, never a point.** There is no ``BUY = 700`` in
this module and there is no way to ask for one: every zone is a
:class:`Zone` with a ``low`` and a ``high``, and both edges are read off the
*executed price distribution* — a volume-weighted quantile of the prices this
stock actually traded at over the lookback. No round numbers, no "support at 700
because 700 is a round number", no fixed percentage bands. Because the quantiles
return an actual traded rate rather than an interpolated one, every zone edge is
a price at which shares genuinely changed hands (``Zones.repaired`` says when a
degenerate distribution forced an exception — see :func:`_ladder`).

**Section 89 — "these numbers must be calculated from actual data; never invent
them."** The whole ladder comes from two measured distributions:

* *volume at price* (:func:`swing_quantam.features.profile`) — where the market
  has been willing to trade; and
* *net absorption at price* (:func:`_flow_by_price`) — at which prices the
  brokers who ended the lookback net-long did their net buying.

The second one is the reason this module exists at all rather than being a
technical-analysis pivot calculator. It is also where the conservation trap that
has already killed five metrics in this package bites again, so read this before
extending it:

    At any single price level, the net quantity absorbed by the window's net
    buyers is EXACTLY the negative of the net quantity shed by the window's net
    sellers. Every broker is on one side of the window or the other, and every
    share has a buyer and a seller, so ``net_acc(p) + net_dis(p) == 0`` at every
    p, on every stock, on every day. Measured on real bands before it was cut:
    NABIL's ``netdis`` column was ``-netacc`` to the last digit in all eight
    price bands. So "where did the accumulators accumulate" and "where did the
    distributors distribute" are the SAME series with a sign flip. It ships
    once, as :attr:`Band.tilt`, and there is deliberately no distributor mirror.

    What is genuinely two-sided is the *gross* side share: the fraction of a
    band's volume bought by window net-buyers (:attr:`Band.demand`) and the
    fraction sold by window net-sellers (:attr:`Band.supply`). Different broker
    subsets, different sides of the tape, nothing conserved between them — on
    real 30-session windows they run r = -0.29 (NABIL) to -0.92 (UPPER) and both
    move. ``supply`` is what places the distribution-risk edge.

**Section 88 — the invalidation zone is not a stop loss.** Floorsheet data cannot
guarantee a stop level; it does not contain a single resting order, only fills.
What it *can* say is below which price the evidence for the thesis runs out, and
that is what :func:`_invalidation` returns, with the measured evidence attached:
the share of net absorption that happened below the line, what the tilt is down
there versus inside the zone, and whether seller supply share rises. A percentage
stop would be an invented number and section 88 explicitly prefers this instead.

**Sections 90-92 — the scores are RANKING MEASURES, NOT PROBABILITIES.** Section
90 says in as many words: *do not hard-code weights before validation*. So the
weights live in :data:`ENTRY_WEIGHTS`, :data:`EXIT_WEIGHTS` and
:data:`SWING_WEIGHTS`, they are documented as provisional and unvalidated, every
one of them is overridable per call, and every score ships its full component
breakdown so a reader can re-weight by hand. Calibrating them against forward
outcomes is :mod:`swing_quantam.backtest`'s job and nothing here should be read
as an edge claim. The honest prior for this archive is that most of it is noise:
six floorsheet operator families have already been tested and died out of sample,
and the only broker metric with out-of-sample support is **net churn** (the sum of
every broker's positive net over total bought — decile-monotone forward 5-day
return, D1 -0.40% to D10 +0.35%). It is included as the ``net_churn`` component
and it is the same quantity as ``brokers.StockFlow.flow_quality``.

Component normalisation avoids invented constants the same way: anything with a
natural [0, 1] range (breadth, consensus, HHI, persistence, side shares) is used
as-is, and anything unbounded is converted by :func:`_rank01` into its percentile
*within this stock's own lookback history* of the same quantity. There is no
"strong accumulation means above 0.05" anywhere in this file.

Section 90 lists *Historical Setup Performance* as a component. It is
deliberately ABSENT — measuring how setups like this one have paid requires
forward returns, which is exactly the look-ahead this package is built to avoid
inside a point-in-time feature module. ``backtest.py`` supplies it later.

Point-in-time by construction: :func:`zones` takes ``upto`` and reads only
sessions at or before it, or takes a pre-sliced ``history`` so a walk-forward
loop can load once and slice cheaply. Pure stdlib.
"""

from __future__ import annotations

import statistics
from typing import NamedTuple, Sequence

from . import brokers, features, flow, network, structure
from .loader import WINDOWS, Session, load_last

#: Sessions of executed history the price structure is read from. The flow
#: features that drive the SCORES stay on the package's 3/7/15/30D decision
#: windows; the ZONES need a longer look because a 30-session range on a liquid
#: NEPSE name is often under 12% wide, which produces profit targets too tight to
#: be a swing. This is a lookback length, not a fitted parameter.
LOOKBACK = 120

#: A session-over-session VWAP jump this large is treated as a corporate action
#: rather than a market move, and the lookback is trimmed to start after it.
#: NEPSE's circuit is +/-15%, so a 20% gap in the executed rate is a restatement
#: (bonus/rights/split), and profiling across one would put half the price
#: distribution at a price that no longer exists. Detection is best-effort and
#: says so in ``warnings``: small bonus ratios hide inside the circuit.
SPLIT_GAP = 0.20

#: How many equal-width price bands :attr:`Zones.bands` reports. Presentation
#: only — every zone edge comes from the raw traded levels, not from these bands.
BANDS = 8

#: Section 110's vocabulary, exactly. Nothing else may be returned as a signal.
SIGNALS = (
    "STRONG BUY ZONE",
    "BUY ZONE",
    "WATCH / BUILDING",
    "NEUTRAL",
    "HOLD / MONITOR",
    "DISTRIBUTION WATCH",
    "SELL / REDUCE ZONE",
    "STRONG EXIT / INVALIDATION",
)

# ---------------------------------------------------------------------------
# PROVISIONAL WEIGHTS — NOT VALIDATED, NOT CALIBRATED, NOT PROBABILITIES
# ---------------------------------------------------------------------------
#
# Section 90: "Do not hard-code weights before validation. Weights should be
# optimized or selected through training/validation without leakage." These
# dictionaries are therefore a STARTING POINT for backtest.py to replace, not an
# answer. They were chosen by one rule and no fitting whatsoever:
#
#   * ``net_churn`` carries the largest single weight in every score, because it
#     is the only component in this file with out-of-sample support on this
#     archive (volume_spike.py:102).
#   * everything else is spread almost evenly, so that no un-validated component
#     can dominate the ranking. Where the spec groups several items into one
#     concept (four timeframe alignments, section 90) they share one weight
#     between them rather than out-voting the rest by arriving four times.
#   * penalties are negative and are capped, in aggregate, well below the
#     positive mass, so a single contradiction cannot flip a score on its own.
#
# Anyone tempted to tune these by hand on full-sample data should read
# MEMORY.md first: full-sample ranking of this universe is itself fake alpha.

ENTRY_WEIGHTS: dict[str, float] = {
    "net_churn": 2.0,        # section 91 flow quality — the one OOS-supported input
    "accumulation": 1.0,     # 30D flow tilt, ranked against this stock's own history
    "breadth": 1.0,          # section 23 net-buyer share of participating brokers
    "consensus": 1.0,        # section 22 ACTIVITY-WEIGHTED vote — see the registry note;
                             # reading the headcount vote here made this a duplicate of
                             # ``breadth`` on 481/481 symbols, doubling breadth to 2.0
    "participation": 1.0,    # section 33 daily roster stability
    "persistence": 1.0,      # section 19 positive-day share
    "large_conviction": 1.0,  # section 28 net share inside the large-trade subset
    "price_zone": 1.0,       # how favourable today's price is against the zone ladder
    "acceptance": 1.0,       # 3D volume executed inside the accumulation/confirmation band
    "alignment": 1.0,        # section 74-81 sign agreement across 3/7/15/30D
    "freshness": 0.5,        # section 81 — how new the current run is
    "contradiction": -1.5,   # section 91's explicit penalty term
}

EXIT_WEIGHTS: dict[str, float] = {
    "distribution": 1.0,       # section 18, ranked against this stock's own history
    "negative_flow": 1.0,      # 3D tilt turned negative
    "seller_breadth": 1.0,     # section 23, mirrored
    "sell_concentration": 1.0,  # section 24 top-seller share of sell volume
    "large_sell": 1.0,         # section 28 largest seller inside the large-trade subset
    "vwap_deterioration": 1.0,  # section 9, 3D VWAP against 30D
    "flow_reversal": 1.0,      # section 21
    "price_extension": 1.0,    # section 86 — how stretched price is against its own base
}

SWING_WEIGHTS: dict[str, float] = {
    "net_churn": 2.0,
    "accumulation": 1.0,
    "flow_stability": 1.0,    # section 19 stability — spiky flow is not a swing setup
    "persistence": 1.0,
    "breadth": 1.0,
    "consensus": 1.0,        # activity-weighted, not headcount — see ENTRY_WEIGHTS
    "participation": 1.0,
    "large_conviction": 1.0,
    "vwap_structure": 1.0,    # where volume sits in the range (section 10)
    "volume_profile": 1.0,    # how tightly the range is used (section 10)
    "price_flow": 1.0,        # tilt measured at the price the stock is trading at
    "alignment": 1.0,
    "freshness": 0.5,
    "concentration": -1.0,    # section 24: a one-broker move is a risk, not a thesis
    "contradiction": -1.5,
}

#: Score cut-offs for the section 110 labels. PROVISIONAL, and quantiles of a
#: MEASURED census rather than round numbers somebody liked the look of.
#:
#: Census: 45 randomly drawn symbols with at least 200 sessions of floorsheet,
#: four decision dates each (offsets 0/30/60/90 sessions), 180 symbol-dates, run
#: 2026-08-21. The realised distributions were::
#:
#:     entry  min 26.6  p25 38.4  p50 45.4  p75 53.5  p90 58.2  max 74.3
#:     exit   min 23.5  p25 37.4  p50 45.8  p75 53.5  p90 64.3  max 77.3
#:     swing  min 33.1  p25 46.4  p50 50.3  p75 55.9  p90 59.2  max 72.3
#:
#: so ``strong_entry`` is the top decile of entry scores, ``exit`` the top
#: quartile of exit scores, ``hold`` the median. The first cut of this file
#: guessed ``strong_entry = 65`` and STRONG BUY ZONE then fired on 2 of 180
#: observations — a label that reads correctly, tests green and effectively never
#: happens is the exact failure mode MEMORY.md's "unreachable mandatory rules"
#: note is about, and a market-wide census is what caught it.
#:
#: These are quantiles of a RANKING measure. They are not calibrated against
#: forward outcomes and must not be read as probabilities; backtest.py is what
#: decides whether any of the labels separates anything.
SIGNAL_CUTS: dict[str, float] = {
    "strong_entry": 58.0,  # ~p90 of the measured entry-score distribution
    "entry": 50.0,         # ~p68
    "exit": 53.5,          # p75 of the measured exit-score distribution
    "hold": 45.5,          # ~p50 of entry
}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _rank01(value: float, sample: Sequence[float]) -> float:
    """Percentile of ``value`` inside ``sample``, in [0, 1]. 0.5 when unknowable.

    This is how every unbounded quantity becomes a score component: against this
    stock's OWN history of the same quantity, never against a constant somebody
    picked. A stock whose flow tilt is always +0.30 scores 0.5 on a +0.30 day,
    which is the honest reading.
    """
    if len(sample) < 5:
        return 0.5
    below = sum(1 for s in sample if s < value)
    ties = sum(1 for s in sample if s == value)
    return (below + 0.5 * ties) / len(sample)


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _roll(vals: Sequence[float], w: int) -> list[float]:
    """Every rolling mean of width ``w``. The sample :func:`_rank01` ranks against."""
    return [statistics.fmean(vals[i : i + w]) for i in range(len(vals) - w + 1)] if len(vals) >= w else []


def _wq(levels: Sequence[float], weight: dict[float, float], q: float) -> float:
    """Weighted quantile of a price distribution — an ACTUALLY TRADED price.

    Deliberately NOT interpolated. Interpolating between two traded rates invents
    a price that never printed, and section 113 wants zones built from executed
    prices. The cost is that a coarse distribution returns coarse edges, which is
    the truth about a stock that only trades at six levels.
    """
    if not levels:
        return 0.0
    total = sum(weight.get(p, 0.0) for p in levels)
    if total <= 0:
        return float(levels[len(levels) // 2])
    target = q * total
    acc = 0.0
    for p in levels:
        acc += weight.get(p, 0.0)
        if acc >= target:
            return float(p)
    return float(levels[-1])


def _tick(levels: Sequence[float]) -> float:
    """The stock's own typical gap between traded rates, for tie separation.

    Median step rather than an assumed 0.10 NEPSE tick: the tick varies by price
    band and this way a 4,000-rupee name and a 12-rupee one both get a sane
    separation. Falls back to a hundredth of the price only when there is
    literally one traded level, where any answer is arbitrary.
    """
    steps = [b - a for a, b in zip(levels, levels[1:]) if b > a]
    if steps:
        return statistics.median(steps)
    return max(0.01, (levels[0] if levels else 1.0) * 0.001)


def _ladder(vals: Sequence[float], tick: float, lo: float, hi: float) -> tuple[list[float], bool]:
    """Force a non-decreasing boundary vector to be strictly increasing, in range.

    The quantiles come out ordered already; what they do NOT come out is
    *distinct*, because a stock that traded at four prices in six months will
    return the same rate for the 25th and the 75th percentile. Rather than emit
    a zone ladder with five identical edges, the ties are separated by one of the
    stock's own ticks and the whole ladder is shifted back down if that pushed it
    past the highest traded price.

    Returns ``(values, repaired)``. ``repaired`` is True when any edge moved,
    which is exactly the case where an edge may no longer be a traded price —
    :attr:`Zones.repaired` passes that on rather than hiding it.
    """
    out: list[float] = []
    prev: float | None = None
    for v in vals:
        v = max(v, lo) if prev is None else max(v, prev + tick)
        out.append(v)
        prev = v
    if out and out[-1] > hi:
        shift = out[-1] - hi
        out = [max(lo, v - shift) for v in out]
    repaired = any(abs(a - b) > 1e-9 for a, b in zip(vals, out))
    return out, repaired


# ---------------------------------------------------------------------------
# 84 / 88. the executed-price distribution, with flow attached to each price
# ---------------------------------------------------------------------------


class Band(NamedTuple):
    """One price band of the lookback, with who was doing what at that price.

    ``tilt`` is signed net absorption as a share of the band's volume: positive
    means the brokers who finished the lookback net-long were net BUYERS at this
    price. There is no distributor mirror of this field and there must never be
    one — it is identically ``-tilt`` (see the module docstring).

    ``demand`` and ``supply`` are the non-degenerate pair: gross buy share of the
    window's net buyers, and gross sell share of the window's net sellers. They
    are different broker sets on different sides of the tape and both vary.
    """

    low: float
    high: float
    volume: int
    volume_share: float
    tilt: float  # signed net absorption / band volume
    demand: float  # band volume bought by window net-buyers / band volume
    supply: float  # band volume sold by window net-sellers / band volume
    churn: float  # net churn inside the band — the OOS-supported flow-quality measure
    brokers: int


class PriceFlow(NamedTuple):
    """The whole flow-by-price relationship for one lookback."""

    levels: tuple[float, ...]  # every traded rate, ascending
    volume: dict[float, float]
    absorb: dict[float, float]  # max(0, net absorbed) per level — the accumulation weight
    tilt: dict[float, float]  # signed net absorption / volume, per level
    demand: dict[float, float]
    supply: dict[float, float]
    bands: tuple[Band, ...]
    accumulators: int
    distributors: int
    total_absorbed: float


def _flow_by_price(sessions: Sequence[Session], bands: int = BANDS) -> PriceFlow:
    """Section 84's "broker-flow concentration" and "price-flow divergence".

    Brokers are classified ONCE over the whole lookback (net long / net short) and
    that label is then applied to every one of their prints. Classifying per band
    would be circular — a broker is a net buyer in a band precisely because it
    bought there.
    """
    agg = brokers.window(sessions)
    acc = {b for b, x in agg.items() if x.net_qty > 0}
    dis = {b for b, x in agg.items() if x.net_qty < 0}

    vol: dict[float, float] = {}
    net: dict[float, float] = {}
    dem: dict[float, float] = {}
    sup: dict[float, float] = {}
    for s in sessions:
        for t in s.trades:
            p = round(t.rate, 2)
            q = t.quantity
            vol[p] = vol.get(p, 0.0) + q
            n = 0.0
            if t.buyer in acc:
                n += q
                dem[p] = dem.get(p, 0.0) + q
            if t.seller in acc:
                n -= q
            if t.seller in dis:
                sup[p] = sup.get(p, 0.0) + q
            net[p] = net.get(p, 0.0) + n

    levels = tuple(sorted(vol))
    absorb = {p: max(0.0, net.get(p, 0.0)) for p in levels}
    tilt = {p: (net.get(p, 0.0) / vol[p]) if vol[p] else 0.0 for p in levels}
    demand = {p: (dem.get(p, 0.0) / vol[p]) if vol[p] else 0.0 for p in levels}
    supply = {p: (sup.get(p, 0.0) / vol[p]) if vol[p] else 0.0 for p in levels}

    total_vol = sum(vol.values())
    lo, hi = (levels[0], levels[-1]) if levels else (0.0, 0.0)
    out: list[Band] = []
    if levels and total_vol > 0:
        width = (hi - lo) / bands if hi > lo else 0.0
        # Half-open bands, closed at the very top, so the shares partition the
        # volume exactly — the demo asserts they sum to 1. The level -> band map
        # is built ONCE and the trades are bucketed in a single pass: doing the
        # membership test per trade per band is what turned this function into a
        # minute-long call on a 120-session liquid name.
        n_bands = bands if width else 1
        where: dict[float, int] = {}
        for p in levels:
            i = min(int((p - lo) / width), n_bands - 1) if width else 0
            where[p] = i
        by_band: list[list] = [[] for _ in range(n_bands)]
        for s in sessions:
            for t in s.trades:
                by_band[where[round(t.rate, 2)]].append(t)

        for i in range(n_bands):
            inside = [p for p in levels if where[p] == i]
            if not inside:
                continue
            bv = sum(vol[p] for p in inside)
            if bv <= 0:
                continue
            sub = brokers.stock_flow(brokers.from_trades(by_band[i]))
            out.append(
                Band(
                    low=(lo + width * i) if width else lo,
                    high=(lo + width * (i + 1)) if width else hi,
                    volume=int(bv),
                    volume_share=bv / total_vol,
                    tilt=sum(net.get(p, 0.0) for p in inside) / bv,
                    demand=sum(dem.get(p, 0.0) for p in inside) / bv,
                    supply=sum(sup.get(p, 0.0) for p in inside) / bv,
                    churn=sub.flow_quality,
                    brokers=sub.brokers,
                )
            )

    return PriceFlow(
        levels=levels,
        volume=vol,
        absorb=absorb,
        tilt=tilt,
        demand=demand,
        supply=supply,
        bands=tuple(out),
        accumulators=len(acc),
        distributors=len(dis),
        total_absorbed=sum(absorb.values()),
    )


# ---------------------------------------------------------------------------
# the zones themselves
# ---------------------------------------------------------------------------


class Zone(NamedTuple):
    """A price RANGE and the measurement that produced it. Never a point.

    ``open_ended`` renders the two zones the spec writes one-sided: the
    invalidation zone as ``<690`` and the distribution-risk zone as ``770+``.
    Both still carry a real low and a real high so that every consumer can treat
    every zone identically.
    """

    name: str
    low: float
    high: float
    basis: str  # the measurement this range came from, in words
    open_ended: str = ""  # "" | "below" | "above"

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2.0

    @property
    def width(self) -> float:
        return self.high - self.low

    @property
    def text(self) -> str:
        if self.open_ended == "below":
            return f"<{self.high:,.2f}"
        if self.open_ended == "above":
            return f"{self.low:,.2f}+"
        return f"{self.low:,.2f}-{self.high:,.2f}"

    def holds(self, price: float) -> bool:
        return self.low <= price <= self.high


class Condition(NamedTuple):
    """One qualifying test behind an entry type, with the number that decided it."""

    name: str
    passed: bool
    value: float
    note: str


class Entry(NamedTuple):
    """One of section 83's three entry types.

    ``qualified`` means every condition passed. The conditions are returned
    whether it qualified or not, because "which one thing is missing" is the
    useful output when it did not.
    """

    kind: str  # aggressive | confirmed | conservative
    qualified: bool
    zone: Zone
    conditions: tuple[Condition, ...]

    @property
    def passed(self) -> int:
        return sum(1 for c in self.conditions if c.passed)


class Exit(NamedTuple):
    """One of section 87's five exit types, as a distinguishable label."""

    kind: str  # take_profit | flow | distribution | momentum | structural
    triggered: bool
    note: str


class Component(NamedTuple):
    """One scored input: its normalised value, its weight, what it contributed."""

    name: str
    value: float  # always in [0, 1]
    weight: float
    contribution: float  # value * weight
    note: str


class Score(NamedTuple):
    """A 0-100 RANKING MEASURE. Not a probability, not calibrated (section 114).

    ``score`` is the weighted component mean rescaled so that "every positive
    component at 0 and every penalty at 1" is 0 and the reverse is 100. Comparing
    two symbols' scores is a ranking statement and nothing more until
    :mod:`swing_quantam.backtest` has calibrated it against forward outcomes.
    """

    name: str
    score: float
    components: tuple[Component, ...]
    weights_are_provisional: bool = True

    @property
    def top(self) -> tuple[Component, ...]:
        return tuple(sorted(self.components, key=lambda c: -abs(c.contribution)))


class Zones(NamedTuple):
    """The complete section 89 entry -> exit map for one symbol at one date."""

    symbol: str
    date: str
    sessions: int  # lookback sessions actually used
    price: float  # last session's VWAP — the floorsheet's own "price now"

    accumulation: Zone
    entry: Zone  # primary / confirmed entry
    confirmation: Zone
    profit1: Zone  # partial profit
    profit2: Zone  # major profit
    distribution: Zone  # distribution risk, open-ended above
    invalidation: Zone  # floorsheet thesis invalidation, open-ended below

    entries: tuple[Entry, ...]
    exits: tuple[Exit, ...]

    entry_score: Score
    exit_score: Score
    swing_score: Score

    signal: str
    confidence: str  # high | medium | low — DATA sufficiency, never conviction
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]

    bands: tuple[Band, ...]
    repaired: bool  # a degenerate price distribution forced tie-separation

    @property
    def ladder(self) -> tuple[Zone, ...]:
        """The seven zones bottom-up — the order section 89 prints them in."""
        return (
            self.invalidation,
            self.accumulation,
            self.entry,
            self.confirmation,
            self.profit1,
            self.profit2,
            self.distribution,
        )

    def text(self) -> str:
        """The section 89 map, as text."""
        rows = [f"{self.symbol}  ({self.date}, last VWAP {self.price:,.2f})"]
        for z in (
            self.accumulation,
            self.entry,
            self.confirmation,
            self.profit1,
            self.profit2,
            self.distribution,
            self.invalidation,
        ):
            rows.append(f"  {z.name:<24}{z.text}")
        rows.append(f"  {'signal':<24}{self.signal}  (entry {self.entry_score.score:.0f}, "
                    f"exit {self.exit_score.score:.0f}, swing {self.swing_score.score:.0f}; "
                    f"ranking measures, not probabilities)")
        return "\n".join(rows)


# ---------------------------------------------------------------------------


def _trim_at_split(ses: list[Session]) -> tuple[list[Session], str]:
    """Drop everything before the newest suspected corporate action.

    Profiling executed rates across a bonus/rights date puts half the price
    distribution at a price that no longer exists, and every zone derived from it
    would be wrong by the adjustment ratio. Detection is by the session-over-
    session VWAP gap: NEPSE's circuit is +/-15%, so a jump past
    :data:`SPLIT_GAP` is a restatement rather than a move. Small bonus ratios
    hide inside the circuit and are NOT caught — hence the warning text rather
    than a claim of correctness.
    """
    for i in range(len(ses) - 1, 0, -1):
        a, b = ses[i - 1].vwap, ses[i].vwap
        if a > 0 and b > 0 and abs(b / a - 1.0) > SPLIT_GAP:
            return ses[i:], (
                f"lookback trimmed to {len(ses) - i} sessions from {ses[i].date}: the executed "
                f"rate gapped {b / a - 1.0:+.0%} overnight, which is past NEPSE's +/-15% circuit "
                f"and so is most likely a corporate action, not a move"
            )
    return ses, ""


def _invalidation(pf: PriceFlow, lo: float) -> tuple[float, str]:
    """Section 88 — where the floorsheet evidence for the thesis runs out.

    NOT a stop loss and not a percentage. The line is the 10th percentile of the
    *net absorption* distribution: below it, 90% of everything the lookback's net
    buyers actually absorbed sits above you, so "they are supporting this price"
    has no evidence left underneath. The returned note carries the measured
    comparison — absorption share, tilt below versus inside, and whether net
    seller supply share rises down there — because section 88 asks for the
    conjunction (accumulation disappears + selling breadth increases + flow
    reverses), not for a single threshold.

    Base rate, measured rather than assumed: on the 180 symbol-date census behind
    :data:`SIGNAL_CUTS`, 35% of observations were already trading BELOW their own
    invalidation line. That is high, and it is not a bug — the median NEPSE
    20-day return is negative, so a stock sitting under the price band where its
    own six-month net absorption happened is a common state, not an exotic one.
    Anyone treating "invalidated" as rare is misreading this market.
    """
    if not pf.levels:
        return lo, "no executed prices"
    cut = _wq(pf.levels, pf.absorb, 0.10)

    below = [p for p in pf.levels if p < cut]
    above = [p for p in pf.levels if p >= cut]
    vb = sum(pf.volume[p] for p in below)
    va = sum(pf.volume[p] for p in above)
    ab = sum(pf.absorb[p] for p in below)
    share = ab / pf.total_absorbed if pf.total_absorbed else 0.0
    tilt_b = (sum(pf.tilt[p] * pf.volume[p] for p in below) / vb) if vb else 0.0
    tilt_a = (sum(pf.tilt[p] * pf.volume[p] for p in above) / va) if va else 0.0
    sup_b = (sum(pf.supply[p] * pf.volume[p] for p in below) / vb) if vb else 0.0
    sup_a = (sum(pf.supply[p] * pf.volume[p] for p in above) / va) if va else 0.0

    note = (
        f"below {cut:,.2f} only {share:.1%} of the lookback's net absorption took place; "
        f"net tilt there is {tilt_b:+.3f} against {tilt_a:+.3f} above it and net-seller supply "
        f"share is {sup_b:.2f} against {sup_a:.2f}. Floorsheet data cannot set a stop — this is "
        f"where the evidence stops, not a guaranteed exit price"
    )
    return cut, note


def _score(name: str, comps: Sequence[Component]) -> Score:
    """Rescale a weighted component sum onto 0-100. Penalties are negative weights."""
    if not comps:
        return Score(name, 0.0, ())
    hi = sum(c.weight for c in comps if c.weight > 0)
    low = sum(c.weight for c in comps if c.weight < 0)
    raw = sum(c.contribution for c in comps)
    span = hi - low
    val = 100.0 * (raw - low) / span if span else 0.0
    return Score(name, _clamp01(val / 100.0) * 100.0, tuple(comps))


def _pick(reg: dict[str, tuple[float, str]], weights: dict[str, float]) -> list[Component]:
    out = []
    for key, w in weights.items():
        if key not in reg:
            continue
        v, note = reg[key]
        v = _clamp01(v)
        out.append(Component(key, v, w, v * w, note))
    return out


# ---------------------------------------------------------------------------


def zones(
    symbol: str,
    upto: str | None = None,
    *,
    history: Sequence[Session] | None = None,
    lookback: int = LOOKBACK,
    entry_weights: dict[str, float] | None = None,
    exit_weights: dict[str, float] | None = None,
    swing_weights: dict[str, float] | None = None,
) -> Zones:
    """The full section 89 entry -> exit map for ``symbol`` as of ``upto``.

    ``history`` lets a walk-forward loop load the archive once and hand over an
    already-sliced, oldest-first list instead of paying the read again per date;
    it MUST already be filtered to ``<= upto`` — this function does not re-check
    what it was handed, exactly like every other window function in the package.

    Raises ValueError only when the symbol has no floorsheet sessions at all
    (a typo). Everything thinner than that degrades: a stock that traded at one
    price on one session comes back NEUTRAL, with collapsed zones, low
    confidence and the reason why in ``warnings``.
    """
    ses = list(history) if history is not None else load_last(symbol, lookback, upto=upto)
    if not ses:
        raise ValueError(f"{symbol}: no floorsheet sessions at or before {upto or 'today'}")

    warnings: list[str] = []
    ses, trimmed = _trim_at_split(ses)
    if trimmed:
        warnings.append(trimmed)

    date = ses[-1].date
    price = ses[-1].vwap
    prof = features.profile(ses)
    pf = _flow_by_price(ses)
    lo, hi = prof.low, prof.high
    tick = _tick(pf.levels)

    # -- the ladder, every edge a weighted quantile of executed prices ---------
    # Accumulation, entry: quantiles of NET ABSORPTION (section 84 — value is
    # where the net buyers actually took stock, not merely where volume traded).
    weight = pf.absorb if pf.total_absorbed > 0 else pf.volume
    if pf.total_absorbed <= 0:
        warnings.append("no net absorption in the lookback; the accumulation zone falls back to raw volume at price")

    inval_hi, inval_note = _invalidation(pf, lo)
    acc_lo = _wq(pf.levels, weight, 0.25)
    ent_lo = _wq(pf.levels, weight, 0.40)
    ent_hi = _wq(pf.levels, weight, 0.60)
    acc_hi = _wq(pf.levels, weight, 0.75)

    # Above the accumulation band the relevant structure is where volume was
    # actually accepted (section 86: "high-volume price zones"), so the upper
    # ladder is quantiles of the volume profile RESTRICTED to prices above the
    # accumulation zone — a sub-distribution, so it always has room to spread.
    upper = [p for p in pf.levels if p > acc_hi] or [p for p in pf.levels if p >= acc_hi]
    u = lambda q: _wq(upper, pf.volume, q)  # noqa: E731 - used six times, one line

    # The distribution edge is the lowest BAND above the accumulation zone where
    # net-seller supply share runs above its own lookback average — section 85's
    # "seller breadth" and "price extension" expressed in price rather than time.
    #
    # Bands, not raw price levels: supply share at a single level is noise (one
    # 100-share print at a lonely rate reads 1.00), so scanning levels would
    # return the first rate above the zone every single time and the whole term
    # would be inert. The 1% volume floor is a noise gate, not a signal threshold.
    all_vol = sum(pf.volume.values()) or 1.0
    mean_supply = sum(pf.supply[p] * pf.volume[p] for p in pf.levels) / all_vol
    heavy = [b for b in pf.bands if b.high > acc_hi and b.supply > mean_supply and b.volume_share >= 0.01]
    supply_edge = max(acc_hi, min(b.low for b in heavy)) if heavy else u(0.80)

    raw = [
        lo,
        inval_hi,
        acc_lo,
        ent_lo,
        ent_hi,
        acc_hi,
        u(0.25),
        u(0.45),
        u(0.70),
        # the supply edge moves this boundary, bounded so it cannot cross its
        # neighbours and turn the ladder into a relabelling exercise
        min(max(u(0.75), supply_edge), u(0.90)),
        u(0.95),
    ]
    raw = sorted(raw)  # quantiles are monotone; supply_edge is the one that can jump
    fixed, repaired = _ladder(raw, tick, lo, hi)
    (i_lo, i_hi, a_lo, e_lo, e_hi, a_hi, c_hi, p1_lo, p1_hi, p2_lo, p2_hi) = fixed
    if repaired:
        warnings.append(
            f"only {prof.levels} distinct traded price{'s' if prof.levels != 1 else ''} in "
            f"{len(ses)} sessions: zone edges had to be separated by one tick ({tick:,.2f}) "
            f"and are no longer all executed prices"
        )

    acc_share = sum(pf.absorb[p] for p in pf.levels if a_lo <= p <= a_hi)
    acc_share = acc_share / pf.total_absorbed if pf.total_absorbed else 0.0

    Z = Zone
    z_inval = Z("Invalidation", i_lo, i_hi, inval_note, "below")
    z_acc = Z("Accumulation Zone", a_lo, a_hi,
              f"interquartile band of net absorption: {acc_share:.0%} of the lookback's net "
              f"absorbed shares changed hands inside it, POC {prof.poc:,.2f}", "")
    z_ent = Z("Primary Entry", e_lo, e_hi,
              f"core of that absorption (40th-60th percentile), 30D VWAP {features.vwap_set(ses)['30d']:,.2f}", "")
    z_conf = Z("Confirmation Entry", a_hi, c_hi,
               "first quartile of volume accepted ABOVE the accumulation zone — section 83C's "
               "price acceptance above a high-volume zone", "")
    z_p1 = Z("Partial Profit Zone", p1_lo, p1_hi,
             "mid of the volume distribution above the zone: the nearest shelf where supply has "
             "historically been available", "")
    z_p2 = Z("Major Profit Zone", p2_lo, p2_hi,
             "upper tail of the executed distribution above the zone", "")
    z_dist = Z("Distribution Risk", p2_lo, hi,
               f"net-seller supply share runs above its lookback mean of {mean_supply:.2f} from "
               f"{supply_edge:,.2f} up", "above")

    # -- the flow features that drive the SCORES, on the decision windows ------
    w3, w7, w15, w30 = WINDOWS
    recent = ses[-w30:]
    tilt_days = flow.stock_days(ses)  # whole lookback: the sample the scores rank against
    tilt_vals = [d.flow for d in tilt_days]
    acc_flow = flow.acceleration(tilt_days)
    rev = flow.reversal(tilt_days)
    pers = flow.persistence(tilt_days, w30)
    side_acc = flow.accumulation(tilt_days[-w30:])
    side_dis = flow.distribution(tilt_days[-w30:])

    agg30 = brokers.window(recent)
    sf30 = brokers.stock_flow(agg30)
    net_churn = sf30.flow_quality  # == volume_spike.py's net_churn, the OOS-supported one
    cons = structure.consensus(agg30)
    brd = structure.breadth(agg30)
    part = network.participation(recent)

    st = structure.analyse(recent, baseline=ses)
    lv30 = st.large_value[w30]
    lv3 = st.large_value[w3]
    conc3 = st.concentration[w3]

    # -- rank samples: this stock's own history of the same quantity -----------
    vwaps = [s.vwap for s in ses]
    vols = [float(s.volume) for s in ses]
    tilt30 = _roll(tilt_vals, w30)
    v3, v30 = _roll(vwaps, w3), _roll(vwaps, w30)
    det_sample = [(b - a) / b for a, b in zip(v3[-len(v30):], v30) if b] if v30 else []
    ext_sample = [(a - b) / b for a, b in zip(vwaps[-len(v30):], v30) if b] if v30 else []
    vol_sample = [a / b for a, b in zip(_roll(vols, w3)[-len(_roll(vols, w30)):], _roll(vols, w30)) if b]

    vwap3 = statistics.fmean(vwaps[-w3:]) if vwaps else 0.0
    vwap30 = statistics.fmean(vwaps[-w30:]) if vwaps else 0.0
    deterioration = (vwap30 - vwap3) / vwap30 if vwap30 else 0.0
    extension = (price - vwap30) / vwap30 if vwap30 else 0.0
    vol_ratio = (statistics.fmean(vols[-w3:]) / statistics.fmean(vols[-w30:])) if vols and statistics.fmean(vols[-w30:]) else 0.0

    means = (acc_flow.m3, acc_flow.m7, acc_flow.m15, acc_flow.m30)
    align = sum(1 for m in means if m > 0) / 4.0
    recent3 = ses[-w3:]
    vol3 = sum(s.volume for s in recent3) or 1
    in_zone = sum(t.quantity for s in recent3 for t in s.trades if a_lo <= t.rate <= c_hi) / vol3
    above_zone = sum(t.quantity for s in recent3 for t in s.trades if t.rate > a_hi) / vol3

    # How much of the ladder's downside room is left, measured against the ladder
    # itself rather than a percentage. Zero once price is BELOW the invalidation
    # line — otherwise "cheaper is always better" would score a broken thesis
    # highest, which is the trap section 88 exists to avoid.
    if price < i_hi or p1_lo <= i_hi:
        price_zone_v = 0.0 if price < i_hi else 0.5
    else:
        price_zone_v = _clamp01((p1_lo - price) / (p1_lo - i_hi))

    # tilt measured where the stock is actually trading right now (section 84's
    # price-flow divergence: strong flow at a price nobody is trading is not a setup)
    near = [p for p in pf.levels if abs(p - price) <= max(tick * 3, price * 0.005)]
    nv = sum(pf.volume[p] for p in near)
    tilt_here = (sum(pf.tilt[p] * pf.volume[p] for p in near) / nv) if nv else 0.0

    # -- contradictions (section 91's explicit penalty term) -------------------
    # The reason must read the SAME series the predicate does. `conc_dynamics` was
    # repointed to non-overlapping blocks, so quoting the nested 30D->3D HHI pair beside
    # `spike` described a comparison that no longer decides anything. hhi_series can be
    # empty (every block filtered out), and a list literal evaluates every f-string
    # eagerly, so the index needs a guard rather than a bare [0]/[-1].
    _cd = st.conc_dynamics
    _cd_pair = (f"{_cd.hhi_series[0][1]:.3f} -> {_cd.hhi_series[-1][1]:.3f}"
                if _cd.hhi_series else "n/a")
    checks = [
        (side_dis.change > 0, f"distribution is heavier in the recent half of the window ({side_dis.change:+.4f})"),
        (_cd.spike, f"broker concentration spiked across non-overlapping 3-session blocks: HHI {_cd_pair}"),
        (st.breadth_trend.trend == "narrowing", f"buying breadth is narrowing ({st.breadth_trend.momentum:+.2f})"),
        (rev.kind == "positive_to_negative", f"flow flipped positive->negative at {rev.flip_at or 'window level'}"),
        (price > prof.value_high, f"price {price:,.2f} is above the value area high {prof.value_high:,.2f}"),
        (lv3.top_seller_share > lv3.top_buyer_share, f"the biggest large-trade actor is a seller ({lv3.top_seller_share:.1%} vs {lv3.top_buyer_share:.1%})"),
        (part.growth < 0, f"broker participation is contracting ({part.growth:+.1%})"),
    ]
    fired = [note for ok, note in checks if ok]
    contradiction = len(fired) / len(checks)

    # -- the component registry: every value already in [0, 1] ----------------
    # DO NOT ADD ``cons.strength`` HERE. Section 22's strength is sum(|net_b|) / gross,
    # and by conservation sum(|net_b|) == 2 * sum(positive net) while gross == 2 * volume,
    # so it reduces to pos / volume — which is ``StockFlow.flow_quality``, which is
    # ``net_churn`` on the line below. Measured on 105 symbols: |strength - flow_quality|
    # == 0.0 exactly, 105/105. Wiring it in would make net_churn's effective weight 3.0
    # and repeat the breadth/consensus mistake documented further down. Audited 2026-08-21:
    # nothing in this file reads it today, and it must stay that way.
    reg: dict[str, tuple[float, str]] = {
        "net_churn": (net_churn, f"net churn {net_churn:.3f} — {sf30.net_qty:,} of {sf30.volume:,} shares changed hands net over {len(recent)} sessions"),
        "accumulation": (_rank01(acc_flow.m30, tilt30), f"30D flow tilt {acc_flow.m30:+.4f}, {_rank01(acc_flow.m30, tilt30):.0%} of this stock's own {len(tilt30)} rolling windows"),
        "breadth": (brd.pct, f"{brd.net_buyers} net buyers vs {brd.net_sellers} net sellers ({brd.pct:.0%})"),
        # ``consensus`` USED TO READ ``(cons.consensus + 1) / 2`` AND WAS A BIT-FOR-BIT
        # DUPLICATE OF ``breadth``. Section 22's headcount vote is
        # (nb - ns) / (nb + ns) (structure.py:248) so (consensus + 1) / 2 == nb / (nb + ns),
        # which is precisely Breadth.pct (structure.py:302) — the same algebra, twice.
        # A 481-symbol audit found the two components identical on 481/481 symbols, and a
        # 105-symbol resample confirmed it (max |difference| 1.1e-16, 93 bit-identical, the
        # rest float rounding). Effect: ``breadth`` was silently carrying weight 2.0 — equal
        # to ``net_churn``, the ONLY component with out-of-sample support and the one
        # deliberately given the largest weight. That design was defeated by an accident.
        #
        # Fixed by option (a): point this at ``weighted_consensus``, the activity-weighted
        # sign vote (structure.py:231) that was already computed and unused here. Chosen
        # over option (b) — deleting the component — because it MEASURED independent, not
        # because it reads independent: on 105 symbols across four turnover tiers
        # (median Rs 14.6m to Rs 816m) against ``breadth`` it runs pearson +0.084,
        # spearman +0.139, and regressing it on breadth leaves residual sd 0.0923 against
        # its own sd 0.0926 — breadth explains 0.7% of its variance. Range -0.695..+0.681
        # raw (0.152..0.840 normalised), zero exact ties, mean |gap| 0.116. So it is a
        # different reading, not another rescaling: headcount breadth counts brokers,
        # this one weights each broker's vote by the share of gross volume it traded, so
        # ten idle brokers outvote one dominant one in ``breadth`` and lose here.
        # timeframes.py:979 already pairs these two the same way.
        "consensus": ((cons.weighted_consensus + 1.0) / 2.0,
                      f"activity-weighted broker consensus {cons.weighted_consensus:+.2f} "
                      f"(headcount vote {cons.consensus:+.2f}), {cons.accumulation} accumulation"),
        "participation": (part.breadth, f"{part.active} brokers active, {part.per_session_mean:.0f} per session ({part.breadth:.0%} roster stability)"),
        "persistence": (pers.positive_pct, f"positive tilt on {pers.positive} of {pers.days} sessions"),
        "flow_stability": (pers.stability, f"flow stability {pers.stability:.2f} (1.0 = identical every session)"),
        "large_conviction": (lv30.net_share, f"{lv30.net_share:.1%} of large-trade volume changed hands net between brokers"),
        "price_zone": (price_zone_v,
                       f"price {price:,.2f} against invalidation {i_hi:,.2f}, entry {e_lo:,.2f}-{e_hi:,.2f} "
                       f"and first profit zone {p1_lo:,.2f}"),
        "acceptance": (in_zone, f"{in_zone:.0%} of the last {len(recent3)} sessions' volume executed inside {a_lo:,.2f}-{c_hi:,.2f}"),
        "alignment": (align, f"{int(align * 4)} of 4 windows positive (3D {acc_flow.m3:+.4f}, 7D {acc_flow.m7:+.4f}, 15D {acc_flow.m15:+.4f}, 30D {acc_flow.m30:+.4f})"),
        "freshness": (_clamp01(max(0, pers.current_run) / w3), f"current positive run {max(0, pers.current_run)} sessions"),
        "vwap_structure": (prof.vwap_position, f"VWAP sits {prof.vwap_position:.0%} up the lookback range"),
        "volume_profile": (1.0 - prof.range_utilisation, f"value area is {prof.range_utilisation:.0%} of the range"),
        "price_flow": ((tilt_here + 1.0) / 2.0, f"net tilt at the current price is {tilt_here:+.3f} on {nv:,.0f} shares"),
        "concentration": (conc3.broker.hhi, f"3D broker HHI {conc3.broker.hhi:.3f} on Rs {conc3.turnover / 1e6:.0f}m — largely a liquidity proxy, read it with the turnover"),
        "contradiction": (contradiction, f"{len(fired)} of {len(checks)} contradiction checks fired"),
        "distribution": (_rank01(-acc_flow.m30, [-x for x in tilt30]), f"30D tilt {acc_flow.m30:+.4f} ranked on the negative side"),
        "negative_flow": (_rank01(-acc_flow.m3, [-x for x in _roll(tilt_vals, w3)]), f"3D tilt {acc_flow.m3:+.4f}"),
        "seller_breadth": (1.0 - brd.pct, f"{brd.net_sellers} of {brd.brokers} brokers net sellers"),
        "sell_concentration": (conc3.sell.top1, f"the largest seller did {conc3.sell.top1:.0%} of 3D sell volume"),
        "large_sell": (lv3.top_seller_share, f"largest large-trade seller took {lv3.top_seller_share:.0%} of that volume"),
        "vwap_deterioration": (_rank01(deterioration, det_sample), f"3D VWAP {vwap3:,.2f} against 30D {vwap30:,.2f} ({-deterioration:+.1%})"),
        "flow_reversal": (1.0 if rev.kind == "positive_to_negative" else 0.5 if rev.pattern == "deterioration" else 0.0,
                          f"reversal {rev.kind or 'none'} / {rev.pattern}"),
        "price_extension": (_rank01(extension, ext_sample), f"price {extension:+.1%} against its own 30D VWAP"),
    }

    es = _score("entry", _pick(reg, entry_weights or ENTRY_WEIGHTS))
    xs = _score("exit", _pick(reg, exit_weights or EXIT_WEIGHTS))
    ss = _score("swing", _pick(reg, swing_weights or SWING_WEIGHTS))

    # -- section 83's three entry types ---------------------------------------
    vol_rank = _rank01(vol_ratio, vol_sample)
    entries = (
        Entry("aggressive", False, z_acc, (
            Condition("3D flow positive", acc_flow.m3 > 0, acc_flow.m3, f"3D tilt {acc_flow.m3:+.4f}"),
            Condition("7D flow positive", acc_flow.m7 > 0, acc_flow.m7, f"7D tilt {acc_flow.m7:+.4f}"),
            Condition("15D flow positive", acc_flow.m15 > 0, acc_flow.m15, f"15D tilt {acc_flow.m15:+.4f}"),
            Condition("price at or below the accumulation zone", price <= a_hi, price, f"price {price:,.2f} vs zone high {a_hi:,.2f}"),
        )),
        Entry("confirmed", False, z_ent, (
            Condition("positive flow", acc_flow.m7 > 0, acc_flow.m7, f"7D tilt {acc_flow.m7:+.4f}"),
            Condition("positive breadth", brd.pct > 0.5, brd.pct, f"{brd.net_buyers}B/{brd.net_sellers}S = {brd.pct:.0%}"),
            Condition("reasonable VWAP position", price <= prof.value_high, price, f"price {price:,.2f} vs value high {prof.value_high:,.2f}"),
            Condition("supporting volume", vol_ratio >= 1.0, vol_ratio, f"3D volume {vol_ratio:.2f}x the 30D average"),
            Condition("multi-timeframe alignment", align >= 0.75, align, f"{int(align * 4)} of 4 windows positive"),
        )),
        Entry("conservative", False, z_conf, (
            Condition("accumulation", acc_flow.m30 > 0, acc_flow.m30, f"30D tilt {acc_flow.m30:+.4f}"),
            Condition("volume expansion", vol_rank > 0.5, vol_rank, f"3D/30D volume {vol_ratio:.2f}x, {vol_rank:.0%} of this stock's own history"),
            Condition("broker breadth expansion", st.breadth_trend.momentum > 0, st.breadth_trend.momentum, f"breadth momentum {st.breadth_trend.momentum:+.2f} ({st.breadth_trend.trend})"),
            Condition("price acceptance above the high-volume zone", above_zone >= 0.5, above_zone, f"{above_zone:.0%} of 3D volume executed above {a_hi:,.2f}"),
        )),
    )
    entries = tuple(e._replace(qualified=all(c.passed for c in e.conditions)) for e in entries)

    # -- section 87's five exit types -----------------------------------------
    exits = (
        Exit("take_profit", price >= p1_lo,
             f"price {price:,.2f} against the partial profit zone {p1_lo:,.2f}-{p1_hi:,.2f}"),
        Exit("flow", acc_flow.m3 <= 0 < acc_flow.m30 or rev.kind == "positive_to_negative",
             f"3D tilt {acc_flow.m3:+.4f} against 30D {acc_flow.m30:+.4f}, reversal {rev.kind or 'none'}"),
        Exit("distribution", conc3.sell.top1 >= conc3.buy.top1 and side_dis.streak >= 2,
             f"top seller holds {conc3.sell.top1:.0%} of 3D sell volume (top buyer {conc3.buy.top1:.0%}), "
             f"{side_dis.streak} straight distribution sessions"),
        Exit("momentum", acc_flow.phase in ("weakening", "exhausted") and acc_flow.direction == "accumulation",
             f"flow is {acc_flow.label} (3D/30D ratio {acc_flow.ratio:.2f})"),
        Exit("structural", price < i_hi or (brd.pct < 0.5 and part.growth < 0),
             f"price {price:,.2f} against invalidation {i_hi:,.2f}; breadth {brd.pct:.0%}, "
             f"participation {part.growth:+.1%}"),
    )

    # -- confidence is DATA SUFFICIENCY, never conviction ---------------------
    quality = statistics.fmean([s.quality.score for s in ses])
    if len(ses) >= w30 and prof.levels >= 20 and prof.trades >= 500 and quality >= 90:
        confidence = "high"
    elif len(ses) >= w7 and prof.levels >= 5:
        confidence = "medium"
    else:
        confidence = "low"

    # -- section 110/111/112: the signal --------------------------------------
    cuts = SIGNAL_CUTS
    n_exits = sum(1 for x in exits if x.triggered and x.kind != "take_profit")
    if confidence == "low":
        # Fewer than a week of sessions or fewer than five traded prices: there
        # is no executed distribution to place zones in, so every edge above has
        # collapsed onto one price and any directional label would be an artefact
        # of that collapse rather than a reading of the tape. ADBLB (one session,
        # one price) otherwise came back "SELL / REDUCE ZONE", which is worse
        # than useless — it looks like a call.
        signal = "NEUTRAL"
    elif price < i_hi:
        signal = "STRONG EXIT / INVALIDATION"
    elif price >= p2_lo:
        signal = "SELL / REDUCE ZONE" if (xs.score >= cuts["exit"] or n_exits >= 2) else "DISTRIBUTION WATCH"
    elif price <= a_hi and price >= i_hi:
        if es.score >= cuts["strong_entry"] and xs.score < cuts["exit"] and z_ent.holds(price):
            signal = "STRONG BUY ZONE"
        elif es.score >= cuts["entry"] and xs.score < cuts["exit"]:
            signal = "BUY ZONE"
        elif xs.score >= cuts["exit"]:
            signal = "DISTRIBUTION WATCH"
        else:
            signal = "NEUTRAL"
    else:  # above the accumulation zone, below the distribution-risk edge
        if xs.score >= cuts["exit"] or n_exits >= 3:
            signal = "SELL / REDUCE ZONE"
        elif es.score >= cuts["hold"]:
            signal = "HOLD / MONITOR"
        elif es.score >= cuts["entry"] - 10.0:
            signal = "WATCH / BUILDING"
        else:
            signal = "NEUTRAL"
    assert signal in SIGNALS  # section 110 vocabulary is closed

    if confidence == "low":
        warnings.append(
            f"low data sufficiency ({len(ses)} sessions, {prof.levels} traded prices): the zone "
            f"ladder has collapsed and the signal is forced to NEUTRAL rather than guessing"
        )
    if len(ses) < w30:
        warnings.append(f"only {len(ses)} sessions of history: the 30D windows are short-window figures wearing a 30D name")
    if prof.trades < 100:
        warnings.append(f"{prof.trades} executed trades in the whole lookback — every zone here is drawn from very little")
    if quality < 90:
        warnings.append(f"mean floorsheet quality {quality:.0f}/100 across the lookback")
    warnings.extend(fired)

    reasons = [
        f"{z_acc.name} {z_acc.text}: {z_acc.basis}",
        f"{z_inval.name} {z_inval.text}: {inval_note}",
        f"net churn {net_churn:.3f} over {len(recent)} sessions — the one broker metric with "
        f"out-of-sample support on this archive, and it is weak (D1 -0.40% to D10 +0.35% forward 5D)",
    ]
    reasons += [c.note for c in es.top[:4] if c.weight > 0 and c.value >= 0.5]
    qualified = [e.kind for e in entries if e.qualified]
    if qualified:
        reasons.append(f"entry types qualified: {', '.join(qualified)}")
    triggered = [x.kind for x in exits if x.triggered]
    if triggered:
        reasons.append(f"exit types triggered: {', '.join(triggered)}")

    return Zones(
        symbol=ses[-1].symbol,
        date=date,
        sessions=len(ses),
        price=price,
        accumulation=z_acc,
        entry=z_ent,
        confirmation=z_conf,
        profit1=z_p1,
        profit2=z_p2,
        distribution=z_dist,
        invalidation=z_inval,
        entries=entries,
        exits=exits,
        entry_score=es,
        exit_score=xs,
        swing_score=ss,
        signal=signal,
        confidence=confidence,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        bands=pf.bands,
        repaired=repaired,
    )


# ---------------------------------------------------------------------------


def _demo() -> None:
    """Self-check on the real archive: invariants, graceful degradation, degeneracy.

    The degeneracy sweep is not padding. Five metrics have already been cut from
    this package for being pinned to a constant, and a zone engine is an easy
    place to ship a sixth — quantiles of a distribution that barely moves would
    give the same "695-710" every session and look authoritative doing it. So
    every headline output is measured across ~20 real sessions and several
    symbols and must actually vary.
    """
    from . import loader

    syms = loader.symbols()
    assert syms, "no floorsheet archive"
    sym = "NABIL" if "NABIL" in syms else syms[0]

    dates = loader.sessions(sym)
    assert len(dates) > 1000, f"{sym}: expected the full archive, got {len(dates)}"
    hist = loader.load_last(sym, LOOKBACK)
    z = zones(sym, history=hist)

    # -- every zone is a RANGE, and the ladder is ordered (sections 89, 113) ---
    for zz in z.ladder:
        assert zz.low <= zz.high, f"{zz.name} is inverted: {zz.low} > {zz.high}"
        assert "-" in zz.text or zz.text.startswith("<") or zz.text.endswith("+")
        assert zz.basis, f"{zz.name} has no stated basis"
    order = [
        ("invalidation", z.invalidation.high),
        ("accumulation", z.accumulation.low),
        ("entry", z.entry.low),
        ("profit1", z.profit1.low),
        ("profit2", z.profit2.low),
    ]
    for (na, a), (nb, b) in zip(order, order[1:]):
        assert a <= b, f"ladder out of order: {na} {a} > {nb} {b}"
    assert z.invalidation.high < z.accumulation.low <= z.entry.low < z.profit1.low < z.profit2.low, \
        "a liquid symbol must produce a strictly increasing ladder"
    assert z.accumulation.low <= z.entry.low <= z.entry.high <= z.accumulation.high
    assert z.confirmation.low == z.accumulation.high
    assert z.distribution.low == z.profit2.low

    # -- every zone price is a price this stock actually traded at ------------
    prof = features.profile(hist)
    traded = {round(t.rate, 2) for s in hist for t in s.trades}
    for zz in z.ladder:
        assert prof.low <= zz.low <= prof.high, f"{zz.name} low {zz.low} outside {prof.low}-{prof.high}"
        assert prof.low <= zz.high <= prof.high, f"{zz.name} high {zz.high} outside {prof.low}-{prof.high}"
    if not z.repaired:
        for zz in z.ladder:
            assert round(zz.low, 2) in traded and round(zz.high, 2) in traded, \
                f"{zz.name} edge is not an executed price"

    # -- scores are 0-100 and carry their components (sections 90-92) ---------
    for sc in (z.entry_score, z.exit_score, z.swing_score):
        assert 0.0 <= sc.score <= 100.0, f"{sc.name} = {sc.score}"
        assert sc.components, f"{sc.name} has no component breakdown"
        assert sc.weights_are_provisional, "the weights must never advertise themselves as validated"
        for c in sc.components:
            assert 0.0 <= c.value <= 1.0, f"{sc.name}.{c.name} = {c.value} outside [0, 1]"
            assert abs(c.contribution - c.value * c.weight) < 1e-12
            assert c.note, f"{sc.name}.{c.name} has no note"
    # ``breadth`` and ``consensus`` must not be the same number. A duplicate fails none of
    # the checks above — it just silently doubles one weight, which is how ``consensus``
    # rode along as a second copy of ``breadth`` on 481/481 symbols until an audit caught
    # it. Several symbols, because a chance tie on one would hide the regression.
    for sym_ in [s for s in (sym, "UPPER", "SHIVM", "NICA") if s in syms]:
        vals = {c.name: c.value for c in zones(sym_).entry_score.components}
        assert abs(vals["breadth"] - vals["consensus"]) > 1e-9, (
            f"{sym_}: breadth and consensus are both {vals['breadth']!r} — consensus is "
            f"back to being a rescaled headcount vote and breadth is weighted twice"
        )

    # every weight key must resolve to a real component, or a weight silently does nothing
    for name, w in (("entry", ENTRY_WEIGHTS), ("exit", EXIT_WEIGHTS), ("swing", SWING_WEIGHTS)):
        got = {c.name for c in {"entry": z.entry_score, "exit": z.exit_score, "swing": z.swing_score}[name].components}
        assert got == set(w), f"{name} weights {set(w) - got} matched no component"
    # weights really are overridable, and really do change the ranking
    alt = zones(sym, history=hist, entry_weights={"net_churn": 1.0})
    assert abs(alt.entry_score.score - z.entry_score.score) > 1e-9 or len(z.entry_score.components) == 1
    assert len(alt.entry_score.components) == 1

    # -- section 110 vocabulary, reasons, entry/exit types --------------------
    assert z.signal in SIGNALS
    assert z.reasons, "a score without reasons is the black box the spec forbids"
    assert z.confidence in ("high", "medium", "low")
    assert {e.kind for e in z.entries} == {"aggressive", "confirmed", "conservative"}
    assert {x.kind for x in z.exits} == {"take_profit", "flow", "distribution", "momentum", "structural"}
    for e in z.entries:
        assert e.conditions and all(c.note for c in e.conditions)
        assert e.qualified == all(c.passed for c in e.conditions)
    for x in z.exits:
        assert x.note

    # -- the conservation trap: net tilt by price has NO distributor mirror ----
    pf = _flow_by_price(hist[-30:])
    for b in pf.bands:
        assert -1.0 <= b.tilt <= 1.0 and 0.0 <= b.demand <= 1.0 and 0.0 <= b.supply <= 1.0
    assert abs(sum(b.volume_share for b in pf.bands) - 1.0) < 1e-9
    # AT EVERY PRICE, accumulator net == -distributor net. This is why Band has
    # one signed tilt and no distributor mirror; if anyone adds one, this fails.
    agg = brokers.window(hist[-30:])
    acc = {b for b, x in agg.items() if x.net_qty > 0}
    dis = {b for b, x in agg.items() if x.net_qty < 0}
    for b in pf.bands:
        sel = [t for s in hist[-30:] for t in s.trades if b.low <= t.rate <= b.high]
        if not sel:
            continue
        na = sum(t.quantity for t in sel if t.buyer in acc) - sum(t.quantity for t in sel if t.seller in acc)
        nd = sum(t.quantity for t in sel if t.buyer in dis) - sum(t.quantity for t in sel if t.seller in dis)
        assert na + nd == 0, f"band {b.low}-{b.high}: net is not conserved, so the mirror is real after all"
    # ...and the gross side shares are NOT mirrors of each other: different broker
    # subsets on different sides of the tape, both free to move.
    if len(pf.bands) > 3:
        assert statistics.pstdev([b.demand for b in pf.bands]) > 1e-6, "demand share is pinned"
        assert statistics.pstdev([b.supply for b in pf.bands]) > 1e-6, "supply share is pinned"

    # -- degeneracy sweep across ~20 real sessions ----------------------------
    long_hist = loader.load_last(sym, LOOKBACK + 25)
    series: dict[str, list[float]] = {k: [] for k in
                                      ("entry", "exit", "swing", "entry_lo", "inval", "profit1", "acc_width")}
    signals: list[str] = []
    for i in range(20):
        window_ = long_hist[: len(long_hist) - i]
        zi = zones(sym, history=window_[-LOOKBACK:])
        series["entry"].append(zi.entry_score.score)
        series["exit"].append(zi.exit_score.score)
        series["swing"].append(zi.swing_score.score)
        series["entry_lo"].append(zi.entry.low)
        series["inval"].append(zi.invalidation.high)
        series["profit1"].append(zi.profit1.low)
        series["acc_width"].append(zi.accumulation.width)
        signals.append(zi.signal)
    for name, vals in series.items():
        assert statistics.pstdev(vals) > 1e-9, f"{name} is constant across 20 real sessions — degenerate"

    # -- several symbols, including illiquid ones, must not crash -------------
    census: dict[str, int] = {}
    picked = [s for s in ("NABIL", "UPPER", "SHIVM", "HDL", "NICA") if s in syms][:5]
    for other in picked:
        zo = zones(other)
        assert zo.signal in SIGNALS and zo.reasons
        census[zo.signal] = census.get(zo.signal, 0) + 1
        for zz in zo.ladder:
            assert zz.low <= zz.high

    thin = [s for s in ("NICAP", "PRVUPO", "ADBLB") if s in syms]
    assert thin, "expected at least one thin symbol to stress"
    for t in thin:
        zt = zones(t)
        assert zt.signal in SIGNALS
        assert zt.confidence in ("low", "medium", "high")
        assert zt.warnings, f"{t} is thin and must say so"
        # A collapsed ladder must never dress itself up as a call.
        if zt.confidence == "low":
            assert zt.signal == "NEUTRAL", f"{t}: {zt.signal} off a collapsed zone ladder"
        for zz in zt.ladder:
            assert zz.low <= zz.high, f"{t}: {zz.name} inverted"
        for a, b in zip(zt.ladder, zt.ladder[1:]):
            assert a.low <= b.low + 1e-9, f"{t}: ladder out of order at {b.name}"

    try:
        zones("NOTASYMBOL")
    except ValueError:
        pass
    else:
        raise AssertionError("an unknown symbol must raise, not return a made-up ladder")

    # -- signals must not be pinned to one label across the archive -----------
    # Deep history, not just the last 20 sessions: a signal that only ever fires
    # in one market regime is the "unreachable mandatory rule" failure this repo
    # has hit before, and 20 adjacent sessions would not show it.
    deep = loader.load_last(sym, LOOKBACK + 400)
    for i in range(0, 400, 40):
        w_ = deep[: len(deep) - i]
        if len(w_) < 30:
            break
        s_ = zones(sym, history=w_[-LOOKBACK:]).signal
        census[s_] = census.get(s_, 0) + 1
    for s_ in signals:
        census[s_] = census.get(s_, 0) + 1
    assert len(census) >= 2, f"only one signal ever fires: {census}"

    print(f"zones ok — {sym} {z.date}: {z.sessions} sessions, {prof.levels} traded levels, "
          f"{z.confidence} confidence, {len(z.reasons)} reasons, {len(z.warnings)} warnings")
    print(z.text())
    b = max(z.bands, key=lambda x: x.volume_share)
    print(f"  heaviest band {b.low:,.2f}-{b.high:,.2f}: {b.volume_share:.0%} of volume, tilt {b.tilt:+.3f}, "
          f"demand {b.demand:.2f}, supply {b.supply:.2f}, churn {b.churn:.3f}")
    types = ", ".join(f"{e.kind} {e.passed}/{len(e.conditions)}{'*' if e.qualified else ''}" for e in z.entries)
    fired_exits = ", ".join(x.kind for x in z.exits if x.triggered) or "none"
    print(f"  entry types: {types} | exits triggered: {fired_exits}")
    print(f"  signal census over the sweep: {census}")
    print(f"  scores span entry {min(series['entry']):.0f}-{max(series['entry']):.0f}, "
          f"exit {min(series['exit']):.0f}-{max(series['exit']):.0f}, "
          f"swing {min(series['swing']):.0f}-{max(series['swing']):.0f} over 20 sessions "
          f"(ranking measures — backtest.py decides whether any of it pays)")


if __name__ == "__main__":
    _demo()
