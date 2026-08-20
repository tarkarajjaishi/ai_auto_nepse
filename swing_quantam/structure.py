"""Consensus, breadth, concentration and trade structure — spec sections 22-32.

Everything here answers one question in different ways: *how was a stock's volume
distributed* — across brokers (22-26) and across individual transactions (27-32).
Two sessions with identical volume and identical net flow can be structurally
opposite, and this module is where that difference becomes numbers.

These are DESCRIPTIVE features. Three of them have already been tested as alpha on
this archive and failed, so the code is built to keep the confound visible rather
than to hide it:

1. **Concentration is largely a liquidity proxy.** Every concentration metric here
   carries r ~ -0.6 with turnover on this archive, because the biggest NEPSE
   brokers are also the busiest; `n_buyers`/`n_sellers`/`bs_ratio` run r = 0.83-0.92
   with turnover outright. That is why :class:`Concentration` carries ``volume`` and
   ``turnover`` in the same tuple — a reader who compares HHI across stocks without
   looking at the turnover beside it is comparing liquidity and calling it structure.
2. **Anything denominated in share COUNT is contaminated by price.** A "large trade"
   of 10,000 shares is mostly a statement that the stock is cheap. Two defences:
   large trades are defined against *this stock's own* trade-size distribution
   (never a fixed global lot size), and every large-trade figure is offered on two
   bases — ``BASIS_SHARES`` and ``BASIS_RUPEES``. Cross-sectional work should read
   the rupee basis; the share basis is kept because within a single stock it is the
   honest description of execution clip size.
3. **HHI is aggregated over the whole window, never averaged per day.** The mean of
   daily HHIs is a different (and much larger) number that mostly measures how thin
   the individual sessions were. :func:`concentration` therefore takes a window
   aggregate, and ``_demo`` asserts the two really do differ on real data.

Degenerate metrics the spec implies and this module deliberately does NOT emit:

* **Stock-level large-trade imbalance.** Every share in any subset of trades is
  bought by somebody and sold by somebody, so buy == sell == subset volume and the
  imbalance is identically 0.000 — the same conservation law that killed the
  stock-level imbalance in :mod:`swing_quantam.brokers`. Selecting the biggest
  prints does not create an aggressor side. What survives is the *distribution*
  across brokers: ``LargeStats.net_qty`` (shares that changed hands net between
  brokers inside the large-trade subset) and the large buyer/seller HHIs.
* **Gross-weighted mean broker imbalance** as "weighted consensus". Weighting each
  broker's imbalance (net/gross) by its gross share reduces algebraically to
  ``sum(net) / sum(gross)``, and ``sum(net)`` is exactly zero over all brokers —
  another guaranteed 0.000 column. :attr:`Consensus.weighted_consensus` therefore
  weights the *sign* of each broker's net by its share of activity, which is not
  degenerate, and :attr:`Consensus.strength` carries the magnitude separately.
* **Self-thresholded large-trade percentage across windows.** If each window takes
  its own P90 as the large threshold then ~10% of trades are "large" in every
  window by construction. Thresholds are computed ONCE from a baseline and reused,
  which is why :func:`large` demands a :class:`Thresholds` rather than deriving one.

Point-in-time by construction: every function takes an already-sliced list of
sessions or an already-built broker aggregate. Nothing here touches the archive.
Pure stdlib — skewness and kurtosis are implemented below because the target box
has no scipy and never will.
"""

from __future__ import annotations

import math
import statistics
from typing import Iterable, NamedTuple, Sequence

from . import brokers
from .brokers import BrokerDay
from .loader import WINDOWS, Session, Trade

#: The two ways to size a trade. Rupees for anything cross-sectional; shares only
#: within one stock, where price is common to every broker (see module docstring).
BASIS_SHARES, BASIS_RUPEES = "shares", "rupees"


# ---------------------------------------------------------------------------
# small stdlib maths — no numpy on the target box
# ---------------------------------------------------------------------------


def _pct(ordered: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile of an already-sorted sequence, q in 0..100.

    Monotone in ``q`` by construction, which is what the P75 <= P90 <= P95 <= P99
    invariant downstream relies on.
    """
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return float(ordered[0])
    i = (len(ordered) - 1) * q / 100.0
    lo = int(i)
    hi = min(lo + 1, len(ordered) - 1)
    return float(ordered[lo]) + (i - lo) * (float(ordered[hi]) - float(ordered[lo]))


def _moments(vals: Sequence[float], mean: float) -> tuple[float, float, float]:
    """Population 2nd/3rd/4th central moments in one pass."""
    n = len(vals)
    m2 = m3 = m4 = 0.0
    for x in vals:
        d = x - mean
        d2 = d * d
        m2 += d2
        m3 += d2 * d
        m4 += d2 * d2
    return m2 / n, m3 / n, m4 / n


def skewness(vals: Sequence[float]) -> float:
    """Sample skewness (G1, the bias-corrected Fisher-Pearson estimator).

    Trade sizes are violently right-skewed, so this is expected to be large and
    positive; a value near zero on a real window is the anomaly, not the norm.
    """
    n = len(vals)
    if n < 3:
        return 0.0
    mean = statistics.fmean(vals)
    m2, m3, _ = _moments(vals, mean)
    if m2 <= 0:
        return 0.0
    g1 = m3 / (m2**1.5)
    return g1 * math.sqrt(n * (n - 1)) / (n - 2)


def kurtosis(vals: Sequence[float]) -> float:
    """Sample EXCESS kurtosis (G2): 0.0 for a normal, not 3.0."""
    n = len(vals)
    if n < 4:
        return 0.0
    mean = statistics.fmean(vals)
    m2, _, m4 = _moments(vals, mean)
    if m2 <= 0:
        return 0.0
    g2 = m4 / (m2 * m2) - 3.0
    return ((n + 1) * g2 + 6.0) * (n - 1) / ((n - 2) * (n - 3))


def _entropy(weights: Sequence[float]) -> float:
    """Shannon entropy in nats of a weight vector. 0 = one holder, ln(n) = even."""
    total = sum(w for w in weights if w > 0)
    if total <= 0:
        return 0.0
    h = 0.0
    for w in weights:
        if w > 0:
            p = w / total
            h -= p * math.log(p)
    return h


def _hhi(weights: Sequence[float]) -> float:
    """Herfindahl index of a weight vector: (0, 1]. 1 = a single participant.

    Fed the WINDOW aggregate, never a per-day average — see the module docstring.
    """
    total = sum(w for w in weights if w > 0)
    if total <= 0:
        return 0.0
    return sum((w / total) ** 2 for w in weights if w > 0)


def _top_share(desc: Sequence[float], k: int, total: float) -> float:
    return sum(desc[:k]) / total if total > 0 else 0.0


def _ratio(a: float, b: float) -> float:
    """a / b, with the "no denominator" case reported as a itself, not inf."""
    return a / b if b else float(a)


def _ordered(by_window: dict[int, object]) -> list[int]:
    """Window keys longest-first: 30, 15, 7, 3 — the direction every trend reads."""
    return sorted(by_window, reverse=True)


# ---------------------------------------------------------------------------
# 22. BROKER CONSENSUS
# ---------------------------------------------------------------------------


class Consensus(NamedTuple):
    """Section 22 — is the stock being bought by *many* brokers or by *one*?

    ``consensus`` is the headcount vote and ``weighted_consensus`` is the same vote
    weighted by how much of the window's activity each broker actually did. When
    they disagree — a positive headcount with a negative weighted vote — the buying
    is a crowd of small brokers against one large seller, which is precisely the
    distinction section 22 exists to draw.
    """

    brokers: int
    net_buyers: int
    net_sellers: int
    neutral: int
    buyer_seller_ratio: float
    net_buyer_pct: float
    net_seller_pct: float
    consensus: float  # headcount vote, -1 (all sellers) .. +1 (all buyers)
    weighted_consensus: float  # activity-weighted sign vote, same scale
    strength: float  # sum|net| / sum gross: how directional the average broker was
    dispersion: float  # stdev of per-broker imbalance: agreement vs disagreement
    entropy: float  # nats over the buyer/seller/neutral split, 0 .. ln 3
    top_buyer_dependence: float  # top buyer's share of ALL net buying
    top_seller_dependence: float
    accumulation: str  # broad | mixed | concentrated | none
    distribution: str


def _breadth_label(n_side: int, dependence: float) -> str:
    """The spec's "many brokers buying" vs "one/few brokers dominating buying"."""
    if n_side == 0:
        return "none"
    if n_side <= 2 or dependence >= 0.50:
        return "concentrated"
    if n_side >= 5 and dependence <= 0.30:
        return "broad"
    return "mixed"


def consensus(agg: dict[int, BrokerDay]) -> Consensus:
    """Section 22, from a session or window aggregate (``brokers.window(...)``)."""
    if not agg:
        return Consensus(0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, "none", "none")

    vals = list(agg.values())
    buyers = [b for b in vals if b.net_qty > 0]
    sellers = [b for b in vals if b.net_qty < 0]
    neutral = len(vals) - len(buyers) - len(sellers)
    sided = len(buyers) + len(sellers)

    gross = sum(b.gross_qty for b in vals)
    # Sign-weighted, NOT imbalance-weighted: sum(gross_b * imbalance_b) == sum(net_b)
    # == 0 for every stock on every day. See the module docstring.
    weighted = sum((b.gross_qty / gross) * (1 if b.net_qty > 0 else -1 if b.net_qty < 0 else 0) for b in vals) if gross else 0.0

    pos_net = sum(b.net_qty for b in buyers)
    neg_net = -sum(b.net_qty for b in sellers)
    top_buy = max((b.net_qty for b in buyers), default=0)
    top_sell = max((-b.net_qty for b in sellers), default=0)
    dep_b = top_buy / pos_net if pos_net else 0.0
    dep_s = top_sell / neg_net if neg_net else 0.0

    return Consensus(
        brokers=len(vals),
        net_buyers=len(buyers),
        net_sellers=len(sellers),
        neutral=neutral,
        buyer_seller_ratio=_ratio(len(buyers), len(sellers)),
        net_buyer_pct=len(buyers) / len(vals),
        net_seller_pct=len(sellers) / len(vals),
        consensus=((len(buyers) - len(sellers)) / sided) if sided else 0.0,
        weighted_consensus=weighted,
        strength=(sum(abs(b.net_qty) for b in vals) / gross) if gross else 0.0,
        dispersion=statistics.pstdev([b.imbalance for b in vals]) if len(vals) > 1 else 0.0,
        entropy=_entropy([len(buyers), len(sellers), neutral]),
        top_buyer_dependence=dep_b,
        top_seller_dependence=dep_s,
        accumulation=_breadth_label(len(buyers), dep_b),
        distribution=_breadth_label(len(sellers), dep_s),
    )


# ---------------------------------------------------------------------------
# 23. ACCUMULATION / DISTRIBUTION BREADTH
# ---------------------------------------------------------------------------


class Breadth(NamedTuple):
    """Section 23 — 20 net buyers / 8 net sellers is not 2 / 26, whatever the net."""

    brokers: int
    net_buyers: int
    net_sellers: int
    accumulation: float  # net buyers / participating brokers
    distribution: float  # net sellers / participating brokers
    ratio: float  # net buyers / net sellers
    pct: float  # net buyers / (net buyers + net sellers), the 0.5-centred one


class BreadthTrend(NamedTuple):
    """Section 23's momentum block, read across 30D -> 15D -> 7D -> 3D."""

    series: tuple[tuple[int, float], ...]  # (window, pct), longest window first
    momentum: float  # shortest minus longest
    velocity: float  # the most recent step
    acceleration: float  # change in step size
    reversal: str  # to_accumulation | to_distribution | none
    trend: str  # broadening | narrowing | stable


def breadth(agg: dict[int, BrokerDay]) -> Breadth:
    """Section 23 for one window."""
    if not agg:
        return Breadth(0, 0, 0, 0.0, 0.0, 0.0, 0.5)
    vals = list(agg.values())
    nb = sum(1 for b in vals if b.net_qty > 0)
    ns = sum(1 for b in vals if b.net_qty < 0)
    return Breadth(
        brokers=len(vals),
        net_buyers=nb,
        net_sellers=ns,
        accumulation=nb / len(vals),
        distribution=ns / len(vals),
        ratio=_ratio(nb, ns),
        pct=(nb / (nb + ns)) if (nb + ns) else 0.5,
    )


def _trend_label(series: Sequence[float], rapid: float, slow: float) -> str:
    """Shared 30D->3D trend classifier. ``series`` runs longest window first."""
    if len(series) < 2:
        return "stable"
    change = series[-1] - series[0]
    rising = all(b >= a for a, b in zip(series, series[1:]))
    falling = all(b <= a for a, b in zip(series, series[1:]))
    if change >= rapid and rising:
        return "rapidly_increasing"
    if change <= -rapid and falling:
        return "rapidly_decreasing"
    if change >= slow:
        return "increasing"
    if change <= -slow:
        return "decreasing"
    return "stable"


def breadth_trend(by_window: dict[int, Breadth]) -> BreadthTrend:
    """Section 23 — breadth momentum, acceleration and reversal across windows."""
    ws = _ordered(by_window)
    vals = [by_window[w].pct for w in ws]
    if not vals:
        return BreadthTrend((), 0.0, 0.0, 0.0, "none", "stable")

    momentum = vals[-1] - vals[0]
    steps = [b - a for a, b in zip(vals, vals[1:])]
    velocity = steps[-1] if steps else 0.0
    acceleration = (steps[-1] - steps[-2]) if len(steps) >= 2 else 0.0

    # A reversal is a crossing of the 50% line between the long and short window:
    # the majority of brokers changed sides, not merely leaned further.
    if vals[0] < 0.5 <= vals[-1]:
        reversal = "to_accumulation"
    elif vals[0] >= 0.5 > vals[-1]:
        reversal = "to_distribution"
    else:
        reversal = "none"

    trend = _trend_label(vals, 0.15, 0.05)
    trend = {"rapidly_increasing": "broadening", "increasing": "broadening",
             "rapidly_decreasing": "narrowing", "decreasing": "narrowing"}.get(trend, "stable")
    return BreadthTrend(tuple(zip(ws, vals)), momentum, velocity, acceleration, reversal, trend)


# ---------------------------------------------------------------------------
# 24. CONCENTRATION
# ---------------------------------------------------------------------------


class Shares(NamedTuple):
    """One concentration profile: top-k shares, HHI, entropy, effective count."""

    n: int
    top1: float
    top3: float
    top5: float
    top10: float
    hhi: float
    entropy: float
    diversity: float  # exp(entropy) — the effective number of equal participants


class Concentration(NamedTuple):
    """Section 24 — buyer / seller / all-broker concentration for one window.

    ``volume`` and ``turnover`` ride along on purpose. Concentration correlates
    ~-0.6 with turnover on this archive, so a concentration number quoted without
    the liquidity beside it is not interpretable.
    """

    volume: int
    turnover: float
    brokers: int
    buy: Shares  # weights = gross BUY quantity per broker
    sell: Shares  # weights = gross SELL quantity per broker
    broker: Shares  # weights = total activity (buy + sell) per broker


def profile(weights: Iterable[float]) -> Shares:
    """Top-k / HHI / entropy / diversity for one weight vector."""
    vals = sorted((float(w) for w in weights if w > 0), reverse=True)
    total = sum(vals)
    if not vals or total <= 0:
        return Shares(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    h = _entropy(vals)
    return Shares(
        n=len(vals),
        top1=_top_share(vals, 1, total),
        top3=_top_share(vals, 3, total),
        top5=_top_share(vals, 5, total),
        top10=_top_share(vals, 10, total),
        hhi=_hhi(vals),
        entropy=h,
        diversity=math.exp(h),
    )


def concentration(agg: dict[int, BrokerDay]) -> Concentration:
    """Section 24 from a WINDOW aggregate — never from averaged daily HHIs."""
    vals = list(agg.values())
    return Concentration(
        volume=sum(b.buy_qty for b in vals),
        turnover=sum(b.buy_amt for b in vals),
        brokers=len(vals),
        buy=profile(b.buy_qty for b in vals),
        sell=profile(b.sell_qty for b in vals),
        broker=profile(b.gross_qty for b in vals),
    )


# ---------------------------------------------------------------------------
# 25. CONCENTRATION DYNAMICS
# ---------------------------------------------------------------------------


class ConcDynamics(NamedTuple):
    """Section 25 — the LEVEL of concentration says less than its direction.

    The spec's worked example (30D 0.12 -> 15D 0.18 -> 7D 0.31 -> 3D 0.47) must
    come back as ``rapidly_increasing``; ``_demo`` asserts exactly that.
    """

    hhi_series: tuple[tuple[int, float], ...]  # (window, broker HHI), longest first
    change: float  # shortest minus longest window
    velocity: float  # the most recent step
    acceleration: float  # change in step size
    buyer_trend: str
    seller_trend: str
    broker_trend: str
    spike: bool  # short-window concentration far above the long-window base
    release: bool  # the opposite: activity spreading back out


def dynamics(by_window: dict[int, Concentration]) -> ConcDynamics:
    """Section 25 across 30D -> 15D -> 7D -> 3D."""
    ws = _ordered(by_window)
    hh = [by_window[w].broker.hhi for w in ws]
    if not hh:
        return ConcDynamics((), 0.0, 0.0, 0.0, "stable", "stable", "stable", False, False)

    steps = [b - a for a, b in zip(hh, hh[1:])]
    long_h, short_h = hh[0], hh[-1]
    return ConcDynamics(
        hhi_series=tuple(zip(ws, hh)),
        change=short_h - long_h,
        velocity=steps[-1] if steps else 0.0,
        acceleration=(steps[-1] - steps[-2]) if len(steps) >= 2 else 0.0,
        buyer_trend=_trend_label([by_window[w].buy.hhi for w in ws], 0.10, 0.03),
        seller_trend=_trend_label([by_window[w].sell.hhi for w in ws], 0.10, 0.03),
        broker_trend=_trend_label(hh, 0.10, 0.03),
        spike=bool(long_h > 0 and short_h >= 1.5 * long_h and short_h - long_h >= 0.05),
        release=bool(long_h > 0 and short_h <= 0.67 * long_h and long_h - short_h >= 0.05),
    )


# ---------------------------------------------------------------------------
# 26. PARETO ANALYSIS
# ---------------------------------------------------------------------------


class ParetoShares(NamedTuple):
    top1pct: float
    top5pct: float
    top10pct: float
    top20pct: float


class Pareto(NamedTuple):
    """Section 26 — how much of the window the busiest slice of brokers controls.

    With ~50 active brokers "top 1%" rounds up to a single broker, so ``top1pct``
    and :attr:`Shares.top1` agree by construction on most NEPSE stocks. That is a
    property of the market's broker count, not a bug.
    """

    brokers: int
    volume: ParetoShares
    turnover: ParetoShares
    net_buy: ParetoShares
    net_sell: ParetoShares


def _pareto(weights: Iterable[float]) -> ParetoShares:
    vals = sorted((float(w) for w in weights if w > 0), reverse=True)
    total = sum(vals)
    n = len(vals)
    if not n or total <= 0:
        return ParetoShares(0.0, 0.0, 0.0, 0.0)
    k = lambda p: max(1, math.ceil(n * p))  # noqa: E731 - one-liner, used four times
    return ParetoShares(
        _top_share(vals, k(0.01), total),
        _top_share(vals, k(0.05), total),
        _top_share(vals, k(0.10), total),
        _top_share(vals, k(0.20), total),
    )


def pareto(agg: dict[int, BrokerDay]) -> Pareto:
    """Section 26. Net buying/selling are ranked over the brokers on that side only."""
    vals = list(agg.values())
    return Pareto(
        brokers=len(vals),
        volume=_pareto(b.buy_qty for b in vals),
        turnover=_pareto(b.buy_amt for b in vals),
        net_buy=_pareto(b.net_qty for b in vals if b.net_qty > 0),
        net_sell=_pareto(-b.net_qty for b in vals if b.net_qty < 0),
    )


# ---------------------------------------------------------------------------
# 27 / 28. LARGE-TRADE ANALYSIS AND CONVICTION
# ---------------------------------------------------------------------------


class Thresholds(NamedTuple):
    """What counts as a large or small print IN THIS STOCK.

    Derived from the stock's own trade-size distribution (spec section 27) and then
    held FIXED across every window, because a per-window percentile would pin the
    large-trade share at (100 - pct)% in every window by construction.
    """

    trades: int  # baseline sample size
    pct: float  # the percentile that defines "large"
    small_pct: float
    large_qty: float
    large_amt: float
    small_qty: float
    small_amt: float
    median_qty: float
    median_amt: float

    def large_for(self, basis: str) -> float:
        return self.large_amt if basis == BASIS_RUPEES else self.large_qty

    def small_for(self, basis: str) -> float:
        return self.small_amt if basis == BASIS_RUPEES else self.small_qty


class LargeStats(NamedTuple):
    """Sections 27 + 28 on ONE basis. Always read the rupee basis cross-sectionally.

    ``net_qty`` is the non-degenerate replacement for the "large-trade imbalance"
    the spec asks for: within any trade subset buy volume == sell volume exactly,
    so the imbalance is 0.000 always, but how that volume is spread across brokers
    is real.
    """

    basis: str
    threshold: float
    trades: int  # large trades in the window
    trade_pct: float  # as a fraction of all trades
    volume: int
    volume_pct: float
    turnover: float
    turnover_pct: float
    largest_qty: int
    largest_amt: float
    top10_pct: float  # the 10 biggest prints as a fraction of window volume
    top50_pct: float
    top100_pct: float
    buyers: int  # distinct brokers on the buy side of a large print
    sellers: int
    buyer_hhi: float  # large-buyer concentration
    seller_hhi: float
    top_buyer: int | None
    top_buyer_share: float
    top_seller: int | None
    top_seller_share: float
    net_qty: int  # shares changing hands net BETWEEN brokers inside the subset
    net_share: float
    persistence: float  # fraction of sessions containing at least one large print
    vwap: float
    vwap_premium: float  # large-trade VWAP vs the window's own VWAP
    rate_p10: float
    rate_p90: float


def thresholds(sessions: Sequence[Session], pct: float = 90.0, small_pct: float = 25.0) -> Thresholds:
    """Section 27 — build the stock's own large/small cutoffs from a baseline.

    Pass the LONGEST history you are willing to look at (already sliced to the
    decision date). Feeding this the same window you then measure is the mistake
    the class docstring warns about.
    """
    qty = sorted(t.quantity for s in sessions for t in s.trades)
    amt = sorted(t.amount for s in sessions for t in s.trades)
    return Thresholds(
        trades=len(qty),
        pct=pct,
        small_pct=small_pct,
        large_qty=_pct(qty, pct),
        large_amt=_pct(amt, pct),
        small_qty=_pct(qty, small_pct),
        small_amt=_pct(amt, small_pct),
        median_qty=_pct(qty, 50.0),
        median_amt=_pct(amt, 50.0),
    )


def _size(t: Trade, basis: str) -> float:
    return t.amount if basis == BASIS_RUPEES else float(t.quantity)


def large(sessions: Sequence[Session], th: Thresholds, basis: str = BASIS_RUPEES) -> LargeStats:
    """Sections 27 + 28 for one window, on one basis."""
    trades = [t for s in sessions for t in s.trades]
    cut = th.large_for(basis)
    if not trades:
        return LargeStats(basis, cut, 0, 0.0, 0, 0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0, 0.0,
                          0, 0, 0.0, 0.0, None, 0.0, None, 0.0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    vol = sum(t.quantity for t in trades)
    to = sum(t.amount for t in trades)
    big = [t for t in trades if _size(t, basis) >= cut] if cut > 0 else []

    ranked = sorted(trades, key=lambda t: _size(t, basis), reverse=True)
    topn = lambda k: (sum(t.quantity for t in ranked[:k]) / vol) if vol else 0.0  # noqa: E731

    agg = brokers.from_trades(big)
    buyers = [b for b in agg.values() if b.buy_qty > 0]
    sellers = [b for b in agg.values() if b.sell_qty > 0]
    tb = max(buyers, key=lambda b: b.buy_qty, default=None)
    ts = max(sellers, key=lambda b: b.sell_qty, default=None)
    big_vol = sum(t.quantity for t in big)
    big_to = sum(t.amount for t in big)
    net = sum(b.net_qty for b in agg.values() if b.net_qty > 0)

    hit = sum(1 for s in sessions if any(_size(t, basis) >= cut for t in s.trades)) if cut > 0 else 0
    rates = sorted(t.rate for t in big)
    vwap = (big_to / big_vol) if big_vol else 0.0
    all_vwap = (to / vol) if vol else 0.0

    return LargeStats(
        basis=basis,
        threshold=cut,
        trades=len(big),
        trade_pct=len(big) / len(trades),
        volume=big_vol,
        volume_pct=(big_vol / vol) if vol else 0.0,
        turnover=big_to,
        turnover_pct=(big_to / to) if to else 0.0,
        largest_qty=max(t.quantity for t in trades),
        largest_amt=max(t.amount for t in trades),
        top10_pct=topn(10),
        top50_pct=topn(50),
        top100_pct=topn(100),
        buyers=len(buyers),
        sellers=len(sellers),
        buyer_hhi=_hhi([b.buy_qty for b in buyers]),
        seller_hhi=_hhi([b.sell_qty for b in sellers]),
        top_buyer=tb.broker if tb else None,
        top_buyer_share=(tb.buy_qty / big_vol) if tb and big_vol else 0.0,
        top_seller=ts.broker if ts else None,
        top_seller_share=(ts.sell_qty / big_vol) if ts and big_vol else 0.0,
        net_qty=net,
        net_share=(net / big_vol) if big_vol else 0.0,
        persistence=hit / len(sessions) if sessions else 0.0,
        vwap=vwap,
        vwap_premium=(vwap / all_vwap - 1.0) if all_vwap and vwap else 0.0,
        rate_p10=_pct(rates, 10.0),
        rate_p90=_pct(rates, 90.0),
    )


# ---------------------------------------------------------------------------
# 29. TRADE-SIZE DISTRIBUTION
# ---------------------------------------------------------------------------


class SizeDist(NamedTuple):
    """Section 29. CV = stdev / mean, as the spec's formula block spells out."""

    n: int
    mean: float
    median: float
    p75: float
    p90: float
    p95: float
    p99: float
    max: float
    stdev: float  # sample stdev
    variance: float
    skew: float  # sample skewness (G1)
    kurtosis: float  # sample EXCESS kurtosis (G2): normal = 0
    cv: float


def size_dist(values: Sequence[float]) -> SizeDist:
    """Section 29 for any list of trade sizes (shares or rupees)."""
    n = len(values)
    if n == 0:
        return SizeDist(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    ordered = sorted(float(v) for v in values)
    mean = statistics.fmean(ordered)
    sd = statistics.stdev(ordered) if n > 1 else 0.0
    return SizeDist(
        n=n,
        mean=mean,
        median=_pct(ordered, 50.0),
        p75=_pct(ordered, 75.0),
        p90=_pct(ordered, 90.0),
        p95=_pct(ordered, 95.0),
        p99=_pct(ordered, 99.0),
        max=ordered[-1],
        stdev=sd,
        variance=sd * sd,
        skew=skewness(ordered),
        kurtosis=kurtosis(ordered),
        cv=(sd / mean) if mean else 0.0,
    )


def trade_sizes(sessions: Sequence[Session], basis: str = BASIS_RUPEES) -> SizeDist:
    """Section 29 over a window of sessions."""
    return size_dist([_size(t, basis) for s in sessions for t in s.trades])


# ---------------------------------------------------------------------------
# 30. BROKER TRADE-SIZE SIGNATURE
# ---------------------------------------------------------------------------


class BrokerSize(NamedTuple):
    """Section 30 — one broker's EXECUTION-BEHAVIOUR signature in this stock.

    This describes how a broker chops its orders on the tape and claims nothing
    whatsoever about that broker's underlying clients: the floorsheet carries
    broker IDs, not client IDs, so a steady 100-share clip is equally consistent
    with one algorithm and with two hundred retail accounts.

    Percentiles are on share count, which is the natural unit for execution clip
    size *within* one stock, where every broker faces the same price. ``median_amt``
    is carried alongside because comparing clip sizes ACROSS stocks in shares is an
    inverse-price proxy, not a behaviour measurement.
    """

    broker: int
    trades: int
    median: float
    p90: float
    p95: float
    p99: float
    max: float
    mean: float
    volatility: float  # stdev of this broker's own clip sizes
    cv: float
    skew: float
    large_ratio: float  # share of its trades at or above the stock's large cutoff
    small_ratio: float  # share at or below the stock's small cutoff
    consistency: float  # 1/(1+CV): 1.0 = every clip the same size
    median_amt: float


def broker_signatures(
    sessions: Sequence[Session], th: Thresholds, min_trades: int = 5
) -> dict[int, BrokerSize]:
    """Section 30 for every broker with at least ``min_trades`` prints.

    A broker's clip sizes include both sides of the tape: buying 500 and selling
    500 are both executions, and this is a description of execution, not of flow.
    """
    qty: dict[int, list[int]] = {}
    amt: dict[int, list[float]] = {}
    for s in sessions:
        for t in s.trades:
            for b in (t.buyer, t.seller):
                qty.setdefault(b, []).append(t.quantity)
                amt.setdefault(b, []).append(t.amount)

    out: dict[int, BrokerSize] = {}
    for b, sizes in qty.items():
        if len(sizes) < min_trades:
            continue
        ordered = sorted(float(x) for x in sizes)
        mean = statistics.fmean(ordered)
        sd = statistics.stdev(ordered) if len(ordered) > 1 else 0.0
        cv = (sd / mean) if mean else 0.0
        out[b] = BrokerSize(
            broker=b,
            trades=len(ordered),
            median=_pct(ordered, 50.0),
            p90=_pct(ordered, 90.0),
            p95=_pct(ordered, 95.0),
            p99=_pct(ordered, 99.0),
            max=ordered[-1],
            mean=mean,
            volatility=sd,
            cv=cv,
            skew=skewness(ordered),
            large_ratio=sum(1 for x in ordered if x >= th.large_qty) / len(ordered),
            small_ratio=sum(1 for x in ordered if x <= th.small_qty) / len(ordered),
            consistency=1.0 / (1.0 + cv),
            median_amt=_pct(sorted(amt[b]), 50.0),
        )
    return out


# ---------------------------------------------------------------------------
# 31 / 32. TRANSACTION FRAGMENTATION AND ITS CHANGE
# ---------------------------------------------------------------------------


class Fragmentation(NamedTuple):
    """Section 31 — how many transactions it took to move the volume.

    The spec's example: 100,000 shares as 10 x 10,000 and as 1,000 x 100 are the
    same volume and different structures. ``frag_ratio`` is the baseline median
    clip divided by this window's average clip, so >1 means the stock is trading in
    smaller pieces than its own history and <1 means bigger ones.
    """

    trades: int
    volume: int
    turnover: float
    trades_per_1k: float  # transactions per 1,000 shares of volume
    avg_size: float
    median_size: float
    frag_ratio: float
    large_ratio: float  # share of TRADES that are large
    large_volume_ratio: float  # share of VOLUME in large trades
    small_ratio: float
    small_volume_ratio: float


class TradeSizeChange(NamedTuple):
    """Section 32 (and the emergence/disappearance items of section 28)."""

    series: tuple[tuple[int, float], ...]  # (window, frag_ratio), longest first
    change: float  # shortest minus longest window
    velocity: float
    trend: str  # fragmenting | consolidating | stable
    avg_size_change: float  # short-window average clip vs long-window, as a fraction
    large_emergence: float  # rise in large-trade VOLUME share, 0 if it fell
    large_disappearance: float  # the fall, 0 if it rose
    small_dominance: float  # small trades' share of the shortest window's volume
    structural: str


def fragmentation(sessions: Sequence[Session], th: Thresholds) -> Fragmentation:
    """Section 31 for one window."""
    trades = [t for s in sessions for t in s.trades]
    if not trades:
        return Fragmentation(0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    vol = sum(t.quantity for t in trades)
    ordered = sorted(t.quantity for t in trades)
    avg = vol / len(trades)
    big = [t for t in trades if t.quantity >= th.large_qty]
    small = [t for t in trades if t.quantity <= th.small_qty]
    return Fragmentation(
        trades=len(trades),
        volume=vol,
        turnover=sum(t.amount for t in trades),
        trades_per_1k=(1000.0 * len(trades) / vol) if vol else 0.0,
        avg_size=avg,
        median_size=_pct(ordered, 50.0),
        frag_ratio=(th.median_qty / avg) if avg else 0.0,
        large_ratio=len(big) / len(trades),
        large_volume_ratio=(sum(t.quantity for t in big) / vol) if vol else 0.0,
        small_ratio=len(small) / len(trades),
        small_volume_ratio=(sum(t.quantity for t in small) / vol) if vol else 0.0,
    )


def frag_change(by_window: dict[int, Fragmentation]) -> TradeSizeChange:
    """Section 32 across 30D -> 15D -> 7D -> 3D."""
    ws = _ordered(by_window)
    if not ws:
        return TradeSizeChange((), 0.0, 0.0, "stable", 0.0, 0.0, 0.0, 0.0, "stable")

    fr = [by_window[w].frag_ratio for w in ws]
    steps = [b - a for a, b in zip(fr, fr[1:])]
    long_f, short_f = by_window[ws[0]], by_window[ws[-1]]

    lbl = _trend_label(fr, 0.50, 0.10)
    trend = {"rapidly_increasing": "fragmenting", "increasing": "fragmenting",
             "rapidly_decreasing": "consolidating", "decreasing": "consolidating"}.get(lbl, "stable")

    d_large = short_f.large_volume_ratio - long_f.large_volume_ratio
    small_dom = short_f.small_volume_ratio

    if d_large >= 0.05:
        structural = "large_trades_emerging"
    elif d_large <= -0.05:
        structural = "large_trades_disappearing"
    elif trend == "fragmenting" and small_dom >= 0.25:
        structural = "small_trade_dominance"
    else:
        structural = "stable"

    return TradeSizeChange(
        series=tuple(zip(ws, fr)),
        change=fr[-1] - fr[0],
        velocity=steps[-1] if steps else 0.0,
        trend=trend,
        avg_size_change=(short_f.avg_size / long_f.avg_size - 1.0) if long_f.avg_size else 0.0,
        large_emergence=max(0.0, d_large),
        large_disappearance=max(0.0, -d_large),
        small_dominance=small_dom,
        structural=structural,
    )


# ---------------------------------------------------------------------------
# the whole of 22-32 for one symbol at one point in time
# ---------------------------------------------------------------------------


class Structure(NamedTuple):
    """Sections 22-32 for one symbol at one decision date."""

    symbol: str
    date: str
    windows: tuple[int, ...]
    thresholds: Thresholds
    consensus: dict[int, Consensus]
    breadth: dict[int, Breadth]
    breadth_trend: BreadthTrend
    concentration: dict[int, Concentration]
    conc_dynamics: ConcDynamics
    pareto: dict[int, Pareto]
    large_value: dict[int, LargeStats]  # rupee basis — the cross-sectionally safe one
    large_shares: dict[int, LargeStats]  # share-count basis — price in disguise
    sizes: dict[int, SizeDist]  # rupee basis
    sizes_shares: dict[int, SizeDist]
    fragmentation: dict[int, Fragmentation]
    frag_change: TradeSizeChange
    signatures: dict[int, BrokerSize]  # over the longest window


def analyse(
    sessions: Sequence[Session],
    baseline: Sequence[Session] | None = None,
    windows: Sequence[int] = WINDOWS,
) -> Structure:
    """Sections 22-32 over ``sessions`` (oldest-first, already sliced by the caller).

    ``baseline`` is the longer history the large/small cutoffs are read from; it
    defaults to ``sessions``, which is fine for a one-window look but makes the
    large-trade share ~constant across windows — pass real history for anything
    that compares windows.
    """
    if not sessions:
        raise ValueError("analyse() needs at least one session; slice it upstream")

    th = thresholds(baseline if baseline else sessions)
    ws = tuple(w for w in sorted(windows, reverse=True))
    per = {w: sessions[-w:] for w in ws}
    aggs = {w: brokers.window(per[w]) for w in ws}

    conc = {w: concentration(aggs[w]) for w in ws}
    brd = {w: breadth(aggs[w]) for w in ws}
    frag = {w: fragmentation(per[w], th) for w in ws}

    return Structure(
        symbol=sessions[-1].symbol,
        date=sessions[-1].date,
        windows=ws,
        thresholds=th,
        consensus={w: consensus(aggs[w]) for w in ws},
        breadth=brd,
        breadth_trend=breadth_trend(brd),
        concentration=conc,
        conc_dynamics=dynamics(conc),
        pareto={w: pareto(aggs[w]) for w in ws},
        large_value={w: large(per[w], th, BASIS_RUPEES) for w in ws},
        large_shares={w: large(per[w], th, BASIS_SHARES) for w in ws},
        sizes={w: trade_sizes(per[w], BASIS_RUPEES) for w in ws},
        sizes_shares={w: trade_sizes(per[w], BASIS_SHARES) for w in ws},
        fragmentation=frag,
        frag_change=frag_change(frag),
        signatures=broker_signatures(per[ws[0]], th),
    )


# ---------------------------------------------------------------------------


def _demo() -> None:
    """Self-check against the real archive. Every assert is an invariant, not a fit."""
    from . import loader

    syms = loader.symbols()
    assert syms, "no floorsheet archive"
    sym = "NABIL" if "NABIL" in syms else syms[0]

    hist = loader.load_last(sym, 120)
    assert len(hist) >= 30, f"{sym}: only {len(hist)} sessions"
    ses = hist[-30:]
    st = analyse(ses, baseline=hist)

    # -- 24: concentration invariants, on real data, every window ------------
    for w, c in st.concentration.items():
        for name, p in (("buy", c.buy), ("sell", c.sell), ("broker", c.broker)):
            assert p.n > 0, f"{w}D {name}: no participants"
            assert 0 < p.hhi <= 1.0, f"{w}D {name} hhi {p.hhi}"
            assert p.top1 <= p.top3 + 1e-12 <= p.top5 + 1e-12 <= p.top10 + 1e-12 <= 1.0 + 1e-9, \
                f"{w}D {name} top-k not monotone"
            assert p.entropy >= 0.0, f"{w}D {name} entropy {p.entropy}"
            assert 1.0 <= p.diversity <= p.n + 1e-9
            # HHI and effective count are two views of the same thing; they must agree
            # in direction: an even split has HHI ~ 1/n.
            assert p.hhi >= 1.0 / p.n - 1e-9, f"{w}D {name} hhi below the even-split floor"
        assert c.turnover > 0 and c.volume > 0, "concentration must ship its liquidity"

    # HHI aggregated over the window is NOT the mean of daily HHIs. If someone
    # "fixes" this by averaging per day, this fails.
    daily = [profile([b.gross_qty for b in brokers.day(s).values()]).hhi for s in ses]
    win_hhi = st.concentration[30].broker.hhi
    assert abs(statistics.fmean(daily) - win_hhi) > 1e-6, "window HHI collapsed to a daily average"
    assert statistics.fmean(daily) > win_hhi, "a single day must look more concentrated than 30"

    # -- 25: the spec's worked example must label as rapidly increasing -------
    flat = Shares(10, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    spec = {w: Concentration(0, 0.0, 10, flat, flat, flat._replace(hhi=h))
            for w, h in ((30, 0.12), (15, 0.18), (7, 0.31), (3, 0.47))}
    d = dynamics(spec)
    assert d.broker_trend == "rapidly_increasing", d.broker_trend
    assert abs(d.change - 0.35) < 1e-9 and d.spike, d
    assert dynamics({w: Concentration(0, 0.0, 10, flat, flat, flat._replace(hhi=h))
                     for w, h in ((30, 0.47), (15, 0.31), (7, 0.18), (3, 0.12))}).release

    # -- 22/23: consensus and breadth ----------------------------------------
    for w in st.windows:
        c, b = st.consensus[w], st.breadth[w]
        assert c.brokers == b.brokers == c.net_buyers + c.net_sellers + c.neutral
        assert -1.0 <= c.consensus <= 1.0 and -1.0 <= c.weighted_consensus <= 1.0
        assert 0.0 <= c.strength <= 1.0 and 0.0 <= c.dispersion <= 1.0
        assert 0.0 <= c.entropy <= math.log(3) + 1e-9
        assert 0.0 <= c.top_buyer_dependence <= 1.0 + 1e-9
        assert c.accumulation in ("broad", "mixed", "concentrated", "none")
        assert abs(b.accumulation + b.distribution + c.neutral / b.brokers - 1.0) < 1e-9
    assert st.breadth_trend.reversal in ("to_accumulation", "to_distribution", "none")

    # The weighted vote must not be the identically-zero one that was cut.
    assert any(abs(st.consensus[w].weighted_consensus) > 1e-6 for w in st.windows), \
        "weighted consensus is pinned at zero — the degenerate definition crept back"

    # -- 26: Pareto shares are monotone and complete -------------------------
    for w, p in st.pareto.items():
        for name in ("volume", "turnover", "net_buy", "net_sell"):
            ps = getattr(p, name)
            assert ps.top1pct <= ps.top5pct + 1e-12 <= ps.top10pct + 1e-12 <= ps.top20pct + 1e-12 <= 1.0 + 1e-9, \
                f"{w}D pareto {name} not monotone"
            assert ps.top20pct > 0.0
        assert p.volume.top20pct >= 0.20, "the top 20% of brokers hold less than 20% of volume?"

    # -- 27/28: large trades, both bases --------------------------------------
    all_trades = sum(len(s.trades) for s in ses)
    for w in st.windows:
        for lg in (st.large_value[w], st.large_shares[w]):
            n_small = sum(1 for s in ses[-w:] for t in s.trades if _size(t, lg.basis) < lg.threshold)
            n_all = sum(len(s.trades) for s in ses[-w:])
            assert lg.trades + n_small == n_all, f"{w}D {lg.basis}: large+small != total"
            assert lg.trades <= n_all and 0.0 <= lg.trade_pct <= 1.0
            assert 0.0 <= lg.volume_pct <= 1.0 and 0.0 <= lg.turnover_pct <= 1.0
            assert lg.top10_pct <= lg.top50_pct + 1e-12 <= lg.top100_pct + 1e-12 <= 1.0 + 1e-9
            assert 0.0 <= lg.buyer_hhi <= 1.0 and 0.0 <= lg.seller_hhi <= 1.0
            assert 0.0 <= lg.persistence <= 1.0
            assert lg.rate_p10 <= lg.rate_p90 + 1e-9
            assert lg.largest_qty <= max(t.quantity for s in ses[-w:] for t in s.trades)

    # The reason both bases exist: ranking trades by SHARE COUNT systematically
    # picks the cheaper prints, which is the whole "clip size is an inverse-price
    # proxy" result. The top 50 by shares must be no dearer than the top 50 by
    # rupees — checked to hold on 160/160 sampled symbol-windows, and forced to
    # equality only when the window traded at a single price.
    tr = [t for s in ses for t in s.trades]
    top_q = sorted(tr, key=lambda t: t.quantity, reverse=True)[:50]
    top_a = sorted(tr, key=lambda t: t.amount, reverse=True)[:50]
    assert statistics.fmean([t.rate for t in top_q]) <= statistics.fmean([t.rate for t in top_a]) + 1e-9, \
        "the biggest trades by share count came out DEARER than the biggest by value"
    assert st.large_value[30].threshold == st.thresholds.large_amt
    assert st.large_shares[30].threshold == st.thresholds.large_qty

    # The reason large-trade IMBALANCE is not a field: it is exactly zero, always.
    big = [t for s in ses for t in s.trades if t.amount >= st.thresholds.large_amt]
    bagg = brokers.from_trades(big)
    assert sum(b.buy_qty for b in bagg.values()) == sum(b.sell_qty for b in bagg.values()), \
        "large-trade buy != sell — conservation broke, not possible"
    assert st.large_value[30].net_qty > 0, "net_qty is the non-degenerate replacement"

    # A fixed baseline threshold must let the large-trade share MOVE across windows,
    # which is the whole reason thresholds() is separate from large().
    shares_seen = {round(st.large_value[w].trade_pct, 4) for w in st.windows}
    assert len(shares_seen) > 1, "large-trade share is constant across windows"
    # ...whereas self-thresholding pins it at the (100 - pct) tail in EVERY window,
    # long or short, so the number stops carrying information about the window.
    # Measured across 228 sampled symbol-windows: never below 0.100, median 0.101.
    # It can only run high, never low, because a tie block sitting on the cutoff is
    # taken whole — thin and heavily-tied symbols reach 0.78 that way, so the bound
    # asserted here is deliberately one-sided rather than a tight band round 0.10.
    for w in (3, 30):
        selfth = large(ses[-w:], thresholds(ses[-w:]), BASIS_RUPEES)
        assert selfth.trade_pct >= 0.10 - 1e-9, f"{w}D self-threshold gave {selfth.trade_pct:.3f}"

    # -- 29: distribution shape ----------------------------------------------
    for w in st.windows:
        for sd in (st.sizes[w], st.sizes_shares[w]):
            assert sd.n > 0
            assert sd.median <= sd.p75 <= sd.p90 <= sd.p95 <= sd.p99 <= sd.max + 1e-9
            assert sd.stdev >= 0 and abs(sd.variance - sd.stdev**2) < 1e-6
            assert abs(sd.cv - sd.stdev / sd.mean) < 1e-12
            # Fat right tail, but ONLY on a real sample. Thin symbols genuinely print
            # negative skew (SDBD87, an 8-trade week, came back at -0.03) so this is
            # gated on size instead of asserted as a law: across 180 sampled windows
            # the minimum skew above 1,000 trades was +4.3.
            if sd.n >= 1000:
                assert sd.skew > 0 and sd.kurtosis > 0 and sd.cv > 0, \
                    f"{w}D {sd.n} trades: skew {sd.skew:.2f}, kurtosis {sd.kurtosis:.2f}"
    # Skew/kurtosis must be the real estimators, not placeholders: a symmetric input
    # has skew 0 and a normal-ish one excess kurtosis near 0.
    assert abs(skewness([1, 2, 3, 4, 5])) < 1e-12
    assert abs(kurtosis([1, 2, 3, 4, 5]) + 1.2) < 0.01, kurtosis([1, 2, 3, 4, 5])
    assert skewness([1, 1, 1, 1, 100]) > 1.5

    # -- 30: broker signatures ------------------------------------------------
    assert st.signatures, "no broker cleared min_trades over 30 sessions"
    for bs in st.signatures.values():
        assert bs.trades >= 5
        assert bs.median <= bs.p90 <= bs.p95 <= bs.p99 <= bs.max + 1e-9
        assert 0.0 <= bs.large_ratio <= 1.0 and 0.0 <= bs.small_ratio <= 1.0
        assert 0.0 < bs.consistency <= 1.0
        assert bs.median_amt > 0
    assert len({round(b.median, 2) for b in st.signatures.values()}) > 1, \
        "every broker has the same median clip — signature is not varying"

    # -- 31/32: fragmentation -------------------------------------------------
    for w, f in st.fragmentation.items():
        assert f.trades > 0 and f.volume > 0
        assert abs(f.avg_size - f.volume / f.trades) < 1e-9
        assert f.trades_per_1k > 0 and f.frag_ratio > 0
        assert 0.0 <= f.large_ratio <= 1.0 and 0.0 <= f.small_ratio <= 1.0
        assert f.large_volume_ratio >= f.large_ratio - 1e-9, \
            "large trades must carry at least their headcount share of volume"
    assert st.frag_change.trend in ("fragmenting", "consolidating", "stable")
    assert st.frag_change.structural in (
        "large_trades_emerging", "large_trades_disappearing", "small_trade_dominance", "stable")
    assert st.frag_change.large_emergence == 0.0 or st.frag_change.large_disappearance == 0.0

    assert st.thresholds.large_qty > st.thresholds.median_qty >= st.thresholds.small_qty > 0
    assert st.thresholds.large_amt > st.thresholds.median_amt >= st.thresholds.small_amt > 0

    c30, c3 = st.concentration[30], st.concentration[3]
    cons = st.consensus[30]
    lg = st.large_value[3]
    print(f"structure ok — {sym} {st.date}: {len(hist)} sessions of history, "
          f"{st.thresholds.trades:,} baseline trades, large >= Rs {st.thresholds.large_amt:,.0f} "
          f"({st.thresholds.large_qty:,.0f} shares)")
    print(f"  30D: {cons.brokers} brokers, {cons.net_buyers}B/{cons.net_sellers}S "
          f"({cons.accumulation} accumulation, dep {cons.top_buyer_dependence:.0%}), "
          f"consensus {cons.consensus:+.2f} wtd {cons.weighted_consensus:+.2f}, "
          f"HHI {c30.broker.hhi:.3f} on Rs {c30.turnover/1e6:.0f}m")
    print(f"  HHI 30D {c30.broker.hhi:.3f} -> 3D {c3.broker.hhi:.3f} "
          f"({st.conc_dynamics.broker_trend}), breadth {st.breadth_trend.trend}, "
          f"top20% of brokers hold {st.pareto[30].volume.top20pct:.0%} of volume")
    print(f"  3D large trades: {lg.trades} of {st.sizes[3].n} ({lg.trade_pct:.1%}) carry "
          f"{lg.volume_pct:.1%} of volume, vwap {lg.vwap_premium:+.2%}, persistence "
          f"{lg.persistence:.0%}; frag {st.frag_change.trend} ({st.frag_change.structural}), "
          f"size skew {st.sizes[3].skew:.1f} kurt {st.sizes[3].kurtosis:.1f}")


if __name__ == "__main__":
    _demo()
