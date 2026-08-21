"""Historical baselines, price-flow relationships and regimes — spec sections 50-73.

This is the module that makes every other number in the package comparable. A raw
figure ("broker 58 bought 40,000 shares") means nothing until you know what this
symbol normally does, so almost everything downstream ends up calling
:func:`pit` — point-in-time — to turn a value into a z-score and a historical
percentile.

WHY THE API LOOKS LIKE THIS
---------------------------
Spec section 100 forbids *future normalisation statistics* and *future percentile
calculations* just as firmly as it forbids future prices. A z-score taken against
a full-sample mean is look-ahead wearing a lab coat: it quietly tells the model
that today's volume was low *for a year that had not happened yet*. This project
has already paid for that lesson once — ranking the universe by full-sample
turnover was itself fake alpha, and the factor it "found" evaporated when the
ranking was made point-in-time.

So the leak-proof call is the only call on offer. :func:`pit` takes the whole
series and an index, and does the slicing itself: it reads ``series[:i]`` and
nothing else, ever. You cannot hand it a mean you computed over the full sample,
because it does not accept a mean. Append ten more years to the series and
``pit(series, i)`` returns the identical answer — that invariant is asserted in
:func:`_demo` and is the single most important test in this file.

:func:`daily` is the other half of the design: ONE pass over a symbol's sessions
produces every per-day statistic the rest of the module needs, so a 1,000-session
sweep parses each floorsheet once rather than once per feature.

Price here is the executed VWAP from the floorsheet itself, not
:func:`loader.bars`. Same source, no join, and no adjusted/unadjusted mismatch.
Day-over-day moves larger than the NEPSE circuit can only be a corporate-action
restatement, so :func:`returns` drops them rather than feeding a fake -40% into a
correlation.

NAMING DISCIPLINE (spec sections 61-63)
---------------------------------------
Sections 61-63 describe things the floorsheet *cannot* see. They are proxies and
they are named as proxies, in the API and in the output labels:
:func:`absorption_like`, :func:`exhaustion_like`, :func:`executed_liquidity`.
Never "absorption", never "exhaustion", and never "liquidity" unqualified — the
floorsheet has no order book, only fills.

Two of these have already been tested in this codebase and came back empty; that
is recorded on each function rather than left for someone to rediscover.

Pure stdlib, like the rest of the package: this runs on a RAM-starved VPS.
"""

from __future__ import annotations

import math
import os
import time
from collections import Counter
from typing import Callable, NamedTuple, Sequence

from . import brokers, loader
from .loader import Session

#: NEPSE's daily circuit is +/-15%. A day-over-day executed-price move past this
#: is not a trade, it is a bonus/rights restatement of the reference price. See
#: the ex-date memo: detect the restatement by the size of the gap, not by the
#: direction of the fall. 0.20 in log terms leaves room for VWAP-vs-close slop.
CIRCUIT_LOG = 0.20

#: Section 71 says the sector map is external and must never be read as a
#: floorsheet field. This is that external file.
SECTORS = os.path.join(loader.ROOT, "Master_data", "sectors.txt")


# ---------------------------------------------------------------------------
# section 50 — the descriptive spine
# ---------------------------------------------------------------------------


def percentile(ordered: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile of an ALREADY SORTED sequence, q in 0-100."""
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return float(ordered[0])
    pos = (len(ordered) - 1) * (max(0.0, min(100.0, q)) / 100.0)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return float(ordered[lo]) * (1 - frac) + float(ordered[hi]) * frac


def skew(values: Sequence[float]) -> float:
    """Population (Fisher-Pearson) skewness. 0.0 when it is undefined.

    Written out because numpy/scipy are not installed on the target box.
    Positive = a long right tail, i.e. one huge positive day rather than many
    consistent ones — which is exactly the distinction spec section 54 asks for.
    """
    n = len(values)
    if n < 3:
        return 0.0
    m = sum(values) / n
    m2 = sum((x - m) ** 2 for x in values) / n
    if m2 <= 0:
        return 0.0
    m3 = sum((x - m) ** 3 for x in values) / n
    return m3 / (m2**1.5)


def kurtosis(values: Sequence[float]) -> float:
    """Population EXCESS kurtosis (normal = 0.0). 0.0 when undefined."""
    n = len(values)
    if n < 4:
        return 0.0
    m = sum(values) / n
    m2 = sum((x - m) ** 2 for x in values) / n
    if m2 <= 0:
        return 0.0
    m4 = sum((x - m) ** 4 for x in values) / n
    return m4 / (m2**2) - 3.0


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Pearson correlation, clamped to [-1, 1]. 0.0 when either side is constant.

    A constant side has no correlation *defined*, not a correlation of zero, but
    every caller here wants "no relationship" rather than an exception.
    """
    n = min(len(xs), len(ys))
    if n < 3:
        return 0.0
    mx = sum(xs[:n]) / n
    my = sum(ys[:n]) / n
    sxy = sxx = syy = 0.0
    for i in range(n):
        dx = xs[i] - mx
        dy = ys[i] - my
        sxy += dx * dy
        sxx += dx * dx
        syy += dy * dy
    if sxx <= 0 or syy <= 0:
        return 0.0
    return max(-1.0, min(1.0, sxy / math.sqrt(sxx * syy)))


def slope(xs: Sequence[float], ys: Sequence[float]) -> float:
    """OLS slope of ys on xs — spec section 57's "flow-price sensitivity"."""
    n = min(len(xs), len(ys))
    if n < 3:
        return 0.0
    mx = sum(xs[:n]) / n
    my = sum(ys[:n]) / n
    sxy = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    sxx = sum((xs[i] - mx) ** 2 for i in range(n))
    return sxy / sxx if sxx > 0 else 0.0


class Baseline(NamedTuple):
    """Section 50 in one object: what this metric normally looks like.

    Build it ONLY from observations available at the decision date. The safe way
    to get one is :func:`pit`, which slices the history for you; calling
    :func:`baseline` on a hand-picked slice is the call that lets leakage in.
    """

    n: int
    mean: float
    median: float
    sd: float
    var: float
    min: float
    max: float
    p10: float
    p25: float
    p50: float
    p75: float
    p90: float
    p95: float
    p99: float
    skew: float
    kurtosis: float


def baseline(values: Sequence[float]) -> Baseline | None:
    """Descriptive statistics for a metric's history. None below 2 observations.

    POINT-IN-TIME: whatever you pass in *is* the baseline. If a single value in
    ``values`` post-dates the decision date, every z-score built from this object
    is contaminated. Prefer :func:`pit`.
    """
    n = len(values)
    if n < 2:
        return None
    xs = sorted(float(v) for v in values)
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / n
    return Baseline(
        n=n,
        mean=mean,
        median=percentile(xs, 50),
        sd=math.sqrt(var),
        var=var,
        min=xs[0],
        max=xs[-1],
        p10=percentile(xs, 10),
        p25=percentile(xs, 25),
        p50=percentile(xs, 50),
        p75=percentile(xs, 75),
        p90=percentile(xs, 90),
        p95=percentile(xs, 95),
        p99=percentile(xs, 99),
        skew=skew(xs),
        kurtosis=kurtosis(xs),
    )


# ---------------------------------------------------------------------------
# sections 51-52 — z-score and historical percentile, point-in-time only
# ---------------------------------------------------------------------------


class PIT(NamedTuple):
    """A value placed against its own prior history. Sections 51 + 52."""

    i: int
    value: float
    z: float  # section 51
    pct: float  # section 52, 0-100
    base: Baseline  # the prior-only history it was measured against

    @property
    def extreme(self) -> bool:
        """Top or bottom 5% of everything this metric has ever done here."""
        return self.pct >= 95.0 or self.pct <= 5.0


def pit(
    series: Sequence[float],
    i: int,
    lookback: int = 0,
    min_obs: int = 20,
) -> PIT | None:
    """Z-score and historical percentile of ``series[i]`` against ``series[:i]``.

    THE POINT-IN-TIME PRIMITIVE. ``series`` is oldest-first. Only elements
    strictly before ``i`` form the baseline — the current value never
    contaminates the mean it is being measured against, and nothing at or after
    ``i`` is read at all. Appending future data to ``series`` cannot change this
    result, which is what :func:`_demo` asserts.

    ``lookback`` 0 = expanding (all prior history); >0 = a trailing window of that
    many observations. Returns None when fewer than ``min_obs`` prior
    observations exist, because a percentile over six numbers is theatre.

    There is deliberately no ``pit(values, current_value)`` overload and no
    ``baseline=`` parameter: those are the shapes that let a full-sample mean in.
    """
    if i <= 0 or i >= len(series):
        return None
    prior = series[max(0, i - lookback) : i] if lookback > 0 else series[:i]
    if len(prior) < min_obs:
        return None
    base = baseline(prior)
    if base is None:
        return None
    value = float(series[i])
    z = (value - base.mean) / base.sd if base.sd > 0 else 0.0
    below = sum(1 for x in prior if x < value)
    equal = sum(1 for x in prior if x == value)
    pct = 100.0 * (below + 0.5 * equal) / len(prior)
    return PIT(i, value, z, max(0.0, min(100.0, pct)), base)


def pit_series(
    series: Sequence[float],
    lookback: int = 0,
    min_obs: int = 20,
) -> list[PIT | None]:
    """:func:`pit` walked over every index — the honest way to build a feature column.

    Entry ``j`` is what you could have known on day ``j``, so this column can be
    handed to the backtest without a second thought.
    """
    return [pit(series, i, lookback, min_obs) for i in range(len(series))]


# ---------------------------------------------------------------------------
# sections 64-65 — executed price distribution
# ---------------------------------------------------------------------------


class PriceProfile(NamedTuple):
    """Sections 64 + 65 — where the day's volume actually printed.

    An executed-price distribution and nothing more. Section 65 is explicit that
    this does not predict future support/resistance orders: we can see the fills,
    never the book behind them.
    """

    distinct: int  # section 64: number of distinct traded prices
    dominant: float  # the price level with the most volume
    dominant_share: float
    hhi: float  # volume concentration across price levels, 0-1
    entropy: float  # normalised Shannon entropy, 0-1 (1 = perfectly spread)
    dispersion: float  # volume-weighted sd of rate / vwap
    low: float
    high: float
    range_pct: float  # (high - low) / vwap
    low_share: float  # section 65: volume in the bottom third of the range
    mid_share: float
    high_share: float
    vwap_position: float  # 0 = vwap at the day's low, 1 = at the high


def price_profile(session: Session) -> PriceProfile:
    """Build the executed-price distribution for one session."""
    levels: Counter[float] = Counter()
    for t in session.trades:
        levels[t.rate] += t.quantity
    total = sum(levels.values())
    if not total:
        return PriceProfile(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.5)

    vwap = session.vwap
    lo, hi = min(levels), max(levels)
    dom, dom_qty = max(levels.items(), key=lambda kv: kv[1])

    hhi = sum((q / total) ** 2 for q in levels.values())
    if len(levels) > 1:
        ent = -sum((q / total) * math.log(q / total) for q in levels.values()) / math.log(len(levels))
    else:
        ent = 0.0
    var = sum(q * (r - vwap) ** 2 for r, q in levels.items()) / total
    disp = math.sqrt(var) / vwap if vwap else 0.0

    if hi > lo:
        buckets = [0, 0, 0]
        for r, q in levels.items():
            pos = (r - lo) / (hi - lo)
            buckets[0 if pos < 1 / 3 else (1 if pos < 2 / 3 else 2)] += q
        shares = tuple(b / total for b in buckets)
        vpos = (vwap - lo) / (hi - lo)
    else:
        # One price all day: the whole range is a point, so "thirds" is nonsense.
        shares = (0.0, 1.0, 0.0)
        vpos = 0.5

    return PriceProfile(
        distinct=len(levels),
        dominant=dom,
        dominant_share=dom_qty / total,
        hhi=hhi,
        entropy=ent,
        dispersion=disp,
        low=lo,
        high=hi,
        range_pct=(hi - lo) / vwap if vwap else 0.0,
        low_share=shares[0],
        mid_share=shares[1],
        high_share=shares[2],
        vwap_position=max(0.0, min(1.0, vpos)),
    )


# ---------------------------------------------------------------------------
# the one-pass workhorse
# ---------------------------------------------------------------------------


class DayStat(NamedTuple):
    """Everything sections 53-73 need about one session, computed in one pass.

    ``tilt`` is the stock-level flow direction and deserves a note. There is no
    stock-level buy/sell imbalance — every share bought is sold, so it is
    identically 0.000 forever, which is why ``brokers.stock_flow`` refuses to
    report one. What is NOT conserved is how lopsidedly that volume is
    distributed: one broker absorbing 30% of the day against ten brokers shedding
    3% each is a genuinely different session from the mirror image. ``tilt`` is
    ``top_buyer_share - top_seller_share``, and :func:`_demo` asserts it varies.
    """

    date: str
    volume: int
    turnover: float
    trades: int
    vwap: float
    brokers: int
    net_buyers: int
    net_sellers: int
    avg_trade: float  # shares per transaction
    median_trade: float
    flow_quality: float  # net shares changing broker hands / volume
    top_buyer_share: float
    top_seller_share: float
    tilt: float  # top_buyer_share - top_seller_share
    concentration: float  # top_buyer_share + top_seller_share
    profile: PriceProfile


def daily(symbol: str, n: int = 0, upto: str | None = None) -> list[DayStat]:
    """Per-session statistics, oldest first. ``n=0`` reads the whole archive.

    ONE parse per session for the whole module — a 1,000-session symbol is
    ~1,000 file reads, so build this once and pass the list around rather than
    calling it per feature. ``upto`` is the point-in-time guard and is handed
    straight to :func:`loader.load_last`.
    """
    out: list[DayStat] = []
    for s in loader.load_last(symbol, n, upto=upto):
        agg = brokers.day(s)
        flow = brokers.stock_flow(agg)
        qtys = sorted(t.quantity for t in s.trades)
        out.append(
            DayStat(
                date=s.date,
                volume=flow.volume,
                turnover=flow.turnover,
                trades=flow.trades,
                vwap=s.vwap,
                brokers=flow.brokers,
                net_buyers=flow.net_buyers,
                net_sellers=flow.net_sellers,
                avg_trade=flow.volume / flow.trades if flow.trades else 0.0,
                median_trade=percentile(qtys, 50),
                flow_quality=flow.flow_quality,
                top_buyer_share=flow.top_buyer_share,
                top_seller_share=flow.top_seller_share,
                tilt=flow.top_buyer_share - flow.top_seller_share,
                concentration=flow.top_buyer_share + flow.top_seller_share,
                profile=price_profile(s),
            )
        )
    return out


def returns(days: Sequence[DayStat]) -> list[float]:
    """Day-over-day log return of the executed VWAP, aligned with ``days``, [0]=0.

    Moves past :data:`CIRCUIT_LOG` are set to 0.0: NEPSE's circuit is +/-15%, so a
    larger gap is a bonus/rights restatement of an unadjusted price series, not a
    trade. Feeding one into a correlation buys a spurious -40% day.
    """
    out = [0.0]
    for prev, cur in zip(days, days[1:]):
        if prev.vwap > 0 and cur.vwap > 0:
            r = math.log(cur.vwap / prev.vwap)
            out.append(0.0 if abs(r) > CIRCUIT_LOG else r)
        else:
            out.append(0.0)
    return out


# ---------------------------------------------------------------------------
# sections 53-55 + 68 — the shape of a flow series
# ---------------------------------------------------------------------------


class FlowShape(NamedTuple):
    """Sections 53, 54, 55 and 68 — all four describe one daily-flow series.

    ``sharpness`` is spec section 53's ``mean/sd``. The spec calls it FlowSharpe
    and then immediately warns it is not one; the name here does not invite the
    confusion in the first place. There is no risk-free rate, no returns and no
    annualisation in it — it is a consistency ratio.
    """

    n: int
    mean: float
    median: float  # mean alone lies when one spike carries the window
    sd: float
    sharpness: float  # section 53
    skew: float  # section 54
    kurtosis: float
    pos_ratio: float  # section 68
    neg_ratio: float
    sign_consistency: float  # share of days on the dominant side, 0.5-1
    consistency: float  # section 68 composite, 0-1
    cum: float  # section 55
    peak: float
    drawdown: float  # current, <= 0 by construction
    max_drawdown: float  # <= 0
    trough_i: int
    days_since_trough: int
    recovery: float  # 0-1, share of the max drawdown regained
    recovery_speed: float  # recovery per day since the trough


def flow_shape(values: Sequence[float]) -> FlowShape | None:
    """Describe a daily net-flow series. None below 3 observations.

    Works on any daily flow series: a stock's ``tilt`` (sections 53-55) or one
    broker-stock pair's flow intensity (section 68). Same maths, so one function.

    Drawdown runs on the CUMULATIVE flow path with an implied 0 start, so the
    peak is never negative and the drawdown is always <= 0. Cumulative flow can
    itself go negative, which is why the drawdown is reported in flow units and
    not as a percentage — a percentage of a negative peak is meaningless.
    """
    n = len(values)
    if n < 3:
        return None
    xs = [float(v) for v in values]
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / n
    sd = math.sqrt(var)

    pos = sum(1 for x in xs if x > 0) / n
    neg = sum(1 for x in xs if x < 0) / n
    sign_consistency = max(pos, neg)
    sharpness = mean / sd if sd > 0 else 0.0
    # Rewards agreeing on a direction AND doing it quietly. Bounded 0-1 so it can
    # sit next to other scores without one term swamping the rest.
    consistency = sign_consistency * (abs(sharpness) / (1.0 + abs(sharpness)))

    cum = 0.0
    peak = 0.0  # implied 0 start: before the window, cumulative flow is 0
    max_dd = 0.0
    trough = 0
    for i, x in enumerate(xs):
        cum += x
        peak = max(peak, cum)
        dd = cum - peak
        if dd < max_dd:
            max_dd = dd
            trough = i
    drawdown = cum - peak

    if max_dd < 0:
        recovery = max(0.0, min(1.0, (drawdown - max_dd) / -max_dd))
    else:
        recovery = 1.0
    since = n - 1 - trough
    return FlowShape(
        n=n,
        mean=mean,
        median=percentile(sorted(xs), 50),
        sd=sd,
        sharpness=sharpness,
        skew=skew(xs),
        kurtosis=kurtosis(xs),
        pos_ratio=pos,
        neg_ratio=neg,
        sign_consistency=sign_consistency,
        consistency=consistency,
        cum=cum,
        peak=peak,
        drawdown=drawdown,
        max_drawdown=max_dd,
        trough_i=trough,
        days_since_trough=since,
        recovery=recovery,
        recovery_speed=recovery / since if since > 0 else recovery,
    )


def broker_flow_series(sessions: Sequence[Session], broker: int) -> list[float]:
    """Section 68 — one broker's daily flow intensity in one stock, oldest first.

    Intensity, not raw shares: a broker's 10,000-share day means something
    different in a stock that trades 20,000 than in one that trades 2,000,000.
    Days the broker sat out are 0.0, which is the honest value — it did not
    trade, so its flow was zero.
    """
    out = []
    for s in sessions:
        agg = brokers.day(s)
        bd = agg.get(broker)
        out.append(brokers.flow_intensity(bd, brokers.stock_flow(agg)) if bd else 0.0)
    return out


# ---------------------------------------------------------------------------
# section 56 — price-flow divergence
# ---------------------------------------------------------------------------


def _trend(values: Sequence[float], flat: float = 0.05) -> int:
    """+1 rising, -1 falling, 0 flat. Slope normalised by the series' own scale.

    ``flat`` is the dead band, as a fraction of mean |value| per day. Without it
    every series "trends" and every divergence pattern fires every day.
    """
    n = len(values)
    if n < 3:
        return 0
    scale = sum(abs(v) for v in values) / n
    if scale <= 0:
        return 0
    s = slope(list(range(n)), list(values)) / scale
    return 1 if s > flat else (-1 if s < -flat else 0)


class Divergence(NamedTuple):
    """Section 56 — the eight named price/flow/volume/concentration mismatches."""

    price_trend: int
    flow_trend: int
    flow_sign: int  # net direction of the tilt over the window
    volume_trend: int
    concentration_trend: int
    patterns: tuple[str, ...]

    @property
    def any(self) -> bool:
        return bool(self.patterns)


def divergences(days: Sequence[DayStat], window: int = 7) -> Divergence:
    """Which of section 56's eight patterns the last ``window`` days match.

    Descriptive only. A divergence is a shape, not a signal — this codebase has
    tested the headline one (big flow, no price move) and found no edge; see
    :func:`absorption_like`.
    """
    w = list(days[-window:])
    if len(w) < 3:
        return Divergence(0, 0, 0, 0, 0, ())

    price = [d.vwap for d in w]
    tilt = [d.tilt for d in w]
    vol = [float(d.volume) for d in w]
    conc = [d.concentration for d in w]

    pt, ft, vt, ct = _trend(price), _trend(tilt), _trend(vol), _trend(conc)
    mean_tilt = sum(tilt) / len(tilt)
    fs = 1 if mean_tilt > 0.01 else (-1 if mean_tilt < -0.01 else 0)

    found = []
    if pt > 0 and fs < 0:
        found.append("price rising + broker net selling")
    if pt < 0 and fs > 0:
        found.append("price falling + broker net buying")
    if ft > 0 and pt == 0:
        found.append("flow increasing + price flat")
    if ft < 0 and pt > 0:
        found.append("flow decreasing + price rising")
    if vt > 0 and pt == 0:
        found.append("volume increasing + price flat")
    if vt < 0 and pt > 0:
        found.append("volume decreasing + price rising")
    if ct > 0 and pt == 0:
        found.append("concentration increasing + price flat")
    if ct < 0 and pt > 0:
        found.append("concentration decreasing + price rising")
    return Divergence(pt, ft, fs, vt, ct, tuple(found))


# ---------------------------------------------------------------------------
# section 57 — price-flow correlation
# ---------------------------------------------------------------------------


class Correlations(NamedTuple):
    """Section 57 over one window. Changes against changes, never level vs change."""

    n: int
    price_flow: float
    volume_price: float
    turnover_price: float
    sensitivity: float  # OLS slope of return on tilt — "flow-price sensitivity"
    regime: str  # convergent | divergent | unclear


CORR_WINDOW = 30
"""Section 57's fixed lookback, in sessions. DELIBERATE, not a truncation bug.

Every symbol's price-flow correlation is computed on the last
:data:`CORR_WINDOW` sessions regardless of how much history was loaded, which is
why section 57 reports n = 29 (one observation is spent differencing) on every
symbol while section 4 reports 32-120 sessions loaded. It is fixed on purpose:
the number is read as "how has flow tracked price *lately*", and letting n follow
the archive would make it a different measurement per symbol — a 120-session
correlation and a 32-session one are not comparable, and ranking symbols on a
mixed-length statistic is a bug that looks like a feature. The board says so on
the row rather than leaving a reader to assume all loaded history was used.
"""


def correlations(days: Sequence[DayStat], window: int = CORR_WINDOW) -> Correlations | None:
    """Correlate price change against flow, volume change and turnover change.

    ``window`` is a FIXED lookback — see :data:`CORR_WINDOW` for why n is the same
    on every symbol. None under 8 observations: a correlation over five points is
    noise with a decimal point. Volume and turnover enter as log changes so both
    sides of every pair are changes — correlating a return against a *level*
    mostly measures how big the stock is.
    """
    w = list(days[-window:])
    if len(w) < 8:
        return None
    rets = returns(w)[1:]
    tilt = [d.tilt for d in w][1:]
    dvol = [
        math.log(b.volume / a.volume) if a.volume > 0 and b.volume > 0 else 0.0
        for a, b in zip(w, w[1:])
    ]
    dto = [
        math.log(b.turnover / a.turnover) if a.turnover > 0 and b.turnover > 0 else 0.0
        for a, b in zip(w, w[1:])
    ]
    pf = pearson(tilt, rets)
    return Correlations(
        n=len(rets),
        price_flow=pf,
        volume_price=pearson(dvol, rets),
        turnover_price=pearson(dto, rets),
        sensitivity=slope(tilt, rets),
        regime="convergent" if pf > 0.3 else ("divergent" if pf < -0.3 else "unclear"),
    )


# ---------------------------------------------------------------------------
# section 58 — flow-price lag
# ---------------------------------------------------------------------------


class LagScan(NamedTuple):
    """Section 58 — cross-correlation of flow against FORWARD price change.

    ``best_lag`` is the lag with the strongest correlation IN SAMPLE. That is a
    fitted parameter, not a finding: scan eleven lags on any series and one of
    them wins by construction. Treat it as a hypothesis to hand to walk-forward
    validation (spec section 99), and never as evidence that flow leads price by
    ``best_lag`` days. ``by_year`` exists to make that obvious — a lead that is
    real shows up in most years, and a fitted one moves around.
    """

    n: int
    best_lag: int
    best_corr: float
    corrs: tuple[tuple[int, float], ...]  # (lag, correlation) for every lag tried
    by_year: tuple[tuple[str, int, float], ...]  # (year, best_lag, corr)


def lag_scan(days: Sequence[DayStat], max_lag: int = 10, min_obs: int = 60) -> LagScan | None:
    """Correlate today's tilt against the price change ``lag`` days later, 0-``max_lag``.

    DESCRIPTIVE, BACKWARD-LOOKING ONLY. It reads price after the flow date by
    definition, so its output must never be fed to a decision at date D. Real
    forward-return labelling — with corporate-action-adjusted prices — lives in
    the backtest module; the unadjusted VWAP used here is fine for a correlation
    only because :func:`returns` drops beyond-circuit restatement days.
    """
    if len(days) < min_obs + max_lag:
        return None
    rets = returns(days)
    tilt = [d.tilt for d in days]

    def scan(t: Sequence[float], r: Sequence[float]) -> tuple[int, float, list[tuple[int, float]]]:
        out = []
        for lag in range(max_lag + 1):
            # tilt[i] against the return on day i+lag. lag 0 = same day.
            n = len(t) - lag
            if n < 10:
                break
            out.append((lag, pearson(t[:n], r[lag : lag + n])))
        if not out:
            return 0, 0.0, []
        best = max(out, key=lambda kv: abs(kv[1]))
        return best[0], best[1], out

    bl, bc, all_lags = scan(tilt, rets)

    per_year: list[tuple[str, int, float]] = []
    years = sorted({d.date[:4] for d in days})
    for y in years:
        idx = [i for i, d in enumerate(days) if d.date[:4] == y]
        if len(idx) < 40:
            continue
        lo, hi = idx[0], idx[-1] + 1
        yl, yc, _ = scan(tilt[lo:hi], rets[lo:hi])
        per_year.append((y, yl, yc))

    return LagScan(len(days), bl, bc, tuple(all_lags), tuple(per_year))


# ---------------------------------------------------------------------------
# sections 59-60 — elasticity and flow efficiency
# ---------------------------------------------------------------------------


class Elasticity(NamedTuple):
    """Section 59 — % price change per % volume change, on days volume moved."""

    n: int
    mean: float
    median: float  # the mean is carried by a thin tail here; read the median
    last: float
    label: str


def elasticity(days: Sequence[DayStat], window: int = 30, min_vol_move: float = 0.05) -> Elasticity | None:
    """Price response per unit of volume expansion. None when nothing moved.

    Days where volume barely changed are dropped, not divided by: the ratio
    explodes as the denominator goes to zero and one such day owns the average.
    Mean AND median are reported because in this market the mean of anything
    return-shaped is carried by a handful of days.
    """
    w = list(days[-window:])
    if len(w) < 5:
        return None
    ratios = []
    for a, b in zip(w, w[1:]):
        if a.volume <= 0 or b.volume <= 0 or a.vwap <= 0 or b.vwap <= 0:
            continue
        dv = b.volume / a.volume - 1.0
        if abs(dv) < min_vol_move:
            continue
        dp = b.vwap / a.vwap - 1.0
        if abs(dp) > CIRCUIT_LOG:
            continue  # restatement, not a trade
        ratios.append(dp / dv)
    if not ratios:
        return None
    mean = sum(ratios) / len(ratios)
    med = percentile(sorted(ratios), 50)
    last = ratios[-1]
    if abs(med) < 0.05:
        label = "volume expansion + weak price response"
    elif med > 0:
        label = "volume expansion + price following"
    else:
        label = "volume expansion + price fading"
    return Elasticity(len(ratios), mean, med, last, label)


class FlowEfficiency(NamedTuple):
    """Section 60 — price movement per unit of net flow, both sides normalised."""

    ret_pct: float  # window log return, in %
    flow_units: float  # sum of daily tilt; each day is a share of that day's volume
    efficiency: float
    label: str


def flow_efficiency(days: Sequence[DayStat], window: int = 7, min_flow: float = 0.05) -> FlowEfficiency | None:
    """How much price the flow bought. None when there was no directional flow.

    Both terms are normalised, so this IS comparable across stocks — which is the
    caveat spec section 60 attaches. The denominator is a share of each day's own
    volume, not raw shares, so a large stock and a small one land on the same
    scale. Below ``min_flow`` cumulative tilt the ratio is division by noise and
    None is returned rather than a big meaningless number.
    """
    w = list(days[-window:])
    if len(w) < 3:
        return None
    ret_pct = sum(returns(w)) * 100.0
    flow_units = sum(d.tilt for d in w)
    if abs(flow_units) < min_flow:
        return None
    eff = ret_pct / flow_units
    if flow_units > 0:
        label = "efficient buying" if ret_pct > 0 else "inefficient buying"
    else:
        label = "efficient selling" if ret_pct < 0 else "inefficient selling"
    return FlowEfficiency(ret_pct, flow_units, eff, label)


# ---------------------------------------------------------------------------
# sections 61-62 — the two proxies that this codebase has already tested
# ---------------------------------------------------------------------------


def _clamp01(z: float, lo: float = 0.0, hi: float = 2.0) -> float:
    """Squash a z-score into 0-1 so score components can be averaged fairly."""
    return max(0.0, min(1.0, (z - lo) / (hi - lo)))


class ProxyDay(NamedTuple):
    """One day's proxy score, with the reasons kept attached (spec section 107)."""

    date: str
    score: float  # 0-100
    parts: tuple[tuple[str, float], ...]  # (component, 0-1 contribution)
    persistence: int  # of the last 5 days including this one, how many scored high
    prior_frequency: float  # share of PRIOR days that scored high, 0-1


def _proxy_run(
    days: Sequence[DayStat],
    components: Sequence[tuple[str, Sequence[float], bool]],
    lookback: int,
    min_obs: int,
    threshold: float,
) -> list[ProxyDay | None]:
    """Blend point-in-time z-scores of several series into a 0-100 daily score.

    Every component goes through :func:`pit`, so each day's score is measured
    against that symbol's history *as of that day only*. ``prior_frequency`` is
    likewise a running count over earlier days — never the full sample.
    """
    cols = [(name, pit_series(vals, lookback, min_obs), invert) for name, vals, invert in components]
    out: list[ProxyDay | None] = []
    hits = seen = 0
    recent: list[bool] = []
    for i, d in enumerate(days):
        parts = []
        for name, col, invert in cols:
            p = col[i]
            if p is None:
                parts = []
                break
            parts.append((name, _clamp01(-p.z if invert else p.z)))
        if not parts:
            out.append(None)
            continue
        score = 100.0 * sum(v for _, v in parts) / len(parts)
        recent.append(score >= threshold)
        recent[:] = recent[-5:]
        out.append(
            ProxyDay(
                date=d.date,
                score=score,
                parts=tuple(parts),
                persistence=sum(recent),
                prior_frequency=hits / seen if seen else 0.0,
            )
        )
        seen += 1
        hits += 1 if score >= threshold else 0
    return out


def absorption_like(
    days: Sequence[DayStat],
    lookback: int = 250,
    min_obs: int = 60,
    threshold: float = 60.0,
) -> list[ProxyDay | None]:
    """Section 61 — ABSORPTION-LIKE PROXY. Never confirmed order-book absorption.

    Large volume + strongly directional broker flow + limited price movement. The
    floorsheet shows fills, not resting orders, so it cannot distinguish a wall
    of bids soaking up supply from a quiet day that happened to be lopsided.

    READ THIS BEFORE BUILDING ON IT. This idea has already been tested in this
    codebase, as a broker-level "quiet absorption" factor with exactly these
    ingredients, and it has NO EDGE — the result was flat, and it is recorded as
    rejected. It is implemented because the spec asks for a descriptive flag, and
    it is descriptive: it tells you the day was lopsided and still, not that the
    stock is going up. Anything predictive must come from walk-forward evidence,
    not from this score being high.

    Returns one entry per day, aligned with ``days``, None until there is enough
    prior history. Point-in-time throughout.
    """
    return _proxy_run(
        days,
        [
            ("volume", [float(d.volume) for d in days], False),
            ("flow direction", [abs(d.tilt) for d in days], False),
            ("price stillness", [abs(r) for r in returns(days)], True),
        ],
        lookback,
        min_obs,
        threshold,
    )


def exhaustion_like(
    days: Sequence[DayStat],
    lookback: int = 250,
    min_obs: int = 60,
    threshold: float = 60.0,
) -> list[ProxyDay | None]:
    """Section 62 — EXHAUSTION-LIKE PROXY. Never confirmed exhaustion.

    Extreme volume + extreme trade size + extreme concentration + a weakening
    price response.

    READ THIS BEFORE BUILDING ON IT. The supply-dry-up / seller-exhaustion score
    was built and tested here and came back flat, and the literal thesis was
    worse than flat: a persistent dominant buyer had the WRONG sign. So a high
    score here is a description of an unusual day, not a turn.

    ``prior_frequency`` answers spec section 62's "historical frequency" using
    only earlier days. "Historical outcome" and "signal decay" are deliberately
    absent: both need forward returns on corporate-action-adjusted prices, which
    is the backtest module's job, and computing them here would put future prices
    inside a decision-date feature.
    """
    return _proxy_run(
        days,
        [
            ("volume", [float(d.volume) for d in days], False),
            ("trade size", [d.avg_trade for d in days], False),
            ("concentration", [d.concentration for d in days], False),
            ("price stillness", [abs(r) for r in returns(days)], True),
        ],
        lookback,
        min_obs,
        threshold,
    )


# ---------------------------------------------------------------------------
# section 63 — executed liquidity
# ---------------------------------------------------------------------------


class ExecutedLiquidity(NamedTuple):
    """Section 63 — EXECUTED LIQUIDITY PROXY. Never actual order-book liquidity.

    The floorsheet records what traded, never what was resting on the book. A
    stock can print heavy turnover into a thin book and a quiet stock can have
    deep untouched bids; neither is visible here. Every field below is an
    execution fact, and ``score`` is only meaningful as a rank against the SAME
    symbol's own prior history — it is a point-in-time percentile blend, not a
    number you can compare across stocks.
    """

    date: str
    turnover: float
    trades: int
    avg_trade: float
    median_trade: float
    dispersion: float  # volume-weighted price dispersion / vwap
    price_hhi: float  # volume concentration across executed price levels
    score: float | None  # 0-100 percentile blend vs prior history, None if too new


def executed_liquidity(
    days: Sequence[DayStat],
    lookback: int = 250,
    min_obs: int = 60,
) -> list[ExecutedLiquidity | None]:
    """Build the executed-liquidity proxy for every day, point-in-time.

    Score = mean of the historical percentiles of turnover, trade count and
    median trade size, minus the percentile of price dispersion (wide dispersion
    for the same turnover means the fills walked the price, which is the
    execution-cost half of the idea).
    """
    cols = {
        "turnover": pit_series([d.turnover for d in days], lookback, min_obs),
        "trades": pit_series([float(d.trades) for d in days], lookback, min_obs),
        "median_trade": pit_series([d.median_trade for d in days], lookback, min_obs),
        "dispersion": pit_series([d.profile.dispersion for d in days], lookback, min_obs),
    }
    out: list[ExecutedLiquidity | None] = []
    for i, d in enumerate(days):
        ps = {k: v[i] for k, v in cols.items()}
        if any(p is None for p in ps.values()):
            score = None
        else:
            good = (ps["turnover"].pct + ps["trades"].pct + ps["median_trade"].pct) / 3.0
            score = max(0.0, min(100.0, good - 0.5 * (ps["dispersion"].pct - 50.0)))
        out.append(
            ExecutedLiquidity(
                date=d.date,
                turnover=d.turnover,
                trades=d.trades,
                avg_trade=d.avg_trade,
                median_trade=d.median_trade,
                dispersion=d.profile.dispersion,
                price_hhi=d.profile.hhi,
                score=score,
            )
        )
    return out


# ---------------------------------------------------------------------------
# sections 66-67 — broker behaviour against price
# ---------------------------------------------------------------------------


def _wmedian(pairs: Sequence[tuple[float, int]]) -> float:
    """Volume-weighted median rate. ``pairs`` is (rate, quantity)."""
    if not pairs:
        return 0.0
    ordered = sorted(pairs)
    half = sum(q for _, q in ordered) / 2.0
    run = 0.0
    for rate, q in ordered:
        run += q
        if run >= half:
            return rate
    return ordered[-1][0]


class Asymmetry(NamedTuple):
    """Section 66 — one broker's buy side against its sell side, in one session."""

    broker: int
    buy_qty: int
    sell_qty: int
    buy_vwap: float
    sell_vwap: float
    buy_median: float
    sell_median: float
    buy_low: float
    buy_high: float
    sell_low: float
    sell_high: float
    spread: float  # (sell_vwap - buy_vwap) / stock vwap; +ve = sold dearer than bought
    qty_ratio: float  # buy / sell shares
    turnover_ratio: float


def price_asymmetry(session: Session) -> dict[int, Asymmetry]:
    """Section 66 for every broker in one session.

    ``spread`` is only meaningful for a broker that traded BOTH sides that day;
    for a one-sided broker it is 0.0 rather than a fabricated number.
    """
    buys: dict[int, list[tuple[float, int]]] = {}
    sells: dict[int, list[tuple[float, int]]] = {}
    for t in session.trades:
        buys.setdefault(t.buyer, []).append((t.rate, t.quantity))
        sells.setdefault(t.seller, []).append((t.rate, t.quantity))

    agg = brokers.day(session)
    svwap = session.vwap
    out: dict[int, Asymmetry] = {}
    for b, bd in agg.items():
        bp = buys.get(b, [])
        sp = sells.get(b, [])
        both = bd.buy_qty > 0 and bd.sell_qty > 0
        out[b] = Asymmetry(
            broker=b,
            buy_qty=bd.buy_qty,
            sell_qty=bd.sell_qty,
            buy_vwap=bd.buy_vwap,
            sell_vwap=bd.sell_vwap,
            buy_median=_wmedian(bp),
            sell_median=_wmedian(sp),
            buy_low=min((r for r, _ in bp), default=0.0),
            buy_high=max((r for r, _ in bp), default=0.0),
            sell_low=min((r for r, _ in sp), default=0.0),
            sell_high=max((r for r, _ in sp), default=0.0),
            spread=((bd.sell_vwap - bd.buy_vwap) / svwap) if both and svwap else 0.0,
            qty_ratio=(bd.buy_qty / bd.sell_qty) if bd.sell_qty else float(bd.buy_qty > 0),
            turnover_ratio=(bd.buy_amt / bd.sell_amt) if bd.sell_amt else float(bd.buy_amt > 0),
        )
    return out


class PriceQuality(NamedTuple):
    """Section 67 — a broker's fills bucketed against the stock's own daily VWAP.

    ZERO-SUM ACROSS BROKERS BY CONSTRUCTION, and that is fine. Every trade below
    VWAP is a buy-below for one broker and a sell-below for another, so
    ``quality`` summed over all brokers is exactly 0. It is meaningful per broker
    and there is deliberately no stock-level version — the same conservation law
    that makes stock-level buy/sell imbalance identically 0.000 applies here.
    """

    broker: int
    buy_below: int
    buy_near: int
    buy_above: int
    sell_below: int
    sell_near: int
    sell_above: int
    quality: float  # -1 to +1: bought cheap and sold dear vs the day's vwap


def flow_quality_by_price(sessions: Sequence[Session], band: float = 0.0025) -> dict[int, PriceQuality]:
    """Section 67 across a window (3D/7D/15D/30D — pass the sessions you want).

    Each session is scored against ITS OWN VWAP, then the buckets are summed. A
    30-day window compared against a single 30-day VWAP would score the trend
    rather than the execution: in a rising stock every early buy looks brilliant.

    ``band`` is the "near VWAP" tolerance as a fraction of VWAP.
    """
    buckets: dict[int, list[int]] = {}
    for s in sessions:
        v = s.vwap
        if v <= 0:
            continue
        lo, hi = v * (1 - band), v * (1 + band)
        for t in s.trades:
            k = 0 if t.rate < lo else (1 if t.rate <= hi else 2)
            buckets.setdefault(t.buyer, [0] * 6)[k] += t.quantity
            buckets.setdefault(t.seller, [0] * 6)[3 + k] += t.quantity

    out: dict[int, PriceQuality] = {}
    for b, q in buckets.items():
        total = sum(q)
        # Bought below + sold above = good execution; the reverse = bad.
        good = q[0] - q[2] + q[5] - q[3]
        out[b] = PriceQuality(b, q[0], q[1], q[2], q[3], q[4], q[5], good / total if total else 0.0)
    return out


# ---------------------------------------------------------------------------
# sections 69-71 — market-wide. EXPENSIVE. Read the cost note.
# ---------------------------------------------------------------------------


class MarketFlow(NamedTuple):
    """Sections 69 + 70 over an explicit list of symbols."""

    date_from: str
    date_to: str
    requested: int
    active: int  # symbols that actually traded in the window
    trades: int
    volume: int
    turnover: float
    brokers: int  # distinct brokers active anywhere in the window
    top_broker: int | None
    top_broker_share: float  # that broker's gross turnover / market gross turnover
    top_net_buyer: int | None
    top_net_buyer_share: float  # its market-wide net money / market turnover
    buyer_hhi: float  # section 69: concentration of market buying across brokers
    seller_hhi: float
    positive: int  # section 70: symbols with a positive flow tilt
    negative: int
    flat: int
    breadth: float  # positive / (positive + negative)
    turnover_breadth: float  # share of market turnover in positive-tilt symbols
    volume_breadth: float
    label: str  # market accumulation | distribution | mixed


def market_flow(symbols: Sequence[str], upto: str | None = None, n: int = 1) -> MarketFlow:
    """Sections 69-70. COST: reads up to ``n * len(symbols)`` floorsheet files.

    The market is ~593 symbols, so one day market-wide is ~593 file parses and a
    30-day window is ~18,000. ``symbols`` is REQUIRED and there is no default:
    this function will not quietly scan the market because someone forgot an
    argument. Pass ``loader.symbols()`` explicitly when you really mean all of
    it, and expect to wait.

    ``upto`` is the point-in-time guard, handed to :func:`loader.load_last`.
    """
    market: dict[int, brokers.BrokerDay] = {}
    trades = volume = 0
    turnover = 0.0
    pos = neg = flat = active = 0
    pos_to = pos_vol = 0.0
    lo_date, hi_date = "", ""

    for sym in symbols:
        ses = loader.load_last(sym, n, upto=upto)
        if not ses:
            continue
        active += 1
        lo_date = min(lo_date or ses[0].date, ses[0].date)
        hi_date = max(hi_date, ses[-1].date)
        agg = brokers.window(ses)
        flow = brokers.stock_flow(agg)
        trades += flow.trades
        volume += flow.volume
        turnover += flow.turnover
        for b, bd in agg.items():
            market[b] = market[b].plus(bd) if b in market else bd
        tilt = flow.top_buyer_share - flow.top_seller_share
        if tilt > 0.01:
            pos += 1
            pos_to += flow.turnover
            pos_vol += flow.volume
        elif tilt < -0.01:
            neg += 1
        else:
            flat += 1

    if not market:
        return MarketFlow("", "", len(symbols), 0, 0, 0, 0.0, 0, None, 0.0, None, 0.0,
                          0.0, 0.0, 0, 0, 0, 0.0, 0.0, 0.0, "no data")

    gross = sum(b.gross_amt for b in market.values())
    tb = max(market.values(), key=lambda b: b.gross_amt)
    tn = max(market.values(), key=lambda b: b.net_amt)
    buy_tot = sum(b.buy_qty for b in market.values())
    sell_tot = sum(b.sell_qty for b in market.values())
    b_hhi = sum((b.buy_qty / buy_tot) ** 2 for b in market.values()) if buy_tot else 0.0
    s_hhi = sum((b.sell_qty / sell_tot) ** 2 for b in market.values()) if sell_tot else 0.0

    breadth = pos / (pos + neg) if (pos + neg) else 0.0
    return MarketFlow(
        date_from=lo_date,
        date_to=hi_date,
        requested=len(symbols),
        active=active,
        trades=trades,
        volume=volume,
        turnover=turnover,
        brokers=len(market),
        top_broker=tb.broker,
        top_broker_share=tb.gross_amt / gross if gross else 0.0,
        top_net_buyer=tn.broker,
        top_net_buyer_share=tn.net_amt / turnover if turnover else 0.0,
        buyer_hhi=b_hhi,
        seller_hhi=s_hhi,
        positive=pos,
        negative=neg,
        flat=flat,
        breadth=breadth,
        turnover_breadth=pos_to / turnover if turnover else 0.0,
        volume_breadth=pos_vol / volume if volume else 0.0,
        label="market accumulation" if breadth > 0.6 else ("market distribution" if breadth < 0.4 else "mixed"),
    )


def sector_map() -> dict[str, str]:
    """Symbol -> sector, from ``Master_data/sectors.txt``. Empty dict if absent.

    Spec section 71: sector is EXTERNAL and static. It is not a floorsheet field
    and nothing in this package may infer it from trades. The file is a
    tab-separated ``symbol<TAB>sector`` table with a header row. A missing or
    unreadable file degrades to ``{}`` — sector analytics simply do not run —
    rather than taking the whole board down.
    """
    if not os.path.isfile(SECTORS):
        return {}
    out: dict[str, str] = {}
    try:
        with open(SECTORS, "r", encoding="utf-8", errors="replace") as fh:
            for i, ln in enumerate(fh):
                parts = ln.rstrip("\n").split("\t")
                if len(parts) < 2:
                    continue
                if i == 0 and parts[0].strip().lower() == "symbol":
                    continue
                sym, sec = parts[0].strip().upper(), parts[1].strip()
                if sym and sec:
                    out[sym] = sec
    except OSError:
        return {}
    return out


class SectorFlow(NamedTuple):
    """Section 71 — one sector's aggregate over the requested symbols."""

    sector: str
    symbols: int
    volume: int
    turnover: float
    trades: int
    brokers: int
    net_tilt: float  # mean flow tilt of the sector's symbols
    positive: int
    negative: int
    breadth: float
    concentration: float  # HHI of buying across brokers within the sector
    top_broker: int | None
    top_broker_share: float


def sector_flow(symbols: Sequence[str], upto: str | None = None, n: int = 1) -> dict[str, SectorFlow]:
    """Section 71. COST: same as :func:`market_flow` — ``n * len(symbols)`` files.

    Returns ``{}`` when the sector map is missing, and silently ignores symbols
    the map does not cover (new listings usually). ``symbols`` is required for
    the same reason as in :func:`market_flow`.
    """
    smap = sector_map()
    if not smap:
        return {}

    acc: dict[str, dict] = {}
    for sym in symbols:
        sec = smap.get(sym.upper())
        if not sec:
            continue
        ses = loader.load_last(sym, n, upto=upto)
        if not ses:
            continue
        agg = brokers.window(ses)
        flow = brokers.stock_flow(agg)
        a = acc.setdefault(sec, {"n": 0, "vol": 0, "to": 0.0, "tr": 0, "tilt": [], "pos": 0, "neg": 0, "bk": {}})
        a["n"] += 1
        a["vol"] += flow.volume
        a["to"] += flow.turnover
        a["tr"] += flow.trades
        tilt = flow.top_buyer_share - flow.top_seller_share
        a["tilt"].append(tilt)
        if tilt > 0.01:
            a["pos"] += 1
        elif tilt < -0.01:
            a["neg"] += 1
        for b, bd in agg.items():
            a["bk"][b] = a["bk"][b].plus(bd) if b in a["bk"] else bd

    out: dict[str, SectorFlow] = {}
    for sec, a in acc.items():
        bk = a["bk"]
        buy_tot = sum(b.buy_qty for b in bk.values())
        top = max(bk.values(), key=lambda b: b.buy_qty, default=None)
        pn = a["pos"] + a["neg"]
        out[sec] = SectorFlow(
            sector=sec,
            symbols=a["n"],
            volume=a["vol"],
            turnover=a["to"],
            trades=a["tr"],
            brokers=len(bk),
            net_tilt=sum(a["tilt"]) / len(a["tilt"]) if a["tilt"] else 0.0,
            positive=a["pos"],
            negative=a["neg"],
            breadth=a["pos"] / pn if pn else 0.0,
            concentration=sum((b.buy_qty / buy_tot) ** 2 for b in bk.values()) if buy_tot else 0.0,
            top_broker=top.broker if top else None,
            top_broker_share=(top.buy_qty / buy_tot) if top and buy_tot else 0.0,
        )
    return out


# ---------------------------------------------------------------------------
# sections 69-71 again — the WHOLE-market pass the builder actually runs
# ---------------------------------------------------------------------------


class BrokerRank(NamedTuple):
    """Section 69's broker activity ranking: one broker, market-wide, one session."""

    broker: int
    gross_qty: int  # bought + sold across every stock it touched
    gross_share: float  # of the market's gross quantity
    net_qty: int
    net_share: float  # signed, as a share of market volume
    symbols: int  # how many stocks it traded

    # No trade count: the broker_flow table records ONE combined count per broker-day
    # with no honest buy/sell split, so a per-broker "trades" here would either double
    # count or invent a split. Gross quantity and stocks touched are the activity.


class SectorPass(NamedTuple):
    """Section 71 — one sector on the session, plus its rotation against the window.

    No ``buying``/``selling`` fields, deliberately: inside a sector every share
    bought is a share sold, so sector buy quantity == sector sell quantity ==
    ``volume`` on every sector, every day. Three columns of the same number is
    what the nine cut metrics in this package all looked like. What is real is
    how that volume is *distributed* — ``net_tilt``, ``breadth``,
    ``concentration`` — and how the sector's share of the market moved.
    """

    sector: str
    symbols: int  # mapped symbols that have a broker_flow table
    active: int  # of those, how many traded on the session
    volume: int
    turnover: float  # recorded money — indicative, see :func:`market_pass`
    trades: int
    brokers: int
    net_tilt: float  # mean per-stock (top net buyer share - top net seller share)
    positive: int
    negative: int
    breadth: float
    concentration: float  # HHI of buy quantity across the sector's brokers
    top_broker: int | None
    top_broker_share: float
    # Shares are of the SECTOR-MAPPED universe, not of the whole market, so they sum
    # to 1 and `rotation` sums to 0 — one sector can only gain share at another's
    # expense, which is what rotation means. Against total market volume they were
    # negative on all 13 sectors at once, because the ~308 unmapped symbols are a
    # different fraction of volume on the session than over the window; that is a
    # coverage artefact of sectors.txt, not sector rotation.
    volume_share: float
    prior_share: float  # the same share over the preceding `window` sessions
    rotation: float  # volume_share - prior_share


class MarketPass(NamedTuple):
    """Sections 69 + 70 + 71 together — one scan, one session, the whole market."""

    date: str  # the market's latest session at or before `upto`
    requested: int
    covered: int  # symbols with broker_flow rows at or before `upto`
    skipped: int  # symbols with none — no table, or nothing yet at this date. Counted, never fatal
    active: int  # symbols that actually traded on `date`
    mapped: int  # symbols the external sector map covers, of `requested`
    seconds: float  # measured cost of this pass, so the output can report it

    # -- section 69 -----------------------------------------------------------
    trades: int  # transactions, market-wide
    volume: int
    turnover: float  # recorded money — indicative only
    brokers: int
    broker_hhi: float  # concentration of gross activity across brokers
    buyer_hhi: float
    seller_hhi: float
    top_broker: int | None
    top_broker_share: float
    top_net_buyer: int | None
    top_net_buyer_share: float
    net_buyers: int  # brokers that ended the session net long
    net_sellers: int
    ranking: tuple[BrokerRank, ...]

    # Section 69's "market accumulation" and "market distribution" are these two counts
    # and nothing more. :func:`market_flow` also renders them as a three-valued verdict
    # ("market accumulation" above 0.6 breadth, "market distribution" below 0.4); that
    # label is NOT carried here because it cannot say all three things. Measured over ten
    # sessions spanning fourteen months, breadth ran 0.375-0.539 and never came near 0.6
    # — the largest net seller in a stock is usually bigger than the largest net buyer,
    # so the tilt is structurally negative. A verdict whose bullish branch is unreachable
    # is the failure mode this repo already has a memo about.
    accumulating: int  # stocks whose flow tilts to the buy side
    distributing: int

    # -- section 70 -----------------------------------------------------------
    flat: int
    breadth: float
    breadth_pct: float
    broker_breadth: float  # net-buying brokers / (net buyers + net sellers)
    turnover_breadth: float
    volume_breadth: float

    # -- section 71 -----------------------------------------------------------
    sectors: tuple[SectorPass, ...]


def market_pass(symbols: Sequence[str], upto: str | None = None, window: int = 20) -> MarketPass:
    """Sections 69-71 for the whole market, cheap enough to run once per build.

    WHY THIS EXISTS NEXT TO :func:`market_flow`
    ------------------------------------------
    :func:`market_flow` re-parses the raw floorsheet, which is right when the money
    has to be right (it recomputes ``quantity * rate``) but wrong for a build: a
    market snapshot needs one session, a *rotation* needs twenty, and twenty
    sessions x 593 symbols is ~12,000 floorsheet files. This one reads
    ``Master_data/broker_flow/<SYM>.txt`` instead — ONE file per symbol, whatever
    the window — and that is the only reason sections 69-71 are affordable at all.

    The price of the fast path is money: ``buy_amount``/``sell_amount`` in that
    table are summed from the floorsheet's *recorded* ``amount`` column, which is
    unreliable in the 2022-era files. Every measure here that decides anything is
    therefore a QUANTITY measure; ``turnover`` is carried because section 69 asks
    for it and is labelled indicative wherever it is printed.

    MEASURED COST: all 593 symbols in **33 s** warm-cache, and **40-46 s** measured
    across six point-in-time dates. Nearly all of it is :func:`loader.flow_rows`
    parsing the 11.5 M rows those tables hold (37 s on its own); the aggregation
    below adds only a few seconds on top. Reported back as ``seconds`` so the
    section can print what the run it belongs to actually cost.

    WHAT IS DELIBERATELY NOT HERE
    -----------------------------
    "Market-wide net broker flow" from the spec's section 69 list is not a metric.
    Summed across all brokers it is identically zero on every session forever —
    every share bought is a share sold — exactly like the stock-level imbalance
    :func:`brokers.stock_flow` already refuses to report. The non-degenerate
    version is the DISTRIBUTION: how many brokers ended net long vs net short
    (``net_buyers``/``net_sellers``, ``broker_breadth``) and how many stocks tilt
    to the buy side (``accumulating``/``distributing``, ``breadth``). Same for the
    sector version — see :class:`SectorPass`. There is no market accumulation/
    distribution *verdict* either, for a measured reason recorded on the fields.

    ``upto`` is the point-in-time guard, threaded into :func:`loader.flow_rows`
    exactly as the per-symbol path threads it into ``load_last``. Symbols with no
    broker_flow table are skipped and counted, never raised.
    """
    started = time.time()
    smap = sector_map()

    # Per symbol: its own last session, that session's per-broker aggregate, and the
    # transaction count. Held for every symbol because the market's session is only
    # known once every symbol has been read.
    snap: dict[str, tuple[str, dict[int, brokers.BrokerDay], int]] = {}
    mkt_vol: dict[str, int] = {}  # date -> market volume, for the rotation window
    sec_vol: dict[str, dict[str, int]] = {}  # sector -> date -> volume
    requested = len(symbols)
    skipped = 0

    for sym in symbols:
        sym = sym.upper()
        rows = loader.flow_rows(sym, upto=upto)
        if not rows:
            skipped += 1
            continue
        # build_broker_flow writes the table date-ascending, so the tail IS the recent
        # window and walking backwards stops after ~20 sessions instead of aggregating
        # all ~19,000 rows of full history each file holds. (flow_rows has already PARSED
        # them all — that cost is unavoidable without touching loader — but this at least
        # does not walk them a second time.) _demo pins that ordering: if the table ever
        # stops being sorted, this reads the wrong window in silence rather than erroring.
        last = rows[-1].date
        agg: dict[int, brokers.BrokerDay] = {}
        sides = 0
        vol: dict[str, int] = {}
        for r in reversed(rows):
            if r.date not in vol:
                if len(vol) > window:
                    break
                vol[r.date] = 0
            vol[r.date] += r.bought
            if r.date == last:
                # trades is left at 0 on both sides on purpose: the table records ONE
                # combined count per broker-day and there is no honest buy/sell split.
                # The session's transaction count is recovered below instead.
                bd = brokers.BrokerDay(r.broker, r.bought, r.buy_amount, 0, 0,
                                       r.sold, r.sell_amount, 0, 0)
                agg[r.broker] = agg[r.broker].plus(bd) if r.broker in agg else bd
                sides += r.trades
        # Every transaction increments the count for its buyer AND its seller, so the
        # sum over brokers is two sides per trade.
        snap[sym] = (last, agg, sides // 2)

        sec = smap.get(sym)
        by_sec = sec_vol.setdefault(sec, {}) if sec else None
        for d, v in vol.items():
            mkt_vol[d] = mkt_vol.get(d, 0) + v
            if by_sec is not None:
                by_sec[d] = by_sec.get(d, 0) + v

    mapped = sum(1 for s in symbols if s.upper() in smap)
    if not snap:
        return MarketPass("", requested, 0, skipped, 0, mapped, time.time() - started,
                          0, 0, 0.0, 0, 0.0, 0.0, 0.0, None, 0.0, None, 0.0, 0, 0, (),
                          0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, ())

    date = max(v[0] for v in snap.values())

    market: dict[int, brokers.BrokerDay] = {}
    bsym: dict[int, int] = {}
    sec_brokers: dict[str, dict[int, brokers.BrokerDay]] = {}
    sec_acc: dict[str, dict] = {}
    for sym in snap:
        sec = smap.get(sym)
        if sec:
            sec_acc.setdefault(sec, {"n": 0, "act": 0, "vol": 0, "to": 0.0, "tr": 0,
                                     "tilt": [], "pos": 0, "neg": 0})["n"] += 1

    active = trades = volume = pos = neg = flat = pos_vol = 0
    turnover = pos_to = 0.0

    for sym, (last, agg, txn) in snap.items():
        if last != date or not agg:
            continue
        active += 1
        f = brokers.stock_flow(agg)
        trades += txn
        volume += f.volume
        turnover += f.turnover
        # Same definition the per-symbol path uses (see __main__._daystats): the top net
        # buyer's share of volume minus the top net seller's. A stock's own net flow is
        # zero by construction; its tilt is not.
        tilt = f.top_buyer_share - f.top_seller_share
        if tilt > 0.01:
            pos += 1
            pos_vol += f.volume
            pos_to += f.turnover
        elif tilt < -0.01:
            neg += 1
        else:
            flat += 1
        for b, bd in agg.items():
            market[b] = market[b].plus(bd) if b in market else bd
            bsym[b] = bsym.get(b, 0) + 1
        sec = smap.get(sym)
        if not sec:
            continue
        sb = sec_brokers.setdefault(sec, {})
        for b, bd in agg.items():
            sb[b] = sb[b].plus(bd) if b in sb else bd
        a = sec_acc[sec]
        a["act"] += 1
        a["vol"] += f.volume
        a["to"] += f.turnover
        a["tr"] += txn
        a["tilt"].append(tilt)
        if tilt > 0.01:
            a["pos"] += 1
        elif tilt < -0.01:
            a["neg"] += 1

    gross = sum(b.gross_qty for b in market.values())
    buy_tot = sum(b.buy_qty for b in market.values())
    sell_tot = sum(b.sell_qty for b in market.values())
    tb = max(market.values(), key=lambda b: b.gross_qty, default=None)
    tn = max(market.values(), key=lambda b: b.net_qty, default=None)
    net_buyers = sum(1 for b in market.values() if b.net_qty > 0)
    net_sellers = sum(1 for b in market.values() if b.net_qty < 0)

    ranking = tuple(
        BrokerRank(
            broker=b.broker,
            gross_qty=b.gross_qty,
            gross_share=b.gross_qty / gross if gross else 0.0,
            net_qty=b.net_qty,
            net_share=b.net_qty / volume if volume else 0.0,
            symbols=bsym.get(b.broker, 0),
        )
        for b in sorted(market.values(), key=lambda b: -b.gross_qty)[:10]
    )

    # The market's own recent calendar: the union of every symbol's trailing dates,
    # last `window`+1 of them. A symbol cannot trade more than `window` times inside a
    # `window`-session span, so its trailing slice always contains all of its trades in
    # this range — the union is complete, not a sample.
    prior = [d for d in sorted(mkt_vol)[-(window + 1):] if d < date]

    # Both share denominators are the mapped universe — see SectorPass.
    s_prior = {sec: sum(sec_vol.get(sec, {}).get(d, 0) for d in prior) for sec in sec_acc}
    map_now = sum(a["vol"] for a in sec_acc.values())
    map_prior = sum(s_prior.values())

    sectors: list[SectorPass] = []
    for sec, a in sorted(sec_acc.items()):
        bk = sec_brokers.get(sec, {})
        s_buy = sum(b.buy_qty for b in bk.values())
        top = max(bk.values(), key=lambda b: b.buy_qty, default=None)
        pn = a["pos"] + a["neg"]
        share = a["vol"] / map_now if map_now else 0.0
        p_share = s_prior[sec] / map_prior if map_prior else 0.0
        sectors.append(SectorPass(
            sector=sec,
            symbols=a["n"],
            active=a["act"],
            volume=a["vol"],
            turnover=a["to"],
            trades=a["tr"],
            brokers=len(bk),
            net_tilt=sum(a["tilt"]) / len(a["tilt"]) if a["tilt"] else 0.0,
            positive=a["pos"],
            negative=a["neg"],
            breadth=a["pos"] / pn if pn else 0.0,
            concentration=sum((b.buy_qty / s_buy) ** 2 for b in bk.values()) if s_buy else 0.0,
            top_broker=top.broker if top else None,
            top_broker_share=(top.buy_qty / s_buy) if top and s_buy else 0.0,
            volume_share=share,
            prior_share=p_share,
            rotation=share - p_share,
        ))

    breadth = pos / (pos + neg) if (pos + neg) else 0.0
    return MarketPass(
        date=date,
        requested=requested,
        covered=len(snap),
        skipped=skipped,
        active=active,
        mapped=mapped,
        seconds=time.time() - started,
        trades=trades,
        volume=volume,
        turnover=turnover,
        brokers=len(market),
        broker_hhi=sum((b.gross_qty / gross) ** 2 for b in market.values()) if gross else 0.0,
        buyer_hhi=sum((b.buy_qty / buy_tot) ** 2 for b in market.values()) if buy_tot else 0.0,
        seller_hhi=sum((b.sell_qty / sell_tot) ** 2 for b in market.values()) if sell_tot else 0.0,
        top_broker=tb.broker if tb else None,
        top_broker_share=tb.gross_qty / gross if tb and gross else 0.0,
        top_net_buyer=tn.broker if tn else None,
        top_net_buyer_share=tn.net_qty / volume if tn and volume else 0.0,
        net_buyers=net_buyers,
        net_sellers=net_sellers,
        ranking=ranking,
        accumulating=pos,
        distributing=neg,
        flat=flat,
        breadth=breadth,
        breadth_pct=100.0 * breadth,
        broker_breadth=net_buyers / (net_buyers + net_sellers) if (net_buyers + net_sellers) else 0.0,
        turnover_breadth=pos_to / turnover if turnover else 0.0,
        volume_breadth=pos_vol / volume if volume else 0.0,
        sectors=tuple(sectors),
    )


# ---------------------------------------------------------------------------
# sections 72-73 — regimes and transitions
# ---------------------------------------------------------------------------

ACCUMULATION, DISTRIBUTION, NEUTRAL = "accumulation", "distribution", "neutral"


class Regime(NamedTuple):
    """Section 72 — a stock/window regime.

    ``label`` is the single flow-direction state (accumulation / distribution /
    neutral) and is the axis :func:`transitions` runs on. ``tags`` carry the rest
    of section 72's list — high/low volume, concentration, participation, trade
    size, flow reversal/acceleration/deterioration. They are ORTHOGONAL to the
    label, not alternatives to it: a window is routinely "accumulation" and "high
    volume" and "flow acceleration" at once, and flattening that into one state
    would invent transitions that never happened.
    """

    date: str
    label: str
    tags: tuple[str, ...]
    tilt: float
    tilt_z: float
    volume_z: float


def regimes(
    days: Sequence[DayStat],
    window: int = 7,
    lookback: int = 250,
    min_obs: int = 60,
    band: float = 0.5,
) -> list[Regime | None]:
    """Classify every day's trailing ``window`` into a regime. Point-in-time.

    At index ``i`` the window is ``days[i-window+1 : i+1]`` — data through D, which
    is allowed — while every threshold comes from :func:`pit` over windows that
    ENDED BEFORE i. That is the part that matters: "high volume" means high for
    this symbol as of that date, not high relative to a decade that had not
    happened. Entries are None until ``min_obs`` prior windows exist.

    ``band`` is the z-score either side of zero that stays neutral.
    """
    n = len(days)
    if n < window:
        return [None] * n

    def rolling(pick: Callable[[DayStat], float]) -> list[float]:
        vals = [pick(d) for d in days]
        out = []
        for i in range(n):
            w = vals[max(0, i - window + 1) : i + 1]
            out.append(sum(w) / len(w))
        return out

    tilt = rolling(lambda d: d.tilt)
    cols = {
        "tilt": pit_series(tilt, lookback, min_obs),
        "volume": pit_series(rolling(lambda d: float(d.volume)), lookback, min_obs),
        "conc": pit_series(rolling(lambda d: d.concentration), lookback, min_obs),
        "brokers": pit_series(rolling(lambda d: float(d.brokers)), lookback, min_obs),
        "size": pit_series(rolling(lambda d: d.avg_trade), lookback, min_obs),
    }

    out: list[Regime | None] = []
    for i, d in enumerate(days):
        p = cols["tilt"][i]
        if p is None or i < window - 1:
            out.append(None)
            continue
        label = ACCUMULATION if p.z >= band else (DISTRIBUTION if p.z <= -band else NEUTRAL)

        tags: list[str] = []
        vz = cols["volume"][i]
        if vz:
            if vz.z >= 1.0:
                tags.append("high volume")
            elif vz.z <= -1.0:
                tags.append("low volume")
        cz = cols["conc"][i]
        if cz and cz.z >= 1.0:
            tags.append("high concentration")
        bz = cols["brokers"][i]
        if bz and bz.z >= 1.0:
            tags.append("broad participation")
        sz = cols["size"][i]
        if sz:
            if sz.z >= 1.0:
                tags.append("high trade size")
            elif sz.z <= -1.0:
                tags.append("low trade size")

        # Flow reversal / acceleration / deterioration: this window against the
        # previous non-overlapping one, so they are two independent readings.
        j = i - window
        if j >= 0:
            prev, cur = tilt[j], tilt[i]
            if prev * cur < 0:
                tags.append("flow reversal")
            elif abs(cur) > abs(prev) * 1.5 and abs(prev) > 1e-9:
                tags.append("flow acceleration")
            elif abs(cur) < abs(prev) * 0.5:
                tags.append("flow deterioration")

        out.append(Regime(d.date, label, tuple(tags), tilt[i], p.z, vz.z if vz else 0.0))
    return out


class Transitions(NamedTuple):
    """Section 73 — how regimes actually follow one another in this symbol.

    ``probs`` sums to 1.0 for each source state. Durations report BOTH mean and
    median run length: a handful of six-month neutral stretches drags the mean
    somewhere no actual regime ever sat.

    ``by_year`` is not decoration. A single aggregate over ten years hides the
    fact that the transition structure of 2021 and 2023 are different markets;
    this project's standing rule is that anything historical shows its years.
    """

    runs: int
    counts: dict[tuple[str, str], int]
    probs: dict[tuple[str, str], float]
    mean_duration: dict[str, float]
    median_duration: dict[str, float]
    by_year: dict[str, dict[str, int]]  # year -> regime -> days


def transitions(regs: Sequence[Regime | None]) -> Transitions:
    """Count regime runs, transition frequencies/probabilities and durations."""
    seq = [(r.date, r.label) for r in regs if r is not None]
    counts: dict[tuple[str, str], int] = {}
    durations: dict[str, list[int]] = {}
    by_year: dict[str, dict[str, int]] = {}

    for date, label in seq:
        by_year.setdefault(date[:4], {}).setdefault(label, 0)
        by_year[date[:4]][label] += 1

    runs = 0
    if seq:
        cur, length = seq[0][1], 1
        for _, label in seq[1:]:
            if label == cur:
                length += 1
                continue
            durations.setdefault(cur, []).append(length)
            counts[(cur, label)] = counts.get((cur, label), 0) + 1
            cur, length = label, 1
        durations.setdefault(cur, []).append(length)
        runs = sum(len(v) for v in durations.values())

    probs: dict[tuple[str, str], float] = {}
    for src in {a for a, _ in counts}:
        total = sum(v for (a, _), v in counts.items() if a == src)
        for (a, b), v in counts.items():
            if a == src:
                probs[(a, b)] = v / total
    return Transitions(
        runs=runs,
        counts=counts,
        probs=probs,
        mean_duration={k: sum(v) / len(v) for k, v in durations.items()},
        median_duration={k: percentile(sorted(v), 50) for k, v in durations.items()},
        by_year=by_year,
    )


def by_year(days: Sequence[DayStat], pick: Callable[[DayStat], float]) -> dict[str, Baseline]:
    """Per-year :class:`Baseline` for any daily metric.

    Standing project rule: never report a historical aggregate without its years.
    A metric that only works in 2021 and 2022 looks fine in a ten-year average
    and loses money live.
    """
    groups: dict[str, list[float]] = {}
    for d in days:
        groups.setdefault(d.date[:4], []).append(pick(d))
    out = {}
    for y, vals in groups.items():
        b = baseline(vals)
        if b is not None:
            out[y] = b
    return out


# ---------------------------------------------------------------------------
# self-check
# ---------------------------------------------------------------------------


def _demo() -> None:
    """Run every section against the real archive and assert real invariants."""
    syms = loader.symbols()
    assert syms, "no floorsheet archive"
    sym = "NABIL" if "NABIL" in syms else syms[0]

    days = daily(sym)
    assert len(days) > 200, f"{sym}: only {len(days)} sessions"
    rets = returns(days)
    assert len(rets) == len(days) and rets[0] == 0.0
    assert all(abs(r) <= CIRCUIT_LOG for r in rets), "beyond-circuit move survived"

    # -- section 50-52: the spine ------------------------------------------
    vols = [float(d.volume) for d in days]
    b = baseline(vols)
    assert b and b.n == len(vols)
    assert b.min <= b.p10 <= b.p25 <= b.p50 <= b.p75 <= b.p90 <= b.p99 <= b.max
    assert abs(b.median - b.p50) < 1e-9
    assert b.sd > 0 and abs(b.var - b.sd**2) < 1e-6

    p = pit(vols, len(vols) - 1, lookback=250)
    assert p and 0.0 <= p.pct <= 100.0, "percentile out of [0,100]"

    # A z-score of the baseline's own mean must be ~0.
    probe = vols[:200] + [baseline(vols[:200]).mean]
    zp = pit(probe, 200, min_obs=50)
    assert zp and abs(zp.z) < 1e-9, f"z of the mean is {zp.z}, not 0"

    # ***** THE LEAKAGE GUARD *****
    # A point-in-time statistic computed at index i must not move when later data
    # is appended. If this ever fails, some baseline is reading the future and
    # every backtest downstream is fiction.
    i = len(vols) // 2
    early = pit(vols[: i + 1], i, lookback=250)
    full = pit(vols, i, lookback=250)
    assert early and full
    assert early == full, "LEAK: pit() changed when future data was appended"
    col = pit_series(vols, lookback=250)
    assert col[i] == full, "LEAK: pit_series disagrees with pit at the same index"
    # And the expanding form, which has no trailing window to hide behind.
    assert pit(vols[: i + 1], i, min_obs=30) == pit(vols, i, min_obs=30), "LEAK: expanding baseline"

    # -- rule 6: nothing pinned to a constant ------------------------------
    tb = baseline([d.tilt for d in days])
    assert tb and tb.sd > 0, "tilt is constant — degenerate, cut it"
    assert tb.min < 0 < tb.max, "tilt never changes sign — degenerate"
    # Stock-level buy/sell imbalance stays cut (brokers.py section 15): every
    # share bought is sold, so it is identically 0.000 and is not reimplemented
    # here under a new name.

    # -- sections 53-55, 68 -------------------------------------------------
    fs = flow_shape([d.tilt for d in days[-30:]])
    assert fs and fs.n == 30
    assert fs.drawdown <= 1e-12, f"drawdown {fs.drawdown} is positive"
    assert fs.max_drawdown <= fs.drawdown + 1e-12
    assert 0.0 <= fs.recovery <= 1.0 and 0.5 <= fs.sign_consistency <= 1.0
    assert 0.0 <= fs.consistency <= 1.0
    assert abs(fs.pos_ratio + fs.neg_ratio) <= 1.0

    ses = loader.load_last(sym, 30)
    w = brokers.window(ses)
    top = max(w.values(), key=lambda x: x.net_qty)
    bshape = flow_shape(broker_flow_series(ses, top.broker))
    assert bshape and -1.0 <= bshape.mean <= 1.0

    # -- sections 56-60 -----------------------------------------------------
    dv = divergences(days, 7)
    assert all(t in (-1, 0, 1) for t in (dv.price_trend, dv.flow_trend, dv.volume_trend))
    c = correlations(days, 30)
    assert c and all(-1.0 <= x <= 1.0 for x in (c.price_flow, c.volume_price, c.turnover_price))
    ls = lag_scan(days)
    assert ls and 0 <= ls.best_lag <= 10 and -1.0 <= ls.best_corr <= 1.0
    assert ls.by_year, "lag scan produced no per-year breakdown"
    el = elasticity(days, 60)
    fe = flow_efficiency(days, 7)

    # -- sections 61-63: proxies -------------------------------------------
    ab = absorption_like(days)
    ex = exhaustion_like(days)
    lq = executed_liquidity(days)
    assert len(ab) == len(ex) == len(lq) == len(days)
    scored = [x for x in ab if x]
    assert scored, "absorption proxy never scored"
    assert all(0.0 <= x.score <= 100.0 for x in scored)
    assert all(0.0 <= x.prior_frequency <= 1.0 for x in scored)
    assert baseline([x.score for x in scored]).sd > 0, "absorption score is constant"
    assert baseline([x.score for x in ex if x]).sd > 0, "exhaustion score is constant"
    assert all(0.0 <= x.score <= 100.0 for x in lq if x and x.score is not None)
    # Proxy scores are point-in-time too — same guard, one level up.
    half = absorption_like(days[: i + 1])
    assert half[i] == ab[i], "LEAK: absorption score changed with future data"

    # -- sections 64-67 -----------------------------------------------------
    s = ses[-1]
    pp = price_profile(s)
    assert pp.distinct >= 1 and pp.low <= s.vwap <= pp.high
    assert 0.0 <= pp.hhi <= 1.0 and 0.0 <= pp.entropy <= 1.0
    assert abs(pp.low_share + pp.mid_share + pp.high_share - 1.0) < 1e-9
    assert 0.0 <= pp.vwap_position <= 1.0

    asym = price_asymmetry(s)
    assert asym and set(asym) == set(brokers.day(s))
    for a in asym.values():
        if a.buy_qty:
            assert a.buy_low <= a.buy_vwap <= a.buy_high

    pq = flow_quality_by_price(ses)
    assert pq and all(-1.0 <= q.quality <= 1.0 for q in pq.values())
    # Zero-sum across brokers, as documented — this is why there is no stock version.
    tot = sum(q.buy_below - q.buy_above + q.sell_above - q.sell_below for q in pq.values())
    assert tot == 0, f"price quality should net to zero across brokers, got {tot}"

    # -- sections 69-71: market-wide, deliberately a small slice ------------
    slice_ = syms[:12]
    mf = market_flow(slice_, n=1)
    assert mf.requested == len(slice_) and mf.active <= mf.requested
    assert mf.positive + mf.negative + mf.flat == mf.active
    assert 0.0 <= mf.breadth <= 1.0 and 0.0 <= mf.buyer_hhi <= 1.0
    smap = sector_map()
    sf = sector_flow(slice_, n=1)
    assert all(0.0 <= v.breadth <= 1.0 for v in sf.values())
    assert all(v.symbols > 0 and v.sector for v in sf.values())
    if smap:
        assert sf, "sectors.txt is present but no sector aggregated"
    # A missing sector map must degrade to nothing, never to an exception: the
    # rest of the board has to survive someone deleting an external file.
    global SECTORS
    keep, SECTORS = SECTORS, os.path.join(loader.ROOT, "Master_data", "no_such_sectors.txt")
    try:
        assert sector_map() == {}, "missing sector map should be empty, not raise"
        assert sector_flow(slice_, n=1) == {}, "sector flow should be empty without a map"
        assert market_pass(slice_).sectors == (), "market_pass must lose its sectors, not raise"
    finally:
        SECTORS = keep

    # The whole-market pass the builder runs. Same slice, so this stays a self-check
    # and not a 45-second market scan.
    mp = market_pass(slice_)
    assert mp.requested == len(slice_) and mp.covered + mp.skipped == mp.requested
    assert mp.accumulating + mp.distributing + mp.flat == mp.active <= mp.covered
    assert 0.0 <= mp.breadth <= 1.0 and 0.0 <= mp.broker_breadth <= 1.0
    assert 0.0 <= mp.volume_breadth <= 1.0 and 0.0 <= mp.turnover_breadth <= 1.0
    assert mp.volume > 0 and mp.trades > 0 and mp.brokers > 0
    assert mp.ranking and mp.ranking[0].gross_qty >= mp.ranking[-1].gross_qty
    # Rotation is zero-sum by construction; if it ever isn't, the two share
    # denominators have drifted apart and every sector reads as rotating one way.
    if mp.sectors:
        assert abs(sum(s.rotation for s in mp.sectors)) < 1e-9, "sector rotation must sum to 0"
        assert abs(sum(s.volume_share for s in mp.sectors) - 1.0) < 1e-9
    # Point-in-time, and the backwards walk depends on the table being date-ascending:
    # if it ever stops being sorted this reads the wrong window in silence.
    cut = days[len(days) // 2].date
    assert market_pass(slice_, upto=cut).date <= cut, "LEAK: market pass read past upto"
    fr = loader.flow_rows(sym)
    assert all(a.date <= b.date for a, b in zip(fr, fr[1:])), "broker_flow is no longer date-ascending"

    # -- sections 72-73 -----------------------------------------------------
    regs = regimes(days)
    assert len(regs) == len(days)
    live = [r for r in regs if r]
    assert live, "no day could be classified"
    assert {r.label for r in live} <= {ACCUMULATION, DISTRIBUTION, NEUTRAL}
    assert len({r.label for r in live}) > 1, "regime label is constant — degenerate"
    tr = transitions(regs)
    for src in {a for a, _ in tr.probs}:
        total = sum(v for (a, _), v in tr.probs.items() if a == src)
        assert abs(total - 1.0) < 1e-9, f"transition probs from {src} sum to {total}"
    assert tr.by_year and len(tr.by_year) > 1, "no per-year regime breakdown"
    yb = by_year(days, lambda d: float(d.volume))
    assert len(yb) > 1

    # Regimes are point-in-time as well.
    assert regimes(days[: i + 1])[i] == regs[i], "LEAK: regime changed with future data"

    # -- one-line real-data summary ----------------------------------------
    last, lastreg = days[-1], live[-1]
    lab = {k: sum(v.get(k, 0) for v in tr.by_year.values()) for k in (ACCUMULATION, DISTRIBUTION, NEUTRAL)}
    print(
        f"history ok — {sym}: {len(days)} sessions {days[0].date}..{last.date}, "
        f"{len(live)} classified, {tr.runs} regime runs, leakage guard PASSED"
    )
    print(
        f"  {last.date}: volume p{p.pct:.0f} (z{p.z:+.2f}), tilt {last.tilt:+.3f}, "
        f"regime {lastreg.label}{' ' + '/'.join(lastreg.tags) if lastreg.tags else ''}"
    )
    print(
        f"  30D flow: sharpness {fs.sharpness:+.2f}, skew {fs.skew:+.2f}, "
        f"max drawdown {fs.max_drawdown:.3f}, recovery {fs.recovery:.0%}"
    )
    print(
        f"  price-flow r {c.price_flow:+.3f} ({c.regime}); best lag {ls.best_lag}d "
        f"r={ls.best_corr:+.3f} — in-sample fit, per-year lags "
        f"{[y[1] for y in ls.by_year]}"
    )
    if el:
        print(f"  elasticity n={el.n} mean {el.mean:+.3f} / MEDIAN {el.median:+.3f} — {el.label}")
    if fe:
        print(f"  7D flow efficiency {fe.efficiency:+.1f} %/flow-unit — {fe.label}")
    print(
        f"  proxies (descriptive, both families tested flat here): absorption-like "
        f"{scored[-1].score:.0f}/100, prior frequency {scored[-1].prior_frequency:.0%}"
    )
    print(f"  regime days: {lab}, mean durations {({k: round(v, 1) for k, v in tr.mean_duration.items()})}, "
          f"medians {({k: round(v, 1) for k, v in tr.median_duration.items()})}")
    print(
        f"  market slice ({mf.active}/{mf.requested} symbols, {mf.date_to}): breadth "
        f"{mf.breadth:.0%}, {mf.brokers} brokers, {len(sf)} sectors from "
        f"{'sectors.txt' if smap else 'NO sector map'}"
    )
    if dv.patterns:
        print(f"  divergences now: {'; '.join(dv.patterns)}")


if __name__ == "__main__":
    _demo()
