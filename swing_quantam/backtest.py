"""Labels, walk-forward validation and expectancy -- spec sections 93-104 and 114.

This is the module that is allowed to say whether anything in the package pays.
Everything upstream *measures*; this file *tests*, and it is written on the
assumption that most of what it tests will fail. That is not pessimism, it is
this repository's measured history: six floorsheet operator families, an
accumulation cascade, counterparty breadth, wash reciprocity, seller exhaustion,
quiet absorption and the whole clip-size family all looked fine in sample and
died out of sample. The one broker metric with out-of-sample support on this
archive is ``net_churn`` (``volume_spike.py:102``), and it is included here as a
positive control rather than as a hope.

Five rules are load-bearing. Every one of them exists because breaking it has
already produced a wrong answer in this repo:

1. **Forward returns come from** :func:`swing_quantam.loader.adjusted_bars`
   **only.** ``loader.bars()`` is raw so that it lines up with floorsheet rates;
   labelling an outcome with it turns every bonus/rights ex-date into a fake
   -12% to -20% loss. There are six such ex-dates inside the demo universe and
   study window, and :func:`_demo` prints what raw bars would have done to them.
   Because zone prices are read off *raw* executed rates while outcomes are read
   off *adjusted* bars, zone levels cross that boundary as RATIOS to the price of
   the day (``profit1.low / zones.price``), never as absolute prices. A ratio is
   invariant to the adjustment factor; a price is not.

2. **Nothing at or after the decision date enters a feature -- including the
   normalisation.** Section 100 names future normalisation statistics and future
   percentiles explicitly, and this repo has already produced fake alpha by
   ranking a universe on full-sample turnover. Every normalised feature here is a
   trailing percentile of the metric's OWN prior history
   (:data:`PIT_LOOKBACK` observations, prior-only), and :func:`leakage_check`
   rebuilds a symbol's whole panel with the archive truncated and demands the
   surviving rows are byte-identical.

3. **Walk-forward, chronological, no whole-history fit** (section 99). Weights
   are fitted on a train block, the feature subset is chosen on a validate block,
   and every number reported as an edge comes from a test block that is strictly
   later than both. :func:`walk_forward` is the only place a weight is ever
   fitted, and :func:`importance` refuses any model that did not come from it.

4. **A baseline and a per-year table, always.** "The strategy made +4%" is
   meaningless if the universe made +9% on the same dates, and a single aggregate
   hid seven losing years out of thirteen the last time this repo skipped the
   per-year table. Every family is reported against (a) buy-and-hold on the same
   dates, (b) a date-matched random-entry control with an empirical p-value, and
   (c) the same-day universe mean, i.e. date-demeaned excess. On a strong market
   day every stock looks accumulated; date-demeaning is what removes that.

5. **Median as well as mean.** The median NEPSE 20-day return is negative and a
   thin tail carries the mean, so a mean-only result actively misleads.

Pure stdlib, like the rest of the package. Section 104's permutation importance
and feature ablation are hand-rolled (they are twenty lines each); **SHAP is out
of scope and is not approximated** -- a fake SHAP value would be worse than none.

Nothing here is calibrated. Section 114 is explicit that a score may not be
called a probability until it is, so :func:`probabilistic` runs a reliability
check and prints the word NOT CALIBRATED when the check fails, which on this
archive it does.
"""

from __future__ import annotations

import bisect
import math
import os
import random
import statistics
import time
from collections import Counter, deque
from datetime import date as _date
from typing import Callable, NamedTuple, Sequence

from . import brokers, history, loader
from . import zones as zones_mod

# ---------------------------------------------------------------------------
# study conventions -- fixed once, stated, and never tuned to improve a result
# ---------------------------------------------------------------------------

#: Study window. The floorsheet archive begins 2022-01-25 for the symbols that
#: have continuous coverage; the last complete session is 2026-08-18.
START, END = "2022-01-25", "2026-08-18"

#: Forward horizons in trading days. 20 is the primary one -- it is the swing
#: horizon the spec is written for, and the horizon every published number in
#: this repo's memory is quoted at.
HORIZONS = (5, 10, 20)
PRIMARY = 20

#: Entry is the OPEN of the session AFTER the decision date. The floorsheet for
#: date D is only complete once D has closed, so entering at D's close would be
#: buying with information that arrived at the same instant. Next-day open is the
#: earliest executable price and needs no assumption about fills.
ENTRY_LAG = 1

#: Target/stop convention for the MFE-based labels of section 97 and for
#: time-to-target / time-to-invalidation. Chosen ONCE, applied to every family,
#: never varied per family. The headline metrics (win rate, mean and median
#: return) are plain fixed-horizon returns and do not depend on these at all,
#: which deliberately limits how much these two numbers can flatter anything.
TARGET, STOP = 0.10, 0.07

#: A signal fires in the top (or bottom) quintile of its own trailing history.
#: One threshold for every family, so no family gets a hand-picked cut.
HOT, COLD = 80.0, 20.0

#: Trailing window and minimum prior observations for every percentile in this
#: file. 250 sessions is about a year; 60 is the point below which a percentile
#: is theatre. Both are passed straight to :func:`history.pit` semantics.
PIT_LOOKBACK, PIT_MIN = 250, 60

#: Decision grid. Features are computed for EVERY session (the rolling windows
#: need continuity) but observations are emitted every ``GRID_STRIDE`` sessions,
#: and zones -- which cost about a second a call on a liquid symbol -- every
#: ``ZONE_STRIDE``. ZONE_STRIDE is a multiple of GRID_STRIDE so that zone dates
#: are a subset of the grid and every family shares one date set.
#: ZONE_STRIDE == PRIMARY also makes the zone observations non-overlapping,
#: which is the only sample here where the 20-day windows do not overlap.
GRID_STRIDE, ZONE_STRIDE = 5, 20

#: Below this many occurrences a rate is not reported at all. Sections 94-96 all
#: ask for hit rates; a hit rate off three events is a number-shaped opinion, and
#: the honest output is the count and the word SUPPRESSED. This floor applies to
#: the rule families and the interaction patterns too, not only to the broker
#: sections -- a suppressed row prints no percentage at all, because a number
#: printed with a caveat beside it gets quoted without the caveat.
MIN_OBS = 30

#: How much of a 15-session window's volume the dominant broker PAIR must carry
#: before section 96 will look at it. Lower than the single-broker floor because
#: a pair is one cell of a roughly 50x50 matrix and 5% of a fortnight's volume
#: through one ordered pair is close to unheard of; the report prints the
#: measured distribution beside the count so the floor can be argued with.
PAIR_MIN_SHARE = 0.02

#: How many date-matched random draws form the control distribution.
CONTROL_DRAWS = 200

#: Rows in the section 103 sensitivity sample, per symbol.
SENS_DAYS = 25

_WINDOWS = (3, 7, 15, 30)

#: Section 102's stock-day feature vector. Names are this package's, the concepts
#: are the spec's; the mapping is in the comment beside each block. Everything
#: here is computed from data at or before its own date.
FEATURES: tuple[str, ...] = (
    # --- section 102, the per-day block -----------------------------------
    "trades",            # TransactionCount
    "volume",            # Volume
    "turnover",          # Turnover
    "vwap",              # VWAP
    "price_range",       # PriceRange, as a fraction of vwap
    "avg_trade",         # AverageTradeSize
    "median_trade",      # MedianTradeSize
    "large_ratio",       # LargeTradeRatio, threshold from PRIOR days only
    "buyers",            # BuyerCount
    "sellers",           # SellerCount
    "brokers",           # BrokerCount
    "top1_conc",         # Top1Concentration
    "top5_conc",         # Top5Concentration
    "buy_hhi",           # BuyerConcentration
    "sell_hhi",          # SellerConcentration
    "net_flow",          # NetBrokerFlow (top-buyer share minus top-seller share)
    "flow_quality",      # FlowQuality / daily net churn
    "flow_imbalance",    # FlowImbalance (net-buyer headcount balance)
    "fragmentation",     # Fragmentation (trades per broker)
    "broker_entropy",    # BrokerEntropy
    "broker_diversity",  # BrokerDiversity (brokers per trade)
    "vwap_position",     # PriceAtVolumeProfile (where vwap sits in the day range)
    "level_hhi",         # volume concentration across executed price levels
    # --- 3D / 7D / 15D / 30D features -------------------------------------
    "netchurn_3", "netchurn_7", "netchurn_15", "netchurn_30",      # FlowIntensity
    "tilt_3", "tilt_7", "tilt_15", "tilt_30",                      # windowed NetBrokerFlow
    "breadth_3", "breadth_7", "breadth_15", "breadth_30",          # BrokerBreadth
    "consensus_3", "consensus_7", "consensus_15", "consensus_30",  # BrokerConsensus
    "conc_7", "conc_30",                                           # windowed concentration
    "large_7", "large_30",                                         # LargeTradeConviction
    # --- derived ----------------------------------------------------------
    "persistence_7", "persistence_15", "persistence_30",  # FlowPersistence
    "momentum",             # FlowMomentum
    "acceleration",         # FlowAcceleration
    "reversal",             # FlowReversal
    "participation_change",  # ParticipationChange
    "vwap_distance",        # VWAPDistance
    "vwap_slope",           # VWAPSlope
    "price_flow_corr",      # PriceFlowCorrelation
    "flow_price_lag",       # FlowPriceLag
    "flow_efficiency",      # FlowEfficiency
    "anomaly",              # AnomalyScore
    "turnover_z",           # HistoricalZScore
    "signal_age",           # SignalAge
    "signal_decay",         # SignalDecay
    "alignment",            # AlignmentScore
    "conflict",             # ConflictScore
    "ret_5", "ret_15", "ret_30",  # trailing price context (never forward)
)

_FI = {name: i for i, name in enumerate(FEATURES)}

#: Section 104 asks for feature-GROUP importance as well as per-feature.
GROUPS: dict[str, tuple[str, ...]] = {
    "activity": ("trades", "volume", "turnover", "anomaly", "turnover_z"),
    "price": ("vwap", "price_range", "vwap_position", "level_hhi", "vwap_distance",
              "vwap_slope", "ret_5", "ret_15", "ret_30"),
    "size": ("avg_trade", "median_trade", "large_ratio", "large_7", "large_30", "fragmentation"),
    "flow": ("net_flow", "flow_quality", "netchurn_3", "netchurn_7", "netchurn_15", "netchurn_30",
             "tilt_3", "tilt_7", "tilt_15", "tilt_30", "momentum", "acceleration", "reversal",
             "persistence_7", "persistence_15", "persistence_30", "flow_efficiency",
             "signal_age", "signal_decay"),
    "breadth": ("buyers", "sellers", "brokers", "flow_imbalance", "broker_entropy",
                "broker_diversity", "breadth_3", "breadth_7", "breadth_15", "breadth_30",
                "consensus_3", "consensus_7", "consensus_15", "consensus_30",
                "participation_change"),
    "concentration": ("top1_conc", "top5_conc", "buy_hhi", "sell_hhi", "conc_7", "conc_30"),
    "timeframe": ("alignment", "conflict"),
    "priceflow": ("price_flow_corr", "flow_price_lag"),
}

#: Section 110 labels that this module treats as a long thesis / a short thesis.
#: NEPSE has no short selling, so a "short" family is an AVOIDANCE signal -- its
#: return column is sign-flipped so that "the signal was right" reads positive,
#: and the report says so rather than implying a tradeable short.
_BUY_SIGNALS = ("STRONG BUY ZONE", "BUY ZONE")
_SELL_SIGNALS = ("SELL / REDUCE ZONE", "STRONG EXIT / INVALIDATION", "DISTRIBUTION WATCH")


# ---------------------------------------------------------------------------
# small stdlib statistics -- nothing here is worth a dependency
# ---------------------------------------------------------------------------


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _median(xs: Sequence[float]) -> float:
    return history.percentile(sorted(xs), 50) if xs else 0.0


def _finite(x: float) -> float:
    return x if isinstance(x, (int, float)) and math.isfinite(x) else 0.0


#: Index of the primary horizon inside HORIZONS-shaped tuples.
_P = HORIZONS.index(PRIMARY)
_ZERO_H = (0.0,) * len(HORIZONS)

#: ``Demean`` maps a decision date to the mean forward return of the WHOLE
#: universe on that date, one entry per horizon. Subtracting it is the control
#: that stops "the market went up" being reported as a signal -- on a strong day
#: every stock looks accumulated, and this repo has already published a factor
#: that turned out to be nothing but a date effect.
Demean = dict


def _dm(demean: Demean, date: str, h: int = _P) -> float:
    return demean.get(date, _ZERO_H)[h]


def _rank(values: Sequence[float]) -> list[float]:
    """Average ranks, ties shared. The half of Spearman that stdlib lacks."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        r = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[order[k]] = r
        i = j + 1
    return out


def _spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Rank correlation. Robust to the fat tails this market has everywhere."""
    if len(xs) < 3:
        return 0.0
    return _finite(history.pearson(_rank(xs), _rank(ys)))


def _rolling_pct(series: Sequence[float], lookback: int = PIT_LOOKBACK,
                 min_obs: int = PIT_MIN) -> list[float]:
    """Trailing historical percentile of every point against its OWN prior history.

    Numerically identical to ``history.pit(series, i, lookback, min_obs).pct`` at
    every index -- :func:`_demo` asserts that against the real thing on real data,
    because :mod:`history` owns the leakage guard and this is only a fast path.
    The expanding form in ``pit_series`` is O(n^2) and a sixty-column panel over a
    thousand sessions per symbol makes that minutes per symbol; a ``bisect``
    window is the same arithmetic in C.

    Prior-only by construction: the value at ``i`` is inserted into the window
    AFTER it has been scored, so nothing is ever ranked against itself and
    appending future data cannot change an earlier entry. NaN where there is not
    enough prior history, never a made-up 50.
    """
    out = [math.nan] * len(series)
    win: deque[float] = deque()
    srt: list[float] = []
    for i, raw in enumerate(series):
        v = _finite(raw)
        if len(srt) >= min_obs:
            lo = bisect.bisect_left(srt, v)
            hi = bisect.bisect_right(srt, v)
            out[i] = max(0.0, min(100.0, 100.0 * (lo + 0.5 * (hi - lo)) / len(srt)))
        win.append(v)
        bisect.insort(srt, v)
        if len(win) > lookback:
            srt.pop(bisect.bisect_left(srt, win.popleft()))
    return out


# ---------------------------------------------------------------------------
# the observation record
# ---------------------------------------------------------------------------


class Zobs(NamedTuple):
    """What :func:`swing_quantam.zones.zones` said at one decision date.

    ``target_ratio`` and ``inval_ratio`` are the profit-1 and invalidation levels
    divided by the zone's own "price now". They are ratios and not prices on
    purpose: zones are built from RAW executed rates and outcomes are measured on
    ADJUSTED bars, and a ratio is the only thing that survives the crossing.
    """

    signal: str
    entry_score: float
    exit_score: float
    swing_score: float
    in_entry_zone: bool
    below_inval: bool
    target_ratio: float
    inval_ratio: float
    tt_target: int  # sessions to touching profit-1, 0 = never within the horizon
    tt_inval: int


class Obs(NamedTuple):
    """One stock-day: what was knowable at D, and what happened after D."""

    symbol: str
    date: str
    f: tuple[float, ...]    # raw section 102 vector, aligned to FEATURES
    p: tuple[float, ...]    # trailing percentile of each, 0-100
    fwd: tuple[float, ...]  # forward return at each of HORIZONS, adjusted bars
    mfe: float              # maximum favourable excursion over PRIMARY days
    mae: float              # maximum adverse excursion
    tt_target: int          # sessions to +TARGET, 0 = never
    tt_stop: int            # sessions to -STOP, 0 = never
    top_buyer: int          # largest net buyer over the trailing 15 sessions
    top_buyer_share: float  # its net as a share of window volume bought
    top_seller: int
    pair: tuple[int, int]   # busiest (buyer, seller) pair over the same window
    pair_share: float
    zone: Zobs | None

    @property
    def year(self) -> str:
        return self.date[:4]

    @property
    def ret(self) -> float:
        return self.fwd[HORIZONS.index(PRIMARY)]

    @property
    def managed(self) -> float:
        """Return with the target/stop convention applied, ties resolved to the stop.

        A day on which both levels were touched is scored as a loss. Intraday
        order is unknowable from a daily bar, so the pessimistic reading is the
        only defensible one -- the optimistic one is how a backtest invents money.
        """
        if self.tt_stop and (not self.tt_target or self.tt_stop <= self.tt_target):
            return -STOP
        if self.tt_target:
            return TARGET
        return self.ret

    @property
    def hold(self) -> int:
        """Sessions held under the same convention: first touch, else the horizon."""
        touches = [t for t in (self.tt_target, self.tt_stop) if t]
        return min(touches) if touches else PRIMARY


# ---------------------------------------------------------------------------
# per-symbol panel -- the expensive half, and the only half that touches disk
# ---------------------------------------------------------------------------


def _entropy(shares: Sequence[float]) -> float:
    vals = [s for s in shares if s > 0]
    if len(vals) < 2:
        return 0.0
    return -sum(s * math.log(s) for s in vals) / math.log(len(vals))


def _day_row(session, prior_amounts: Sequence[float]) -> tuple[dict, dict, dict]:
    """Per-day features, the per-broker book, and the pair counter for one session.

    ``prior_amounts`` is the pooled rupee value of every trade over the PREVIOUS
    sessions only -- the large-trade threshold has to come from history the
    decision date could see, not from the day being measured, or "large" is
    definitionally the top decile of every single day and the feature is inert.
    """
    trades = session.trades
    agg = brokers.day(session)
    sf = brokers.stock_flow(agg)
    prof = history.price_profile(session)

    vol = sf.volume or 1
    bought = float(sum(b.buy_qty for b in agg.values())) or 1.0
    buy_shares = sorted((b.buy_qty / bought for b in agg.values()), reverse=True)
    sell_shares = sorted((b.sell_qty / bought for b in agg.values()), reverse=True)

    qtys = sorted(t.quantity for t in trades)
    amounts = [t.amount for t in trades]
    if prior_amounts:
        cut = history.percentile(sorted(prior_amounts), 90)
        large_vol = sum(t.quantity for t in trades if t.amount >= cut)
    else:
        large_vol = 0
    pairs = Counter((t.buyer, t.seller) for t in trades)

    row = {
        "trades": float(sf.trades),
        "volume": float(sf.volume),
        "turnover": sum(amounts),
        "vwap": session.vwap,
        "price_range": prof.range_pct,
        "avg_trade": sf.volume / sf.trades if sf.trades else 0.0,
        "median_trade": history.percentile(qtys, 50),
        "large_ratio": large_vol / vol,
        "buyers": float(len({t.buyer for t in trades})),
        "sellers": float(len({t.seller for t in trades})),
        "brokers": float(sf.brokers),
        "top1_conc": sf.top_buyer_share,
        "top5_conc": sum(buy_shares[:5]),
        "buy_hhi": sum(s * s for s in buy_shares),
        "sell_hhi": sum(s * s for s in sell_shares),
        "net_flow": sf.top_buyer_share - sf.top_seller_share,
        "flow_quality": sf.flow_quality,
        "flow_imbalance": (sf.net_buyers - sf.net_sellers) / sf.brokers if sf.brokers else 0.0,
        "fragmentation": sf.trades / sf.brokers if sf.brokers else 0.0,
        "broker_entropy": _entropy(buy_shares),
        "broker_diversity": sf.brokers / sf.trades if sf.trades else 0.0,
        "vwap_position": prof.vwap_position,
        "level_hhi": prof.hhi,
    }
    book = {b.broker: (b.buy_qty, b.sell_qty) for b in agg.values()}
    return row, book, pairs


def _window_flow(books: Sequence[dict], w: int) -> dict[str, float]:
    """Pool ``w`` days of per-broker books into the section 11-23 window measures.

    ``net_churn`` here is exactly ``volume_spike.py``'s: the sum of the positive
    broker nets divided by everything bought. That is the one broker metric on
    this archive with out-of-sample support, so it is the positive control every
    other family in this file is measured beside.
    """
    if len(books) < w:
        return {}
    pooled: dict[int, list[int]] = {}
    for b in books[-w:]:
        for k, (bq, sq) in b.items():
            e = pooled.setdefault(k, [0, 0])
            e[0] += bq
            e[1] += sq
    bought = float(sum(v[0] for v in pooled.values())) or 1.0
    nets = [v[0] - v[1] for v in pooled.values()]
    pos = sum(n for n in nets if n > 0)
    up = sum(1 for n in nets if n > 0)
    dn = sum(1 for n in nets if n < 0)
    tot = len(nets) or 1
    return {
        "netchurn": pos / bought,
        "tilt": (max(nets) + min(nets)) / bought if nets else 0.0,
        "breadth": up / tot,
        "consensus": (up - dn) / tot,
        "conc": (max(nets) - min(nets)) / bought if nets else 0.0,
    }


def _panel(symbol: str, start: str = START, end: str = END,
           grid: int = GRID_STRIDE, zone_stride: int = ZONE_STRIDE,
           sens: bool = False) -> tuple[list[Obs], list[tuple]]:
    """Build every observation for one symbol. The whole per-symbol pipeline.

    Walks the archive oldest-first keeping a bounded rolling state, so memory is
    a hundred-odd sessions rather than the symbol's whole history -- NIFRA alone
    is 44 MB of floorsheet. Features are computed for every session because the
    rolling windows and the trailing percentiles need continuity; observations
    are emitted only on the grid.

    Everything in this function reads backwards. If a line in here ever reads
    forwards, :func:`leakage_check` fails, which is the entire point of it.
    """
    dates = [d for d in loader.sessions(symbol) if d <= end]
    if len(dates) < 200:
        return [], []

    abars = loader.adjusted_bars(symbol)
    bdates = [b.date for b in abars]
    if len(abars) < 60:
        return [], []

    win: deque = deque(maxlen=zones_mod.LOOKBACK)   # Session objects, for zones
    books: deque[dict] = deque(maxlen=60)
    pair_hist: deque[Counter] = deque(maxlen=15)
    vol_hist: deque[int] = deque(maxlen=15)
    amt_hist: deque[list[float]] = deque(maxlen=30)
    rows: list[dict] = []
    kept: list[str] = []
    #: index in ``kept`` -> everything the emission needs that is NOT a percentile.
    #: Built here, in the single forward pass, because the zone map and the broker
    #: marks both need the rolling window that only exists inside this loop --
    #: rebuilding either one later means re-reading 120 files per observation.
    marks: dict[int, tuple] = {}
    tilts: list[float] = []
    vwaps: list[float] = []
    rets: list[float] = []
    sens_rows: list[tuple] = []
    sens_every = max(1, len(dates) // SENS_DAYS)
    rng = random.Random(hash(symbol) & 0xFFFF)

    for n, d in enumerate(dates):
        s = loader.load(symbol, d)
        if s is None or not s.trades:
            continue
        prior_amounts = [a for day in amt_hist for a in day]
        row, book, pairs = _day_row(s, prior_amounts)

        win.append(s)
        books.append(book)
        pair_hist.append(pairs)
        vol_hist.append(s.volume)
        amt_hist.append([t.amount for t in s.trades])

        # --- windowed flow, sections 11-23 over 3/7/15/30D -------------------
        wf = {w: _window_flow(list(books), w) for w in _WINDOWS}
        for w in _WINDOWS:
            b = wf[w]
            row[f"netchurn_{w}"] = b.get("netchurn", 0.0)
            row[f"tilt_{w}"] = b.get("tilt", 0.0)
            row[f"breadth_{w}"] = b.get("breadth", 0.0)
            row[f"consensus_{w}"] = b.get("consensus", 0.0)
        row["conc_7"] = wf[7].get("conc", 0.0)
        row["conc_30"] = wf[30].get("conc", 0.0)

        tilts.append(row["tilt_3"])
        vwaps.append(row["vwap"])
        # Circuit guard, same rule as history.returns: NEPSE's band is +/-15%, so
        # a larger session-over-session move in an unadjusted executed rate is a
        # restatement, not a trade. Zero it rather than feed a fake -40% day in.
        if len(vwaps) > 1 and vwaps[-2] > 0 and vwaps[-1] > 0:
            lr = math.log(vwaps[-1] / vwaps[-2])
            rets.append(0.0 if abs(lr) > history.CIRCUIT_LOG else lr)
        else:
            rets.append(0.0)

        lr_ = rows  # local alias, keeps the derived block below one screen
        prev = lambda key, k: [r.get(key, 0.0) for r in lr_[-k:]]  # noqa: E731
        row["large_7"] = _mean(prev("large_ratio", 7) + [row["large_ratio"]])
        row["large_30"] = _mean(prev("large_ratio", 30) + [row["large_ratio"]])
        for w in (7, 15, 30):
            hist_t = tilts[-w:]
            row[f"persistence_{w}"] = sum(1 for t in hist_t if t > 0.001) / len(hist_t)
        row["momentum"] = row["tilt_3"] - row["tilt_15"]
        row["acceleration"] = (row["tilt_3"] - row["tilt_7"]) - (row["tilt_7"] - row["tilt_15"])
        row["reversal"] = 1.0 if row["tilt_3"] * row["tilt_15"] < 0 else 0.0
        b3 = _mean(prev("brokers", 3) + [row["brokers"]])
        b30 = _mean(prev("brokers", 30) + [row["brokers"]])
        row["participation_change"] = (b3 / b30 - 1.0) if b30 else 0.0
        v30 = _mean(vwaps[-30:])
        v15 = _mean(vwaps[-15:])
        v3 = _mean(vwaps[-3:])
        row["vwap_distance"] = (row["vwap"] / v30 - 1.0) if v30 else 0.0
        row["vwap_slope"] = (v3 / v15 - 1.0) if v15 else 0.0
        row["price_flow_corr"] = _finite(history.pearson(rets[-30:], tilts[-30:]))
        # FlowPriceLag: how many sessions flow leads price by, over the trailing 60.
        # rr[k:] against tt[:m-k] compares "flow on day j" with "return on day j+k",
        # so a positive k means the flow moved first. Both slices are the same
        # length on purpose -- history.pearson silently truncates to the shorter one
        # and a mismatched pair would be a quietly misaligned correlation.
        m = min(60, len(rets))
        best = 0
        if m >= 20:
            rr, tt = rets[-m:], tilts[-m:]
            score = -1.0
            for k in range(6):
                if m - k < 15:
                    break
                c = abs(_finite(history.pearson(rr[k:], tt[:m - k])))
                if c > score:
                    score, best = c, k
        row["flow_price_lag"] = float(best)
        gross = sum(abs(t) for t in tilts[-15:]) or 1.0
        row["flow_efficiency"] = sum(rets[-15:]) / gross
        age = 0
        for t in reversed(tilts[:-1]):
            if (t > 0) != (row["tilt_3"] > 0):
                break
            age += 1
        row["signal_age"] = float(age)
        row["signal_decay"] = math.exp(-age / 7.0)
        signs = [1 if row[f"tilt_{w}"] > 0 else (-1 if row[f"tilt_{w}"] < 0 else 0) for w in _WINDOWS]
        row["alignment"] = sum(signs) / 4.0
        row["conflict"] = 1.0 - abs(sum(signs)) / 4.0
        for w in (5, 15, 30):
            row[f"ret_{w}"] = sum(rets[-w:])
        # AnomalyScore and HistoricalZScore need a prior baseline, so they are the
        # one block that uses history.baseline directly -- over the PRIOR rows only.
        pv = [r["volume"] for r in lr_[-PIT_LOOKBACK:]]
        pt = [r["turnover"] for r in lr_[-PIT_LOOKBACK:]]
        bv = history.baseline(pv) if len(pv) >= PIT_MIN else None
        bt = history.baseline(pt) if len(pt) >= PIT_MIN else None
        row["anomaly"] = abs((row["volume"] - bv.mean) / bv.sd) if bv and bv.sd > 0 else 0.0
        row["turnover_z"] = ((row["turnover"] - bt.mean) / bt.sd) if bt and bt.sd > 0 else 0.0

        rows.append(row)
        kept.append(d)
        i = len(rows) - 1

        if sens and n % sens_every == 0 and len(s.trades) >= 20:
            sens_rows.append(_sensitivity_row(s, prior_amounts, rng))

        # --- everything that needs the rolling window, done while we have it --
        if i % grid or d < start:
            continue
        fwd = _forward(abars, bdates, d)
        if fwd is None:
            continue
        entry, path = fwd
        zone = None
        if i % zone_stride == 0 and len(win) >= 30:
            zone = _zone_obs(symbol, list(win), entry, path)
        marks[i] = (entry, path,
                    _broker_marks(list(books), list(pair_hist), list(vol_hist)), zone)

    if len(rows) < PIT_MIN + 20 or not marks:
        return [], sens_rows

    # --- trailing percentiles, prior-only, one column at a time -------------
    # Done after the walk because a percentile column is a whole-column operation,
    # but every entry in it still only sees its own prior history -- that is what
    # _rolling_pct guarantees and what leakage_check re-proves on the real panel.
    cols = {name: _rolling_pct([_finite(r.get(name, 0.0)) for r in rows]) for name in FEATURES}

    out: list[Obs] = []
    for i in sorted(marks):
        pcts = tuple(cols[name][i] for name in FEATURES)
        if any(math.isnan(x) for x in pcts):
            continue
        entry, path, bk, zone = marks[i]
        out.append(
            Obs(
                symbol=symbol,
                date=kept[i],
                f=tuple(_finite(rows[i].get(name, 0.0)) for name in FEATURES),
                p=pcts,
                fwd=tuple(path[h - 1].close / entry - 1.0 for h in HORIZONS),
                mfe=max(b.high for b in path) / entry - 1.0,
                mae=min(b.low for b in path) / entry - 1.0,
                tt_target=_first_touch(path, entry * (1 + TARGET), True),
                tt_stop=_first_touch(path, entry * (1 - STOP), False),
                top_buyer=bk[0],
                top_buyer_share=bk[1],
                top_seller=bk[2],
                pair=bk[3],
                pair_share=bk[4],
                zone=zone,
            )
        )
    return out, sens_rows


def _forward(abars, bdates, d: str):
    """(entry price, the PRIMARY-day forward path) from ADJUSTED bars, or None.

    Entry is the OPEN of the first bar strictly after ``d``. None when the
    archive does not yet hold a full horizon past ``d`` -- a truncated forward
    window is a survivorship-flavoured half-trade and is dropped, not padded.
    """
    i = bisect.bisect_right(bdates, d)
    if i + PRIMARY > len(abars):
        return None
    entry = abars[i].open
    if entry <= 0:
        return None
    return entry, abars[i:i + PRIMARY]


def _first_touch(path, level: float, upward: bool) -> int:
    for j, b in enumerate(path, start=1):
        if (b.high >= level) if upward else (b.low <= level):
            return j
    return 0


def _zone_obs(symbol: str, hist: list, entry: float, path) -> Zobs | None:
    """Run the section 82-92 zone map and convert its levels into ratios.

    The ratio conversion is the whole reason this helper exists -- see rule 1 in
    the module docstring. ``z.price`` is the last executed VWAP in RAW money and
    ``entry`` is an ADJUSTED open; only the quotient of two raw prices can cross
    between them.
    """
    try:
        z = zones_mod.zones(symbol, history=hist)
    except ValueError:
        return None
    if z.price <= 0:
        return None
    tr = z.profit1.low / z.price
    ir = z.invalidation.high / z.price
    return Zobs(
        signal=z.signal,
        entry_score=z.entry_score.score,
        exit_score=z.exit_score.score,
        swing_score=z.swing_score.score,
        in_entry_zone=z.entry.holds(z.price),
        below_inval=z.price < z.invalidation.high,
        target_ratio=tr,
        inval_ratio=ir,
        tt_target=_first_touch(path, entry * tr, True) if tr > 1.0 else 0,
        tt_inval=_first_touch(path, entry * ir, False) if ir < 1.0 else 0,
    )


def _broker_marks(books: Sequence[dict], pair_hist: Sequence[Counter],
                  vol_hist: Sequence[int]) -> tuple[int, float, int, tuple[int, int], float]:
    """Largest net buyer, largest net seller and busiest pair over 15 sessions.

    Sections 94-96 need broker IDENTITY, which the aggregate features throw away.
    It is read off the rolling state rather than by re-reading the archive: the
    first draft of this file re-parsed fifteen files per observation and spent
    more time on that than on everything else combined.
    """
    vol = sum(vol_hist[-15:])
    if not vol or not books:
        return (0, 0.0, 0, (0, 0), 0.0)
    pooled: dict[int, list[int]] = {}
    for b in books[-15:]:
        for k, (bq, sq) in b.items():
            e = pooled.setdefault(k, [0, 0])
            e[0] += bq
            e[1] += sq
    pairs: Counter = Counter()
    for c in pair_hist[-15:]:
        pairs.update(c)
    if not pooled or not pairs:
        return (0, 0.0, 0, (0, 0), 0.0)
    nets = {k: v[0] - v[1] for k, v in pooled.items()}
    tb = max(nets.items(), key=lambda kv: kv[1])
    ts = min(nets.items(), key=lambda kv: kv[1])
    pr, pq = pairs.most_common(1)[0]
    return (tb[0], tb[1] / vol, ts[0], pr, pq / vol)


def _sensitivity_row(session, prior_amounts, rng) -> tuple:
    """Section 103 -- the same day measured four ways.

    Returns (base, no_largest_trade, 10%_rows_dropped) triples of the day-level
    feature block. The parent correlates these across every symbol, which is what
    "sensitivity to extreme trades" and "sensitivity to missing rows" actually
    mean once you have to put a number on them.
    """
    from .loader import Session

    base, _, _ = _day_row(session, prior_amounts)
    biggest = max(session.trades, key=lambda t: t.amount)
    trimmed = tuple(t for t in session.trades if t is not biggest)
    dropped = tuple(t for t in session.trades if rng.random() > 0.10)
    outs = [base]
    for tr in (trimmed, dropped):
        if len(tr) < 5:
            outs.append(base)
            continue
        outs.append(_day_row(Session(session.symbol, session.date, tr, session.quality),
                             prior_amounts)[0])
    return tuple(outs)


def _worker(args) -> tuple:
    """Process-pool entry point. Must stay module-level and picklable."""
    symbol, start, end, grid, zone_stride, sens = args
    try:
        obs, sr = _panel(symbol, start, end, grid, zone_stride, sens)
    except Exception as exc:  # one bad symbol must not lose the whole study
        return symbol, [], [], f"{type(exc).__name__}: {exc}"
    return symbol, obs, sr, ""


# ---------------------------------------------------------------------------
# section 97 -- outcome labels
# ---------------------------------------------------------------------------

LABELS = (
    "successful accumulation",
    "failed accumulation",
    "successful distribution",
    "failed distribution",
    "breakout after accumulation",
    "breakdown after distribution",
    "false signal",
    "delayed signal",
    "signal reversal",
    "no resolution",
)


def label(o: Obs, side: str = "long") -> str:
    """Section 97's label for one observation. Uses ONLY data after the decision.

    Every branch is a statement about the forward path and nothing else, so a
    label can never leak into a feature: features are built in :func:`_panel`
    from the trailing window and this function is not called there.

    The operationalisation, stated once so it can be argued with:

    * target first  -> successful; stop first -> failed
    * stop inside 3 sessions -> the signal was wrong immediately: false signal
    * target only after half the horizon -> delayed signal
    * resolved one way but finishing the other -> signal reversal
    * MFE past 1.5x the target with the target hit -> breakout / breakdown
    * neither level touched -> no resolution, which on this market is common
    """
    tgt, stp = (o.tt_target, o.tt_stop) if side == "long" else (o.tt_stop, o.tt_target)
    excursion = o.mfe if side == "long" else -o.mae
    final = o.ret if side == "long" else -o.ret
    ok, bad = ("successful accumulation", "failed accumulation") if side == "long" \
        else ("successful distribution", "failed distribution")
    big = "breakout after accumulation" if side == "long" else "breakdown after distribution"

    if stp and (not tgt or stp <= tgt):
        if stp <= 3:
            return "false signal"
        return "signal reversal" if final > 0 else bad
    if tgt:
        if final < 0:
            return "signal reversal"
        if excursion >= TARGET * 1.5:
            return big
        return "delayed signal" if tgt > PRIMARY // 2 else ok
    return "no resolution"


# ---------------------------------------------------------------------------
# sections 93 + 98 -- the metric block
# ---------------------------------------------------------------------------


class Perf(NamedTuple):
    """Sections 93 and 98 in one record. Every rate is a fraction, not a percent."""

    name: str
    n: int
    win_rate: float
    loss_rate: float
    mean: float
    median: float
    avg_win: float
    avg_loss: float
    median_win: float
    median_loss: float
    max_win: float
    max_loss: float
    mfe_mean: float
    mfe_median: float
    mae_mean: float
    mae_median: float
    max_mfe: float
    max_mae: float
    profit_factor: float
    expectancy: float
    expected_value: float
    payoff: float
    max_drawdown: float
    avg_hold: float
    time_to_target: float
    time_to_invalidation: float
    target_rate: float
    invalidation_rate: float
    managed_mean: float
    excess_mean: float      # against the same-day universe -- the date-demeaned edge
    excess_median: float
    excess_win_rate: float
    coverage: float         # share of the universe this family holds on its firing dates
    labels: tuple[tuple[str, int], ...]

    @property
    def usable(self) -> bool:
        return self.n >= MIN_OBS


def _drawdown(dated: Sequence[tuple[str, float]]) -> float:
    """Peak-to-trough of an equal-weight book held in NON-OVERLAPPING blocks.

    Trades overlap -- the grid fires every 5 sessions and the horizon is 20 --
    so there is no single unambiguous equity curve. Compounding every signal date
    in order, which is what the first draft of this function did, silently levers
    the book four times over and reported a -91% drawdown on plain buy-and-hold.
    The convention here instead: average everything that fired on a date, then
    skip forward past the horizon before compounding again, so each link in the
    chain is a real 20-session holding period that does not overlap the last.
    """
    by_date: dict[str, list[float]] = {}
    for d, r in dated:
        by_date.setdefault(d, []).append(r)
    gap = PRIMARY * 7 // 5  # trading sessions -> calendar days
    eq, peak, dd = 1.0, 1.0, 0.0
    last: _date | None = None
    for d in sorted(by_date):
        cur = _date.fromisoformat(d)
        if last is not None and (cur - last).days < gap:
            continue
        last = cur
        eq *= 1.0 + _mean(by_date[d])
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1.0)
    return dd


def perf(name: str, sel: Sequence[Obs], demean: Demean, side: str = "long",
         pool_by_date: dict[str, list[Obs]] | None = None) -> Perf:
    """Every section 93/98 metric for one selection of observations.

    ``demean`` maps date -> mean forward return of the WHOLE universe on that
    date. Subtracting it is what stops "the market went up" from being reported
    as an edge; on a strong day every stock looks accumulated, and the excess
    columns are the only ones in this record that are immune to that.

    ``coverage`` is the caveat that goes with it, and it needs ``pool_by_date``:
    a family that fires on 80% of the universe on its firing dates IS most of the
    universe mean, so its date-demeaned excess is mechanically pushed toward
    zero. A near-zero excess on a high-coverage family is weak evidence of no
    edge; on a low-coverage one it is strong evidence.

    ``side="short"`` flips the return sign so that a correct bearish call reads
    positive. NEPSE has no short selling, so those rows are avoidance signals and
    the report says so.
    """
    if not sel:
        return Perf(name, 0, *([0.0] * 31), ())
    sgn = 1.0 if side == "long" else -1.0
    rs = [sgn * o.ret for o in sel]
    ex = [sgn * (o.ret - _dm(demean, o.date)) for o in sel]
    mfes = [o.mfe if side == "long" else -o.mae for o in sel]
    maes = [o.mae if side == "long" else -o.mfe for o in sel]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    aw, al = _mean(wins), abs(_mean(losses))
    pw = len(wins) / len(rs)
    tgt = [o.tt_target if side == "long" else o.tt_stop for o in sel]
    stp = [o.tt_stop if side == "long" else o.tt_target for o in sel]
    hit_t = [t for t in tgt if t]
    hit_s = [t for t in stp if t]
    lab = Counter(label(o, side) for o in sel)
    per_date = Counter(o.date for o in sel)
    cov = _mean([per_date[o.date] / len(pool_by_date.get(o.date, (o,))) for o in sel]) \
        if pool_by_date else 0.0
    return Perf(
        name=name,
        n=len(sel),
        win_rate=pw,
        loss_rate=1.0 - pw,
        mean=_mean(rs),
        median=_median(rs),
        avg_win=aw,
        avg_loss=al,
        median_win=_median(wins),
        median_loss=abs(_median(losses)),
        max_win=max(rs),
        max_loss=min(rs),
        mfe_mean=_mean(mfes),
        mfe_median=_median(mfes),
        mae_mean=_mean(maes),
        mae_median=_median(maes),
        max_mfe=max(mfes),
        max_mae=min(maes),
        profit_factor=(sum(wins) / abs(sum(losses))) if losses and sum(losses) else float("inf"),
        # Section 93 asks for expected value AND expectancy. They are the same
        # arithmetic unless expectancy is quoted per unit of risk, so it is: the
        # R-multiple, i.e. how much is expected back for every 1.0 of average
        # loss risked. Printing one number under two headings would be padding.
        expectancy=((pw * aw - (1 - pw) * al) / al) if al else float("inf"),
        expected_value=pw * aw - (1 - pw) * al,
        payoff=(aw / al) if al else float("inf"),
        max_drawdown=_drawdown([(o.date, sgn * o.managed) for o in sel]),
        avg_hold=_mean([float(o.hold) for o in sel]),
        time_to_target=_mean([float(t) for t in hit_t]),
        time_to_invalidation=_mean([float(t) for t in hit_s]),
        target_rate=len(hit_t) / len(sel),
        invalidation_rate=len(hit_s) / len(sel),
        managed_mean=_mean([sgn * o.managed for o in sel]),
        excess_mean=_mean(ex),
        excess_median=_median(ex),
        excess_win_rate=sum(1 for e in ex if e > 0) / len(ex),
        coverage=cov,
        labels=tuple(sorted(lab.items(), key=lambda kv: -kv[1])),
    )


# ---------------------------------------------------------------------------
# section 98 -- the rule families
# ---------------------------------------------------------------------------


def _pc(o: Obs, name: str) -> float:
    return o.p[_FI[name]]


Family = tuple[str, str, Callable[[Obs], bool]]


def families() -> tuple[Family, ...]:
    """Section 98's list, one predicate each, one shared threshold.

    Every percentile family fires in the top (or bottom) quintile of its own
    trailing history -- :data:`HOT` / :data:`COLD`, the same cut for all of them,
    so no family gets a threshold picked to make it look good. The zone families
    use the section 110 labels exactly as :mod:`zones` emits them.

    ``net_churn`` is in the list as a POSITIVE CONTROL, not as a candidate: it is
    the only broker metric on this archive with prior out-of-sample support, so if
    it also comes out flat here then the harness -- not the market -- is the thing
    to doubt.
    """
    def zsig(o: Obs, want: tuple[str, ...]) -> bool:
        return o.zone is not None and o.zone.signal in want

    return (
        ("accumulation", "long", lambda o: _pc(o, "tilt_15") >= HOT),
        ("distribution", "short", lambda o: _pc(o, "tilt_15") <= COLD),
        ("net_churn (control)", "long", lambda o: _pc(o, "netchurn_30") >= HOT),
        ("concentration", "long", lambda o: _pc(o, "conc_7") >= HOT),
        ("breadth", "long", lambda o: _pc(o, "breadth_7") >= HOT),
        ("large_trade", "long", lambda o: _pc(o, "large_7") >= HOT),
        ("consensus", "long", lambda o: _pc(o, "consensus_7") >= HOT),
        ("vwap_slope", "long", lambda o: _pc(o, "vwap_slope") >= HOT),
        ("flow_price_divergence", "long",
         lambda o: _pc(o, "tilt_15") >= 70.0 and _pc(o, "ret_15") <= 30.0),
        ("mtf_alignment", "long", lambda o: o.f[_FI["alignment"]] >= 1.0),
        ("zone_buy", "long", lambda o: zsig(o, _BUY_SIGNALS)),
        ("zone_strong_buy", "long", lambda o: zsig(o, ("STRONG BUY ZONE",))),
        ("zone_in_entry_band", "long",
         lambda o: o.zone is not None and o.zone.in_entry_zone and o.zone.signal in _BUY_SIGNALS),
        ("zone_sell", "short", lambda o: zsig(o, _SELL_SIGNALS)),
        ("zone_invalidation", "short", lambda o: o.zone is not None and o.zone.below_inval),
    )


# ---------------------------------------------------------------------------
# baselines -- compulsory, per rule 6
# ---------------------------------------------------------------------------


class Control(NamedTuple):
    """A date-matched random-entry control and the empirical p-value it implies."""

    draws: int
    mean: float
    median: float
    win_rate: float
    p_value: float  # share of random draws whose mean matched or beat the family


def control(sel: Sequence[Obs], pool_by_date: dict[str, list[Obs]],
            side: str = "long", draws: int = CONTROL_DRAWS, seed: int = 7) -> Control:
    """Random entries on EXACTLY the family's dates, same count per date.

    Date-matching is the point. An unmatched random sample would be spread over
    the whole history and would mostly be measuring which months the family
    happened to trade in -- this repo has already been fooled once by a "signal"
    that turned out to be a date effect.
    """
    if not sel:
        return Control(0, 0.0, 0.0, 0.0, 1.0)
    sgn = 1.0 if side == "long" else -1.0
    want = Counter(o.date for o in sel)
    actual = _mean([sgn * o.ret for o in sel])
    rng = random.Random(seed)
    means, meds, wins, beat = [], [], [], 0
    for _ in range(draws):
        picks: list[float] = []
        for d, k in want.items():
            pool = pool_by_date.get(d, ())
            if not pool:
                continue
            picks.extend(sgn * rng.choice(pool).ret for _ in range(k))
        if not picks:
            continue
        m = _mean(picks)
        means.append(m)
        meds.append(_median(picks))
        wins.append(sum(1 for x in picks if x > 0) / len(picks))
        if m >= actual:
            beat += 1
    if not means:
        return Control(0, 0.0, 0.0, 0.0, 1.0)
    return Control(len(means), _mean(means), _mean(meds), _mean(wins), beat / len(means))


def per_year(sel: Sequence[Obs], demean: Demean, side: str = "long") -> dict[str, Perf]:
    """The mandatory per-year table. One aggregate hides a regime failure."""
    buckets: dict[str, list[Obs]] = {}
    for o in sel:
        buckets.setdefault(o.year, []).append(o)
    return {y: perf(y, v, demean, side) for y, v in sorted(buckets.items())}


# ---------------------------------------------------------------------------
# sections 99 + 104 -- walk-forward and importance
# ---------------------------------------------------------------------------


class Fold(NamedTuple):
    train: tuple[str, str]
    validate: tuple[str, str]
    test: tuple[str, str]
    n_train: int
    n_test: int
    weights: dict[str, float]
    ic: float            # OOS rank IC on the test block
    top_decile: float    # OOS date-demeaned mean return of the top decile
    bottom_decile: float


def _demeaned(obs: Sequence[Obs], demean: Demean) -> list[float]:
    return [o.ret - _dm(demean, o.date) for o in obs]


def _fit(train: Sequence[Obs], demean: Demean, keep: Sequence[str]) -> dict[str, float]:
    """Weights = in-train rank IC of each feature against the date-demeaned return.

    Deliberately the simplest fit that can be called a fit: no regularisation to
    pick, no hyper-parameter to quietly tune, and the weights are interpretable
    as "how well did this feature rank next month's excess return LAST year".
    A heavier model would fit this noise better and generalise worse.
    """
    y = _demeaned(train, demean)
    return {f: _spearman([o.p[_FI[f]] for o in train], y) for f in keep}


def _score(o: Obs, w: dict[str, float]) -> float:
    return sum(wt * (o.p[_FI[f]] / 100.0 - 0.5) for f, wt in w.items())


def _decile_split(obs: Sequence[Obs], w: dict[str, float],
                  demean: Demean) -> tuple[float, float, float]:
    if len(obs) < 20:
        return 0.0, 0.0, 0.0
    sc = [_score(o, w) for o in obs]
    y = _demeaned(obs, demean)
    ic = _spearman(sc, y)
    order = sorted(range(len(obs)), key=lambda i: sc[i])
    k = max(1, len(obs) // 10)
    return ic, _mean([y[i] for i in order[-k:]]), _mean([y[i] for i in order[:k]])


def walk_forward(obs: Sequence[Obs], demean: Demean,
                 keep_n: int = 12) -> list[Fold]:
    """Train -> validate -> test -> roll, in calendar order (section 99).

    Folds are calendar years: everything up to year Y-2 trains, year Y-1 selects
    the ``keep_n`` features worth keeping, year Y is tested and never touched
    before it is tested. The train block expands rather than slides, which is the
    honest shape for a four-year archive -- a sliding block of one year would fit
    on 1,500 rows and it would show.
    """
    years = sorted({o.year for o in obs})
    folds: list[Fold] = []
    for k in range(2, len(years)):
        ty, vy, testy = years[:k - 1], years[k - 1], years[k]
        tr = [o for o in obs if o.year in ty]
        va = [o for o in obs if o.year == vy]
        te = [o for o in obs if o.year == testy]
        if len(tr) < 200 or len(va) < 100 or len(te) < 100:
            continue
        w_all = _fit(tr, demean, FEATURES)
        # validate: keep the features whose train weight still ranks on unseen data
        v_ic = {f: _spearman([o.p[_FI[f]] for o in va], _demeaned(va, demean)) for f in FEATURES}
        agree = [f for f in FEATURES if w_all[f] * v_ic[f] > 0]
        keep = sorted(agree, key=lambda f: -abs(w_all[f] * v_ic[f]))[:keep_n] or \
            sorted(FEATURES, key=lambda f: -abs(w_all[f]))[:keep_n]
        w = _fit(tr + va, demean, keep)
        ic, top, bot = _decile_split(te, w, demean)
        folds.append(Fold((ty[0], ty[-1]), (vy, vy), (testy, testy),
                          len(tr) + len(va), len(te), w, ic, top, bot))
    return folds


def importance(obs: Sequence[Obs], demean: Demean,
               folds: Sequence[Fold], draws: int = 12, seed: int = 11) -> dict[str, tuple[float, float]]:
    """Permutation importance and ablation, on the walk-forward TEST blocks only.

    Section 104: "do not use importance from a leaked or non-walk-forward model",
    so this function takes folds rather than fitting anything, and it raises if
    handed none. Permutation shuffles one feature's column inside the test block
    and measures the fall in test rank IC; ablation drops the feature from the
    model and re-scores. Both are averaged over folds.

    SHAP IS NOT IMPLEMENTED. It needs a differentiable or tree model and a
    background distribution; approximating it with something else and calling it
    SHAP would be a lie, so this returns permutation and ablation only.
    """
    if not folds:
        raise ValueError("importance() refuses to run without walk-forward folds (section 104)")
    rng = random.Random(seed)
    acc: dict[str, list[tuple[float, float]]] = {}
    for fd in folds:
        te = [o for o in obs if o.year == fd.test[0]]
        if len(te) < 50:
            continue
        base = _decile_split(te, fd.weights, demean)[0]
        y = _demeaned(te, demean)
        for f in fd.weights:
            col = [o.p[_FI[f]] for o in te]
            drops = []
            for _ in range(draws):
                sh = col[:]
                rng.shuffle(sh)
                sc = [
                    _score(o, {k: v for k, v in fd.weights.items() if k != f})
                    + fd.weights[f] * (sh[i] / 100.0 - 0.5)
                    for i, o in enumerate(te)
                ]
                drops.append(base - _spearman(sc, y))
            abl = base - _decile_split(te, {k: v for k, v in fd.weights.items() if k != f}, demean)[0]
            acc.setdefault(f, []).append((_mean(drops), abl))
    return {f: (_mean([a for a, _ in v]), _mean([b for _, b in v])) for f, v in acc.items()}


def group_importance(obs: Sequence[Obs], demean: Demean,
                     folds: Sequence[Fold]) -> dict[str, float]:
    """Section 104's feature-group ablation: drop a whole concept, re-score OOS."""
    out: dict[str, float] = {}
    for gname, members in GROUPS.items():
        deltas = []
        for fd in folds:
            te = [o for o in obs if o.year == fd.test[0]]
            if len(te) < 50 or not any(m in fd.weights for m in members):
                continue
            base = _decile_split(te, fd.weights, demean)[0]
            w = {k: v for k, v in fd.weights.items() if k not in members}
            if not w:
                continue
            deltas.append(base - _decile_split(te, w, demean)[0])
        if deltas:
            out[gname] = _mean(deltas)
    return out


# ---------------------------------------------------------------------------
# sections 94-96 -- broker, broker-stock and broker-pair edge
# ---------------------------------------------------------------------------


class Edge(NamedTuple):
    """One historical edge claim, or a refusal to make one.

    ``suppressed`` is not an error state. Sections 94-96 all ask for a hit rate,
    and a hit rate off nine events is a number-shaped opinion; printing the count
    and the word SUPPRESSED is the correct output, not a percentage.
    """

    key: str
    n: int
    positive_rate: float
    negative_rate: float
    mean: float
    median: float
    best: float
    worst: float
    consistency: float   # share of YEARS in which the mean excess was positive
    lead_time: float     # mean sessions to +TARGET among the occurrences that got there
    decay: tuple[float, ...]  # mean excess at each of HORIZONS -- the signal-decay curve
    suppressed: bool


def _edge(key: str, sel: Sequence[Obs], demean: Demean) -> Edge:
    if len(sel) < MIN_OBS:
        return Edge(key, len(sel), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, (), True)
    ex = [o.ret - _dm(demean, o.date) for o in sel]
    years: dict[str, list[float]] = {}
    for o, e in zip(sel, ex):
        years.setdefault(o.year, []).append(e)
    lead = [float(o.tt_target) for o in sel if o.tt_target]
    # Signal decay is the excess at each horizon, each demeaned against the
    # universe's OWN mean at that horizon. Scaling one horizon's demean to fit
    # another would import the market's shape into the decay curve.
    decay = tuple(_mean([o.fwd[i] - _dm(demean, o.date, i) for o in sel])
                  for i in range(len(HORIZONS)))
    return Edge(
        key=key,
        n=len(sel),
        positive_rate=sum(1 for e in ex if e > 0) / len(ex),
        negative_rate=sum(1 for e in ex if e <= 0) / len(ex),
        mean=_mean(ex),
        median=_median(ex),
        best=max(ex),
        worst=min(ex),
        consistency=sum(1 for v in years.values() if _mean(v) > 0) / len(years),
        lead_time=_mean(lead),
        decay=decay,
        suppressed=False,
    )


def broker_edge(obs: Sequence[Obs], demean: Demean, min_share: float = 0.05) -> list[Edge]:
    """Section 94 -- when broker X becomes a major net buyer, what happens after?

    "Major" is a measured, stated cut: net buying of at least ``min_share`` of the
    trailing 15-session volume AND being the single largest net buyer. Nothing
    about it is fitted.
    """
    by: dict[int, list[Obs]] = {}
    for o in obs:
        if o.top_buyer and o.top_buyer_share >= min_share:
            by.setdefault(o.top_buyer, []).append(o)
    return sorted((_edge(f"broker {k}", v, demean) for k, v in by.items()),
                  key=lambda e: (e.suppressed, -e.mean))


def broker_stock_edge(obs: Sequence[Obs], demean: Demean,
                      min_share: float = 0.05) -> list[Edge]:
    """Section 95 -- broker X on stock Y specifically. Almost all get suppressed."""
    by: dict[tuple[int, str], list[Obs]] = {}
    for o in obs:
        if o.top_buyer and o.top_buyer_share >= min_share:
            by.setdefault((o.top_buyer, o.symbol), []).append(o)
    return sorted((_edge(f"broker {k[0]} on {k[1]}", v, demean) for k, v in by.items()),
                  key=lambda e: (e.suppressed, -e.mean))


def pair_edge(obs: Sequence[Obs], demean: Demean, min_share: float = 0.05) -> list[Edge]:
    """Section 96 -- broker X buying while broker Y sells, as the dominant pair."""
    by: dict[tuple[int, int], list[Obs]] = {}
    for o in obs:
        if o.pair != (0, 0) and o.pair_share >= min_share:
            by.setdefault(o.pair, []).append(o)
    return sorted((_edge(f"pair {k[0]}<-{k[1]}", v, demean) for k, v in by.items()),
                  key=lambda e: (e.suppressed, -e.mean))


# ---------------------------------------------------------------------------
# section 101 -- feature interactions
# ---------------------------------------------------------------------------


class Interaction(NamedTuple):
    name: str
    n: int
    excess: float
    additive: float   # sum of the individual marginal excesses
    lift: float       # excess - additive; >0 means the conjunction adds something
    suppressed: bool


def interactions(obs: Sequence[Obs], demean: Demean) -> list[Interaction]:
    """The spec's two named conjunctions, plus the marginals they are built from.

    Section 101 says to treat these as patterns to backtest, not facts about
    intent, so the useful column is ``lift``: the conjunction's date-demeaned
    excess minus the sum of its parts. A lift near zero means the interaction is
    just its ingredients arriving together.

    TERCILES here, not the quintiles the rule families use, and the reason is
    measured rather than chosen: the spec's five-way conjunction at the quintile
    cut fired on ZERO of 1,539 stock-days in the pilot run. An untestable pattern
    is worse than a weak one, and the spec's arrows say "up", not "top 20%", so
    the conjunctions are read at 1/3 - 2/3. The marginals below use the same cut
    so that ``additive`` is comparable with ``excess``.
    """
    hi, lo = 100.0 * 2 / 3, 100.0 / 3
    marg = {
        "flow up": ("tilt_15", hi, True),
        "breadth up": ("breadth_7", hi, True),
        "breadth down": ("breadth_7", lo, False),
        "large up": ("large_7", hi, True),
        "vwap up": ("vwap_slope", hi, True),
        "conc down": ("conc_7", lo, False),
        "conc up": ("conc_7", hi, True),
    }

    def sel(names: Sequence[str]) -> list[Obs]:
        out = []
        for o in obs:
            ok = True
            for nm in names:
                f, cut, up = marg[nm]
                v = _pc(o, f)
                if (v < cut) if up else (v > cut):
                    ok = False
                    break
            if ok:
                out.append(o)
        return out

    def exc(names: Sequence[str]) -> tuple[int, float]:
        s = sel(names)
        if not s:
            return 0, 0.0
        return len(s), _mean([o.ret - _dm(demean, o.date) for o in s])

    combos = [
        ("broad accumulation: flow+ breadth+ large+ vwap+ conc-",
         ["flow up", "breadth up", "large up", "vwap up", "conc down"]),
        ("concentrated accumulation: flow+ breadth- conc+ large+",
         ["flow up", "breadth down", "conc up", "large up"]),
    ]
    out: list[Interaction] = []
    for nm in marg:
        n, e = exc([nm])
        out.append(Interaction(f"[marginal] {nm}", n, e, e, 0.0, n < MIN_OBS))
    for title, names in combos:
        n, e = exc(names)
        add = sum(exc([nm])[1] for nm in names)
        out.append(Interaction(title, n, e, add, e - add, n < MIN_OBS))
    return out


# ---------------------------------------------------------------------------
# section 103 -- feature stability
# ---------------------------------------------------------------------------


class Stability(NamedTuple):
    feature: str
    ic_overall: float
    ic_by_year: tuple[tuple[str, float], ...]  # (year, rank IC in that year), ...
    year_sign_agreement: float   # share of years whose IC has the overall sign
    ic_up_regime: float
    ic_down_regime: float
    dist_drift: float            # max |year mean - overall mean| / overall sd
    extreme_sensitivity: float   # mean |shift| after removing the day's largest trade, in sd
    missing_sensitivity: float   # mean |shift| after dropping 10% of rows, in sd
    lookback_sensitivity: float  # 1 - corr(7D version, 15D version), where both exist


def stability(obs: Sequence[Obs], demean: Demean,
              sens: Sequence[tuple], regime: dict[str, int]) -> list[Stability]:
    """Section 103, every clause of it, one row per feature.

    ``regime`` maps date -> +1/-1 from the universe's TRAILING 30-session mean
    return. Trailing on purpose: splitting the sample by what the market did
    afterwards would be exactly the leak this file exists to prevent.
    """
    y_all = _demeaned(obs, demean)
    years = sorted({o.year for o in obs})
    idx_by_year = {yy: [i for i, o in enumerate(obs) if o.year == yy] for yy in years}
    up = [i for i, o in enumerate(obs) if regime.get(o.date, 0) > 0]
    dn = [i for i, o in enumerate(obs) if regime.get(o.date, 0) <= 0]

    sens_cols = list(zip(*sens)) if sens else []
    out: list[Stability] = []
    for f in FEATURES:
        col = [o.p[_FI[f]] for o in obs]
        raw = [o.f[_FI[f]] for o in obs]
        ic = _spearman(col, y_all)
        per = [(yy, _spearman([col[i] for i in ix], [y_all[i] for i in ix]))
               for yy, ix in idx_by_year.items() if len(ix) >= 50]
        agree = (sum(1 for _, v in per if v * ic > 0) / len(per)) if per else 0.0
        sd = statistics.pstdev(raw) if len(raw) > 1 else 0.0
        m = _mean(raw)
        drift = max((abs(_mean([raw[i] for i in ix]) - m) / sd for ix in idx_by_year.values()
                     if ix and sd > 0), default=0.0)
        # Sensitivity is the mean PER-DAY shift expressed in the feature's own
        # cross-day standard deviations, not one minus a correlation: removing a
        # single trade out of a thousand leaves the cross-day correlation at
        # 1.000 for every feature, which reads as "nothing is sensitive to
        # anything" and is an artefact of the measure, not a finding.
        ex_s = ms_s = 0.0
        if sens_cols:
            base = [r.get(f, 0.0) for r in sens_cols[0]]
            s_sd = statistics.pstdev(base) if len(base) > 1 else 0.0
            if s_sd > 0:
                ex_s = _mean([abs(a - b.get(f, 0.0)) for a, b in zip(base, sens_cols[1])]) / s_sd
                ms_s = _mean([abs(a - b.get(f, 0.0)) for a, b in zip(base, sens_cols[2])]) / s_sd
        lb = 0.0
        if f.endswith("_7") and f[:-2] + "_15" in _FI:
            lb = 1.0 - _finite(history.pearson(raw, [o.f[_FI[f[:-2] + "_15"]] for o in obs]))
        out.append(Stability(
            feature=f,
            ic_overall=ic,
            ic_by_year=tuple(per),  # type: ignore[arg-type]
            year_sign_agreement=agree,
            ic_up_regime=_spearman([col[i] for i in up], [y_all[i] for i in up]) if len(up) > 50 else 0.0,
            ic_down_regime=_spearman([col[i] for i in dn], [y_all[i] for i in dn]) if len(dn) > 50 else 0.0,
            dist_drift=drift,
            extreme_sensitivity=ex_s,
            missing_sensitivity=ms_s,
            lookback_sensitivity=lb,
        ))
    return sorted(out, key=lambda s: -abs(s.ic_overall))


# ---------------------------------------------------------------------------
# section 114 -- probabilistic output, and whether it may be called one
# ---------------------------------------------------------------------------


class Calibration(NamedTuple):
    buckets: tuple[tuple[float, float, int], ...]  # (mean score 0-1, realised win rate, n)
    slope: float          # realised win rate regressed on predicted; 1.0 = calibrated
    mean_abs_error: float
    calibrated: bool
    note: str


def calibration(obs: Sequence[Obs], folds: Sequence[Fold], bins: int = 10) -> Calibration:
    """Reliability of the walk-forward score, read as a win probability.

    A score is not a probability until this says it is (section 114, last line).
    The test is the standard one: bucket the OOS scores, min-max them into [0, 1]
    within each test block, and compare the implied rate with the realised
    positive-return rate. A slope far from 1 or a mean absolute error past 10
    points means the number ranks but does not predict, and the report must then
    print NOT CALIBRATED rather than a percentage that reads like a probability.
    """
    pts: list[tuple[float, float]] = []
    for fd in folds:
        te = [o for o in obs if o.year == fd.test[0]]
        if len(te) < 50:
            continue
        sc = [_score(o, fd.weights) for o in te]
        lo, hi = min(sc), max(sc)
        if hi <= lo:
            continue
        pts.extend(((s - lo) / (hi - lo), 1.0 if o.ret > 0 else 0.0) for s, o in zip(sc, te))
    if len(pts) < bins * 10:
        return Calibration((), 0.0, 1.0, False, "not enough walk-forward observations to test")
    pts.sort()
    k = len(pts) // bins
    buckets = []
    for i in range(bins):
        chunk = pts[i * k:(i + 1) * k] if i < bins - 1 else pts[i * k:]
        if not chunk:
            continue
        buckets.append((_mean([a for a, _ in chunk]), _mean([b for _, b in chunk]), len(chunk)))
    xs = [b[0] for b in buckets]
    ys = [b[1] for b in buckets]
    slope = history.slope(xs, ys)
    mae = _mean([abs(y - x) for x, y in zip(xs, ys)])
    ok = 0.7 <= slope <= 1.3 and mae <= 0.10
    note = ("reliability slope {:.2f} and mean absolute error {:.1%} -- the score may be read "
            "as a probability".format(slope, mae) if ok else
            "reliability slope {:.2f}, mean absolute error {:.1%}: NOT CALIBRATED. This is a "
            "RANKING measure. Section 114 forbids calling it a probability.".format(slope, mae))
    return Calibration(tuple(buckets), slope, mae, ok, note)


def probabilistic(name: str, p: Perf, cal: Calibration) -> str:
    """Section 114's output block, with the calibration verdict attached."""
    if not p.usable:
        return f"{name}: {p.n} observations -- below the {MIN_OBS}-observation floor, no rate reported."
    return "\n".join((
        f"{name}",
        f"  observations                 {p.n}",
        f"  historical favourable rate   {p.win_rate:.1%}   (raw)  "
        f"{p.excess_win_rate:.1%}  (vs the same-day universe)",
        f"  average favourable movement  {p.mfe_mean:+.2%}   median {p.mfe_median:+.2%}",
        f"  average adverse movement     {p.mae_mean:+.2%}   median {p.mae_median:+.2%}",
        f"  expected value               {p.expected_value:+.2%}  "
        f"({'positive' if p.expected_value > 0 else 'negative'})",
        f"  date-demeaned excess         {p.excess_mean:+.2%} mean, {p.excess_median:+.2%} median",
        f"  signal confidence            {cal.note}",
    ))


# ---------------------------------------------------------------------------
# section 100 -- the leakage guard, with proof
# ---------------------------------------------------------------------------


def leakage_check(symbol: str, cut: str, end: str = END) -> tuple[int, str]:
    """Rebuild one symbol's panel with the archive truncated at ``cut``.

    Every observation on or before ``cut`` must be byte-identical in its features
    AND in its percentiles -- this is the assertion :mod:`history` makes for
    :func:`history.pit` extended to the whole sixty-column panel, the zone call
    included. A single forward-looking line anywhere in :func:`_panel` fails it.

    Returns (rows compared, "" | the first mismatch). Comparison is on ``repr``,
    so a difference in the last bit of a float is a failure, not a rounding note.
    """
    full, _ = _panel(symbol, START, end, sens=False)
    early, _ = _panel(symbol, START, cut, sens=False)
    fmap = {o.date: o for o in full}
    n = 0
    for o in early:
        ref = fmap.get(o.date)
        if ref is None:
            continue
        n += 1
        if repr(o.f) != repr(ref.f):
            return n, f"{symbol} {o.date}: feature vector changed when later data was appended"
        if repr(o.p) != repr(ref.p):
            return n, f"{symbol} {o.date}: percentiles changed when later data was appended"
        if repr(o.zone) != repr(ref.zone):
            return n, f"{symbol} {o.date}: zone output changed when later data was appended"
    return n, ""


# ---------------------------------------------------------------------------
# the study
# ---------------------------------------------------------------------------


def universe(n: int = 36, min_sessions: int = 700) -> list[str]:
    """A liquidity-stratified sample of symbols with continuous coverage.

    Sorted by total floorsheet bytes (a size proxy that costs a stat call rather
    than a parse) and sampled at even ranks, so the set spans NIFRA at 44 MB down
    to symbols with a few thousand prints a year.

    SURVIVORSHIP, stated because it does not go away by being ignored: every
    symbol here traded from 2022 to 2026. That biases the LEVEL of every return
    in this file upwards. It does not bias the comparisons, which is exactly why
    the buy-and-hold baseline on the same universe and dates is compulsory.
    """
    rows = []
    for s in loader.symbols():
        d = loader.sessions(s)
        if len(d) < min_sessions or d[0] > "2022-02-15" or d[-1] < "2026-08-01":
            continue
        p = os.path.join(loader.FLOORSHEET, s)
        try:
            rows.append((s, sum(os.path.getsize(os.path.join(p, f)) for f in os.listdir(p))))
        except OSError:
            continue
    rows.sort(key=lambda r: -r[1])
    if len(rows) <= n:
        return [r[0] for r in rows]
    return [rows[(i * len(rows)) // n][0] for i in range(n)]


class Result(NamedTuple):
    symbols: tuple[str, ...]
    obs: tuple[Obs, ...]
    demean: Demean
    regime: dict[str, int]
    baseline: Perf
    baseline_years: dict[str, Perf]
    fams: tuple[tuple[str, str, Perf, Control, dict[str, Perf]], ...]
    folds: tuple[Fold, ...]
    imp: dict[str, tuple[float, float]]
    groups: dict[str, float]
    stab: tuple[Stability, ...]
    inter: tuple[Interaction, ...]
    edges94: tuple[Edge, ...]
    edges95: tuple[Edge, ...]
    edges96: tuple[Edge, ...]
    cal: Calibration
    seconds: float
    errors: tuple[str, ...]


def run(symbols: Sequence[str] | None = None, start: str = START, end: str = END,
        grid: int = GRID_STRIDE, zone_stride: int = ZONE_STRIDE,
        workers: int | None = None, sens: bool = True) -> Result:
    """The whole study. Computes and RETURNS; writes nothing.

    Per-symbol work is embarrassingly parallel and the zone call is about a
    second on a liquid name, so it goes through a process pool when one is
    available and falls back to a plain loop when it is not (a sandbox, an
    already-loaded box). The fallback is not a degraded result, only a slower one.
    """
    t0 = time.time()
    syms = list(symbols) if symbols else universe()
    args = [(s, start, end, grid, zone_stride, sens) for s in syms]
    results: list[tuple] = []
    errors: list[str] = []

    n_work = workers if workers is not None else min(6, max(1, (os.cpu_count() or 2) - 1))
    if n_work > 1 and len(args) > 1:
        try:
            from concurrent.futures import ProcessPoolExecutor
            with ProcessPoolExecutor(max_workers=n_work) as ex:
                results = list(ex.map(_worker, args))
        except Exception as exc:  # noqa: BLE001 - any pool failure means: do it here
            errors.append(f"process pool unavailable ({type(exc).__name__}), ran sequentially")
            results = []
    if not results:
        results = [_worker(a) for a in args]

    obs: list[Obs] = []
    sens_rows: list[tuple] = []
    for sym, o, sr, err in results:
        if err:
            errors.append(f"{sym}: {err}")
        obs.extend(o)
        sens_rows.extend(sr)
    obs.sort(key=lambda o: (o.date, o.symbol))

    # --- the cross-sectional controls --------------------------------------
    by_date: dict[str, list[Obs]] = {}
    for o in obs:
        by_date.setdefault(o.date, []).append(o)
    demean: Demean = {
        d: tuple(_mean([x.fwd[i] for x in v]) for i in range(len(HORIZONS)))
        for d, v in by_date.items()
    }
    # Regime is the universe's TRAILING 30-session mean return, never the forward one.
    dates = sorted(by_date)
    daily_mkt = []
    for d in dates:
        daily_mkt.append(_mean([x.f[_FI["ret_5"]] for x in by_date[d]]))
    regime = {}
    for i, d in enumerate(dates):
        prior = daily_mkt[max(0, i - 6):i + 1]
        regime[d] = 1 if _mean(prior) > 0 else -1

    base = perf("buy-and-hold (every stock-day on the grid)", obs, demean)
    base_years = per_year(obs, demean)

    fams = []
    for name, side, fn in families():
        sel = [o for o in obs if fn(o)]
        fams.append((name, side, perf(name, sel, demean, side),
                     control(sel, by_date, side), per_year(sel, demean, side)))

    folds = walk_forward(obs, demean)
    imp = importance(obs, demean, folds) if folds else {}
    grp = group_importance(obs, demean, folds) if folds else {}
    cal = calibration(obs, folds)

    return Result(
        symbols=tuple(syms),
        obs=tuple(obs),
        demean=demean,
        regime=regime,
        baseline=base,
        baseline_years=base_years,
        fams=tuple(fams),
        folds=tuple(folds),
        imp=imp,
        groups=grp,
        stab=tuple(stability(obs, demean, sens_rows, regime)),
        inter=tuple(interactions(obs, demean)),
        edges94=tuple(broker_edge(obs, demean)),
        edges95=tuple(broker_stock_edge(obs, demean)),
        edges96=tuple(pair_edge(obs, demean)),
        cal=cal,
        seconds=time.time() - t0,
        errors=tuple(errors),
    )


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


def _yeartable(title: str, years: dict[str, Perf]) -> list[str]:
    out = [f"  {title}",
           f"    {'year':<6}{'n':>7}{'win':>9}{'mean':>10}{'median':>10}{'excess':>10}{'exc.win':>9}"]
    for y, p in sorted(years.items()):
        out.append(f"    {y:<6}{p.n:>7}{p.win_rate:>8.1%}{p.mean:>10.2%}{p.median:>10.2%}"
                   f"{p.excess_mean:>10.2%}{p.excess_win_rate:>9.1%}")
    return out


def report(r: Result) -> str:
    """The whole study as text. Every claim carries its sample size beside it."""
    L: list[str] = []
    add = L.append
    add("=" * 100)
    add("SWING QUANTUM BACKTEST -- spec sections 93-104 + 114")
    add("=" * 100)
    add(f"universe        {len(r.symbols)} symbols, liquidity-stratified, survivorship-biased by construction")
    add(f"                {', '.join(r.symbols)}")
    add(f"window          {START} -> {END}   grid every {GRID_STRIDE} sessions, zones every {ZONE_STRIDE}")
    add(f"observations    {len(r.obs):,} stock-days over {len({o.date for o in r.obs})} decision dates")
    add(f"outcome         entry at the NEXT session's OPEN on ADJUSTED bars; horizon {PRIMARY} sessions")
    add(f"                secondary horizons {HORIZONS}; target/stop {TARGET:+.0%}/{-STOP:.0%}, fixed, never tuned")
    add(f"runtime         {r.seconds:.0f}s")
    for e in r.errors:
        add(f"  ! {e}")

    add("")
    add("-" * 100)
    add("BASELINE (compulsory) -- buy-and-hold every stock-day on the same dates")
    add("-" * 100)
    b = r.baseline
    add(f"  n {b.n:,}   win {b.win_rate:.1%}   mean {b.mean:+.2%}   MEDIAN {b.median:+.2%}   "
        f"payoff {b.payoff:.2f}   profit factor {b.profit_factor:.2f}")
    add(f"  MFE {b.mfe_mean:+.2%} / MAE {b.mae_mean:+.2%}   max drawdown {b.max_drawdown:.1%}   "
        f"reached {TARGET:+.0%} {b.target_rate:.1%} of the time, {-STOP:.0%} {b.invalidation_rate:.1%}")
    add(f"  the median is {'NEGATIVE' if b.median < 0 else 'positive'} while the mean is "
        f"{'positive' if b.mean > 0 else 'negative'} -- a thin tail carries this market, "
        f"which is why every line below prints both")
    L += _yeartable("per-year (buy-and-hold):", r.baseline_years)

    add("")
    add("-" * 100)
    add("SECTION 98 -- RULE FAMILIES, each against the baseline and a date-matched random control")
    add("-" * 100)
    add(f"  {'family':<26}{'side':>6}{'n':>7}{'win':>8}{'mean':>9}{'median':>9}"
        f"{'EXCESS':>9}{'rand':>9}{'p':>7}{'PF':>7}{'MFE':>8}{'MAE':>8}{'hold':>6}")
    for name, side, p, c, _ in r.fams:
        if p.n == 0:
            add(f"  {name:<26}{side:>6}{0:>7}   (never fired)")
            continue
        flag = "" if p.usable else "  <SUPPRESSED n<%d>" % MIN_OBS
        add(f"  {name:<26}{side:>6}{p.n:>7}{p.win_rate:>8.1%}{p.mean:>9.2%}{p.median:>9.2%}"
            f"{p.excess_mean:>9.2%}{c.mean:>9.2%}{c.p_value:>7.2f}{p.profit_factor:>7.2f}"
            f"{p.mfe_mean:>8.2%}{p.mae_mean:>8.2%}{p.avg_hold:>6.1f}{flag}")
    add("")
    add("  EXCESS is the date-demeaned edge: the family's return minus the mean of EVERY stock in")
    add("  the universe on the same day. On a strong market day every stock looks accumulated, so")
    add("  this column -- not `mean` -- is the one that says whether the rule knew anything.")
    add("  p is the share of 200 date-matched random-entry draws that matched or beat the family.")
    add("  A `short` side is an AVOIDANCE signal, sign-flipped so 'right' reads positive: NEPSE")
    add("  has no short selling, so a profitable short row is not a tradeable strategy.")

    add("")
    add("  section 93 EXPECTED VALUE and section 97 LABELS, per family:")
    for name, side, p, _, _ in r.fams:
        if not p.usable:
            continue
        add(f"    {name} ({side}, n={p.n})")
        add(f"      win {p.win_rate:.1%} / loss {p.loss_rate:.1%}   avg win {p.avg_win:+.2%} "
            f"(median {p.median_win:+.2%}) / avg loss {p.avg_loss:.2%} (median {p.median_loss:.2%})")
        add(f"      EV {p.expected_value:+.2%}   expectancy {p.expectancy:+.2%}   payoff {p.payoff:.2f}   "
            f"max fav {p.max_mfe:+.1%} / max adv {p.max_mae:+.1%}   max DD {p.max_drawdown:.1%}")
        add(f"      time to target {p.time_to_target:.1f} sessions ({p.target_rate:.0%} got there), "
            f"time to invalidation {p.time_to_invalidation:.1f} ({p.invalidation_rate:.0%})")
        add("      labels: " + ", ".join(f"{k} {v}" for k, v in p.labels[:6]))

    add("")
    add("-" * 100)
    add("PER-YEAR TABLES (mandatory) -- a single aggregate hides a regime failure")
    add("-" * 100)
    for name, side, p, _, ys in r.fams:
        if not p.usable:
            continue
        L += _yeartable(f"{name} ({side}):", ys)
        neg = [y for y, q in ys.items() if q.excess_mean <= 0 and q.n >= 10]
        add(f"    -> negative-excess years: {len(neg)} of {len([1 for q in ys.values() if q.n >= 10])} "
            f"({', '.join(neg) if neg else 'none'})")

    add("")
    add("-" * 100)
    add("SECTION 99 -- WALK-FORWARD VALIDATION (train -> validate -> test -> roll)")
    add("-" * 100)
    if not r.folds:
        add("  not enough calendar years in the sample to form a fold")
    else:
        add(f"  {'train':<14}{'validate':>10}{'test':>8}{'n train':>10}{'n test':>9}"
            f"{'OOS IC':>9}{'top dec':>10}{'bot dec':>10}{'spread':>10}")
        for f in r.folds:
            add(f"  {f.train[0]}-{f.train[1]:<9}{f.validate[0]:>10}{f.test[0]:>8}{f.n_train:>10,}"
                f"{f.n_test:>9,}{f.ic:>9.3f}{f.top_decile:>10.2%}{f.bottom_decile:>10.2%}"
                f"{f.top_decile - f.bottom_decile:>10.2%}")
        ics = [f.ic for f in r.folds]
        add(f"  mean OOS rank IC {_mean(ics):+.3f} over {len(ics)} folds "
            f"({sum(1 for i in ics if i > 0)} positive)")
        add("  IC is against the DATE-DEMEANED forward return, so it is a cross-sectional")
        add("  statement and cannot be inflated by a rising market.")

    add("")
    add("-" * 100)
    add("SECTION 104 -- FEATURE IMPORTANCE (walk-forward test blocks only; SHAP not implemented)")
    add("-" * 100)
    if not r.imp:
        add("  no walk-forward model, so no importance is reported -- section 104 forbids the alternative")
    else:
        add(f"  {'feature':<24}{'permutation':>13}{'ablation':>11}")
        for f, (pm, ab) in sorted(r.imp.items(), key=lambda kv: -kv[1][0])[:15]:
            add(f"  {f:<24}{pm:>13.4f}{ab:>11.4f}")
        add("")
        add(f"  {'feature group':<24}{'ablation d.IC':>14}")
        for g, d in sorted(r.groups.items(), key=lambda kv: -kv[1]):
            add(f"  {g:<24}{d:>14.4f}")
        add("  A positive number means removing it HURT the out-of-sample model.")

    add("")
    add("-" * 100)
    add("SECTION 103 -- FEATURE STABILITY (top 15 by |IC|)")
    add("-" * 100)
    add(f"  {'feature':<22}{'IC':>8}{'yr agree':>10}{'up reg':>9}{'dn reg':>9}"
        f"{'drift':>8}{'extreme':>9}{'missing':>9}{'lookback':>10}")
    for s in r.stab[:15]:
        add(f"  {s.feature:<22}{s.ic_overall:>8.3f}{s.year_sign_agreement:>10.0%}"
            f"{s.ic_up_regime:>9.3f}{s.ic_down_regime:>9.3f}{s.dist_drift:>8.2f}"
            f"{s.extreme_sensitivity:>9.3f}{s.missing_sensitivity:>9.3f}{s.lookback_sensitivity:>10.3f}")
    add("  'yr agree' is the share of years whose IC keeps the overall sign -- under ~70% the")
    add("  feature is not stable enough to trade regardless of how good the pooled IC looks.")
    add("  'extreme'/'missing' are 1 - corr after removing the day's largest trade / 10% of rows.")

    add("")
    add("-" * 100)
    add("SECTION 101 -- FEATURE INTERACTIONS")
    add("-" * 100)
    add(f"  {'pattern':<58}{'n':>7}{'excess':>10}{'additive':>10}{'lift':>9}")
    for it in r.inter:
        tag = "  SUPPRESSED" if it.suppressed else ""
        add(f"  {it.name:<58}{it.n:>7}{it.excess:>10.2%}{it.additive:>10.2%}{it.lift:>9.2%}{tag}")
    add("  'lift' is the conjunction minus the sum of its parts. Near zero = the interaction is")
    add("  nothing more than its ingredients arriving on the same day.")

    add("")
    add("-" * 100)
    add(f"SECTIONS 94-96 -- BROKER EDGE (no rate reported below {MIN_OBS} occurrences)")
    add("-" * 100)
    for title, edges in (("94 broker", r.edges94), ("95 broker-stock", r.edges95),
                         ("96 broker-pair", r.edges96)):
        live = [e for e in edges if not e.suppressed]
        add(f"  section {title}: {len(edges)} candidates, {len(live)} above the floor, "
            f"{len(edges) - len(live)} SUPPRESSED for sample size")
        if not live:
            add("    nothing reportable -- every candidate is below the floor. That is the result.")
            continue
        add(f"    {'key':<26}{'n':>6}{'pos':>7}{'mean ex':>10}{'median':>9}{'consist':>9}"
            f"{'lead':>7}{'5d':>8}{'10d':>8}{'20d':>8}")
        for e in live[:8]:
            d = e.decay + (0.0,) * 3
            add(f"    {e.key:<26}{e.n:>6}{e.positive_rate:>7.0%}{e.mean:>10.2%}{e.median:>9.2%}"
                f"{e.consistency:>9.0%}{e.lead_time:>7.1f}{d[0]:>8.2%}{d[1]:>8.2%}{d[2]:>8.2%}")
        if len(live) > 8:
            add(f"    ... and {len(live) - 8} more")
        best = live[0]
        add(f"    NOTE: with {len(live)} brokers tested, the best one is expected to look good by "
            f"chance alone. {best.key} is positive in {best.consistency:.0%} of years -- "
            f"{'that is the number that matters' if best.consistency >= 0.7 else 'well short of consistent'}.")

    add("")
    add("-" * 100)
    add("SECTION 114 -- PROBABILISTIC OUTPUT")
    add("-" * 100)
    add("  " + r.cal.note)
    if r.cal.buckets:
        add(f"  {'predicted':>11}{'realised':>11}{'n':>8}")
        for pr, re_, n in r.cal.buckets:
            add(f"  {pr:>11.2f}{re_:>11.2f}{n:>8}")
    add("")
    best = max((f for f in r.fams if f[2].usable), key=lambda f: f[2].excess_mean, default=None)
    if best:
        add(probabilistic(f"best family by date-demeaned excess: {best[0]}", best[2], r.cal))
    add("")
    add("=" * 100)
    add("VERDICT")
    add("=" * 100)
    wins = [(n, p) for n, s, p, c, _ in r.fams if p.usable and p.excess_mean > 0 and c.p_value < 0.05]
    for n, s, p, c, ys in r.fams:
        if not p.usable:
            continue
        yrs = [q for q in ys.values() if q.n >= 10]
        pos = sum(1 for q in yrs if q.excess_mean > 0)
        verdict = ("EDGE" if (p.excess_mean > 0 and c.p_value < 0.05 and yrs and pos / len(yrs) >= 0.6)
                   else "no edge")
        add(f"  {n:<26}{verdict:<9} excess {p.excess_mean:+.2%}, control p {c.p_value:.2f}, "
            f"positive in {pos}/{len(yrs)} years")
    add("")
    add(f"  {len(wins)} of {sum(1 for _, _, p, _, _ in r.fams if p.usable)} testable families beat their "
        f"random control at p<0.05 with a positive date-demeaned excess.")
    add("  Anything not marked EDGE should not be traded, and no threshold in this file was moved")
    add("  to make one appear.")
    return "\n".join(L)


# ---------------------------------------------------------------------------


def _demo() -> None:
    """A real walk-forward backtest over the real archive, with the real verdict.

    Asserts, in order: the leakage guard holds on the whole panel; the fast
    percentile column matches :func:`history.pit` exactly; adjusted bars really
    do differ from raw ones inside this universe (rule 1 has teeth); win rates
    are rates; the per-year table covers every year in the sample; the baseline
    is populated; and every edge claim under the sample floor is suppressed.
    """
    syms = universe()
    assert len(syms) >= 30, f"only {len(syms)} symbols qualify; the study needs at least 30"

    # -- section 100: the percentile column must equal history.pit exactly ----
    probe = [math.sin(i * 1.7) * 100 + i for i in range(400)]
    fast = _rolling_pct(probe)
    for i in (60, 120, 250, 399):
        ref = history.pit(probe, i, PIT_LOOKBACK, PIT_MIN)
        assert ref is not None and abs(fast[i] - ref.pct) < 1e-9, (
            f"the fast percentile column disagrees with history.pit at {i}: {fast[i]} vs {ref.pct}")
    assert all(math.isnan(x) for x in fast[:PIT_MIN]), "a percentile appeared before it could be known"

    # -- rule 1: adjusted bars are a different series, and it matters ---------
    fakes = 0
    for s in syms:
        a = {b.date: b for b in loader.adjusted_bars(s)}
        raw = {b.date: b for b in loader.bars(s)}
        common = sorted(set(a) & set(raw))
        common = [d for d in common if d >= START]
        prev = None
        for d in common:
            k = raw[d].close / a[d].close if a[d].close > 0 else 1.0
            if prev is not None and abs(k / prev - 1) > 0.02:
                fakes += 1
            prev = k
    assert fakes > 0, ("no corporate action in the universe -- the adjusted-bar rule cannot be "
                       "demonstrated, so pick a universe where it can")
    print(f"adjusted-bar guard: {fakes} ex-dates inside the study window across {len(syms)} symbols.")
    print(f"  Using loader.bars() instead would book each of them as a fake loss of 12-20%.")

    # -- section 100: the whole panel, rebuilt truncated ----------------------
    probe_sym = syms[len(syms) // 2]
    t = time.time()
    n_cmp, bad = leakage_check(probe_sym, "2025-06-30")
    assert not bad, bad
    assert n_cmp >= 20, f"leakage check only compared {n_cmp} rows on {probe_sym} -- not a real test"
    print(f"leakage guard: {n_cmp} observations of {probe_sym} are byte-identical when the archive "
          f"is truncated at 2025-06-30 and re-run ({time.time() - t:.0f}s).")

    # -- the study ------------------------------------------------------------
    print(f"running the study over {len(syms)} symbols, {START} -> {END} ...")
    r = run(syms)

    assert r.obs, "no observations at all"
    assert 0.0 <= r.baseline.win_rate <= 1.0, "a win rate outside [0, 1]"
    assert r.baseline.n >= 1000, f"baseline only has {r.baseline.n} observations"
    years = {o.year for o in r.obs}
    assert set(r.baseline_years) == years, (
        f"the per-year table misses {years - set(r.baseline_years)}")
    for name, side, p, c, ys in r.fams:
        assert 0.0 <= p.win_rate <= 1.0, f"{name}: win rate {p.win_rate}"
        if p.usable:
            assert c.draws > 0, f"{name}: the random control is empty"
            assert set(ys) <= years and ys, f"{name}: per-year table broken"
    for e in r.edges94 + r.edges95 + r.edges96:
        if e.n < MIN_OBS:
            assert e.suppressed and e.positive_rate == 0.0, (
                f"{e.key}: a rate was reported off {e.n} occurrences")
    assert r.cal.note, "section 114 must always state a calibration verdict"

    text = report(r)
    print(text)

    path = os.path.join(loader.OUT, "backtest.txt")
    os.makedirs(loader.OUT, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print(f"\nwritten: {path}")


if __name__ == "__main__":
    _demo()
