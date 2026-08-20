"""Accumulation, distribution, persistence, acceleration, reversal — spec sections 17-21.

Everything here reads one already-sliced ``list[Session]`` (oldest first) and turns
it into a *daily signed flow series*, then describes that series. The description
is the product: counts, runs, slopes and the labels they produce. No score is
returned without the numbers that made it.

Three things this module deliberately does NOT do, each because the project has
already paid for the lesson:

1. **No "is someone accumulating?" boolean.** Measured across this archive, the
   top accumulating broker is net-positive on ~100% of symbol-days. Accumulation
   is the default state of a stock, not an event. So every feature here is about
   MAGNITUDE (share of the stock's own volume), PERSISTENCE (how many days) and
   CHANGE (how the recent window compares with the longer one) — never existence.

2. **No monotone 3D>7D>15D>30D "cascade" requirement.** That ramp was tested and
   is noise out of sample. :func:`acceleration` therefore reads the *ratio* of the
   short window to the long one and reports the intermediate steps for inspection;
   it never demands the four windows be ordered.

3. **No claim of predictive power.** These are descriptive execution-flow
   features. Whether any of them pays is :mod:`swing_quantam.backtest`'s question,
   and the honest prior is that they are weak.

Normalisation, section 14: the series value is always a *share of that day's stock
volume*, never a raw quantity. Raw net flow is not comparable between a 5,000-share
name and a 5,000,000-share one. Even so, **comparing these features across stocks
requires date-demeaning first**: on a strong market day every stock looks
accumulated, so a cross-sectional ranking that skips demeaning is measuring the
date, not the stock.

Naming discipline (section 2) holds: these are broker execution facts. "Broker 58
had net buying for nine straight sessions" is sayable; what any investor intended
is not in this data.

Two stock-level quantities were cut as degenerate before shipping — see
:func:`stock_days` and the note in :func:`accumulation`.
"""

from __future__ import annotations

import statistics
from itertools import accumulate
from typing import NamedTuple, Sequence

from . import brokers
from .brokers import BrokerDay
from .loader import WINDOWS, Session

#: A day is "neutral" when the actor moved less than 0.1% of the stock's volume.
#: Measured, not guessed (NABIL, 120 sessions, 7,848 broker-days): exact-zero net
#: happens on only 0.47% of broker-days, so a zero-width band would make "neutral"
#: never fire and force every dust-sized position to take a side. |flow| p10 is
#: 0.0005 and the median is 0.0064; a 0.001 band marks 16.7% of broker-days
#: neutral, in line with the spec's own 4-in-30 illustration in section 19.
NEUTRAL = 0.001

#: Section 20 phase cut-offs on ratio = short-window mean / long-window mean.
#: Round numbers on purpose, and tunable: nothing here has been fitted, and the
#: backtest is what decides whether the boundaries matter at all.
EXHAUSTED, WEAK, HOT = 0.25, 0.75, 1.25

#: Section 21: a window-to-window drift smaller than this fraction of the larger
#: end is called stable rather than deterioration/improvement. Also unfitted.
SHIFT = 0.25


class FlowDay(NamedTuple):
    """One session of signed, normalised flow for one actor (a broker, or the stock).

    ``flow`` is the canonical series value everything else in this module reads:
    net quantity as a share of that day's stock volume. ``net_qty``/``net_amt`` are
    kept raw for reporting and audit only — never compare them across stocks.
    """

    date: str
    flow: float  # signed net qty / stock volume  (section 14 flow intensity)
    amt_flow: float  # signed net amount / stock turnover
    buy_share: float
    sell_share: float
    quality: float  # |net| / gross for this actor (section 16)
    breadth: float  # stock: net buyers / participating brokers
    breadth_sell: float  # stock: net sellers / participating brokers
    volume: int
    turnover: float
    net_qty: int  # raw — reporting only
    net_amt: float

    def side(self, neutral: float = NEUTRAL) -> str:
        return side_of(self.flow, neutral)


def side_of(flow: float, neutral: float = NEUTRAL) -> str:
    """positive / negative / neutral for one day's normalised flow."""
    if flow > neutral:
        return "positive"
    if flow < -neutral:
        return "negative"
    return "neutral"


# --------------------------------------------------------------------------- #
# The daily series
# --------------------------------------------------------------------------- #

def all_series(sessions: Sequence[Session]) -> dict[int | None, list[FlowDay]]:
    """Every broker's daily flow series over the window, plus the stock's under ``None``.

    One pass: the per-session broker aggregate is the expensive part and is shared.
    A broker that traded on some days of the window gets an explicit zero row on
    the days it did not — otherwise the section 19 day counts would not add up to
    the window length, which is the one invariant that makes persistence readable.
    """
    scanned = [(s, brokers.day(s)) for s in sessions]
    universe = sorted({b for _, agg in scanned for b in agg})

    out: dict[int | None, list[FlowDay]] = {b: [] for b in universe}
    out[None] = []

    for s, agg in scanned:
        flow = brokers.stock_flow(agg)
        vol, to = flow.volume, flow.turnover
        sell_breadth = flow.net_sellers / flow.brokers if flow.brokers else 0.0

        for b in universe:
            bd = agg.get(b) or BrokerDay(b, 0, 0.0, 0, 0, 0, 0.0, 0, 0)
            sh = brokers.shares(bd, flow)
            out[b].append(
                FlowDay(
                    date=s.date,
                    flow=sh["flow_intensity"],
                    amt_flow=sh["turnover_flow_intensity"],
                    buy_share=sh["buy_share"],
                    sell_share=sh["sell_share"],
                    quality=bd.flow_quality,
                    breadth=flow.breadth,
                    breadth_sell=sell_breadth,
                    volume=vol,
                    turnover=to,
                    net_qty=bd.net_qty,
                    net_amt=bd.net_amt,
                )
            )

        # -- the stock's own row, see the docstring of stock_days -------------
        tb = max(agg.values(), key=lambda x: x.net_qty, default=None)
        ts = min(agg.values(), key=lambda x: x.net_qty, default=None)
        net = (tb.net_qty if tb else 0) + (ts.net_qty if ts else 0)
        net_amt = (tb.net_amt if tb else 0.0) + (ts.net_amt if ts else 0.0)
        out[None].append(
            FlowDay(
                date=s.date,
                flow=net / vol if vol else 0.0,
                amt_flow=net_amt / to if to else 0.0,
                buy_share=flow.top_buyer_share,
                sell_share=flow.top_seller_share,
                quality=flow.flow_quality,
                breadth=flow.breadth,
                breadth_sell=sell_breadth,
                volume=vol,
                turnover=to,
                net_qty=net,
                net_amt=net_amt,
            )
        )
    return out


def broker_days(sessions: Sequence[Session], broker: int) -> list[FlowDay]:
    """One broker's daily flow series. Absent days are explicit zero rows."""
    series = all_series(sessions)
    if broker in series:
        return series[broker]
    return [d._replace(flow=0.0, amt_flow=0.0, buy_share=0.0, sell_share=0.0,
                       quality=0.0, net_qty=0, net_amt=0.0) for d in series[None]]


def stock_days(sessions: Sequence[Session]) -> list[FlowDay]:
    """The stock's own daily flow series — top accumulator minus top distributor.

    DEGENERATE METRIC, CUT: the obvious stock-level "net flow" — summing every
    broker's net quantity — is identically 0 on every stock, every day, because
    every share bought is sold (``brokers._demo`` asserts this). It is not small,
    it is exactly zero, so it never became a column of zeros pretending to be a
    signal. The same conservation kills stock-level buy/sell imbalance, which
    :mod:`swing_quantam.brokers` had already cut for the same reason.

    What does vary is how that conserved volume is *distributed*: the largest net
    buyer's share of volume minus the largest net seller's. Measured on NABIL over
    120 sessions this runs mean +0.022, sd 0.151, range -0.441 to +0.556 — genuinely
    two-sided, so "one broker soaked up the day" and "one broker unloaded into it"
    are distinguishable. ``quality`` on these rows carries the stock's net-to-gross
    (section 16) and ``breadth`` the net-buyer count share, for the days where the
    tilt is small because nobody dominated rather than because nothing happened.

    Known floor: on a session with a single trade the lone buyer and lone seller
    are equal and opposite, so the tilt is exactly 0 by construction — PRVUPO
    around 2025-02 is 30 such sessions in a row and its tilt series is flat. That
    is the honest reading rather than a bug, but it means this series carries
    nothing on stocks that thin; check ``volume`` and ``breadth`` before using it.
    """
    return all_series(sessions)[None]


def _flows(days: Sequence[FlowDay] | Sequence[float]) -> list[float]:
    """Accept either a FlowDay series or bare normalised numbers (spec examples)."""
    return [d.flow if isinstance(d, FlowDay) else float(d) for d in days]


# --------------------------------------------------------------------------- #
# Sections 17 & 18 — accumulation and distribution
# --------------------------------------------------------------------------- #

class Side(NamedTuple):
    """One side of the flow — accumulation (section 17) or distribution (section 18).

    Distribution is the mirror image of accumulation, not a separate calculation,
    so both come out of the same code with the sign flipped. Quantities on the
    distribution side are reported as positive magnitudes; ``daily`` keeps the
    signed contributions so ``sum(daily)`` reproduces ``total``.
    """

    label: str  # "accumulation" | "distribution"
    days: int  # sessions on this side
    total_days: int
    daily: tuple[float, ...]  # per-day normalised contribution, 0 off-side
    qty: int  # cumulative shares on this side (raw, this stock only)
    amt: float  # cumulative amount
    total: float  # sum of the daily normalised contributions
    pct: float  # cumulative side qty / window volume — "accumulation percentage"
    intensity: float  # mean normalised size of a side day
    peak: float  # biggest single side day
    consistency: float  # side days / total days
    streak: int  # side days at the end of the window
    max_streak: int
    change: float  # recent-half mean minus older-half mean of the side series
    accel: float  # max(change, 0) — the spec names acceleration separately
    decel: float  # max(-change, 0)
    reversal: bool  # the window ends on the opposite side after a >=2 day run here
    breadth: float  # mean fraction of brokers on this side, on side days
    quality: float  # mean actor net-to-gross on side days

    @property
    def cumulative(self) -> tuple[float, ...]:
        """The running sum — "cumulative accumulation" as a series, for plotting."""
        return tuple(accumulate(self.daily))


def _side(days: Sequence[FlowDay], sign: int, label: str, neutral: float) -> Side:
    want = "positive" if sign > 0 else "negative"
    rows = [d for d in days if side_of(d.flow, neutral) == want]

    daily = tuple(d.flow if side_of(d.flow, neutral) == want else 0.0 for d in days)
    volume = sum(d.volume for d in days)

    streak = 0
    for d in reversed(days):
        if side_of(d.flow, neutral) != want:
            break
        streak += 1

    runs, run = [0], 0
    for d in days:
        run = run + 1 if side_of(d.flow, neutral) == want else 0
        runs.append(run)
    max_streak = max(runs)

    # Section 17's "acceleration/deceleration": is the side heavier in the recent
    # half of this window than the older half? Half-split, not a fitted trend —
    # section 20 does the proper multi-window version.
    half = len(days) // 2
    older = statistics.fmean(daily[:half]) if half else 0.0
    recent = statistics.fmean(daily[half:]) if daily[half:] else 0.0
    change = (recent - older) * sign

    opposite = "negative" if sign > 0 else "positive"
    ended_other = bool(days) and side_of(days[-1].flow, neutral) == opposite
    prior_run = 0
    for d in reversed(days[:-1]):
        if side_of(d.flow, neutral) != want:
            break
        prior_run += 1

    return Side(
        label=label,
        days=len(rows),
        total_days=len(days),
        daily=daily,
        qty=sign * sum(d.net_qty for d in rows),
        amt=sign * sum(d.net_amt for d in rows),
        total=sign * sum(daily),
        pct=(sign * sum(d.net_qty for d in rows) / volume) if volume else 0.0,
        intensity=(sign * statistics.fmean([d.flow for d in rows])) if rows else 0.0,
        peak=(sign * max((d.flow for d in rows), key=abs)) if rows else 0.0,
        consistency=len(rows) / len(days) if days else 0.0,
        streak=streak,
        max_streak=max_streak,
        change=change,
        accel=max(change, 0.0),
        decel=max(-change, 0.0),
        reversal=ended_other and prior_run >= 2,
        breadth=statistics.fmean([d.breadth if sign > 0 else d.breadth_sell for d in rows]) if rows else 0.0,
        quality=statistics.fmean([d.quality for d in rows]) if rows else 0.0,
    )


def accumulation(days: Sequence[FlowDay], neutral: float = NEUTRAL) -> Side:
    """Section 17. The positive side of the series, described.

    Read ``pct``, ``intensity`` and ``consistency`` — the magnitude and how often —
    not ``days > 0``, which is true almost always and says nothing. Across stocks,
    date-demean first.
    """
    return _side(days, +1, "accumulation", neutral)


def distribution(days: Sequence[FlowDay], neutral: float = NEUTRAL) -> Side:
    """Section 18. The negative side, same maths, magnitudes reported positive."""
    return _side(days, -1, "distribution", neutral)


# --------------------------------------------------------------------------- #
# Section 19 — persistence  (CRITICAL FOR SWING TRADING)
# --------------------------------------------------------------------------- #

class Persistence(NamedTuple):
    """Section 19: a one-day flow spike versus flow that keeps showing up.

    ``positive + negative + neutral == days`` always — that is what makes the
    percentages readable, and the demo asserts it.
    """

    window: int
    days: int
    positive: int
    negative: int
    neutral: int
    positive_pct: float
    negative_pct: float
    neutral_pct: float
    max_positive_run: int
    max_negative_run: int
    current_run: int  # signed: + positive days, - negative days, 0 if ending neutral
    flips: int  # sign changes between non-neutral days
    direction: str  # "positive" | "negative" | "flat" — sign of the window's mean
    persistence: float  # dominant-side days / days
    positive_persistence: float  # the spec's 19/30
    negative_persistence: float
    consistency: float  # of non-neutral days, the share agreeing with direction
    stability: float  # 1/(1+cv) on (0,1]; 1 = identical every day, low = spiky
    mean_flow: float
    net_flow: float


def persistence(days: Sequence[FlowDay] | Sequence[float], window: int | None = None,
                neutral: float = NEUTRAL) -> Persistence:
    """Describe how persistent the flow is over the last ``window`` days.

    Persistence is a count, not a claim: 19 positive days in 30 says the flow kept
    coming back, not that it is informative. Note that most brokers print more
    positive than negative days on most stocks, so a bare positive_persistence
    above 0.5 is the norm and only its size relative to this stock's own history
    is worth reading.
    """
    vals = _flows(days)
    if window:
        vals = vals[-window:]
    n = len(vals)
    if not n:
        return Persistence(window or 0, 0, 0, 0, 0, 0.0, 0.0, 0.0, 0, 0, 0, 0,
                           "flat", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    sides = [side_of(v, neutral) for v in vals]
    pos = sides.count("positive")
    neg = sides.count("negative")
    neu = n - pos - neg

    best = {"positive": 0, "negative": 0}
    run = {"positive": 0, "negative": 0}
    for s in sides:
        for k in run:
            run[k] = run[k] + 1 if s == k else 0
            best[k] = max(best[k], run[k])

    last = sides[-1]
    tail = 0
    if last != "neutral":
        for s in reversed(sides):
            if s != last:
                break
            tail += 1
    current = tail if last == "positive" else -tail

    directional = [s for s in sides if s != "neutral"]
    flips = sum(1 for a, b in zip(directional, directional[1:]) if a != b)

    mean = statistics.fmean(vals)
    # side_of(0.0) is "neutral", which at window level reads as no direction at all
    direction = {"positive": "positive", "negative": "negative", "neutral": "flat"}[
        side_of(mean, 0.0)
    ]

    agree = pos if direction == "positive" else neg if direction == "negative" else 0
    sd = statistics.pstdev(vals) if n > 1 else 0.0
    mean_abs = statistics.fmean([abs(v) for v in vals])

    return Persistence(
        window=window or n,
        days=n,
        positive=pos,
        negative=neg,
        neutral=neu,
        positive_pct=pos / n,
        negative_pct=neg / n,
        neutral_pct=neu / n,
        max_positive_run=best["positive"],
        max_negative_run=best["negative"],
        current_run=current,
        flips=flips,
        direction=direction,
        persistence=max(pos, neg) / n,
        positive_persistence=pos / n,
        negative_persistence=neg / n,
        consistency=agree / len(directional) if directional else 0.0,
        # DEGENERATE FORM CUT: the textbook 1 - sd/|mean| clips to exactly 0.0 on
        # 100% of real 30-day windows here (816 broker/stock windows tested on
        # NABIL) — daily flow changes sign, so its sd always exceeds its mean
        # magnitude and the metric is a column of zeros. 1/(1+cv) is the same
        # ordering, cannot clip, and on the same 816 windows spans 0.157-0.489.
        stability=1.0 / (1.0 + sd / mean_abs) if mean_abs else 0.0,
        mean_flow=mean,
        net_flow=sum(vals),
    )


# --------------------------------------------------------------------------- #
# Section 20 — flow acceleration
# --------------------------------------------------------------------------- #

class Accel(NamedTuple):
    """Section 20: 30D → 15D → 7D → 3D, and the phase that falls out of it.

    Window figures are per-day MEANS, not window totals. Totals are not comparable
    across windows of different length — a 30-day total is bigger than a 7-day one
    for no reason but its length — so a "30D +20M vs 7D +3M" comparison of totals
    reads as decay even when the daily rate is flat. Means make the four numbers
    the same unit; ``m3`` vs ``m30`` is then an honest question.
    """

    phase: str  # accelerating | persistent | weakening | exhausted | flat
    direction: str  # accumulation | distribution | flat
    m30: float
    m15: float
    m7: float
    m3: float
    d15: float  # m15 - m30
    d7: float  # m7  - m15
    d3: float  # m3  - m7
    slope: float  # m3 - m30, the whole change in flow rate
    curvature: float  # d3 - d15: are the recent steps bigger than the early ones
    ratio: float  # m3 / m30
    accel: float  # slope in the direction of the long window, when positive
    decel: float  # ... and when negative
    accum_slope: float  # same slope on the positive part of the series only
    dist_slope: float  # ... and on the negative part (more negative = heavier)

    @property
    def label(self) -> str:
        return self.phase if self.direction == "flat" else f"{self.phase} {self.direction}"


def _mean_tail(vals: Sequence[float], w: int) -> float:
    tail = vals[-w:]
    return statistics.fmean(tail) if tail else 0.0


def accel_from_means(m30: float, m15: float, m7: float, m3: float,
                     accum_slope: float = 0.0, dist_slope: float = 0.0,
                     neutral: float = NEUTRAL) -> Accel:
    """The classification alone, given the four window means. Scale-free.

    Kept separate so the spec's worked examples can be checked directly, and so
    the phase never depends on a monotone 30>15>7>3 ramp — that ramp was tested
    on this archive and is noise out of sample.
    """
    direction = {"positive": "accumulation", "negative": "distribution", "neutral": "flat"}[
        side_of(m30, neutral)
    ]
    slope = m3 - m30
    ratio = m3 / m30 if m30 else 0.0

    if direction == "flat":
        phase = "flat"
    elif ratio <= EXHAUSTED:
        phase = "exhausted"
    elif ratio < WEAK:
        phase = "weakening"
    elif ratio <= HOT:
        phase = "persistent"
    else:
        phase = "accelerating"

    sign = 1.0 if m30 > 0 else -1.0 if m30 < 0 else 0.0
    intensifying = slope * sign

    return Accel(
        phase=phase,
        direction=direction,
        m30=m30, m15=m15, m7=m7, m3=m3,
        d15=m15 - m30, d7=m7 - m15, d3=m3 - m7,
        slope=slope,
        curvature=(m3 - m7) - (m15 - m30),
        ratio=ratio,
        accel=max(intensifying, 0.0),
        decel=max(-intensifying, 0.0),
        accum_slope=accum_slope,
        dist_slope=dist_slope,
    )


def acceleration(days: Sequence[FlowDay] | Sequence[float], neutral: float = NEUTRAL) -> Accel:
    """Section 20 over a real series, using :data:`WINDOWS`."""
    vals = _flows(days)
    w3, w7, w15, w30 = WINDOWS
    pos = [max(v, 0.0) for v in vals]
    neg = [min(v, 0.0) for v in vals]
    return accel_from_means(
        _mean_tail(vals, w30), _mean_tail(vals, w15), _mean_tail(vals, w7), _mean_tail(vals, w3),
        accum_slope=_mean_tail(pos, w3) - _mean_tail(pos, w30),
        dist_slope=_mean_tail(neg, w3) - _mean_tail(neg, w30),
        neutral=neutral,
    )


# --------------------------------------------------------------------------- #
# Section 21 — flow reversal
# --------------------------------------------------------------------------- #

class Reversal(NamedTuple):
    """Section 21. A sign flip between windows, plus how big, how fast, how stuck.

    The spec lists "positive → negative" and "accumulation → distribution" as
    separate detections; at flow level they are one event described twice, so
    ``kind`` carries it once and :attr:`regime` renames it in the section's other
    vocabulary. ``pattern`` covers the case the spec's own example is really about:
    flow that is falling hard without having flipped yet.
    """

    kind: str  # positive_to_negative | negative_to_positive | none
    pattern: str  # deterioration | improvement | stable
    flip_at: str  # "7D->3D" etc., or "" when no window boundary flips
    magnitude: float  # m3 - m30, in normalised flow units
    magnitude_pct: float  # ... as a fraction of |m30|
    speed: float  # magnitude per window step (three steps: 30>15>7>3)
    run: int  # trailing days already on the new side
    persistence: float  # run / 3, capped — is the new side sticking
    m30: float
    m15: float
    m7: float
    m3: float

    @property
    def regime(self) -> str:
        return {
            "positive_to_negative": "accumulation_to_distribution",
            "negative_to_positive": "distribution_to_accumulation",
        }.get(self.kind, "none")

    @property
    def flipped(self) -> bool:
        return self.kind != "none"


def reversal_from_means(m30: float, m15: float, m7: float, m3: float, run: int = 0,
                        neutral: float = NEUTRAL) -> Reversal:
    """The section 21 classification from the four window means. Scale-free.

    The spec's worked example — 30D +20, 15D +12, 7D +3, 3D -5 — lands here as
    kind ``positive_to_negative`` flipping at ``7D->3D`` with pattern
    ``deterioration``, whether the four numbers are window totals (as the spec
    writes them) or per-day means.
    """
    sides = [side_of(m, neutral) for m in (m30, m15, m7, m3)]
    names = ("30D", "15D", "7D", "3D")

    start = next((s for s in sides if s != "neutral"), "neutral")
    kind, flip_at = "none", ""
    if start != "neutral":
        for i in range(1, 4):
            if sides[i] != "neutral" and sides[i] != start:
                kind = f"{start}_to_{sides[i]}"
                flip_at = f"{names[i - 1]}->{names[i]}"
                break

    slope = m3 - m30
    scale = max(abs(m30), abs(m3))
    if scale and abs(slope) > SHIFT * scale:
        pattern = "deterioration" if slope < 0 else "improvement"
    else:
        pattern = "stable"

    return Reversal(
        kind=kind,
        pattern=pattern,
        flip_at=flip_at,
        magnitude=slope,
        magnitude_pct=slope / abs(m30) if m30 else 0.0,
        speed=slope / 3.0,
        run=run,
        persistence=min(1.0, run / min(WINDOWS)),
        m30=m30, m15=m15, m7=m7, m3=m3,
    )


def reversal(days: Sequence[FlowDay] | Sequence[float], neutral: float = NEUTRAL) -> Reversal:
    """Section 21 over a real series. ``run`` counts trailing days on the 3D side."""
    vals = _flows(days)
    w3, w7, w15, w30 = WINDOWS
    m3 = _mean_tail(vals, w3)
    now = side_of(m3, neutral)

    run = 0
    if now != "neutral":
        for v in reversed(vals):
            if side_of(v, neutral) != now:
                break
            run += 1

    return reversal_from_means(
        _mean_tail(vals, w30), _mean_tail(vals, w15), _mean_tail(vals, w7), m3,
        run=run, neutral=neutral,
    )


# --------------------------------------------------------------------------- #
# Putting sections 17-21 together
# --------------------------------------------------------------------------- #

class Profile(NamedTuple):
    """Sections 17-21 for one actor over one window, reasons attached."""

    actor: int | None  # broker number, or None for the stock itself
    date: str  # last session in the window — the point-in-time stamp
    days: tuple[FlowDay, ...]
    accumulation: Side
    distribution: Side
    persistence: dict[int, Persistence]  # keyed by window length
    acceleration: Accel
    reversal: Reversal


def profile(days: Sequence[FlowDay], actor: int | None = None,
            neutral: float = NEUTRAL) -> Profile:
    """Everything in sections 17-21 for one already-sliced series."""
    return Profile(
        actor=actor,
        date=days[-1].date if days else "",
        days=tuple(days),
        accumulation=accumulation(days, neutral),
        distribution=distribution(days, neutral),
        persistence={w: persistence(days, w, neutral) for w in WINDOWS},
        acceleration=acceleration(days, neutral),
        reversal=reversal(days, neutral),
    )


class Rank(NamedTuple):
    """One row of the section 17/18 broker ranking, with the numbers behind it."""

    broker: int
    net_share: float  # window net qty / window volume — the ranking key
    net_qty: int
    days: int
    positive_days: int
    streak: int
    phase: str
    persistence: float


def ranking(sessions: Sequence[Session], side: str = "accumulation", limit: int = 10,
            neutral: float = NEUTRAL) -> list[Rank]:
    """Top accumulating (or distributing) brokers over the window.

    Descriptive only. Somebody is always top of this list — a stock always has a
    largest net buyer — so the rank itself carries no information; the numbers
    beside it (share of volume, how many days, the phase) are the content.

    The spec's "top accumulating stocks" is this function run per symbol and sorted
    on ``net_share``. If you do that, **date-demean first**: on a strong market day
    every stock shows accumulation, and an un-demeaned cross-section ranks the day.
    """
    series = all_series(sessions)
    volume = sum(d.volume for d in series[None]) or 1
    sign = 1 if side == "accumulation" else -1

    rows = []
    for b, days in series.items():
        if b is None:
            continue
        net = sum(d.net_qty for d in days)
        p = persistence(days, len(days), neutral)
        a = acceleration(days, neutral)
        rows.append(Rank(
            broker=b,
            net_share=net / volume,
            net_qty=net,
            days=sum(1 for d in days if d.net_qty),
            positive_days=p.positive if sign > 0 else p.negative,
            streak=abs(p.current_run) if (p.current_run > 0) == (sign > 0) else 0,
            phase=a.label,
            persistence=p.positive_persistence if sign > 0 else p.negative_persistence,
        ))

    rows.sort(key=lambda r: sign * r.net_share, reverse=True)
    return rows[:limit]


def _demo() -> None:
    """Self-check on the real archive plus the spec's two worked examples."""
    from . import loader

    syms = loader.symbols()
    sym = "NABIL" if "NABIL" in syms else syms[0]
    dates = loader.sessions(sym)
    assert len(dates) > 1000, f"{sym}: expected the full archive, got {len(dates)} sessions"

    ses = loader.load_last(sym, 30)
    assert len(ses) == 30, f"got {len(ses)} sessions"

    series = all_series(ses)
    sd = series[None]
    assert len(sd) == 30

    # -- section 19's example, exactly as written --------------------------------
    spec19 = [0.02] * 19 + [-0.02] * 7 + [0.0] * 4
    p19 = persistence(spec19, 30)
    assert (p19.positive, p19.negative, p19.neutral) == (19, 7, 4), p19
    assert abs(p19.positive_persistence - 19 / 30) < 1e-12
    assert p19.positive + p19.negative + p19.neutral == p19.days == 30

    # -- section 21's example: totals as written, and as per-day means ------------
    for m in ((20.0, 12.0, 3.0, -5.0), (20 / 30, 12 / 15, 3 / 7, -5 / 3)):
        r = reversal_from_means(*m)
        a = accel_from_means(*m)
        assert r.kind == "positive_to_negative", r
        assert r.regime == "accumulation_to_distribution"
        assert r.pattern == "deterioration", r
        assert r.flip_at == "7D->3D", r
        assert r.magnitude < 0 and r.speed < 0
        assert a.direction == "accumulation" and a.phase == "exhausted", a
        assert a.label == "exhausted accumulation"

    # -- the degeneracy guard: stock net is conserved, the tilt series is not -----
    for d, s in zip(sd, ses):
        agg = brokers.day(s)
        assert sum(b.net_qty for b in agg.values()) == 0, "stock net flow is not zero?"
    spread = statistics.pstdev([d.flow for d in sd])
    assert spread > 0.01, f"stock tilt series is pinned flat (sd {spread:.5f}) — cut it"
    assert min(d.flow for d in sd) < 0 < max(d.flow for d in sd), "tilt never changes sign"

    # Same rule for every derived metric: a constant column is not a feature. The
    # first stability formula tried here (1 - sd/|mean|) read exactly 0.000 on 100%
    # of real windows, which is how it got caught.
    stab = {round(persistence(d, 30).stability, 6) for d in series.values()}
    assert len(stab) > 5 and min(stab) > 0.0, f"stability is pinned: {sorted(stab)[:5]}"

    # -- real-data invariants, stock series and the busiest broker ----------------
    busiest = max((b for b in series if b is not None),
                  key=lambda b: sum(abs(d.net_qty) for d in series[b]))
    for actor in (None, busiest):
        days = series[actor]
        prof = profile(days, actor)

        for w, p in prof.persistence.items():
            assert p.days == min(w, len(days)), (w, p.days)
            assert p.positive + p.negative + p.neutral == p.days
            for frac in (p.positive_pct, p.negative_pct, p.neutral_pct, p.persistence,
                         p.positive_persistence, p.negative_persistence,
                         p.consistency, p.stability):
                assert 0.0 <= frac <= 1.0, (w, frac)
            assert p.max_positive_run <= p.days and p.max_negative_run <= p.days
            assert abs(p.current_run) <= max(p.max_positive_run, p.max_negative_run)

        acc, dis = prof.accumulation, prof.distribution
        # cumulative == sum of daily, and the two sides partition the window
        assert abs(acc.total - sum(acc.daily)) < 1e-12
        assert abs(dis.total + sum(dis.daily)) < 1e-12
        # not ==: sum() is compensated (Neumaier) since 3.12, accumulate() is naive
        assert abs(acc.cumulative[-1] - acc.total) < 1e-12
        assert acc.days + dis.days + sum(
            1 for d in days if d.side() == "neutral") == len(days)
        assert acc.qty == sum(d.net_qty for d in days if d.side() == "positive")
        assert dis.qty == -sum(d.net_qty for d in days if d.side() == "negative")
        assert acc.streak <= acc.max_streak <= len(days)
        assert dis.streak <= dis.max_streak <= len(days)
        assert acc.qty >= 0 and dis.qty >= 0 and acc.intensity >= 0 and dis.intensity >= 0
        assert not (acc.streak and dis.streak), "cannot end on both sides"

        a, r = prof.acceleration, prof.reversal
        assert a.phase in ("accelerating", "persistent", "weakening", "exhausted", "flat")
        assert abs(a.slope - (a.m3 - a.m30)) < 1e-12
        assert abs((a.d15 + a.d7 + a.d3) - a.slope) < 1e-12, "steps must telescope"
        assert 0.0 <= r.persistence <= 1.0 and r.run <= len(days)
        assert (a.accel > 0) != (a.decel > 0) or a.slope == 0.0

    top = ranking(ses, "accumulation", 3)
    assert top and top[0].net_share >= top[-1].net_share
    assert all(-1.0 <= t.net_share <= 1.0 for t in top)

    prof = profile(sd, None)
    p30 = prof.persistence[30]
    t = top[0]
    print(f"flow ok — {sym} 30D to {prof.date} ({len(dates)} sessions from {dates[0]})")
    print(f"  stock tilt: {p30.positive}+/{p30.negative}-/{p30.neutral}0 days, "
          f"persistence {p30.positive_persistence:.2f}, stability {p30.stability:.2f}, "
          f"{prof.acceleration.label} (30D {prof.acceleration.m30:+.4f} -> 3D "
          f"{prof.acceleration.m3:+.4f}), reversal {prof.reversal.kind}/{prof.reversal.pattern}")
    print(f"  accumulation {prof.accumulation.pct:+.2%} of volume over "
          f"{prof.accumulation.days}d (max streak {prof.accumulation.max_streak}), "
          f"distribution {prof.distribution.pct:.2%} over {prof.distribution.days}d")
    print(f"  top accumulator: broker {t.broker} {t.net_share:+.2%} of window volume, "
          f"{t.positive_days}/30 positive days, {t.phase}")


if __name__ == "__main__":
    _demo()
