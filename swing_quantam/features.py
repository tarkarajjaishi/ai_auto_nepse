"""Price, volume, turnover, VWAP and the executed-price profile — spec sections 5-10.

Everything here describes ONE stock from its own executed transactions. No broker
identity is used: that starts at :mod:`swing_quantam.brokers`. Nothing here reads
the filesystem — every window function takes an already-sliced ``list[Session]``
from ``loader.load_last(..., upto=)``, which is what makes the package
point-in-time by construction rather than by discipline.

Two things shape the maths and are worth knowing before reading the code:

1. **Rates repeat, heavily.** NEPSE quotes NPR on a coarse tick, so a session of
   3,000 trades routinely lands on well under 100 distinct prices. That is why
   "most frequently traded price" and "most heavily traded price" are separate
   fields (they disagree often — the modal print is small and retail, the heavy
   one is where size went), and why the price profile bins on raw traded rates by
   default instead of inventing buckets.
2. **Everything relative is relative to THIS stock.** A "large trade" is large
   against this symbol's own trade-size distribution over the window passed in
   (spec section 27), never against a market-wide share count. 500 shares is a
   block in one name and a scalp in another.

Metrics the spec lists that were CUT because they are pinned to a constant and
would ship as a column that can never move — measured on real sessions first,
not argued from theory:

* **Stock-level buy VWAP vs sell VWAP** (section 9). Every trade has one buyer
  and one seller at the same rate, so the buy side and the sell side aggregate to
  byte-identical VWAPs. The difference is exactly 0.0 on every stock, every day.
  Buy/sell VWAP is real *per broker* — see ``brokers.BrokerDay``.
* **Volume-weighted mean VWAP distance** (section 9). Sum(q*(r - vwap))/Sum(q)
  expands to (turnover - vwap*volume)/volume == 0 by the definition of VWAP. The
  *unweighted* mean distance across trades is not zero and is shipped instead, as
  ``PriceStats.vwap_skew_unweighted``.
* **Value-area volume share** (section 10). The value area is *defined* as the
  narrowest band holding 70% of volume, so its volume share is ~0.70 always. The
  informative half is its WIDTH, shipped as ``range_utilisation``.
* **Percentage of volume at each price** (section 7) is not a separate field; it
  is ``Profile.at_price`` divided by ``Profile.volume``, available as
  ``Profile.share_at_price()``.

Turnover by broker / by broker pair (section 8) is deliberately absent: broker
aggregation lives in :mod:`swing_quantam.brokers` and must not be duplicated here.

Pure stdlib — the target VPS has no numpy and never will.
"""

from __future__ import annotations

import statistics
from typing import NamedTuple, Sequence

from .loader import WINDOWS, Session, Trade


# ---------------------------------------------------------------------------
# small maths helpers
# ---------------------------------------------------------------------------

def _pct(ordered: Sequence[float], p: float) -> float:
    """Percentile of an ALREADY-SORTED sequence, linear interpolation.

    ``statistics.quantiles`` would need a fresh 99-way cut per call; this reuses
    one sort across every percentile of the same population.
    """
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return float(ordered[0])
    k = (len(ordered) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return float(ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo))


def _moments(vals: Sequence[float]) -> tuple[float, float]:
    """Population skewness and EXCESS kurtosis. Zero when undefined.

    Hand-rolled because scipy is not installed. Population (biased) moments are
    used on purpose: these are ranked and z-scored downstream, never reported as
    inferential statistics, and the n/(n-1) correction only rescales the ranking.
    """
    n = len(vals)
    if n < 3:
        return 0.0, 0.0
    mean = statistics.fmean(vals)
    var = statistics.fmean([(v - mean) ** 2 for v in vals])
    if var <= 0:
        return 0.0, 0.0
    sd = var ** 0.5
    m3 = statistics.fmean([(v - mean) ** 3 for v in vals])
    m4 = statistics.fmean([(v - mean) ** 4 for v in vals])
    return m3 / sd ** 3, m4 / sd ** 4 - 3.0


def _hhi(weights: Sequence[float]) -> float:
    """Herfindahl concentration of a set of positive weights. 0 = flat, 1 = all one."""
    total = sum(weights)
    if total <= 0:
        return 0.0
    return sum((w / total) ** 2 for w in weights)


def _corr(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Pearson r, 0.0 when either side is constant (which happens on thin days)."""
    if len(xs) < 3:
        return 0.0
    try:
        return statistics.correlation(xs, ys)
    except statistics.StatisticsError:
        return 0.0


def _trades(sessions: Sequence[Session]) -> list[Trade]:
    return [t for s in sessions for t in s.trades]


def _blank(cls, **kw):
    """A zero-filled result for the no-trades case.

    Built with ``_make`` rather than a hand-written argument list: the first cut of
    this module miscounted the zeros in three of four such lists, and adding a
    field later would have re-broken every one of them silently.
    """
    return cls._make([0] * len(cls._fields))._replace(**kw)


# ---------------------------------------------------------------------------
# 5. BASIC MARKET STATISTICS
# ---------------------------------------------------------------------------

class DayStats(NamedTuple):
    """Section 5 — one session, described without reference to any broker.

    ``buyers``/``sellers`` are counts of distinct broker numbers, not investors.
    The floorsheet has no client identity, so "unique buyers" and "unique buyer
    brokers" from the spec are the same number here; they are not shipped twice.
    """

    symbol: str
    date: str
    trades: int
    volume: int
    turnover: float
    avg_price: float  # mean of per-trade rates, unweighted
    median_price: float
    vwap: float  # volume-weighted; differs from avg_price whenever size clusters
    min_rate: float
    max_rate: float
    price_range: float
    range_pct: float  # range as a fraction of vwap — comparable across stocks
    avg_size: float
    median_size: float
    min_size: int
    max_size: int
    buyers: int
    sellers: int
    participants: int


def stats(session: Session) -> DayStats:
    """Section 5 for one session."""
    ts = session.trades
    if not ts:
        return _blank(DayStats, symbol=session.symbol, date=session.date)

    rates = sorted(t.rate for t in ts)
    sizes = sorted(t.quantity for t in ts)
    vwap = session.vwap
    rng = rates[-1] - rates[0]
    buyers = {t.buyer for t in ts}
    sellers = {t.seller for t in ts}

    return DayStats(
        symbol=session.symbol,
        date=session.date,
        trades=len(ts),
        volume=session.volume,
        turnover=session.turnover,
        avg_price=statistics.fmean(rates),
        median_price=statistics.median(rates),
        vwap=vwap,
        min_rate=rates[0],
        max_rate=rates[-1],
        price_range=rng,
        range_pct=rng / vwap if vwap else 0.0,
        avg_size=statistics.fmean(sizes),
        median_size=statistics.median(sizes),
        min_size=sizes[0],
        max_size=sizes[-1],
        buyers=len(buyers),
        sellers=len(sellers),
        participants=len(buyers | sellers),
    )


# ---------------------------------------------------------------------------
# 6. PRICE ANALYSIS
# ---------------------------------------------------------------------------

class PriceStats(NamedTuple):
    """Section 6 over a window of sessions.

    Percentiles are over per-trade rates, unweighted — one 10-share print counts
    as much as one 10,000-share print. That is the point: this describes where
    the market *quoted*, while ``vwap`` and the profile describe where size went.
    """

    sessions: int
    trades: int
    volume: int
    high: float
    low: float
    mean: float
    median: float
    vwap: float
    price_range: float
    range_pct: float
    stdev: float
    variance: float
    p10: float
    p25: float
    p50: float
    p75: float
    p90: float
    p95: float
    freq_price: float  # most FREQUENTLY traded rate, by trade count
    freq_price_share: float
    heavy_price: float  # most HEAVILY traded rate, by volume
    heavy_price_share: float
    levels: int  # distinct traded rates
    clustering: float  # share of trades sitting on the 5 busiest rates
    concentration: float  # HHI of volume across rates
    dispersion: float  # stdev / mean, i.e. coefficient of variation
    skew: float
    price_volume_corr: float  # per-trade rate vs per-trade size
    vwap_skew_unweighted: float  # mean (rate - vwap)/vwap; the WEIGHTED one is 0 by construction


def price(sessions: Sequence[Session]) -> PriceStats:
    """Section 6 for an already-sliced window, oldest first."""
    ts = _trades(sessions)
    if not ts:
        return _blank(PriceStats, sessions=len(sessions))

    rates = sorted(t.rate for t in ts)
    volume = sum(t.quantity for t in ts)
    turnover = sum(t.amount for t in ts)
    vwap = turnover / volume if volume else 0.0
    mean = statistics.fmean(rates)
    var = statistics.variance(rates) if len(rates) > 1 else 0.0
    rng = rates[-1] - rates[0]

    by_count: dict[float, int] = {}
    by_volume: dict[float, int] = {}
    for t in ts:
        by_count[t.rate] = by_count.get(t.rate, 0) + 1
        by_volume[t.rate] = by_volume.get(t.rate, 0) + t.quantity

    freq_price = max(by_count, key=lambda r: (by_count[r], by_volume[r]))
    heavy_price = max(by_volume, key=lambda r: (by_volume[r], by_count[r]))
    top5 = sorted(by_count.values(), reverse=True)[:5]

    return PriceStats(
        sessions=len(sessions),
        trades=len(ts),
        volume=volume,
        high=rates[-1],
        low=rates[0],
        mean=mean,
        median=statistics.median(rates),
        vwap=vwap,
        price_range=rng,
        range_pct=rng / vwap if vwap else 0.0,
        stdev=var ** 0.5,
        variance=var,
        p10=_pct(rates, 10),
        p25=_pct(rates, 25),
        p50=_pct(rates, 50),
        p75=_pct(rates, 75),
        p90=_pct(rates, 90),
        p95=_pct(rates, 95),
        freq_price=freq_price,
        freq_price_share=by_count[freq_price] / len(ts),
        heavy_price=heavy_price,
        heavy_price_share=by_volume[heavy_price] / volume if volume else 0.0,
        levels=len(by_count),
        clustering=sum(top5) / len(ts),
        concentration=_hhi(list(by_volume.values())),
        dispersion=(var ** 0.5) / mean if mean else 0.0,
        skew=_moments(rates)[0],
        # Paired per-trade, so the lists must stay in trade order — not the sorted
        # `rates` above, which would correlate a price with somebody else's size.
        price_volume_corr=_corr([t.rate for t in ts], [float(t.quantity) for t in ts]),
        vwap_skew_unweighted=statistics.fmean([(t.rate - vwap) / vwap for t in ts]) if vwap else 0.0,
    )


# ---------------------------------------------------------------------------
# 7. VOLUME ANALYSIS
# ---------------------------------------------------------------------------

class VolumeStats(NamedTuple):
    """Section 7 — the trade-SIZE distribution and what sits in its tails.

    ``large_cut`` and ``small_cut`` are this stock's own P90 and P25 trade size
    over the window passed in (spec section 27). A fixed share threshold would
    make every high-priced, low-float name look institution-free and every cheap
    name look like a block market.
    """

    trades: int
    volume: int
    mean_size: float
    median_size: float
    max_size: int
    min_size: int
    stdev: float
    variance: float
    skew: float
    kurtosis: float  # excess
    p90: float
    p95: float
    p99: float
    large_cut: float
    large_trades: int
    large_volume: int
    large_trade_pct: float  # of trade COUNT
    large_volume_pct: float  # of VOLUME — the two say different things, ship both
    small_cut: float
    small_trades: int
    small_volume: int
    small_trade_pct: float
    small_volume_pct: float
    concentration: float  # HHI over individual trade sizes
    top10_share: float  # 10 biggest prints as a share of volume


def volume(sessions: Sequence[Session]) -> VolumeStats:
    """Section 7 for an already-sliced window, oldest first."""
    ts = _trades(sessions)
    if not ts:
        return _blank(VolumeStats)

    sizes = sorted(t.quantity for t in ts)
    total = sum(sizes)
    var = statistics.variance(sizes) if len(sizes) > 1 else 0.0
    skew, kurt = _moments(sizes)

    large_cut = _pct(sizes, 90)
    small_cut = _pct(sizes, 25)
    # >= for large, strictly < for small, so a degenerate distribution (every trade
    # the same size, which really happens on 1-2 trade sessions) cannot classify a
    # trade as both. The demo asserts large + small <= total on real data.
    large = [q for q in sizes if q >= large_cut]
    small = [q for q in sizes if q < small_cut]

    return VolumeStats(
        trades=len(ts),
        volume=total,
        mean_size=statistics.fmean(sizes),
        median_size=statistics.median(sizes),
        max_size=sizes[-1],
        min_size=sizes[0],
        stdev=var ** 0.5,
        variance=var,
        skew=skew,
        kurtosis=kurt,
        p90=large_cut,
        p95=_pct(sizes, 95),
        p99=_pct(sizes, 99),
        large_cut=large_cut,
        large_trades=len(large),
        large_volume=sum(large),
        large_trade_pct=len(large) / len(ts),
        large_volume_pct=sum(large) / total if total else 0.0,
        small_cut=small_cut,
        small_trades=len(small),
        small_volume=sum(small),
        small_trade_pct=len(small) / len(ts),
        small_volume_pct=sum(small) / total if total else 0.0,
        concentration=_hhi(sizes),
        top10_share=sum(sizes[-10:]) / total if total else 0.0,
    )


# ---------------------------------------------------------------------------
# 8. TURNOVER ANALYSIS
# ---------------------------------------------------------------------------

class TurnoverStats(NamedTuple):
    """Section 8, stock side only.

    Turnover by buyer, by seller and by broker pair are section 8 items that live
    in :mod:`swing_quantam.brokers` (``BrokerDay.buy_amt`` / ``sell_amt``) and the
    pair layer; "turnover by stock" is this module's ``total`` by definition, and
    "top turnover stocks/brokers" are cross-sectional rankings that belong to the
    board builder, not to a single-symbol feature.
    """

    trades: int
    total: float
    mean_amount: float
    median_amount: float
    max_amount: float
    min_amount: float
    p90_amount: float
    stdev: float
    concentration: float  # HHI over per-trade amounts
    top10_share: float
    largest_share: float  # single biggest ticket as a share of turnover


def turnover(sessions: Sequence[Session]) -> TurnoverStats:
    """Section 8 for an already-sliced window, oldest first."""
    ts = _trades(sessions)
    if not ts:
        return _blank(TurnoverStats)

    amts = sorted(t.amount for t in ts)
    total = sum(amts)

    return TurnoverStats(
        trades=len(ts),
        total=total,
        mean_amount=statistics.fmean(amts),
        median_amount=statistics.median(amts),
        max_amount=amts[-1],
        min_amount=amts[0],
        p90_amount=_pct(amts, 90),
        stdev=statistics.stdev(amts) if len(amts) > 1 else 0.0,
        concentration=_hhi(amts),
        top10_share=sum(amts[-10:]) / total if total else 0.0,
        largest_share=amts[-1] / total if total else 0.0,
    )


# ---------------------------------------------------------------------------
# 9. VWAP ANALYSIS
# ---------------------------------------------------------------------------

def vwap_distance(price_: float, vwap: float) -> float:
    """Section 9 — normalised distance, ``(price - vwap) / vwap``."""
    return (price_ - vwap) / vwap if vwap else 0.0


def vwap_set(symbol_sessions: Sequence[Session]) -> dict[str, float]:
    """Section 9 — daily VWAP plus every window VWAP, from one sliced history.

    Keys: ``1d``, ``3d``, ``7d``, ``15d``, ``30d`` and ``sessions``.

    A window VWAP is turnover-over-volume across the last N sessions, NOT the mean
    of the daily VWAPs — averaging VWAPs gives a quiet day the same weight as a
    heavy one and is a different (wrong) number.

    When fewer than N sessions exist the value is computed from what is there.
    ``sessions`` is returned so the caller can tell a true 30D VWAP from a 4-day
    one wearing its name — a short window presented as a full one is exactly the
    staleness failure this project keeps hitting.
    """
    out: dict[str, float] = {"sessions": float(len(symbol_sessions))}
    out["1d"] = symbol_sessions[-1].vwap if symbol_sessions else 0.0
    for n in WINDOWS:
        chunk = symbol_sessions[-n:]
        vol = sum(s.volume for s in chunk)
        out[f"{n}d"] = (sum(s.turnover for s in chunk) / vol) if vol else 0.0
    return out


# ---------------------------------------------------------------------------
# 10. VOLUME-AT-PRICE / EXECUTED PRICE PROFILE
# ---------------------------------------------------------------------------

class Profile(NamedTuple):
    """Section 10 — an EXECUTED-volume profile. Never an order-book profile.

    The floorsheet records fills and nothing else: there are no unexecuted bids or
    offers in this data, so "support at 520" here means *520 is where shares
    actually changed hands*, not *there are buyers waiting at 520*. Any downstream
    wording that implies resting orders is wrong about the source.

    ``acceptance``/``rejection`` are proxies in the same spirit: volume that piled
    up inside a band is price the market accepted; a third of the range that took
    almost no volume is price it rejected. Both are inferences, labelled as such.
    """

    at_price: dict[float, int]  # price -> volume, in price order
    turnover_at_price: dict[float, float]
    count_at_price: dict[float, int]
    sessions: int
    trades: int
    volume: int
    turnover: float
    low: float
    high: float
    vwap: float
    poc: float  # point of control — the single highest-volume price
    poc_volume: int
    poc_share: float
    value_low: float  # 70% value area, grown outward from the POC
    value_high: float
    value_width: float
    range_utilisation: float  # value width / full range: how tightly volume sits
    vwap_position: float  # (vwap - low) / range — volume-weighted place in the range
    poc_position: float
    low_third: float  # volume share of the bottom third of the range
    mid_third: float
    high_third: float
    levels: int
    hvn: tuple[float, ...]  # high-volume nodes, busiest first
    lvn: tuple[float, ...]  # thin prices INSIDE the value area — the gaps in acceptance
    clusters: int  # contiguous runs of traded prices
    top_cluster_share: float

    def share_at_price(self) -> dict[float, float]:
        """Section 7's 'percentage of volume at each price' — a view, not a metric."""
        return {p: v / self.volume for p, v in self.at_price.items()} if self.volume else {}


def _value_area(prices: Sequence[float], vol: dict[float, int], poc_i: int, target: float) -> tuple[float, float]:
    """Grow outward from the POC, always taking the heavier neighbour, until 70%.

    This is the standard volume-profile construction rather than a simple
    percentile band: a percentile band around the median price ignores which side
    the volume actually sits on, and on a trending session that is the whole story.
    """
    lo = hi = poc_i
    acc = vol[prices[poc_i]]
    while acc < target and (lo > 0 or hi < len(prices) - 1):
        below = vol[prices[lo - 1]] if lo > 0 else -1
        above = vol[prices[hi + 1]] if hi < len(prices) - 1 else -1
        if above >= below:
            hi += 1
            acc += vol[prices[hi]]
        else:
            lo -= 1
            acc += vol[prices[lo]]
    return prices[lo], prices[hi]


def profile(sessions: Sequence[Session], bins: int | None = None) -> Profile:
    """Section 10 for an already-sliced window, oldest first.

    ``bins=None`` (the default) profiles on the raw traded rates. NEPSE rates
    repeat heavily, so the raw levels ARE the natural buckets and inventing even
    spacing only smears the clustering that matters. Pass ``bins`` to force even
    buckets when comparing two stocks with very different tick densities.
    """
    ts = _trades(sessions)
    if not ts:
        return _blank(
            Profile, at_price={}, turnover_at_price={}, count_at_price={},
            sessions=len(sessions), hvn=(), lvn=(),
        )

    lo = min(t.rate for t in ts)
    hi = max(t.rate for t in ts)
    width = (hi - lo) / bins if bins and hi > lo else 0.0

    def level(rate: float) -> float:
        if not width:
            return round(rate, 2)
        i = min(int((rate - lo) / width), bins - 1)
        return round(lo + (i + 0.5) * width, 2)

    vol: dict[float, int] = {}
    amt: dict[float, float] = {}
    cnt: dict[float, int] = {}
    for t in ts:
        p = level(t.rate)
        vol[p] = vol.get(p, 0) + t.quantity
        amt[p] = amt.get(p, 0.0) + t.amount
        cnt[p] = cnt.get(p, 0) + 1

    prices = sorted(vol)
    total_vol = sum(vol.values())
    total_amt = sum(amt.values())
    vwap = total_amt / total_vol if total_vol else 0.0
    rng = hi - lo

    poc = max(prices, key=lambda p: (vol[p], cnt[p]))
    v_lo, v_hi = _value_area(prices, vol, prices.index(poc), total_vol * 0.70)

    # Thirds of the PRICE range, not of the level list: a range with 40 levels
    # bunched at the bottom must show its emptiness up top, and quantile-thirds
    # would hide exactly that.
    def band_share(a: float, b: float, include_top: bool) -> float:
        got = sum(v for p, v in vol.items() if a <= p < b or (include_top and p == b))
        return got / total_vol if total_vol else 0.0

    if rng > 0:
        low_third = band_share(lo, lo + rng / 3, False)
        mid_third = band_share(lo + rng / 3, lo + 2 * rng / 3, False)
        high_third = band_share(lo + 2 * rng / 3, hi, True)
    else:
        low_third, mid_third, high_third = 0.0, 1.0, 0.0

    mean_level = total_vol / len(prices)
    hvn = tuple(sorted((p for p in prices if vol[p] >= 1.5 * mean_level), key=lambda p: -vol[p])[:10])
    lvn = tuple(p for p in prices if v_lo <= p <= v_hi and vol[p] <= 0.25 * mean_level)[:10]

    # A "cluster" is a run of prices with no unusual gap between them. The break
    # threshold is twice the typical step, so it adapts to the stock's own tick
    # density instead of assuming a 0.10 tick that only holds in some price bands.
    steps = [b - a for a, b in zip(prices, prices[1:])]
    brk = 2 * statistics.median(steps) if steps else 0.0
    runs: list[int] = []
    run = vol[prices[0]]
    for step, p in zip(steps, prices[1:]):
        if brk and step > brk:
            runs.append(run)
            run = 0
        run += vol[p]
    runs.append(run)

    return Profile(
        at_price={p: vol[p] for p in prices},
        turnover_at_price={p: amt[p] for p in prices},
        count_at_price={p: cnt[p] for p in prices},
        sessions=len(sessions),
        trades=len(ts),
        volume=total_vol,
        turnover=total_amt,
        low=lo,
        high=hi,
        vwap=vwap,
        poc=poc,
        poc_volume=vol[poc],
        poc_share=vol[poc] / total_vol if total_vol else 0.0,
        value_low=v_lo,
        value_high=v_hi,
        value_width=v_hi - v_lo,
        range_utilisation=(v_hi - v_lo) / rng if rng > 0 else 1.0,
        vwap_position=(vwap - lo) / rng if rng > 0 else 0.5,
        poc_position=(poc - lo) / rng if rng > 0 else 0.5,
        low_third=low_third,
        mid_third=mid_third,
        high_third=high_third,
        levels=len(prices),
        hvn=hvn,
        lvn=lvn,
        clusters=len(runs),
        top_cluster_share=max(runs) / total_vol if total_vol else 0.0,
    )


# ---------------------------------------------------------------------------

def _demo() -> None:
    """Self-check on the real archive. Asserts invariants AND that metrics vary.

    The variation checks are not padding: this project has shipped a metric that
    was mathematically pinned to a constant before, so anything that could be
    degenerate is measured across real sessions and must move.
    """
    from . import loader

    syms = loader.symbols()
    assert syms, "no floorsheet archive"
    sym = "NABIL" if "NABIL" in syms else syms[0]

    hist = loader.load_last(sym, 30)
    assert len(hist) >= 5, f"{sym}: only {len(hist)} sessions"
    s = hist[-1]

    # -- section 5 ----------------------------------------------------------
    d = stats(s)
    assert d.trades == len(s.trades) and d.volume == s.volume
    assert d.min_rate <= d.vwap <= d.max_rate, f"vwap {d.vwap} outside range"
    assert d.min_rate <= d.median_price <= d.max_rate
    assert d.price_range == d.max_rate - d.min_rate
    assert d.min_size <= d.avg_size <= d.max_size
    assert d.participants <= d.buyers + d.sellers
    assert abs(d.avg_size * d.trades - d.volume) < 1e-6

    # -- section 6 ----------------------------------------------------------
    p = price(hist)
    assert p.low <= p.p10 <= p.p25 <= p.p50 <= p.p75 <= p.p90 <= p.p95 <= p.high, "percentiles not monotonic"
    assert p.low <= p.vwap <= p.high
    assert p.freq_price in {t.rate for t in _trades(hist)}
    assert p.heavy_price in {t.rate for t in _trades(hist)}
    assert 0 < p.levels <= p.trades
    assert 0 < p.clustering <= 1.0 and 0 < p.concentration <= 1.0
    assert -1.0 <= p.price_volume_corr <= 1.0

    # -- section 7 ----------------------------------------------------------
    v = volume(hist)
    assert v.volume == sum(x.volume for x in hist)
    assert v.p90 <= v.p95 <= v.p99 <= v.max_size
    assert v.large_trades + v.small_trades <= v.trades, "large/small trade sets overlap"
    assert v.large_volume + v.small_volume <= v.volume
    assert v.min_size <= v.median_size <= v.max_size
    assert 0.0 < v.large_volume_pct <= 1.0 and 0.0 <= v.small_volume_pct < 1.0
    assert v.large_volume_pct > v.large_trade_pct, "large trades must carry more volume than their count share"

    # -- section 8 ----------------------------------------------------------
    to = turnover(hist)
    assert abs(to.total - sum(x.turnover for x in hist)) < 1.0
    assert to.min_amount <= to.median_amount <= to.max_amount
    assert 0.0 < to.largest_share <= to.top10_share <= 1.0

    # -- section 9 ----------------------------------------------------------
    vs = vwap_set(hist)
    assert abs(vs["1d"] - s.vwap) < 1e-9
    for n in loader.WINDOWS:
        chunk = hist[-n:]
        assert min(t.rate for t in _trades(chunk)) <= vs[f"{n}d"] <= max(t.rate for t in _trades(chunk))
    assert abs(vwap_distance(s.vwap * 1.10, s.vwap) - 0.10) < 1e-9
    # The CUT metric, guarded so it cannot come back: volume-weighting the VWAP
    # distance is identically zero by the definition of VWAP.
    wd = sum(t.quantity * vwap_distance(t.rate, s.vwap) for t in s.trades) / s.volume
    assert abs(wd) < 1e-9, "weighted vwap distance is supposed to be pinned to zero"
    # Same reason stock-level buy VWAP == sell VWAP: one buyer, one seller, one rate.
    bq = sum(t.quantity for t in s.trades)
    assert abs(sum(t.amount for t in s.trades) / bq - s.vwap) < 1e-9

    # -- section 10 ---------------------------------------------------------
    pr = profile([s])
    assert sum(pr.at_price.values()) == s.volume, "volume at price does not sum to session volume"
    assert sum(pr.count_at_price.values()) == len(s.trades)
    assert abs(sum(pr.turnover_at_price.values()) - s.turnover) < 1.0
    assert pr.poc in {round(t.rate, 2) for t in s.trades}, "POC is not a traded price"
    assert pr.low <= pr.value_low <= pr.poc <= pr.value_high <= pr.high
    assert abs(pr.low_third + pr.mid_third + pr.high_third - 1.0) < 1e-9, "range thirds do not partition volume"
    assert 0.0 <= pr.range_utilisation <= 1.0 and 0.0 <= pr.vwap_position <= 1.0
    assert 1 <= pr.clusters <= pr.levels and 0.0 < pr.top_cluster_share <= 1.0
    binned = profile([s], bins=10)
    assert sum(binned.at_price.values()) == s.volume and binned.levels <= 10

    # -- degeneracy sweep: every headline metric must actually move ----------
    series = {
        "poc_share": [], "range_utilisation": [], "vwap_position": [],
        "clustering": [], "concentration": [], "large_volume_pct": [],
        "price_volume_corr": [], "top_cluster_share": [], "dispersion": [],
    }
    for x in hist[-20:]:
        px, vx, prx = price([x]), volume([x]), profile([x])
        series["poc_share"].append(prx.poc_share)
        series["range_utilisation"].append(prx.range_utilisation)
        series["vwap_position"].append(prx.vwap_position)
        series["top_cluster_share"].append(prx.top_cluster_share)
        series["clustering"].append(px.clustering)
        series["concentration"].append(px.concentration)
        series["dispersion"].append(px.dispersion)
        series["price_volume_corr"].append(px.price_volume_corr)
        series["large_volume_pct"].append(vx.large_volume_pct)
    for name, vals in series.items():
        assert statistics.pstdev(vals) > 1e-6, f"{name} is constant across 20 real sessions — degenerate"

    # An INVALID session still reaches these functions (loader hands one back rather
    # than None so callers can tell "did not trade" from "could not read"), so the
    # no-trades path must return a well-formed zero, not raise.
    dead = loader.Session(sym, "1970-01-01", (), loader.Quality(loader.INVALID, 0, 0, 0, ()))
    assert stats(dead).volume == 0 and stats(dead).date == "1970-01-01"
    assert price([dead]).trades == 0 and volume([dead]).volume == 0
    assert turnover([dead]).total == 0 and profile([dead]).at_price == {}
    assert vwap_set([dead])["30d"] == 0.0

    print(f"features ok — {sym} {s.date}: {d.trades} trades, {d.volume:,} shares, "
          f"NPR {d.turnover/1e6:.1f}M, vwap {d.vwap:.2f} in [{d.min_rate:.2f}, {d.max_rate:.2f}], "
          f"{d.participants} brokers")
    print(f"  30D: vwap {vs['30d']:.2f} (7D {vs['7d']:.2f}, 3D {vs['3d']:.2f}), "
          f"P25/P75 {p.p25:.2f}/{p.p75:.2f}, {p.levels} price levels, "
          f"freq {p.freq_price:.2f} vs heavy {p.heavy_price:.2f}, clustering {p.clustering:.1%}")
    print(f"  size: median {v.median_size:,.0f}, large cut {v.large_cut:,.0f}+ = "
          f"{v.large_trade_pct:.1%} of trades carrying {v.large_volume_pct:.1%} of volume, "
          f"top-10 prints {v.top10_share:.1%}")
    print(f"  profile {s.date}: POC {pr.poc:.2f} ({pr.poc_share:.1%}), value {pr.value_low:.2f}-{pr.value_high:.2f} "
          f"= {pr.range_utilisation:.0%} of range, vwap at {pr.vwap_position:.0%}, "
          f"thirds {pr.low_third:.0%}/{pr.mid_third:.0%}/{pr.high_third:.0%}, {pr.clusters} clusters")


if __name__ == "__main__":
    _demo()
