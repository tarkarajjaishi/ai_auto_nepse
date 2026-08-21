"""Multi-timeframe alignment, conflict, freshness, decay and evidence — spec sections 74-81.

Everything above this module produces *one window's* view of a stock. This one puts
the four decision windows side by side and answers the questions that only exist
once you have all four: do they agree, do they disagree, how old is the signal, is
it fading, and what evidence actually stands behind it.

Four rules are structural here, not stylistic:

1. **3D / 7D / 15D / 30D are the only decision windows** (section 74). Longer history
   is read for *baselines* — percentile cut-offs, large-trade thresholds — and never
   turned into a fifth opinion. :data:`~swing_quantam.loader.WINDOWS` is the list.

2. **A conflicted setup is never collapsed into a direction** (section 77). When the
   short windows disagree with the long ones the headline is
   ``"short-term bullish / higher-window conflict"``. It is not "bullish". For the
   same reason :attr:`Alignment.direction` reports ``mixed`` the moment both a
   bullish and a bearish window exist — the signed :attr:`Alignment.weighted` number
   is still there for anyone who wants it, but no label launders it into agreement.

3. **Contradictions are first-class output** (section 81). :func:`evidence` returns
   the dimensions that argue *against* the thesis in the same shape, with the same
   detail, as the ones that argue for it, plus a net-after-contradictions figure.
   Nothing is filtered out for being inconvenient.

4. **No black-box scores.** Every score ships the :class:`Reason` list that produced
   it: the raw measurement, its normalised value, its weight and its contribution.
   ``score`` and ``sum(r.contribution for r in reasons)`` are the same number, and
   ``_demo`` asserts it.

**Why the components are normalised.** The four direction components are measured on
wildly different scales — over 1,080 real symbol-windows (NABIL, NICA, HBL, SCB,
UPPER; 200 sessions each) flow persistence has sd 0.49 while accumulation magnitude
has sd 0.098. Summed raw, persistence would be the score and the other three would be
decoration. Two of them are also *biased*: broker breadth runs at mean -0.063 (net
sellers outnumber net buyers on a typical window, because a few large buyers absorb
from many small sellers) and buy-side concentration at +0.039. A zero-centred rule
would have called most windows bearish and most buying concentrated. So every
component is centred on its measured bias and divided by twice its measured sd
before it is weighted. All of those numbers are recorded in :data:`_CAL`.

The same measurement is what makes section 81 do real work: accumulation tilt and the
activity-weighted broker vote correlate **-0.39** on this archive and agree in sign
only 33.7% of the time. One dominant broker soaking up stock is, by construction,
most brokers being net sellers. Those two dimensions contradict each other constantly
and honestly, which is precisely the evidence a single "bullish" number would hide.
"""

from __future__ import annotations

import math
import statistics
from typing import NamedTuple, Sequence

from . import brokers, features, flow, network, structure
from .loader import WINDOWS, Session

BULLISH, BEARISH, NEUTRAL, MIXED = "bullish", "bearish", "neutral", "mixed"

#: Window weights for the alignment vote. Unfitted round numbers, descending on
#: purpose: this is a 3-30D swing engine, the entry is taken near-term, and section
#: 74 calls 15D/30D "context" rather than the trade. Weighting the short end is only
#: safe because section 77 runs alongside and refuses to hide what the long end says
#: — drop the conflict output and these weights become a way to lie.
WEIGHTS = {3: 0.35, 7: 0.30, 15: 0.20, 30: 0.15}

#: |score| below this is called neutral rather than forced onto a side.
#: MEASURED, not guessed. Over the 1,080 symbol-windows described in the module
#: docstring the composite score has mean +0.0001 and sd 0.242. A band of 0.10 leaves
#: 33.0% of windows neutral overall — 20% at 3D, 27% at 7D, 37% at 15D, 47% at 30D.
#: That spread is the point: 3D is meant to flip (section 74 calls it "short-term
#: pressure") and 30D is meant to sit still. The rate also matches the spec's own
#: illustrations, which show one neutral window in four (section 76) and none
#: (section 77). Alternatives measured at the same time: 0.08 -> 26.8% neutral,
#: 0.12 -> 39.0%, 0.15 -> 47.9%, 0.20 -> 59.9% (which mutes 30D almost entirely).
BAND = 0.10

#: (bias, scale) per direction component, measured over the 1,080 windows above.
#: ``scale`` is 2x the measured sd, so a two-sd move saturates the clamp at 1.0.
_CAL = {
    "flow_persistence": (0.032, 0.980),  # raw sd 0.490
    "accumulation": (0.004, 0.196),      # raw sd 0.098
    "breadth": (-0.063, 0.356),          # raw sd 0.178
    "concentration": (0.039, 0.216),     # raw sd 0.108
}

#: Component weights. Concentration is deliberately the smallest: this project has
#: already measured the broker-concentration family to be largely a liquidity proxy
#: (it runs about -0.6 against turnover), so it gets a vote but not a loud one.
#: Persistence and magnitude are the same evidence counted two ways — how *often*
#: and how *much* — hence equal, and jointly the majority.
_COMPONENT_W = {
    "flow_persistence": 0.30,
    "accumulation": 0.30,
    "breadth": 0.25,
    "concentration": 0.15,
}

#: (bias, scale) per evidence dimension, measured over 270 real 7D-vs-30D decisions
#: on the same five symbols. Same 2x-sd convention as :data:`_CAL`.
_EVIDENCE_CAL = {
    "accumulation": (0.003, 0.211),        # raw sd 0.106
    "flow_imbalance": (0.007, 0.219),      # raw sd 0.109
    "broker_breadth": (-0.047, 0.352),     # raw sd 0.176
    "large_trades": (-0.009, 0.120),       # raw sd 0.060
    "vwap_trend": (-0.003, 0.050),         # raw sd 0.025
    "volume_expansion": (-0.008, 0.811),   # raw sd 0.406
    "participation": (-0.005, 0.128),      # raw sd 0.064
    "short_alignment": (0.011, 0.537),     # mean of the 3D/7D scores, raw sd 0.268
    "long_context": (-0.011, 0.334),       # mean of the 15D/30D scores, raw sd 0.167
}
# Sanity: across 75 real decisions no dimension is constant (normalised sd 0.27-0.58,
# all two-sided) and none saturates at the clamp more than 10.7% of the time, so the
# 2x-sd scale is neither flattening the spread nor clipping it away. ``_demo`` re-runs
# the constant-column half of that check on every invocation.

#: Which measurement each dimension really is. The independent-confirmation count
#: (section 80) counts *families*, not dimensions: accumulation tilt and the weighted
#: broker vote are both flow, and short_alignment/long_context are the window scores
#: re-expressed, so counting all nine would inflate "independent" by roughly double.
_FAMILY = {
    "accumulation": "flow",
    "flow_imbalance": "flow",
    "broker_breadth": "breadth",
    "large_trades": "size",
    "vwap_trend": "price",
    "volume_expansion": "liquidity",
    "participation": "participation",
    "short_alignment": "timeframe",
    "long_context": "timeframe",
}

#: Section 81's own example groups the evidence into a flow side and a structural
#: side ("Bullish flow / bearish structural conflict"). These are those two groups.
#: ``timeframe`` is in neither: it is the window scores restated, so letting it vote
#: in the headline would be the flow side voting twice.
_FLOW_GROUP = ("flow", "breadth", "size")
_STRUCTURAL_GROUP = ("price", "participation", "liquidity")

#: A dimension reading below this (normalised) is too small to call either way.
#: Set at 0.15, which lands near the 30th percentile of |normalised| for every
#: dimension in the 270-decision sample — so roughly the quietest third of readings
#: abstains instead of voting on noise. Same spirit as :data:`BAND`.
EVIDENCE_FLOOR = 0.15

#: Baseline percentile that defines a "spike"/"expansion" event in section 78. A
#: percentile rather than a level, so it self-calibrates per stock; 90 means these
#: events fire on about one day in ten by construction, which ``_demo`` verifies.
EVENT_PCT = 90.0

#: Section 79 strength floor. Levels are on a 0-100 scale indexed to the signal's
#: peak, so 50 is "half the signal is gone" — the natural half-life crossing.
HALF = 50.0


class Reason(NamedTuple):
    """One component of a score, with everything needed to re-derive it by hand."""

    name: str
    value: float  # the raw measurement, in its own units
    normalised: float  # after bias/scale, clamped to [-1, 1]
    weight: float
    contribution: float  # weight * normalised — these sum to the score


def _z(name: str, raw: float, cal: dict[str, tuple[float, float]] = _CAL) -> float:
    """Centre on the measured bias, divide by the measured scale, clamp to [-1, 1]."""
    bias, scale = cal[name]
    if not scale:
        return 0.0
    return max(-1.0, min(1.0, (raw - bias) / scale))


def _direction(score: float, band: float = BAND) -> str:
    if score > band:
        return BULLISH
    if score < -band:
        return BEARISH
    return NEUTRAL


def _opposed(a: str, b: str) -> bool:
    """True only for a genuine head-on disagreement. Neutral opposes nothing."""
    return {a, b} == {BULLISH, BEARISH}


def _pct(values: Sequence[float], p: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    i = min(len(ordered) - 1, max(0, int(round(p / 100.0 * (len(ordered) - 1)))))
    return ordered[i]


def _ratio(short: float, long: float) -> float:
    """``short/long - 1``, and 0.0 rather than a division error when long is empty."""
    return (short / long - 1.0) if long else 0.0


# --------------------------------------------------------------------------- #
# Section 74 — one decision window's view
# --------------------------------------------------------------------------- #

class WindowView(NamedTuple):
    """One of the four decision windows, its direction, and the numbers behind it.

    ``purpose`` carries section 74's own description of what the window is *for*, so
    a reader never has to remember that 3D is pressure and 30D is context.
    """

    window: int
    purpose: str
    days: int  # sessions actually available — a 30D view on 12 sessions is not 30D
    date: str  # the last session in the window: the point-in-time stamp
    direction: str  # bullish | bearish | neutral
    score: float  # -1 .. +1
    reasons: tuple[Reason, ...]

    # the raw measurements, kept so the direction is auditable without re-running
    flow_mean: float
    positive_days: int
    negative_days: int
    neutral_days: int
    persistence: float
    accumulation_pct: float
    distribution_pct: float
    breadth_pct: float
    buy_concentration: float
    sell_concentration: float
    volume: int
    turnover: float
    vwap: float
    brokers: int

    @property
    def full(self) -> bool:
        """False when the archive could not supply the whole window."""
        return self.days >= self.window


PURPOSE = {
    3: "short-term pressure / freshness",
    7: "short swing momentum",
    15: "swing trend",
    30: "broader swing context",
}


def window_view(sessions: Sequence[Session], tilt: Sequence[flow.FlowDay], window: int) -> WindowView:
    """Score one decision window. ``tilt`` is :func:`flow.stock_days` over ``sessions``."""
    win = list(sessions[-window:])
    seg = list(tilt[-window:])
    agg = brokers.window(win)

    p = flow.persistence(seg, window)
    acc = flow.accumulation(seg)
    dis = flow.distribution(seg)
    b = structure.breadth(agg)
    c = structure.concentration(agg)

    # A window where no broker finished net either way carries no breadth opinion.
    # Left as the raw 2*pct-1 it would read -1.0 and fake a maximal bearish vote.
    sided = b.net_buyers + b.net_sellers
    raw = {
        "flow_persistence": p.positive_pct - p.negative_pct,
        "accumulation": acc.pct - dis.pct,
        "breadth": (2.0 * b.pct - 1.0) if sided else 0.0,
        "concentration": c.buy.top5 - c.sell.top5,
    }
    reasons = tuple(
        Reason(k, raw[k], _z(k, raw[k]), _COMPONENT_W[k], _COMPONENT_W[k] * _z(k, raw[k]))
        for k in _COMPONENT_W
    )
    score = sum(r.contribution for r in reasons)

    vol = sum(s.volume for s in win)
    to = sum(s.turnover for s in win)
    return WindowView(
        window=window,
        purpose=PURPOSE.get(window, ""),
        days=len(win),
        date=win[-1].date if win else "",
        direction=_direction(score),
        score=score,
        reasons=reasons,
        flow_mean=p.mean_flow,
        positive_days=p.positive,
        negative_days=p.negative,
        neutral_days=p.neutral,
        persistence=p.persistence,
        accumulation_pct=acc.pct,
        distribution_pct=dis.pct,
        breadth_pct=b.pct,
        buy_concentration=c.buy.top5,
        sell_concentration=c.sell.top5,
        volume=vol,
        turnover=to,
        vwap=(to / vol) if vol else 0.0,
        brokers=b.brokers,
    )


def _views(sessions: Sequence[Session], tilt: Sequence[flow.FlowDay]) -> dict[int, WindowView]:
    return {w: window_view(sessions, tilt, w) for w in WINDOWS}


def _scored(views: dict[int, WindowView] | dict[int, str]) -> dict[int, tuple[str, float]]:
    """Accept real views or the spec's bare labels, so worked examples check directly.

    ``{3: "bullish", 7: "bullish", 15: "bearish", 30: "bearish"}`` is section 77's
    example verbatim; it must be feedable to :func:`align` and :func:`conflict`
    without constructing anything.
    """
    out: dict[int, tuple[str, float]] = {}
    for w, v in views.items():
        if isinstance(v, WindowView):
            out[w] = (v.direction, v.score)
        else:
            label = str(v).lower()
            out[w] = (label, {BULLISH: 1.0, BEARISH: -1.0}.get(label, 0.0))
    return out


# --------------------------------------------------------------------------- #
# Section 75 — cross-window comparison
# --------------------------------------------------------------------------- #

class WindowPair(NamedTuple):
    """One short-vs-long comparison: 3D vs 7D, 7D vs 15D, 15D vs 30D, 3D vs 30D."""

    name: str
    short: int
    long: int
    short_flow: float  # mean normalised tilt over the short window
    long_flow: float
    delta: float  # short_flow - long_flow
    ratio: float  # short_flow / long_flow; <0 means the sign flipped
    short_direction: str
    long_direction: str
    agree: bool
    label: str  # strengthening | fading | flipping | stable


class Accelerations(NamedTuple):
    """Section 75's acceleration block: the short window against the long one.

    ``flow``, ``breadth`` and ``concentration`` are delegated to the modules that
    already own those definitions rather than recomputed here — :func:`flow.acceleration`,
    :func:`structure.breadth_trend` and :func:`structure.dynamics` — so there is
    exactly one definition of each in the package.
    """

    volume: float  # 3D mean daily volume vs 30D, as a ratio - 1
    flow: float  # flow.Accel.slope, i.e. the 3D window mean minus the 30D one
    breadth: float  # structure.BreadthTrend.acceleration
    concentration: float  # structure.ConcDynamics.acceleration
    vwap_trend: float  # 3D VWAP vs 30D VWAP, as a ratio - 1
    participation: float  # 3D mean daily participants vs 30D, as a ratio - 1
    flow_phase: str  # flow.Accel.phase
    breadth_trend: str
    concentration_trend: str


class CrossWindow(NamedTuple):
    pairs: tuple[WindowPair, ...]
    accel: Accelerations
    vwap: dict[int, float]  # window VWAPs, so the trend can be read directly


def _pair(name: str, short: int, long: int, means: dict[int, float],
          views: dict[int, tuple[str, float]]) -> WindowPair:
    s, l = means[short], means[long]
    delta = s - l
    sd, ld = views[short][0], views[long][0]

    if _opposed(sd, ld) or (s * l < 0):
        label = "flipping"
    elif abs(delta) < flow.SHIFT * max(abs(s), abs(l), 1e-12):
        # flow.SHIFT is this package's existing "too small to call a change" cut-off.
        label = "stable"
    elif delta * (1.0 if l > 0 else -1.0 if l < 0 else (1.0 if s > 0 else -1.0)) > 0:
        label = "strengthening"
    else:
        label = "fading"

    return WindowPair(
        name=name, short=short, long=long,
        short_flow=s, long_flow=l, delta=delta,
        ratio=(s / l) if l else 0.0,
        short_direction=sd, long_direction=ld,
        agree=not _opposed(sd, ld),
        label=label,
    )


def compare(sessions: Sequence[Session], tilt: Sequence[flow.FlowDay],
            views: dict[int, WindowView]) -> CrossWindow:
    """Section 75 — every window pair the spec asks for, plus the acceleration block."""
    scored = _scored(views)
    vals = [d.flow for d in tilt]
    means = {w: (statistics.fmean(vals[-w:]) if vals else 0.0) for w in WINDOWS}

    w3, w7, w15, w30 = WINDOWS
    pairs = tuple(
        _pair(f"{a}D vs {b}D", a, b, means, scored)
        for a, b in ((w3, w7), (w7, w15), (w15, w30), (w3, w30))
    )

    by_window_agg = {w: brokers.window(sessions[-w:]) for w in WINDOWS}
    bt = structure.breadth_trend({w: structure.breadth(a) for w, a in by_window_agg.items()})
    cd = structure.dynamics({w: structure.concentration(a) for w, a in by_window_agg.items()})
    fa = flow.acceleration(tilt)

    vw = features.vwap_set(sessions)
    vwap = {w: vw.get(f"{w}d", 0.0) for w in WINDOWS}

    def mean_volume(w: int) -> float:
        chunk = sessions[-w:]
        return statistics.fmean([s.volume for s in chunk]) if chunk else 0.0

    p_short = network.participation(list(sessions[-w3:]))
    p_long = network.participation(list(sessions[-w30:]))

    accel = Accelerations(
        volume=_ratio(mean_volume(w3), mean_volume(w30)),
        flow=fa.slope,
        breadth=bt.acceleration,
        concentration=cd.acceleration,
        vwap_trend=_ratio(vwap[w3], vwap[w30]),
        participation=_ratio(p_short.per_session_mean, p_long.per_session_mean),
        flow_phase=fa.phase,
        breadth_trend=bt.trend,
        concentration_trend=cd.broker_trend,
    )
    return CrossWindow(pairs=pairs, accel=accel, vwap=vwap)


# --------------------------------------------------------------------------- #
# Section 76 — alignment
# --------------------------------------------------------------------------- #

class Alignment(NamedTuple):
    """Section 76. ``direction`` says ``mixed`` whenever both sides are represented.

    That is not timidity: a set of windows containing both a bullish and a bearish
    reading has no single direction, and naming one is exactly the collapse section
    77 exists to prevent. The signed :attr:`weighted` vote is still here for anyone
    who needs a number to sort on.
    """

    bullish: int
    bearish: int
    neutral: int
    windows: int
    weighted: float  # -1 .. +1, weighted vote over the direction LABELS
    weighted_score: float  # -1 .. +1, same weights over the continuous scores
    score: float  # 0 .. 100, |weighted| — how aligned, regardless of which way
    strength: str  # strong | moderate | weak | none
    direction: str  # bullish | bearish | neutral | mixed
    persistence: float  # 0 .. 1, share of recent decision dates with this direction
    persistence_days: int  # how many prior dates that share was measured over
    reasons: tuple[Reason, ...]  # one per window


def align(views: dict[int, WindowView] | dict[int, str],
          prior: Sequence[str] = ()) -> Alignment:
    """Section 76 over real views, or over the spec's bare labels.

    ``prior`` is the alignment direction at each of the preceding decision dates,
    most recent first; it is what turns a one-day reading into persistence.
    """
    scored = _scored(views)
    total_w = sum(WEIGHTS.get(w, 0.0) for w in scored) or 1.0

    reasons = []
    for w in sorted(scored):
        label, score = scored[w]
        weight = WEIGHTS.get(w, 0.0) / total_w
        vote = {BULLISH: 1.0, BEARISH: -1.0}.get(label, 0.0)
        reasons.append(Reason(f"{w}D {label}", score, vote, weight, weight * vote))

    weighted = sum(r.contribution for r in reasons)
    weighted_score = sum(
        (WEIGHTS.get(w, 0.0) / total_w) * scored[w][1] for w in scored
    )

    bull = sum(1 for lab, _ in scored.values() if lab == BULLISH)
    bear = sum(1 for lab, _ in scored.values() if lab == BEARISH)
    neu = len(scored) - bull - bear

    if bull and bear:
        direction = MIXED
    elif bull:
        direction = BULLISH
    elif bear:
        direction = BEARISH
    else:
        direction = NEUTRAL

    score = 100.0 * abs(weighted)
    # Unfitted round cut-offs on an already-calibrated quantity, in the same spirit
    # as flow.py's phase boundaries. |weighted| is a weight share, so 0.70 means
    # windows carrying 70% of the weight point the same way.
    strength = "strong" if score >= 70 else "moderate" if score >= 40 else "weak" if score > 0 else "none"

    agree = sum(1 for d in prior if d == direction)
    return Alignment(
        bullish=bull, bearish=bear, neutral=neu, windows=len(scored),
        weighted=weighted, weighted_score=weighted_score,
        score=score, strength=strength, direction=direction,
        persistence=(agree / len(prior)) if prior else 0.0,
        persistence_days=len(prior),
        reasons=tuple(reasons),
    )


# --------------------------------------------------------------------------- #
# Section 77 — conflict.  The section that must not be fudged.
# --------------------------------------------------------------------------- #

class Conflict(NamedTuple):
    """Section 77. When the short end disagrees with the long end, say so.

    The spec is explicit: 3D bullish / 7D bullish / 15D bearish / 30D bearish is
    **"short-term bullish / higher-window conflict"** and *not* "bullish". So
    :attr:`label` is built from both ends, and it is the string every caller should
    print. A conflicted setup is not a weaker version of a clean one; it is a
    different fact, and averaging it away is how a trade gets taken into a trend
    that the 15D and 30D windows already said was over.
    """

    score: float  # 0 .. 100
    conflicted: bool  # short and long ends genuinely opposed
    label: str
    short_direction: str  # the 3D/7D consensus
    long_direction: str  # the 15D/30D consensus
    short_score: float
    long_score: float
    gap: float  # short_score - long_score, on the -1..1 scale
    disagreeing: tuple[str, ...]  # which window pairs point opposite ways
    reasons: tuple[Reason, ...]


def _group_direction(scored: dict[int, tuple[str, float]], group: Sequence[int]) -> tuple[str, float]:
    present = [w for w in group if w in scored]
    if not present:
        return NEUTRAL, 0.0
    total = sum(WEIGHTS.get(w, 0.0) for w in present) or 1.0
    score = sum(WEIGHTS.get(w, 0.0) * scored[w][1] for w in present) / total
    votes = sum(
        WEIGHTS.get(w, 0.0) * {BULLISH: 1.0, BEARISH: -1.0}.get(scored[w][0], 0.0)
        for w in present
    ) / total
    # Take the label from the LABEL vote, not the raw score: two windows that were
    # each individually called neutral must not add up to a direction.
    return _direction(votes, 0.0) if votes else NEUTRAL, score


def conflict(views: dict[int, WindowView] | dict[int, str]) -> Conflict:
    """Section 77 over real views, or over the spec's bare labels."""
    scored = _scored(views)
    ws = sorted(scored)

    short_group = [w for w in ws if w <= 7]
    long_group = [w for w in ws if w > 7]
    sd, ss = _group_direction(scored, short_group)
    ld, ls = _group_direction(scored, long_group)

    disagreeing = tuple(
        f"{a}D vs {b}D"
        for i, a in enumerate(ws) for b in ws[i + 1:]
        if _opposed(scored[a][0], scored[b][0])
    )
    total_pairs = max(1, len(ws) * (len(ws) - 1) // 2)

    gap = ss - ls
    pair_share = len(disagreeing) / total_pairs
    gap_share = min(1.0, abs(gap) / 2.0)  # scores live in [-1,1], so the gap is <= 2

    reasons = (
        Reason("opposed window pairs", float(len(disagreeing)), pair_share, 0.5, 0.5 * pair_share),
        Reason("short-vs-long score gap", gap, gap_share, 0.5, 0.5 * gap_share),
    )
    score = 100.0 * sum(r.contribution for r in reasons)

    conflicted = _opposed(sd, ld)
    if conflicted:
        # The one string this module exists to produce. Section 77, verbatim.
        label = f"short-term {sd} / higher-window conflict"
    elif disagreeing:
        base = sd if sd != NEUTRAL else ld
        label = f"{base} with internal timeframe disagreement" if base != NEUTRAL \
            else "no directional agreement across windows"
    elif sd == ld == NEUTRAL:
        label = NEUTRAL
    elif sd == NEUTRAL or ld == NEUTRAL:
        lead, other = (sd, "higher") if sd != NEUTRAL else (ld, "short")
        label = f"{lead}, {other} windows neutral"
    else:
        label = sd

    return Conflict(
        score=score, conflicted=conflicted, label=label,
        short_direction=sd, long_direction=ld,
        short_score=ss, long_score=ls, gap=gap,
        disagreeing=disagreeing, reasons=reasons,
    )


# --------------------------------------------------------------------------- #
# Section 78 — freshness
# --------------------------------------------------------------------------- #

class DayMark(NamedTuple):
    """The per-session scalars section 78 dates its events from. One pass, reused.

    ``side`` is taken from ``line`` — the trailing 3D mean — and deliberately NOT
    from the raw daily ``tilt``. Measured on this archive, the raw daily sign flips
    every 2.0-2.9 days (NABIL 2.55, NICA 2.86, UPPER 2.03), so runs built on it are
    1-2 days long on 65-75% of decision dates. That is below the resolution of the
    spec's own shortest decision window, it makes "days since accumulation began"
    read 0 or 1 almost always, and it leaves section 79 fitting a decay curve to two
    points. On the 3D line the same runs average 4.3-7.1 days and reach 18-26, which
    is a regime worth dating and long enough to measure decay across.
    """

    date: str
    tilt: float  # the stock's raw signed flow tilt for the day
    line: float  # trailing 3D mean tilt — the signal line
    trend: float  # trailing 15D mean tilt — the swing-trend line
    side: str  # positive | negative | neutral, from `line`
    hhi: float  # daily broker-activity concentration
    participants: int
    large_share: float  # that day's volume traded in large (by value) prints
    volume: int
    vwap: float


def _trailing(vals: Sequence[float], w: int) -> list[float]:
    """Trailing ``w``-day mean at every position, short at the start of the series."""
    return [statistics.fmean(vals[max(0, i - w + 1): i + 1]) for i in range(len(vals))]


def marks(sessions: Sequence[Session], th: structure.Thresholds,
          tilt: Sequence[flow.FlowDay] | None = None) -> list[DayMark]:
    """Daily scalars for every session, oldest first."""
    series = list(tilt) if tilt is not None else flow.stock_days(sessions)
    raw = [d.flow for d in series]
    w3, _, w15, _ = WINDOWS
    line, trend = _trailing(raw, w3), _trailing(raw, w15)

    out: list[DayMark] = []
    for i, (s, d) in enumerate(zip(sessions, series)):
        agg = brokers.day(s)
        vol = s.volume
        large_vol = sum(t.quantity for t in s.trades if t.amount >= th.large_amt)
        out.append(
            DayMark(
                date=s.date,
                tilt=d.flow,
                line=line[i],
                trend=trend[i],
                side=flow.side_of(line[i]),
                hhi=structure.profile([b.gross_qty for b in agg.values()]).hhi,
                participants=len(agg),
                large_share=(large_vol / vol) if vol else 0.0,
                volume=vol,
                vwap=s.vwap,
            )
        )
    return out


class Event(NamedTuple):
    """One dated observation. ``age`` is days from the event to the decision date."""

    name: str
    date: str
    index: int  # position in the mark series
    age: int  # >= 0 always; 0 means it happened on the decision date
    level: float  # the measurement that fired it
    active: bool  # is the condition still true on the decision date


class Freshness(NamedTuple):
    """Section 78. Every ``days_since_*`` is None when the event never fired.

    None and 0 are different facts — "it has not happened in the observed history"
    versus "it happened today" — and collapsing them into a sentinel like -1 is how
    a stale setup gets read as a fresh one.
    """

    date: str
    signal_age: int | None  # age of the most recent directional run start
    signal: str  # which run that was: accumulation | distribution | none
    days_since_accumulation: int | None
    days_since_distribution: int | None
    days_since_flow_reversal: int | None
    days_since_concentration_spike: int | None
    days_since_participation_expansion: int | None
    days_since_large_trade_emergence: int | None
    accumulation_run: int  # length of the latest positive run
    distribution_run: int
    accumulation_active: bool
    distribution_active: bool
    events: tuple[Event, ...]
    origin_index: int | None  # where section 79 starts measuring decay from


def _last_run(mk: Sequence[DayMark], side: str) -> tuple[int, int, bool] | None:
    """(start index, length, still running) of the most recent run of ``side``."""
    end = None
    for i in range(len(mk) - 1, -1, -1):
        if mk[i].side == side:
            end = i
            break
    if end is None:
        return None
    start = end
    while start > 0 and mk[start - 1].side == side:
        start -= 1
    return start, end - start + 1, end == len(mk) - 1


def _last_above(mk: Sequence[DayMark], pick, cut: float) -> tuple[int, float] | None:
    for i in range(len(mk) - 1, -1, -1):
        v = pick(mk[i])
        if v > cut:
            return i, v
    return None


def freshness(mk: Sequence[DayMark], pct: float = EVENT_PCT) -> Freshness:
    """Section 78. Spike/expansion cut-offs are percentiles of this stock's own history."""
    n = len(mk)
    if not n:
        return Freshness("", None, "none", None, None, None, None, None, None,
                         0, 0, False, False, (), None)
    last = n - 1
    events: list[Event] = []

    def add(name: str, hit: tuple[int, float] | None, active: bool | None = None) -> int | None:
        if hit is None:
            return None
        i, level = hit
        events.append(Event(name, mk[i].date, i, last - i, level,
                            (i == last) if active is None else active))
        return last - i

    pos = _last_run(mk, "positive")
    neg = _last_run(mk, "negative")
    acc_since = add("accumulation start", (pos[0], mk[pos[0]].line) if pos else None,
                    active=bool(pos and pos[2]))
    dis_since = add("distribution start", (neg[0], mk[neg[0]].line) if neg else None,
                    active=bool(neg and neg[2]))

    # The reversal is dated on the 15D trend, not on the 3D line. On the 3D line a
    # "reversal" is just the boundary of the current run and would duplicate the two
    # events above exactly; on the swing-trend window it is the genuinely different
    # and slower fact the spec is after — the broader flow turning, not this week's.
    rev = None
    for i in range(n - 1, 0, -1):
        a, b = mk[i - 1].trend, mk[i].trend
        if a * b < 0 and abs(a) > flow.NEUTRAL and abs(b) > flow.NEUTRAL:
            rev = (i, b)
            break
    rev_since = add("flow reversal", rev)

    conc_since = add("concentration spike",
                     _last_above(mk, lambda m: m.hhi, _pct([m.hhi for m in mk], pct)))
    part_since = add("participation expansion",
                     _last_above(mk, lambda m: float(m.participants),
                                 _pct([float(m.participants) for m in mk], pct)))
    large_since = add("large-trade emergence",
                      _last_above(mk, lambda m: m.large_share,
                                  _pct([m.large_share for m in mk], pct)))

    # The setup's own age is the start of whichever directional run is more recent.
    if pos and neg:
        origin, signal = (pos[0], "accumulation") if pos[0] >= neg[0] else (neg[0], "distribution")
    elif pos:
        origin, signal = pos[0], "accumulation"
    elif neg:
        origin, signal = neg[0], "distribution"
    else:
        origin, signal = None, "none"

    return Freshness(
        date=mk[last].date,
        signal_age=(last - origin) if origin is not None else None,
        signal=signal,
        days_since_accumulation=acc_since,
        days_since_distribution=dis_since,
        days_since_flow_reversal=rev_since,
        days_since_concentration_spike=conc_since,
        days_since_participation_expansion=part_since,
        days_since_large_trade_emergence=large_since,
        accumulation_run=pos[1] if pos else 0,
        distribution_run=neg[1] if neg else 0,
        accumulation_active=bool(pos and pos[2]),
        distribution_active=bool(neg and neg[2]),
        events=tuple(sorted(events, key=lambda e: e.age)),
        origin_index=origin,
    )


# --------------------------------------------------------------------------- #
# Section 79 — decay
# --------------------------------------------------------------------------- #

class Decay(NamedTuple):
    """Section 79 — how the signal changed after it appeared.

    Levels are indexed to the signal's **peak** = 100, not to its first day. The
    spec's own example starts at its peak (100, 82, 55, 21) so the two agree there,
    but indexing to day one divides by whatever the first day happened to be — and
    a run often starts at a whisker over the neutral band, which would turn a normal
    build-up into a 4000% "decay rate". :attr:`day1` keeps the first-day level so
    nothing is lost.
    """

    points: tuple[tuple[int, float], ...]  # (day offset, level indexed to peak=100)
    days: int
    day1: float  # level on the signal's first day
    peak_day: int
    latest: float
    rate: float  # per-day exponential decay rate; > 0 is decaying, < 0 is growing
    half_life: float | None  # days to fall to 50; None when it is not decaying
    persistence: float  # 0..1, share of post-peak days still at or above 50
    recovery: float  # 0..1, biggest post-trough rise as a fraction of the peak
    reacceleration: float  # last-3-day mean level minus the 3 before it
    label: str  # decaying | persistent | reaccelerating | recovering | flat


def decay_from_points(points: Sequence[tuple[int, float]]) -> Decay:
    """Section 79 from bare (day, value) pairs, so the spec's example checks directly.

    ``decay_from_points([(1, 100), (3, 82), (7, 55), (15, 21)])`` is section 79
    verbatim. The rate is a log-linear least-squares fit across *all* the points,
    not an endpoint ratio — with four samples the endpoints alone throw away half
    the information and are hostage to a single noisy day.
    """
    pts = [(int(d), float(v)) for d, v in points if v == v]
    if not pts:
        return Decay((), 0, 0.0, 0, 0.0, 0.0, None, 0.0, 0.0, 0.0, "flat")

    peak_v = max(v for _, v in pts)
    if peak_v <= 0:
        return Decay(tuple(pts), len(pts), pts[0][1], pts[0][0], pts[-1][1],
                     0.0, None, 0.0, 0.0, 0.0, "flat")

    base_day = pts[0][0]
    scaled = [(d - base_day, 100.0 * v / peak_v) for d, v in pts]
    peak_i = max(range(len(scaled)), key=lambda i: scaled[i][1])
    peak_day = scaled[peak_i][0]

    tail = scaled[peak_i:]
    usable = [(d, v) for d, v in tail if v > 0]
    rate = 0.0
    if len(usable) >= 2:
        xs = [d for d, _ in usable]
        ys = [math.log(v) for _, v in usable]
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        rate = -(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx) if sxx else 0.0

    half_life = (math.log(2.0) / rate) if rate > 1e-9 else None
    persistence = (sum(1 for _, v in tail if v >= HALF) / len(tail)) if tail else 0.0

    trough = peak_v
    recovery = 0.0
    lowest = 100.0
    for _, v in tail:
        lowest = min(lowest, v)
        recovery = max(recovery, (v - lowest) / 100.0)

    levels = [v for _, v in scaled]
    reaccel = (statistics.fmean(levels[-3:]) - statistics.fmean(levels[-6:-3])) \
        if len(levels) >= 6 else 0.0

    if reaccel > 5.0:
        label = "reaccelerating"
    elif recovery > 0.25:
        label = "recovering"
    elif rate > 0.05:
        label = "decaying"
    elif persistence >= 0.5:
        label = "persistent"
    else:
        label = "flat"

    return Decay(
        points=tuple(scaled), days=len(scaled), day1=scaled[0][1], peak_day=peak_day,
        latest=scaled[-1][1], rate=rate, half_life=half_life,
        persistence=persistence, recovery=recovery, reacceleration=reaccel, label=label,
    )


def decay(mk: Sequence[DayMark], origin: int | None) -> Decay:
    """Section 79 on the real signal line: |trailing 3D tilt|, from ``origin`` on."""
    if origin is None or not mk:
        return decay_from_points(())
    return decay_from_points([(i - origin, abs(mk[i].line)) for i in range(origin, len(mk))])


# --------------------------------------------------------------------------- #
# Sections 80 & 81 — confirmation and contradiction
# --------------------------------------------------------------------------- #

class Dimension(NamedTuple):
    """One independent piece of evidence, before or after a thesis is applied.

    ``kind`` matters. A *directional* dimension has its own bullish/bearish sense.
    An *amplifier* — volume, participation, large-trade emergence — has none: it
    confirms whatever the flow says when it expands and contradicts when it dries
    up. A move on shrinking volume is unconfirmed whichever way it points, and
    signing an amplifier as if it were directional would make thin tape look bearish.
    """

    name: str
    family: str
    kind: str  # directional | amplifier
    value: float  # the raw measurement
    normalised: float  # -1 .. +1
    signed: float  # normalised, oriented to the thesis; +ve confirms
    verdict: str  # confirms | contradicts | neutral


class Evidence(NamedTuple):
    """Sections 80 and 81 together, because splitting them is how contradictions hide."""

    thesis: str
    dimensions: tuple[Dimension, ...]
    confirmations: tuple[Dimension, ...]
    contradictions: tuple[Dimension, ...]

    confirmation_count: int
    confirmation_strength: float  # 0..1, mean |signed| over confirmations
    independent_count: int  # distinct FAMILIES confirming, not dimensions
    families: int  # distinct families with any material reading

    contradiction_count: int
    contradiction_severity: float  # 0..100
    contradiction_persistence: float  # 0..1, share of recent dates that also contradicted
    # There was a ``contradiction_days`` field here and it was NOT a measurement of
    # this symbol: it was ``len(prior)``, the size of the lookback the caller asked
    # for. Section 76's ``persistence_days`` is the same ``len(prior)`` from the same
    # ``analyse(history=...)`` argument, so the two sections printed one number under
    # two names that each implied a different thing ("the alignment persisted N days"
    # / "the contradiction persisted N days"). Measured on the shipped board:
    # identical on 481 of 481 symbols, joint histogram {5: 475, 4: 3, 3: 2, 2: 1} —
    # 98.8% pinned at the default, varying only where the archive runs out. The
    # lookback is disclosed once, in section 76, and section 81 points at it.

    net: float  # -1..+1, confirmation weight minus contradiction weight
    confidence: float  # 0..100
    label: str  # section 81's headline, e.g. "bullish flow / bearish structural conflict"
    reasons: tuple[Reason, ...]


def dimensions(sessions: Sequence[Session], tilt: Sequence[flow.FlowDay],
               views: dict[int, WindowView], th: structure.Thresholds,
               short: int = 7, long: int = 30) -> tuple[Dimension, ...]:
    """The nine measurements section 80 lists, normalised but not yet taking a side."""
    ws, wl = list(sessions[-short:]), list(sessions[-long:])
    a_s = brokers.window(ws)

    acc, dis = flow.accumulation(tilt[-short:]), flow.distribution(tilt[-short:])
    cs = structure.consensus(a_s)
    br = structure.breadth(a_s)
    lg_s, lg_l = structure.large(ws, th), structure.large(wl, th)
    vw = features.vwap_set(wl)
    p_s, p_l = network.participation(ws), network.participation(wl)
    v_s = statistics.fmean([s.volume for s in ws]) if ws else 0.0
    v_l = statistics.fmean([s.volume for s in wl]) if wl else 0.0

    def score(w: int) -> float:
        return views[w].score if w in views else 0.0

    w3, w7, w15, w30 = WINDOWS
    raw = {
        "accumulation": acc.pct - dis.pct,
        # NOT the stock's net buy/sell imbalance: that is identically zero, because
        # every share bought is sold. The activity-weighted broker vote is the
        # imbalance that survives, and it is genuinely a different reading —
        # measured at r=-0.39 against accumulation tilt over 270 decisions.
        "flow_imbalance": cs.weighted_consensus,
        "broker_breadth": (2.0 * br.pct - 1.0) if (br.net_buyers + br.net_sellers) else 0.0,
        "large_trades": lg_s.volume_pct - lg_l.volume_pct,
        "vwap_trend": _ratio(vw.get(f"{short}d", 0.0), vw.get(f"{long}d", 0.0)),
        "volume_expansion": _ratio(v_s, v_l),
        "participation": _ratio(p_s.per_session_mean, p_l.per_session_mean),
        "short_alignment": (score(w3) + score(w7)) / 2.0,
        "long_context": (score(w15) + score(w30)) / 2.0,
    }
    kinds = {"large_trades": "amplifier", "volume_expansion": "amplifier",
             "participation": "amplifier"}
    return tuple(
        Dimension(name=k, family=_FAMILY[k], kind=kinds.get(k, "directional"),
                  value=raw[k], normalised=_z(k, raw[k], _EVIDENCE_CAL),
                  signed=0.0, verdict=NEUTRAL)
        for k in raw
    )


def _apply(dims: Sequence[Dimension], thesis: str) -> tuple[Dimension, ...]:
    sign = 1.0 if thesis == BULLISH else -1.0 if thesis == BEARISH else 0.0
    out = []
    for d in dims:
        # An amplifier does not know which way it points, so it is oriented by the
        # thesis: expanding confirms, contracting contradicts, either direction.
        signed = d.normalised if d.kind == "amplifier" else d.normalised * sign
        verdict = ("confirms" if signed > EVIDENCE_FLOOR
                   else "contradicts" if signed < -EVIDENCE_FLOOR else NEUTRAL)
        out.append(d._replace(signed=signed, verdict=verdict))
    return tuple(out)


def _group_sign(dims: Sequence[Dimension], group: Sequence[str], thesis: str) -> float:
    """Mean per-family reading over a group, in absolute (not thesis-relative) terms."""
    sign = 1.0 if thesis == BULLISH else -1.0 if thesis == BEARISH else 1.0
    per_family: dict[str, list[float]] = {}
    for d in dims:
        if d.family in group:
            # undo the thesis orientation so the label describes the world, not the trade
            per_family.setdefault(d.family, []).append(
                d.signed * sign if d.kind == "amplifier" else d.normalised
            )
    if not per_family:
        return 0.0
    return statistics.fmean([statistics.fmean(v) for v in per_family.values()])


def evidence(dims: Sequence[Dimension], thesis: str,
             prior: Sequence[Sequence[Dimension]] = ()) -> Evidence:
    """Sections 80 and 81. ``prior`` is ``dimensions()`` at the preceding dates."""
    applied = _apply(dims, thesis)
    confirms = tuple(d for d in applied if d.verdict == "confirms")
    contras = tuple(d for d in applied if d.verdict == "contradicts")

    strength = statistics.fmean([abs(d.signed) for d in confirms]) if confirms else 0.0
    independent = len({d.family for d in confirms})
    families = len({d.family for d in applied if d.verdict != NEUTRAL})

    # Severity is the total weight of the case against, not just how many objections
    # there are: one dimension screaming is worse than three muttering. Normalised by
    # the number of dimensions so it stays inside 0..100 whatever the set size.
    severity = 100.0 * sum(abs(d.signed) for d in contras) / max(1, len(applied))

    persistence = 0.0
    if prior and contras:
        names = {d.name for d in contras}
        hits = 0
        for snapshot in prior:
            past = _apply(snapshot, thesis)
            hits += sum(1 for d in past if d.name in names and d.verdict == "contradicts")
        persistence = hits / (len(prior) * len(names))

    net = (sum(d.signed for d in confirms) + sum(d.signed for d in contras)) / max(1, len(applied))

    reasons = (
        Reason("confirming families", float(independent),
               independent / max(1, len(set(_FAMILY.values()))), 0.35,
               0.35 * independent / max(1, len(set(_FAMILY.values())))),
        Reason("confirmation strength", strength, strength, 0.35, 0.35 * strength),
        Reason("net after contradictions", net, max(0.0, min(1.0, (net + 1.0) / 2.0)), 0.30,
               0.30 * max(0.0, min(1.0, (net + 1.0) / 2.0))),
        Reason("contradiction severity", severity, -severity / 100.0, 1.0, -severity / 100.0),
    )
    confidence = max(0.0, min(100.0, 100.0 * sum(r.contribution for r in reasons)))

    flow_side = _group_sign(applied, _FLOW_GROUP, thesis)
    struct_side = _group_sign(applied, _STRUCTURAL_GROUP, thesis)
    fl, sl = _direction(flow_side, BAND), _direction(struct_side, BAND)
    if _opposed(fl, sl):
        # Section 81's own wording: "Bullish flow / bearish structural conflict".
        label = f"{fl} flow / {sl} structural conflict"
    elif contras:
        label = f"{thesis} thesis, {len(contras)} contradiction" + ("s" if len(contras) != 1 else "")
    elif confirms:
        label = f"{thesis} thesis confirmed on {independent} independent dimension" + \
            ("s" if independent != 1 else "")
    else:
        label = f"{thesis} thesis, no material evidence either way"

    return Evidence(
        thesis=thesis, dimensions=applied, confirmations=confirms, contradictions=contras,
        confirmation_count=len(confirms), confirmation_strength=strength,
        independent_count=independent, families=families,
        contradiction_count=len(contras), contradiction_severity=severity,
        contradiction_persistence=persistence,
        net=net, confidence=confidence, label=label, reasons=reasons,
    )


# --------------------------------------------------------------------------- #
# The whole picture
# --------------------------------------------------------------------------- #

class Timeframes(NamedTuple):
    """Sections 74-81 for one symbol at one point in time.

    :attr:`label` is the headline and it is the conflict-aware one. Print that, not
    :attr:`Alignment.direction`, or section 77 was written for nothing.
    """

    symbol: str
    date: str
    sessions: int
    windows: dict[int, WindowView]
    comparison: CrossWindow
    alignment: Alignment
    conflict: Conflict
    freshness: Freshness
    decay: Decay
    evidence: Evidence
    label: str


def analyse(sessions: Sequence[Session], baseline: Sequence[Session] | None = None,
            history: int = 5, thesis: str | None = None) -> Timeframes:
    """Sections 74-81 over an already-sliced, oldest-first session list.

    ``sessions`` is the point-in-time guard: whatever is in it is all that is known.
    Nothing here reads a date, so a caller cannot accidentally reach past the
    decision. ``baseline`` may be longer history — it is used for the large-trade
    threshold only, which is what section 74 permits longer history for.

    ``history`` is how many preceding decision dates to re-derive for the two
    persistence figures. It is the expensive part; pass 0 to skip it.
    """
    ses = list(sessions)
    if not ses:
        raise ValueError("analyse() needs at least one session")

    tilt = flow.stock_days(ses)
    th = structure.thresholds(list(baseline) if baseline else ses)

    views = _views(ses, tilt)
    cross = compare(ses, tilt, views)
    cf = conflict(views)
    mk = marks(ses, th, tilt)
    fr = freshness(mk)
    dc = decay(mk, fr.origin_index)
    dims = dimensions(ses, tilt, views, th)

    prior_dirs: list[str] = []
    prior_dims: list[tuple[Dimension, ...]] = []
    for k in range(1, history + 1):
        cut, cut_tilt = ses[:-k], tilt[:-k]
        if len(cut) < max(WINDOWS):
            break
        v2 = _views(cut, cut_tilt)
        prior_dirs.append(align(v2).direction)
        # Re-derived at that date, not reused from today: today's large-trade
        # threshold is future information at a past date, however harmless it looks.
        prior_dims.append(dimensions(cut, cut_tilt, v2, structure.thresholds(cut)))

    al = align(views, prior_dirs)

    if thesis is None:
        # A mixed or neutral alignment has no thesis of its own, so fall back to the
        # short end — that is the side a swing entry would be taken on — and finally
        # to bullish, which is the case a screener is asking about. The conflict
        # label travels alongside, so the fallback never reads as agreement.
        thesis = al.direction if al.direction in (BULLISH, BEARISH) else \
            cf.short_direction if cf.short_direction in (BULLISH, BEARISH) else BULLISH

    ev = evidence(dims, thesis, prior_dims)

    return Timeframes(
        symbol=ses[-1].symbol, date=ses[-1].date, sessions=len(ses),
        windows=views, comparison=cross, alignment=al, conflict=cf,
        freshness=fr, decay=dc, evidence=ev,
        label=cf.label,
    )


# --------------------------------------------------------------------------- #

def _demo() -> None:
    """Self-check: the spec's worked examples, then the real archive."""
    from . import loader

    # ---- section 77, the example that must not collapse into "bullish" --------
    spec77 = {3: BULLISH, 7: BULLISH, 15: BEARISH, 30: BEARISH}
    c77 = conflict(spec77)
    assert c77.conflicted, c77
    assert c77.label == "short-term bullish / higher-window conflict", c77.label
    assert c77.label != BULLISH and "conflict" in c77.label
    assert c77.short_direction == BULLISH and c77.long_direction == BEARISH
    assert 0.0 <= c77.score <= 100.0 and c77.score > 0.0, c77.score
    # every window pair across the divide disagrees: 3-15, 3-30, 7-15, 7-30
    assert len(c77.disagreeing) == 4, c77.disagreeing
    a77 = align(spec77)
    assert (a77.bullish, a77.bearish, a77.neutral) == (2, 2, 0), a77
    assert a77.direction == MIXED, f"a split set must not be given a direction: {a77.direction}"
    assert a77.direction != BULLISH

    # ---- section 76's example: strong short-term alignment, no conflict -------
    spec76 = {3: BULLISH, 7: BULLISH, 15: BULLISH, 30: NEUTRAL}
    a76 = align(spec76)
    assert (a76.bullish, a76.bearish, a76.neutral) == (3, 0, 1), a76
    assert a76.bullish + a76.bearish + a76.neutral == a76.windows == 4
    assert a76.direction == BULLISH and a76.strength == "strong", a76
    assert abs(a76.weighted - 0.85) < 1e-9, a76.weighted
    c76 = conflict(spec76)
    assert not c76.conflicted and "conflict" not in c76.label, c76.label
    assert abs(sum(r.contribution for r in a76.reasons) - a76.weighted) < 1e-12

    # ---- section 79's example, verbatim ---------------------------------------
    d79 = decay_from_points([(1, 100.0), (3, 82.0), (7, 55.0), (15, 21.0)])
    assert d79.label == "decaying", d79.label
    assert d79.peak_day == 0 and abs(d79.day1 - 100.0) < 1e-9
    assert d79.half_life is not None and 5.0 < d79.half_life < 8.0, d79.half_life
    assert d79.rate > 0 and abs(d79.latest - 21.0) < 1e-9
    assert 0.0 <= d79.persistence <= 1.0

    # ---- the real archive ------------------------------------------------------
    syms = loader.symbols()
    assert syms, "no floorsheet archive"
    sym = "NABIL" if "NABIL" in syms else syms[0]
    dates = loader.sessions(sym)
    assert len(dates) > 1000, f"{sym}: expected the full archive, got {len(dates)}"

    hist = loader.load_last(sym, 70)
    assert len(hist) >= 60, f"{sym}: only {len(hist)} sessions"
    tf = analyse(hist)

    # 74: four windows, and only four.
    assert set(tf.windows) == set(WINDOWS) == {3, 7, 15, 30}
    assert max(tf.windows) == 30, "60D/120D/1Y must never become decision windows"
    for w, v in tf.windows.items():
        assert v.days == min(w, len(hist)) and v.full
        assert v.positive_days + v.negative_days + v.neutral_days == v.days
        assert -1.0 <= v.score <= 1.0, (w, v.score)
        assert v.direction in (BULLISH, BEARISH, NEUTRAL)
        assert v.direction == _direction(v.score)
        # rule 7: the score IS its reasons, not a number beside them
        assert abs(sum(r.contribution for r in v.reasons) - v.score) < 1e-12
        assert len(v.reasons) == 4 and all(-1.0 <= r.normalised <= 1.0 for r in v.reasons)
        assert v.date == hist[-1].date, "every window must end on the decision date"
        assert v.volume > 0 and v.brokers > 0
        lo = min(t.rate for s in hist[-w:] for t in s.trades)
        hi = max(t.rate for s in hist[-w:] for t in s.trades)
        assert lo <= v.vwap <= hi, (w, v.vwap)

    # 75: the four pairs the spec names, in order.
    assert [p.name for p in tf.comparison.pairs] == \
        ["3D vs 7D", "7D vs 15D", "15D vs 30D", "3D vs 30D"]
    for p in tf.comparison.pairs:
        assert abs(p.delta - (p.short_flow - p.long_flow)) < 1e-12
        assert p.label in ("strengthening", "fading", "flipping", "stable")
        assert p.agree == (not _opposed(p.short_direction, p.long_direction))
    ac = tf.comparison.accel
    assert ac.flow_phase in ("accelerating", "persistent", "weakening", "exhausted", "flat")
    assert all(tf.comparison.vwap[w] > 0 for w in WINDOWS)

    # 76 / 77: counts add up, scores stay in their boxes, and the headline is the
    # conflict-aware one.
    al, cf = tf.alignment, tf.conflict
    assert al.bullish + al.bearish + al.neutral == al.windows == 4
    assert -1.0 <= al.weighted <= 1.0 and -1.0 <= al.weighted_score <= 1.0
    assert 0.0 <= al.score <= 100.0 and abs(al.score - 100.0 * abs(al.weighted)) < 1e-9
    assert 0.0 <= al.persistence <= 1.0 and al.persistence_days <= 5
    assert 0.0 <= cf.score <= 100.0
    assert cf.conflicted == _opposed(cf.short_direction, cf.long_direction)
    assert tf.label == cf.label
    if al.bullish and al.bearish:
        assert al.direction == MIXED
    if cf.conflicted:
        assert "conflict" in tf.label and tf.label not in (BULLISH, BEARISH)

    # 78: ages are non-negative, dated at or before the decision, and never faked.
    fr = tf.freshness
    assert fr.date == hist[-1].date
    since = (fr.signal_age, fr.days_since_accumulation, fr.days_since_distribution,
             fr.days_since_flow_reversal, fr.days_since_concentration_spike,
             fr.days_since_participation_expansion, fr.days_since_large_trade_emergence)
    assert any(x is not None for x in since), "no event fired in 70 sessions?"
    for x in since:
        assert x is None or 0 <= x < len(hist), x
    for e in fr.events:
        assert e.age >= 0 and e.date <= hist[-1].date, e
        assert e.date == hist[e.index].date
    assert not (fr.accumulation_active and fr.distribution_active)
    # the percentile cut-off must actually behave like a percentile
    mk = marks(hist, structure.thresholds(hist))
    hi = _pct([m.hhi for m in mk], EVENT_PCT)
    fired = sum(1 for m in mk if m.hhi > hi) / len(mk)
    assert 0.02 < fired < 0.20, f"concentration spike fires on {fired:.1%} of days"

    # 79: bounded, and consistent with the freshness origin it was measured from.
    dc = tf.decay
    assert dc.days >= 1 and dc.points[0][0] == 0
    assert all(0.0 <= v <= 100.0 + 1e-9 for _, v in dc.points), "levels must index to peak=100"
    assert abs(max(v for _, v in dc.points) - 100.0) < 1e-9
    assert 0.0 <= dc.persistence <= 1.0 and 0.0 <= dc.recovery <= 1.0
    assert dc.half_life is None or dc.half_life > 0
    if fr.origin_index is not None:
        assert dc.days == len(hist) - fr.origin_index

    # 80 / 81: contradictions are shipped, not swallowed.
    ev = tf.evidence
    assert ev.thesis in (BULLISH, BEARISH)
    assert len(ev.dimensions) == 9 and len(set(d.name for d in ev.dimensions)) == 9
    assert ev.confirmation_count == len(ev.confirmations)
    assert ev.contradiction_count == len(ev.contradictions)
    assert ev.confirmation_count + ev.contradiction_count <= len(ev.dimensions)
    assert ev.independent_count <= len({d.family for d in ev.dimensions})
    assert ev.independent_count <= ev.confirmation_count
    assert 0.0 <= ev.confirmation_strength <= 1.0
    assert 0.0 <= ev.contradiction_severity <= 100.0
    assert 0.0 <= ev.contradiction_persistence <= 1.0
    assert -1.0 <= ev.net <= 1.0 and 0.0 <= ev.confidence <= 100.0
    for d in ev.dimensions:
        assert -1.0 <= d.normalised <= 1.0 and -1.0 <= d.signed <= 1.0
        assert d.verdict in ("confirms", "contradicts", NEUTRAL)
        assert d.kind in ("directional", "amplifier")
        assert (d.verdict == "confirms") == (d.signed > EVIDENCE_FLOOR)
    assert ev.confirmations or ev.contradictions, "nine dimensions and nothing to say?"
    assert all(r.name for r in ev.reasons)

    # ---- degeneracy sweep: a constant column is not a feature -------------------
    # Five metrics have already been cut from this package for being pinned to a
    # constant. Every number this module ships has to move on real data, so walk 20
    # real decision dates and demand it does.
    sweep = loader.load_last(sym, 90)
    scores = {w: [] for w in WINDOWS}
    seen = {"align": [], "conflict": [], "confidence": [], "net": [], "severity": [],
            "decay_rate": [], "decay_days": [], "age": [], "conf_count": [], "contra_count": []}
    labels: set[str] = set()
    directions: set[str] = set()
    for i in range(len(sweep) - 20, len(sweep)):
        t = analyse(sweep[: i + 1], history=0)
        for w in WINDOWS:
            scores[w].append(t.windows[w].score)
        seen["align"].append(t.alignment.weighted_score)
        seen["conflict"].append(t.conflict.score)
        seen["confidence"].append(t.evidence.confidence)
        seen["net"].append(t.evidence.net)
        seen["severity"].append(t.evidence.contradiction_severity)
        seen["decay_rate"].append(t.decay.rate)
        seen["decay_days"].append(float(t.decay.days))
        seen["age"].append(float(t.freshness.signal_age or 0))
        seen["conf_count"].append(float(t.evidence.confirmation_count))
        seen["contra_count"].append(float(t.evidence.contradiction_count))
        labels.add(t.label)
        directions.add(t.alignment.direction)
        # point-in-time: nothing may be dated past the decision date
        assert t.date == sweep[i].date
        assert all(v.date <= t.date for v in t.windows.values())
        assert all(e.date <= t.date for e in t.freshness.events)

    for w in WINDOWS:
        sd = statistics.pstdev(scores[w])
        assert sd > 0.01, f"{w}D score is pinned flat (sd {sd:.5f}) — cut it"
    for k, v in seen.items():
        sd = statistics.pstdev(v)
        assert sd > 0.0, f"{k} is a constant column (sd {sd:.6f}) — cut it or fix it"
    assert len(labels) > 1, f"the headline never changes over 20 sessions: {labels}"
    assert sum(seen["contra_count"]) > 0, \
        "no contradiction in 20 sessions — section 81 would be decoration"
    # Section 79 has to have something to fit. Anchored on the raw daily tilt sign
    # this averaged 2.0-2.3 days and was two points and noise; on the 3D line the
    # regimes measured 4.3-7.1 days, so anything at or below 3 means the origin has
    # regressed to a per-tick anchor and the decay curve is meaningless again.
    mean_days = statistics.fmean(seen["decay_days"])
    assert mean_days > 3.0, f"decay window averages {mean_days:.1f}d — too short to fit"

    v7 = tf.windows[7]
    top = max(v7.reasons, key=lambda r: abs(r.contribution))
    print(f"timeframes ok — {sym} {tf.date} ({tf.sessions} sessions, {len(dates)} in archive "
          f"from {dates[0]})")
    print(f"  {tf.label}  |  " + "  ".join(
        f"{w}D {tf.windows[w].direction[:4]} {tf.windows[w].score:+.2f}" for w in WINDOWS))
    print(f"  alignment {al.bullish}+/{al.bearish}-/{al.neutral}n -> {al.direction} "
          f"{al.strength} {al.score:.0f}/100 (persistence {al.persistence:.0%} over "
          f"{al.persistence_days}d), conflict {cf.score:.0f}/100 "
          f"[short {cf.short_direction} {cf.short_score:+.2f} vs higher {cf.long_direction} "
          f"{cf.long_score:+.2f}]")
    print(f"  7D top reason: {top.name} raw {top.value:+.4f} -> {top.normalised:+.2f} "
          f"x {top.weight:.2f} = {top.contribution:+.3f}")
    print(f"  freshness: {fr.signal} age {fr.signal_age}d, reversal {fr.days_since_flow_reversal}d, "
          f"conc spike {fr.days_since_concentration_spike}d, participation "
          f"{fr.days_since_participation_expansion}d, large trades "
          f"{fr.days_since_large_trade_emergence}d")
    print(f"  decay: {dc.label}, day1 {dc.day1:.0f} -> now {dc.latest:.0f} over {dc.days}d, "
          f"rate {dc.rate:+.3f}/d, half-life "
          f"{'n/a' if dc.half_life is None else format(dc.half_life, '.1f') + 'd'}, "
          f"recovery {dc.recovery:.0%}")
    print(f"  evidence ({ev.thesis}): {ev.confirmation_count} confirm "
          f"({ev.independent_count} independent, strength {ev.confirmation_strength:.2f}) vs "
          f"{ev.contradiction_count} contradict (severity {ev.contradiction_severity:.0f}/100, "
          f"persistence {ev.contradiction_persistence:.0%}) -> net {ev.net:+.3f}, "
          f"confidence {ev.confidence:.0f}/100")
    print(f"    {ev.label}")
    for d in ev.confirmations:
        print(f"    + {d.name:<16} {d.family:<13} {d.value:+.4f} -> {d.signed:+.2f}")
    for d in ev.contradictions:
        print(f"    - {d.name:<16} {d.family:<13} {d.value:+.4f} -> {d.signed:+.2f}")


if __name__ == "__main__":
    _demo()
