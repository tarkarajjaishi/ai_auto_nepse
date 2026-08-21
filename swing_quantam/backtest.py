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

   That guard is itself MUTATION-TESTED, because it spent a long time being weaker
   than its own docstring: of three deliberate leaks planted in ``_panel`` it caught
   one. Truncating the floorsheet date list is not truncating the archive, and
   comparing only the rows that have a forward window means comparing only rows a
   full horizon INSIDE the archive -- where "tomorrow" is present on both sides and
   a short peek cannot show up. :data:`_MUTATIONS` plants the two it missed on every
   run and :func:`_demo` fails if either survives.

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

   The control's null has to be the null of the thing being claimed, and for a
   while it was not: the claim is "pick k of today's N stocks", so the draw is a
   k-of-N SUBSET (:func:`control`, without replacement) and its variance carries
   the finite-population correction. Drawing k times with replacement made the
   null up to 30% too wide and buried a real result. The number of draws is part
   of the same rule -- an empirical p quantised to 1/200 cannot resolve a
   Bonferroni bar of 0.0017, so :data:`CONTROL_DRAWS` is sized against the bar and
   :func:`_demo` asserts that it still is. The bar is over 2N and not N because
   every family is judged in both directions (:func:`bonferroni`).

   And the benchmark must not contain the thing it benchmarks. A family that
   holds half the universe is half of its own comparison, so every excess is
   reported twice: against the whole universe, and against that day's
   NON-members (``Perf.excess_lfo``). The second is the larger number in both
   directions.

   The control answers "could a random pick THAT DAY have done this", and that is
   not the same question as "is there an effect here at all". It treats the rows as
   exchangeable within a date, which they are, and as independent ACROSS dates,
   which they are not: a handful of symbols supply most of the rows and most of the
   effect. So a second null is compulsory for any strong verdict --
   :func:`clustered` resamples SYMBOLS, and :func:`verdict` will not print EDGE or
   NEGATIVE while its interval spans zero. On this archive that gate is what decides
   the headline, and the symbol concentration behind it is printed for every family
   whether or not it changes the answer.

5. **Median as well as mean.** The median NEPSE 20-day return is negative and a
   thin tail carries the mean, so a mean-only result actively misleads.

Pure stdlib, like the rest of the package. Section 104's permutation importance
and feature ablation are hand-rolled (they are twenty lines each); **SHAP is out
of scope and is not approximated** -- a fake SHAP value would be worse than none.

Nothing here is calibrated. Section 114 is explicit that a score may not be
called a probability until it is, so :func:`probabilistic` runs a reliability
check and prints the word NOT CALIBRATED when the check fails, which on this
archive it does.

Two artefacts come out of a run, and the second one is the point. ``backtest.txt``
is this module's report, written for a human and read by nobody. ``backtest_summary.txt``
(:func:`write_summary` / :func:`read_summary`) is the same verdict in ``key<TAB>value``
form, small enough that :mod:`swing_quantam.__main__` reads it once per board build --
so the board that prints an entry zone and a 0-100 score also prints, beside them, what
each of them measured. As of the run that fixed the control, that is: one family of
fifteen clears the corrected bar and it is a price-momentum feature, not a floorsheet
one; the board's own BUY ZONE is significantly WORSE than a date-matched random pick;
and the board's own 0-100 swing score does not order the outcome at all. The study costs
~490 s and the board rebuild is 22 minutes, so re-running it per build is not an option;
carrying the answer forward in a file is.
"""

from __future__ import annotations

import bisect
import contextlib
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
#:
#: The stride is walked over a SHARED calendar built in :func:`run`, not over
#: each symbol's own session index. Striding per symbol looks equivalent and is
#: not: symbols have different session counts, so their grids fall out of phase
#: and the pilot run put 1.9 stocks on the average decision date. Date-demeaning
#: against a universe of two is not a control, it is a rounding error, and it
#: would have quietly reported every family's excess as ~0.00%.
GRID_STRIDE, ZONE_STRIDE = 5, 20

#: Below this many occurrences a rate is not reported at all. Sections 94-96 all
#: ask for hit rates; a hit rate off three events is a number-shaped opinion, and
#: the honest output is the count and the word SUPPRESSED. This floor applies to
#: the rule families and the interaction patterns too, not only to the broker
#: sections -- a suppressed row prints no percentage at all, because a number
#: printed with a caveat beside it gets quoted without the caveat.
MIN_OBS = 30

#: How much of a 15-session window's volume the dominant broker PAIR must carry
#: before section 96 will look at it. Set from the MEASURED scale of the
#: quantity, not from an outcome: across 6,964 stock-days the busiest ordered
#: pair carries a median of 0.0%, a p90 of 0.0% and a MAXIMUM of 1.2% of the
#: window's volume, so the 5% single-broker floor is unreachable by construction
#: and would make section 96 vacuous rather than negative. The report prints that
#: distribution beside the result so the floor can be argued with.
PAIR_MIN_SHARE = 0.005

#: How many date-matched random draws form the control distribution.
#:
#: This number has to be able to RESOLVE the bar it is compared against. Fifteen
#: families tested in two directions make the Bonferroni threshold 0.05/30 = 0.00167,
#: and with 200 draws an empirical p can only be 0.005 or 0.010 or ... -- so the
#: corrected bar was not reachable AT ALL, and 0/200 only bounds the true p at
#: <1.5% by the rule of three. That coarseness misclassified a real family:
#: ``large_trade``'s exact p is 0.046 and the 200-draw run reported 0.08.
#:
#: 10,000 resolves p to 1e-4, which puts ~16 attainable values below the corrected
#: bar and bounds a 0/N result at <0.03%. MEASURED cost on the 36-symbol universe:
#: the study went from 323 s to 486 s, so the fifteen controls plus the phase splits
#: are about 160 s of it. The work is O(sum of family sizes x draws), so 50,000 --
#: which is what it takes to put a second significant figure on p = 0.0001 -- would
#: add ~800 s and roughly triple the study. Not worth it to decorate a p-value that
#: has already cleared its bar; raise it by hand if a specific number needs it.
CONTROL_DRAWS = 10_000

#: Resamples in the SYMBOL-BLOCK bootstrap (:func:`clustered`), and the bar it is
#: judged against.
#:
#: The date-matched control answers "could a random pick that day have done this",
#: and it needs many draws because it is compared against a Bonferroni bar. This one
#: answers a different question -- "is the effect in the RULE or in three stocks" --
#: and is compared against a plain 0.05, so it needs far less resolution. 20,000 is
#: chosen to make the 2.5% tail of the interval stable to the printed decimal, not to
#: resolve a small p. Cost is ~36 lookups per resample, so all fifteen families
#: together are seconds, not minutes.
#:
#: CLUSTER_P is 0.05 and is the SAME bar in both directions. That is the whole point:
#: the upper and lower tails of the date-matched control were being judged against
#: 0.0033 and 0.05 respectively, which is how a NEGATIVE verdict came to cost fifteen
#: times less evidence than an EDGE one.
CLUSTER_DRAWS, CLUSTER_P = 20_000, 0.05

#: Round-trip cost of one NEPSE trade (broker commission + SEBON + DP, both legs),
#: as a fraction. A range, not a point: it is size-dependent and the small-ticket
#: end is the expensive one. Nothing in this file is net of cost -- every return
#: here is GROSS -- so this exists only to print what an excess would survive to.
ROUND_TRIP = (0.006, 0.009)

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
           grid: frozenset[str] | None = None, zones_on: frozenset[str] | None = None,
           sens: bool = False, unlabelled: bool = False) -> tuple[list[Obs], list[tuple]]:
    """Build every observation for one symbol. The whole per-symbol pipeline.

    ``grid`` and ``zones_on`` are the SHARED decision dates -- see GRID_STRIDE
    for why they are dates and not a per-symbol stride. When they are None the
    function falls back to striding this symbol's own sessions, which is what
    :func:`leakage_check` wants: a single symbol, no universe, and a grid that
    cannot move when the archive is truncated.

    Walks the archive oldest-first keeping a bounded rolling state, so memory is
    a hundred-odd sessions rather than the symbol's whole history -- NIFRA alone
    is 44 MB of floorsheet. Features are computed for every session because the
    rolling windows and the trailing percentiles need continuity; observations
    are emitted only on the grid.

    ``end`` truncates the ARCHIVE, not just the floorsheet date list. It used to
    do only the latter -- ``adjusted_bars`` was read in full -- and that hole is
    what made :func:`leakage_check` blind to a feature that read tomorrow's bar:
    the price series was identical in the truncated rebuild, so the mutated
    feature came back identical too. See that function for the mutation results.

    ``unlabelled`` is for :func:`leakage_check` alone and emits the rows in the last
    :data:`PRIMARY` sessions before ``end`` -- the ones with no forward window --
    carrying their FEATURE vector and percentiles and nothing else (``fwd`` empty,
    ``zone`` None, so ``Obs.ret`` on one of them is meaningless and raises).

    Those rows are the entire point of the guard and dropping them is what made it
    weak. Every row that HAS a forward window inside a truncated archive is at least
    PRIMARY sessions from the edge, so its "tomorrow" exists in the truncated build
    and in the full one alike; comparing only those rows can therefore never detect a
    peek shorter than PRIMARY sessions, which is every peek anyone would actually
    write. The boundary rows are the only place where "tomorrow" is missing on one
    side and present on the other.

    Everything in this function reads backwards. If a line in here ever reads
    forwards, :func:`leakage_check` fails, which is the entire point of it.
    """
    dates = [d for d in loader.sessions(symbol) if d <= end]
    if len(dates) < 200:
        return [], []

    abars = loader.adjusted_bars(symbol, end)
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
        on_grid = (d in grid) if grid is not None else (i % GRID_STRIDE == 0)
        if not on_grid or d < start:
            continue
        fwd = _forward(abars, bdates, d)
        if fwd is None:
            # No forward window. Normally that is a half-trade and is dropped; for the
            # leakage guard it is the ONLY row that can see the edge of the archive.
            if unlabelled:
                marks[i] = None
            continue
        entry, path = fwd
        zone = None
        want_zone = (d in zones_on) if zones_on is not None else (i % ZONE_STRIDE == 0)
        if want_zone and len(win) >= 30:
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
        feats = tuple(_finite(rows[i].get(name, 0.0)) for name in FEATURES)
        if marks[i] is None:
            # Feature-only boundary row. Empty fwd is the marker leakage_check reads.
            out.append(Obs(symbol, kept[i], feats, pcts, (), 0.0, 0.0, 0, 0,
                           0, 0.0, 0, (0, 0), 0.0, None))
            continue
        entry, path, bk, zone = marks[i]
        out.append(
            Obs(
                symbol=symbol,
                date=kept[i],
                f=feats,
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
    symbol, start, end, grid, zones_on, sens = args
    try:
        obs, sr = _panel(symbol, start, end, grid, zones_on, sens)
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
    excess_lfo: float       # against the same-day NON-MEMBERS -- coverage removed
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


def _nonmember_means(sel: Sequence[Obs], per_date: Counter,
                     pool_by_date: dict[str, list[Obs]], demean: Demean) -> list[float]:
    """For each observation, the same day's mean return over the family's NON-members.

    Taken by subtraction rather than by re-scanning the pool: the day's total is
    already known and the family's own total costs one pass, so this is O(n) and
    not O(n * pool). Membership is by date and count, which is exact because
    ``sel`` is by construction a subset of that date's pool.
    """
    mem_sum: dict[str, float] = {}
    for o in sel:
        mem_sum[o.date] = mem_sum.get(o.date, 0.0) + o.ret
    ref: dict[str, float] = {}
    for d, k in per_date.items():
        pool = pool_by_date.get(d, ())
        rest = len(pool) - k
        ref[d] = ((sum(x.ret for x in pool) - mem_sum[d]) / rest) if rest > 0 \
            else _dm(demean, d)
    return [ref[o.date] for o in sel]


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

    ``excess_lfo`` stops that being a caveat and makes it a number: the same
    excess taken against the same day's NON-MEMBERS, so the family is no longer
    inside its own benchmark. Printing ``coverage`` and leaving the reader to
    correct for it by eye was the old behaviour and it understated every family in
    the table -- at ~50% coverage the pull toward zero is about a factor of two
    (``vwap_slope`` +0.70% -> +1.81%, ``zone_buy`` -0.86% -> -1.45%). On a date
    where the family IS the whole universe there are no non-members and the
    universe mean is used instead, which is why the buy-and-hold baseline's
    ``excess_lfo`` is exactly 0.0 rather than undefined.

    ``side="short"`` flips the return sign so that a correct bearish call reads
    positive. NEPSE has no short selling, so those rows are avoidance signals and
    the report says so.
    """
    if not sel:
        # Sized off _fields so that adding a metric upstream cannot silently shift
        # the empty record's columns by one.
        return Perf(name, 0, *([0.0] * (len(Perf._fields) - 3)), ())
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
    lfo = _mean([sgn * (o.ret - m) for o, m in
                 zip(sel, _nonmember_means(sel, per_date, pool_by_date, demean))]) \
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
        excess_lfo=lfo,
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
    p_value: float      # share of random draws whose mean matched or BEAT the family
    p_low: float = 1.0  # share whose mean matched or fell BELOW it -- the other tail


def control(sel: Sequence[Obs], pool_by_date: dict[str, list[Obs]],
            side: str = "long", draws: int = CONTROL_DRAWS, seed: int = 7) -> Control:
    """Random entries on EXACTLY the family's dates, same count per date.

    Date-matching is the point. An unmatched random sample would be spread over
    the whole history and would mostly be measuring which months the family
    happened to trade in -- this repo has already been fooled once by a "signal"
    that turned out to be a date effect.

    WITHOUT REPLACEMENT, which is not a detail. The estimand is "pick k of the N
    stocks trading that day", so the null is a k-of-N subset and its variance
    carries the finite-population correction (N-k)/(N-1). Drawing k times WITH
    replacement -- which is what this function did until it was audited -- omits
    that factor and makes the null distribution too WIDE in proportion to the
    family's coverage: sd_correct/sd_as_coded measured 0.68 at 53% coverage, 0.72
    at 54%, 0.98 at 8%. A null that is 30% too wide swallows real effects, and it
    swallowed one (``vwap_slope``, p 0.01 -> 0.0002 -- which, as it turned out, only
   moved that family from unproven to over-stated; see the clustered null below).
   :func:`screening_test` in
    this same file always did it correctly -- shuffle the pool, then slice -- and
    this function's own docstring described that scheme while the code did the
    other one.

    The subsets are drawn by partial Fisher-Yates on a per-date copy: k swaps
    rather than a full shuffle, which is the same distribution at O(k) instead of
    O(N) and is what keeps :data:`CONTROL_DRAWS` affordable at 10,000.

    Both tails come back. ``p_value`` answers "could random entries have done this
    WELL"; ``p_low`` answers "could they have done this BADLY", which is the only
    way a board signal that is actively worse than the coin flip can be called
    what it is instead of merely unproven.

    Both are the UNBIASED empirical p, ``(beat + 1) / (draws + 1)``, not
    ``beat / draws``. The observed statistic is itself one draw from the null, so
    the naive form can return exactly 0.0000 -- a claim that the effect is
    impossible under the null, which no finite resampling can support. At
    :data:`CONTROL_DRAWS` the two forms differ by ~1e-4 and change no verdict here;
    the point is that the floor is 1/(draws+1) rather than zero, so a p printed to
    four places is always a number the sample could actually have produced.
    """
    if not sel:
        return Control(0, 0.0, 0.0, 0.0, 1.0, 1.0)
    sgn = 1.0 if side == "long" else -1.0
    want = Counter(o.date for o in sel)
    actual = _mean([sgn * o.ret for o in sel])
    # Sign applied once, per date, so the inner loop moves floats and nothing else.
    pools = {d: [sgn * o.ret for o in pool_by_date.get(d, ())] for d in want}
    rng = random.Random(seed)
    means, meds, wins, beat, below = [], [], [], 0, 0
    for _ in range(draws):
        picks: list[float] = []
        for d, k in want.items():
            pool = pools[d]
            n = len(pool)
            if not n:
                continue
            k = min(k, n)  # sel is a subset of the pool, so this only ever binds if it is not
            for i in range(k):
                j = rng.randrange(i, n)
                pool[i], pool[j] = pool[j], pool[i]
            picks.extend(pool[:k])
        if not picks:
            continue
        m = _mean(picks)
        means.append(m)
        meds.append(_median(picks))
        wins.append(sum(1 for x in picks if x > 0) / len(picks))
        if m >= actual:
            beat += 1
        if m <= actual:
            below += 1
    if not means:
        return Control(0, 0.0, 0.0, 0.0, 1.0, 1.0)
    return Control(len(means), _mean(means), _mean(meds), _mean(wins),
                   (beat + 1) / (len(means) + 1), (below + 1) / (len(means) + 1))


class Cluster(NamedTuple):
    """A SYMBOL-BLOCK bootstrap of the same excess, plus the concentration behind it."""

    symbols: int       # distinct symbols the family fired on
    agree: int         # how many of them carry the family's OWN sign in total excess
    top3_share: float  # share of the family's whole excess carried by its three biggest
    excess: float      # the date-demeaned excess being resampled, restated here
    lo: float          # 2.5th percentile of the resampled excess
    hi: float          # 97.5th
    p_value: float     # share of resamples that reached or crossed zero from the observed side

    @property
    def spans_zero(self) -> bool:
        """The 95% interval contains zero -- the effect is not separated from nothing."""
        return self.lo <= 0.0 <= self.hi


def clustered(sel: Sequence[Obs], demean: Demean, side: str = "long",
              draws: int = CLUSTER_DRAWS, seed: int = 11) -> Cluster:
    """Resample SYMBOLS, not rows. The control's independence assumption, tested.

    :func:`control` treats the family's rows as exchangeable within a date, which
    is the right null for "did this pick beat a random pick that day" and the WRONG
    one for "is there an effect here at all". The rows are not independent across
    dates: one symbol contributes dozens of them, its 20-day windows overlap, and
    its idiosyncratic run shows up in every one. A p-value computed as if 1,472
    rows were 1,472 pieces of evidence is over-confident by whatever factor the
    symbol clustering implies, and on this archive that factor is large -- three of
    thirty-six names carried most of the headline family's entire excess.

    So: draw ``len(symbols)`` symbols WITH replacement, pool all of their rows, take
    the mean. That is the standard cluster (block) bootstrap and it prices in the
    fact that the sample really contains ~36 independent things, not ~1,500.

    ``p_value`` is one-sided in the family's OWN direction -- the share of resamples
    that reached or crossed zero from the observed side. It is deliberately the SAME
    statistic for a positive family and a negative one, so the clustered bar is one
    bar and not two (see :func:`verdict`, where the upper and lower tails of the
    date-matched control used to be judged against different thresholds).

    ``top3_share`` is the disclosure that goes with it and is the cheaper of the two
    numbers to read: a family whose top three symbols carry 87% of its excess, or
    168% of it with the remaining names net negative, is three stocks' history
    wearing a rule's name. Reported for every family, not only the ones that clear.
    """
    if not sel:
        return Cluster(0, 0, 0.0, 0.0, 0.0, 0.0, 1.0)
    sgn = 1.0 if side == "long" else -1.0
    tot: dict[str, float] = {}
    cnt: dict[str, int] = {}
    for o in sel:
        tot[o.symbol] = tot.get(o.symbol, 0.0) + sgn * (o.ret - _dm(demean, o.date))
        cnt[o.symbol] = cnt.get(o.symbol, 0) + 1
    T = list(tot.values())
    C = [cnt[s] for s in tot]
    grand, n = sum(T), sum(C)
    ex = grand / n if n else 0.0
    # The three biggest contributors IN THE FAMILY'S OWN DIRECTION, so the number
    # reads the same way for a long family and an avoidance one.
    top3 = sorted(T, reverse=ex > 0)[:3]
    share = (sum(top3) / grand) if grand else 0.0
    agree = sum(1 for v in T if (v > 0) == (ex > 0))

    k = len(T)
    rng = random.Random(seed)
    means = []
    for _ in range(draws):
        s_, c_ = 0.0, 0
        for _ in range(k):
            j = rng.randrange(k)
            s_ += T[j]
            c_ += C[j]
        if c_:
            means.append(s_ / c_)
    if not means:
        return Cluster(k, agree, share, ex, 0.0, 0.0, 1.0)
    means.sort()
    lo = means[int(0.025 * len(means))]
    hi = means[min(len(means) - 1, int(0.975 * len(means)))]
    crossed = sum(1 for m in means if (m <= 0.0 if ex > 0 else m >= 0.0))
    return Cluster(k, agree, share, ex, lo, hi, (crossed + 1) / (len(means) + 1))


class Phase(NamedTuple):
    """One non-overlapping slice of a family's observations."""

    phase: int
    n: int
    excess: float
    p_value: float


def phase_split(sel: Sequence[Obs], obs: Sequence[Obs], demean: Demean,
                pool_by_date: dict[str, list[Obs]], side: str = "long") -> list[Phase]:
    """The same family re-measured on each NON-OVERLAPPING subset of its dates.

    The grid fires every :data:`GRID_STRIDE` sessions and the horizon is
    :data:`PRIMARY`, so four consecutive observations of the same symbol share
    most of their forward window. Overlap does not bias the mean but it destroys
    the independence the control's p-value assumes: 1,471 overlapping rows carry
    roughly 368 rows' worth of information, and a p-value computed as if they were
    independent is four times too confident.

    Taking every fourth decision date fixes that, at the cost of a quarter of the
    sample. Four phases exist and all four are reported, because reporting the
    best one would be the same screening error this whole file is built to avoid.
    A family whose excess survives on one phase and inverts on another has not
    been demonstrated -- it has been sliced until it agreed.

    Only run for families that reached EDGE, since it costs a full control each.
    """
    order = {d: i for i, d in enumerate(sorted({o.date for o in obs}))}
    step = max(1, PRIMARY // GRID_STRIDE)
    out = []
    for ph in range(step):
        sp = [o for o in sel if order[o.date] % step == ph]
        pool = {d: v for d, v in pool_by_date.items() if order.get(d, -1) % step == ph}
        if not sp:
            continue
        p = perf(f"phase {ph}", sp, demean, side, pool)
        out.append(Phase(ph, len(sp), p.excess_mean, control(sp, pool, side).p_value))
    return out


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
    """(pooled rank IC, top-decile excess, bottom-decile excess) on one block.

    The deciles are taken WITHIN EACH DATE and then averaged across dates, not
    pooled over the block. Pooling looks equivalent and is not: the top pooled
    decile can be a handful of dates on which every stock scored highly, which
    makes the "portfolio" a bet on those weeks rather than a cross-sectional
    selection. Per date, it is what it claims to be -- own the best few names
    today, rebalance next signal date.
    """
    if len(obs) < 20:
        return 0.0, 0.0, 0.0
    y_all = _demeaned(obs, demean)
    ic = _spearman([_score(o, w) for o in obs], y_all)
    by_date: dict[str, list[tuple[float, float]]] = {}
    for o, y in zip(obs, y_all):
        by_date.setdefault(o.date, []).append((_score(o, w), y))
    tops, bots = [], []
    for rows in by_date.values():
        if len(rows) < 5:
            continue
        rows.sort()
        k = max(1, len(rows) // 10)
        tops.append(_mean([y for _, y in rows[-k:]]))
        bots.append(_mean([y for _, y in rows[:k]]))
    return ic, _mean(tops), _mean(bots)


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
    symbols: int         # distinct stocks the occurrences are spread over
    top_symbol_share: float  # share of them coming from the single busiest stock
    suppressed: bool


def _edge(key: str, sel: Sequence[Obs], demean: Demean) -> Edge:
    if len(sel) < MIN_OBS:
        return Edge(key, len(sel), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, (), 0, 0.0, True)
    syms = Counter(o.symbol for o in sel)
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
        # The shuffle control in screening_test removes the market effect but NOT
        # symbol clustering: a broker whose occurrences are one stock's good run
        # scores exactly like a broker with a real edge. These two columns are how
        # you tell them apart, and they are the difference between a finding and
        # the sixth dead operator family.
        symbols=len(syms),
        top_symbol_share=syms.most_common(1)[0][1] / len(sel),
        suppressed=False,
    )


#: The three grouping rules of sections 94-96 and the floor each one uses.
#: "Major net buyer" is a measured, stated cut -- net buying of at least this
#: share of the trailing 15-session volume AND being the single largest net
#: buyer. Nothing about it is fitted to an outcome.
EDGE_KINDS: dict[str, tuple[str, float]] = {
    "94 broker": ("broker", 0.05),
    "95 broker-stock": ("broker_stock", 0.05),
    "96 broker-pair": ("pair", PAIR_MIN_SHARE),
}


def _edge_groups(obs: Sequence[Obs], kind: str, min_share: float) -> dict:
    """The observation groups behind sections 94-96. One place, three keys."""
    by: dict = {}
    for o in obs:
        if kind == "pair":
            if o.pair != (0, 0) and o.pair_share >= min_share:
                by.setdefault(o.pair, []).append(o)
        elif o.top_buyer and o.top_buyer_share >= min_share:
            by.setdefault(o.top_buyer if kind == "broker" else (o.top_buyer, o.symbol),
                          []).append(o)
    return by


def _edge_key(kind: str, k) -> str:
    if kind == "broker":
        return f"broker {k}"
    if kind == "broker_stock":
        return f"broker {k[0]} on {k[1]}"
    return f"pair {k[0]}<-{k[1]}"


def edges(obs: Sequence[Obs], demean: Demean, kind: str, min_share: float) -> list[Edge]:
    """Sections 94, 95 and 96 -- what happened after this broker / pair showed up."""
    by = _edge_groups(obs, kind, min_share)
    return sorted((_edge(_edge_key(kind, k), v, demean) for k, v in by.items()),
                  key=lambda e: (e.suppressed, -e.mean))


class Screen(NamedTuple):
    """Best-of-N control for a screened table. The number that kills most of them."""

    best: float      # the best group's date-demeaned mean excess
    p_value: float   # share of random regroupings whose BEST matched or beat it
    groups: int      # how many groups cleared the sample floor
    candidates: int  # how many were screened to find them
    draws: int = 0   # how finely p is resolved -- 1/draws is its quantum
    p_matched: float = 1.0  # the same test with the reshuffle done WITHIN each date


def screening_test(obs: Sequence[Obs], demean: Demean, kind: str, min_share: float,
                   draws: int = CONTROL_DRAWS, seed: int = 13) -> Screen:
    """Is the best broker better than the best of N RANDOM brokers?

    Sections 94-96 screen dozens to hundreds of candidates and then report the
    top of the list. With 80 brokers, the best of them has a large mean excess
    with probability ~1 even when broker identity carries nothing at all, which
    is precisely how this repo previously convinced itself of six operator
    families. The control: keep the group SIZES, shuffle which observation lands
    in which group, and ask how often the best of that random partition matches
    the best real one. A p-value near 1 means the table is a sorting artefact.

    TWO nulls, because the difference between them is the known limitation of this
    control and printing it is cheaper than arguing about it:

    * ``p_value`` reshuffles the whole pool. Group sizes are kept, group membership
      is not, and residual DATE clustering inside a group is not removed -- a broker
      whose prints all land in one good fortnight keeps that advantage under this
      null.
    * ``p_matched`` reshuffles WITHIN each date, so every random group gets exactly
      as many rows from each date as the real one had. That removes the date
      clustering the first null leaves in. It is the same relationship
      :func:`control` has to an unmatched random sample, applied here.

    The report prints both and says whether they differ. If they do, the pooled
    number was reading a calendar and not a broker.

    ``draws`` defaults to :data:`CONTROL_DRAWS`, not to the 200 this ran at for most
    of its life. At 200 the p is quantised to 0.005 and the section 94 table came
    back as a flat ``0.0000`` -- the same false precision that had already been fixed
    one function away in :func:`control` and left here. Both p's use the unbiased
    ``(beat + 1) / (draws + 1)`` for the same reason.

    Cost is O(draws x rows actually dealt), not O(draws x pool): only the rows the
    live groups consume are shuffled, by partial Fisher-Yates, so a table whose
    groups cover a tenth of the pool costs a tenth as much.
    """
    grouped = [[(o.date, o.ret - _dm(demean, o.date)) for o in v]
               for v in _edge_groups(obs, kind, min_share).values()]
    live = [g for g in grouped if len(g) >= MIN_OBS]
    if not live:
        return Screen(0.0, 1.0, 0, len(grouped), draws, 1.0)
    observed = max(_mean([e for _, e in g]) for g in live)
    sizes = [len(g) for g in live]
    rng = random.Random(seed)

    # --- null 1: one pool, sizes kept ---------------------------------------
    pool = [e for g in grouped for _, e in g]
    n_pool, need = len(pool), sum(sizes)
    beat = 0
    for _ in range(draws):
        for i in range(min(need, n_pool)):
            j = rng.randrange(i, n_pool)
            pool[i], pool[j] = pool[j], pool[i]
        i, best = 0, -1e9
        for s in sizes:
            best = max(best, _mean(pool[i:i + s]))
            i += s
        if best >= observed:
            beat += 1

    # --- null 2: the same, dealt date by date --------------------------------
    dpool: dict[str, list[float]] = {}
    for g in grouped:
        for d, e in g:
            dpool.setdefault(d, []).append(e)
    counts = [Counter(d for d, _ in g) for g in live]
    demand: Counter = Counter()
    for cnt in counts:
        demand.update(cnt)
    beat_m = 0
    for _ in range(draws):
        cursor = {}
        for d, k in demand.items():
            pl = dpool[d]
            n = len(pl)
            for i in range(min(k, n)):
                j = rng.randrange(i, n)
                pl[i], pl[j] = pl[j], pl[i]
            cursor[d] = 0
        best = -1e9
        for cnt in counts:
            tot, num = 0.0, 0
            for d, k in cnt.items():
                pl, st = dpool[d], cursor[d]
                take = pl[st:st + k]
                cursor[d] = st + len(take)
                tot += sum(take)
                num += len(take)
            if num:
                best = max(best, tot / num)
        if best >= observed:
            beat_m += 1

    return Screen(observed, (beat + 1) / (draws + 1), len(live), len(grouped),
                  draws, (beat_m + 1) / (draws + 1))


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


class SwingRank(NamedTuple):
    """Whether the BOARD'S OWN 0-100 swing score orders the forward return."""

    n: int
    ic: float           # rank IC of swing_score against the date-demeaned return
    spread_win: float   # top bucket win rate minus bottom bucket win rate
    spread_excess: float  # ...and the same for date-demeaned excess
    buckets: tuple[tuple[float, float, float, int], ...]  # (score, win rate, excess, n)
    ranks: str          # "yes" / "BACKWARDS" / "no"
    note: str


def swing_rank(obs: Sequence[Obs], demean: Demean, bins: int = 10) -> SwingRank:
    """Test ``zone.swing_score`` -- the number the board actually prints.

    THIS FUNCTION EXISTS BECAUSE THE BOARD WAS PRINTING SOMEONE ELSE'S RESULT.
    Section 114 rendered "does the swing score rank? NO" out of
    :func:`calibration`, which measures :func:`_score` -- the walk-forward FITTED
    composite over feature percentiles. That is a different number with different
    inputs and a different fitting story; the board's swing score appeared in no
    family predicate, no calibration and no fold, so it was untested in either
    direction while the board claimed it had been tested and had failed.

    The measurement is the plain one and needs no fold, because there is nothing
    fitted here to leak: the score is a fixed weighted sum emitted by
    :mod:`zones`, so its whole history is out of sample by construction. Rank IC
    and equal-count buckets against the DATE-DEMEANED return, so "the market went
    up while the score was high" cannot pass for ranking.
    """
    rows = [o for o in obs if o.zone is not None]
    if len(rows) < bins * 10:
        return SwingRank(len(rows), 0.0, 0.0, 0.0, (), "untested",
                         f"only {len(rows)} scored observations -- too few to test the ranking")
    xs = [o.zone.swing_score for o in rows]
    ys = [o.ret - _dm(demean, o.date) for o in rows]
    ic = _spearman(xs, ys)
    pts = sorted(zip(xs, ys, [1.0 if o.ret > 0 else 0.0 for o in rows]))
    k = len(pts) // bins
    buckets = []
    for i in range(bins):
        chunk = pts[i * k:(i + 1) * k] if i < bins - 1 else pts[i * k:]
        if chunk:
            buckets.append((_mean([a for a, _, _ in chunk]), _mean([c for _, _, c in chunk]),
                            _mean([b for _, b, _ in chunk]), len(chunk)))
    dw = buckets[-1][1] - buckets[0][1]
    de = buckets[-1][2] - buckets[0][2]
    # Two standard errors of a Spearman correlation under the null. A spread that
    # looks like a ranking is worth nothing if the IC behind it is inside the noise,
    # and on a sample this size the noise is +/-0.05.
    se = 1.0 / math.sqrt(len(rows) - 1)
    ranks = "no" if abs(ic) <= 2 * se else ("yes" if ic > 0 else "BACKWARDS")
    tail = {
        "no": (f"|IC| is inside two standard errors of zero (+/-{2 * se:.3f}), so this score "
               f"orders NOTHING measurable -- it is not a ranking, let alone a probability"),
        "yes": "the score ORDERS the outcome; it is a ranking and still not a probability",
        "BACKWARDS": ("the score orders the outcome BACKWARDS -- a HIGHER swing score picked a "
                      "WORSE stock. The board prints it as if higher were better"),
    }[ranks]
    note = (f"the board's own swing score over {len(rows):,} scored stock-days: rank IC {ic:+.3f} "
            f"against the date-demeaned {PRIMARY}-day return, top bucket minus bottom bucket "
            f"{dw:+.1%} on win rate and {de:+.2%} on excess -- " + tail)
    return SwingRank(len(rows), ic, dw, de, tuple(buckets), ranks, note)


def probabilistic(name: str, p: Perf, cal: Calibration) -> str:
    """Section 114's output block, with the calibration verdict attached."""
    if not p.usable:
        return f"{name}: {p.n} observations -- below the {MIN_OBS}-observation floor, no rate reported."
    return "\n".join((
        f"{name}",
        f"  observations                 {p.n}",
        f"  historical favourable rate   {p.win_rate:.1%}   (raw)  "
        f"{p.excess_win_rate:.1%}  (vs the same-day universe -- compare to the baseline's, not to 50%)",
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


@contextlib.contextmanager
def _archive_capped(cut: str):
    """Every :mod:`loader` read inside this block sees an archive that ends at ``cut``.

    The guard below used to rely on :func:`_panel` passing its own ``end`` down to
    every read. That is a rule about lines of code, and it was broken by two of the
    three mutations tested against it -- both of which went round ``end`` by reading
    the archive directly. Capping the READER instead makes the truncation a property
    of the environment, so a leak does not have to be anticipated to be caught: any
    line that reaches past ``cut``, through any helper, at any depth, now sees a
    different archive in the rebuild than it did in the reference and the ``repr``
    comparison fires.

    Monkeypatching a module is a blunt instrument and it is deliberate: it is scoped
    to one function, restored in a ``finally``, and the alternative -- threading a cut
    through every call site -- is the thing that already failed.
    """
    names = ("sessions", "load", "bars", "adjusted_bars", "load_last", "flow_rows")
    o = {n: getattr(loader, n) for n in names}

    def cap(u: str | None) -> str:
        return min(u, cut) if u else cut

    patched = {
        "sessions": lambda s, _f=o["sessions"]: [d for d in _f(s) if d <= cut],
        "load": lambda s, d, _f=o["load"]: None if d > cut else _f(s, d),
        "bars": lambda s, u=None, _f=o["bars"]: _f(s, cap(u)),
        "adjusted_bars": lambda s, u=None, _f=o["adjusted_bars"]: _f(s, cap(u)),
        "load_last": lambda s, n, u=None, _f=o["load_last"]: _f(s, n, cap(u)),
        "flow_rows": lambda s, u=None, _f=o["flow_rows"]: _f(s, cap(u)),
    }
    for k, v in patched.items():
        setattr(loader, k, v)
    try:
        yield
    finally:
        for k, v in o.items():
            setattr(loader, k, v)


def leakage_check(symbol: str, cut: str, end: str = END) -> tuple[int, str]:
    """Rebuild one symbol's panel against an archive that STOPS at ``cut``.

    Every observation on or before ``cut`` must be byte-identical in its features
    AND in its percentiles -- this is the assertion :mod:`history` makes for
    :func:`history.pit` extended to the whole sixty-column panel, the zone call
    included.

    What this covers, stated as MEASURED rather than as claimed. This docstring used
    to say "a single forward-looking line anywhere in :func:`_panel` fails it", and
    three mutations of :func:`_panel` said otherwise. As it stood:

    * A -- a feature reading the FUTURE BAR (``abars`` at the index after ``d``):
      **PASSED**. ``_panel`` filtered floorsheet dates by ``end`` but read
      ``adjusted_bars`` in full, so the price series was the same in both rebuilds.
    * B -- a feature reading TOMORROW'S FLOORSHEET via a fresh ``loader.sessions()``
      / ``loader.load()``: **PASSED**, because the re-read went round ``end`` entirely.
    * C -- a percentile ranked against the whole column, future included: **CAUGHT**.

    One leak class of three. Three things had to change for A and B to fail, and the
    third is the one that is easy to miss:

    1. ``_panel`` passes ``end`` to ``adjusted_bars``, so the price series is cut too.
    2. :func:`_archive_capped` caps the READER, so a re-read at any depth is cut as
       well -- a leak no longer has to be anticipated to be caught.
    3. ``unlabelled=True``, so the rows in the final :data:`PRIMARY` sessions before
       the cut are COMPARED. Without this the first two changes buy nothing: every
       row with a forward window sits at least PRIMARY sessions inside the archive,
       its "tomorrow" is present on both sides, and a one-session peek is invisible.
       The boundary rows are where the truncation actually bites, and they were being
       dropped for lack of a label the guard never looks at.

    :func:`_demo` re-runs A and B against this function on every study run rather
    than trusting this paragraph -- see :data:`_MUTATIONS`.

    Returns (rows compared, "" | the first mismatch). Comparison is on ``repr``,
    so a difference in the last bit of a float is a failure, not a rounding note.
    """
    # EVERY session up to the cut, not the every-fifth decision grid. The stride is a
    # cost control for the study and it silently gutted this check: the last row it
    # emitted was 2025-06-29 for a cut of 2025-06-30, so the one row where "tomorrow"
    # is missing on the truncated side -- the only row that can catch a one-session
    # peek -- was never built. It is also CHEAPER, because the reference build now
    # stops at the cut instead of running to the end of the archive.
    dense = frozenset(d for d in loader.sessions(symbol) if d <= cut)
    full, _ = _panel(symbol, START, end, grid=dense, sens=False)
    with _archive_capped(cut):
        early, _ = _panel(symbol, START, cut, grid=dense, sens=False, unlabelled=True)
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
        # A boundary row has no forward window on the truncated side, so it has no zone
        # to compare -- its features and percentiles are the whole claim. The reference
        # row DOES have one, and comparing them would fail on the label, not on a leak.
        if o.fwd and repr(o.zone) != repr(ref.zone):
            return n, f"{symbol} {o.date}: zone output changed when later data was appended"
    return n, ""


#: The two leaks :func:`leakage_check` was blind to, as source mutations of THIS file.
#:
#: A guard is only worth what a deliberate break proves. Each entry is a (find,
#: replace) pair applied to this module's own source; :func:`_mutation_survives`
#: re-executes the mutated copy and asks the guard about it. They live here, beside
#: the guard, rather than in a test file, because the claim they check is made in the
#: guard's docstring and the two have already drifted apart once.
#:
#: Mutation C -- a percentile ranked against the whole column -- is not in this table.
#: It was CAUGHT before any of this work and is caught by the same assertion
#: :func:`_demo` already makes against :func:`history.pit`; re-proving it would cost
#: two more panel builds to learn nothing.
_ANCHOR = "        rows.append(row)\n        kept.append(d)"
_MUTATIONS: dict[str, tuple[str, str]] = {
    "A: a feature reads TOMORROW'S BAR": (_ANCHOR, (
        "        _j = bisect.bisect_right(bdates, d)\n"
        "        if _j < len(abars):\n"
        '            row["anomaly"] = float(abars[_j].close)\n' + _ANCHOR)),
    "B: a feature re-reads TOMORROW'S FLOORSHEET": (_ANCHOR, (
        "        _all = loader.sessions(symbol)\n"
        "        _k = bisect.bisect_right(_all, d)\n"
        "        if _k < len(_all):\n"
        "            _nx = loader.load(symbol, _all[_k])\n"
        "            if _nx is not None:\n"
        '                row["anomaly"] = float(_nx.volume)\n' + _ANCHOR)),
}


def _mutation_survives(name: str, symbol: str, cut: str) -> str:
    """Break :func:`_panel` on purpose and return "" if the guard CAUGHT it.

    The mutated source is executed into a throwaway module inside this package, so
    nothing on disk is touched and the mutation cannot escape the call.
    """
    import importlib.util

    old, new = _MUTATIONS[name]
    with open(__file__, "r", encoding="utf-8") as fh:
        src = fh.read()
    if src.count(old) != 1:
        return f"the anchor for mutation {name} is no longer unique in this file"
    spec = importlib.util.spec_from_file_location(f"{__name__}_mutant", __file__)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = __package__
    exec(compile(src.replace(old, new), __file__, "exec"), mod.__dict__)
    n, bad = mod.leakage_check(symbol, cut)
    if bad:
        return ""
    return (f"mutation {name} went UNDETECTED on {symbol} across {n} compared rows -- "
            f"the leakage guard does not cover this leak class")


# ---------------------------------------------------------------------------
# the study
# ---------------------------------------------------------------------------


#: The universe filter's two date clauses, named once because :func:`survivorship`
#: has to reproduce EXACTLY the split :func:`universe` makes. They were written out
#: twice for a while and the report's prose quoted a third, stale copy.
FIRST_BY, LAST_BY = "2022-02-15", "2026-08-01"


class Surv(NamedTuple):
    """One group of symbols, measured on the study's own grid and horizon.

    ``label`` is compulsory and carries the group SIZE, because the defect this
    record exists to fix was a comparison against an unlabelled "qualifying
    universe" that meant the 170-symbol pool in one paragraph and the 36-symbol
    study universe thirty lines later.
    """

    label: str
    symbols: int
    n: int
    mean: float
    median: float
    win_rate: float


def survivorship(grid: Sequence[str], study: Sequence[str],
                 end: str = END, min_sessions: int = 700) -> tuple[Surv, ...]:
    """What the universe filter's start-date clause excludes, MEASURED, not recalled.

    The report used to carry these figures as prose and they had drifted: it claimed
    the 69 late-listed exclusions returned a median of -0.03% and won 49.3%, against
    a "qualifying universe" that was the 170-symbol pool in one sentence and the
    36-symbol study universe in another. Neither number reproduced. Computing them
    here costs one adjusted-bar read per symbol -- no floorsheet parse at all, since
    a forward return needs only prices -- which is a few seconds inside a ~490 s
    study, and a computed number cannot go stale the way a remembered one did.

    All three groups are measured the SAME way: the study's own shared grid dates,
    the study's horizon, entry at the next session's open on adjusted bars. That is
    what makes them comparable; it is not the study's own baseline, which is
    additionally filtered by the panel builder's data requirements.

    The MEAN is reported beside the median deliberately. The median gap between the
    excluded names and the pool is under a point, and quoting only that understates
    the selection by about an order of magnitude -- the mean gap is far larger,
    because late listings in this market carry the right tail.
    """
    qual, late = [], []
    for s in loader.symbols():
        d = loader.sessions(s)
        if len(d) < min_sessions or d[-1] < LAST_BY:
            continue
        (qual if d[0] <= FIRST_BY else late).append(s)

    def measure(label: str, names: Sequence[str]) -> Surv:
        out: list[float] = []
        used = 0
        for s in names:
            ab = loader.adjusted_bars(s, end)
            if len(ab) < 60:
                continue
            used += 1
            bd = [b.date for b in ab]
            for d in grid:
                f = _forward(ab, bd, d)
                if f is not None:
                    out.append(f[1][PRIMARY - 1].close / f[0] - 1.0)
        return Surv(label, used, len(out), _mean(out), _median(out),
                    (sum(1 for x in out if x > 0) / len(out)) if out else 0.0)

    return (
        measure(f"EXCLUDED by the start-date clause ({len(late)} late listings)", late),
        measure(f"the qualifying pool it was drawn from ({len(qual)} symbols)", qual),
        measure(f"the study universe actually sampled ({len(study)} symbols)", study),
    )


def universe(n: int = 36, min_sessions: int = 700) -> list[str]:
    """A liquidity-stratified sample of symbols with continuous coverage.

    Sorted by total floorsheet bytes (a size proxy that costs a stat call rather
    than a parse) and sampled at even ranks, so the set spans NIFRA at 44 MB down
    to symbols with a few thousand prints a year.

    SELECTION, stated because it does not go away by being ignored -- and stated
    in the direction it actually runs, which is not the direction this docstring
    used to claim. The filter has three clauses and they are not equally guilty:

    * ``d[-1] >= LAST_BY`` is the survivorship clause, and it is a NO-OP. It
      removes exactly three symbols (CMF2, PFL, SFCL) and all three have zero
      price bars, so :func:`_panel` drops them anyway at ``len(abars) < 60``.
      Measured impact on every number in this file: 0.00%.
    * ``d[0] <= FIRST_BY`` is the clause that actually selects. It removes the
      symbols listed after the window opened, and the excluded names did BETTER --
      by a little at the median and by a lot at the mean. The figures are NOT
      quoted here any more: they are measured every run by :func:`survivorship`
      and printed in the report, because the copy that used to live in this
      docstring drifted from the code and was still being read as current.
    * ``len(d) >= 700`` removes ~351 symbols with too little history to compute a
      trailing percentile against; that is a measurement floor, not a selection.

    So the universe is selection-DEPRESSED, not inflated: dropping the recent
    listings drops the better half. The earlier claim that this "biases the LEVEL of
    every return in this file upwards" was backwards. What has not changed is the
    mitigation, and it is the one that matters: every comparison in this file is
    WITHIN this universe, against a buy-and-hold baseline built from the same symbols
    on the same dates, so a level bias in either direction cancels out of every
    excess column.
    """
    rows = []
    for s in loader.symbols():
        d = loader.sessions(s)
        if len(d) < min_sessions or d[0] > FIRST_BY or d[-1] < LAST_BY:
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
    surv: tuple[Surv, ...]
    fams: tuple[tuple[str, str, Perf, Control, dict[str, Perf], Cluster], ...]
    folds: tuple[Fold, ...]
    imp: dict[str, tuple[float, float]]
    groups: dict[str, float]
    stab: tuple[Stability, ...]
    inter: tuple[Interaction, ...]
    edge_tables: tuple[tuple[str, tuple[Edge, ...], Screen], ...]  # sections 94, 95, 96
    cal: Calibration
    swing: SwingRank
    phases: tuple[tuple[str, tuple[Phase, ...]], ...]  # only for families that reached EDGE
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

    # One shared calendar, so every symbol is asked the same question on the same
    # day and the cross-section is a real cross-section. The union of the
    # universe's session dates IS the NEPSE trading calendar for this universe;
    # it costs one listdir per symbol and nothing is parsed.
    cal = sorted({d for s in syms for d in loader.sessions(s) if d <= end})
    grid_dates = frozenset(cal[i] for i in range(0, len(cal), grid))
    zone_dates = frozenset(cal[i] for i in range(0, len(cal), zone_stride))
    args = [(s, start, end, grid_dates, zone_dates, sens) for s in syms]
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

    base = perf("buy-and-hold (every stock-day on the grid)", obs, demean, "long", by_date)
    base_years = per_year(obs, demean)

    fams = []
    for name, side, fn in families():
        sel = [o for o in obs if fn(o)]
        fams.append((name, side, perf(name, sel, demean, side, by_date),
                     control(sel, by_date, side), per_year(sel, demean, side),
                     clustered(sel, demean, side)))

    folds = walk_forward(obs, demean)
    imp = importance(obs, demean, folds) if folds else {}
    grp = group_importance(obs, demean, folds) if folds else {}
    cal = calibration(obs, folds)

    # Anything that clears the corrected bar IN EITHER DIRECTION gets the overlap
    # test. It is the cheapest way to stop this file publishing a p-value that is
    # four times too confident.
    #
    # Keyed on the control p and not on the verdict, deliberately. The clustered gate
    # can now demote a family that clears this bar, and if the trigger were the
    # verdict then a family losing EDGE would also silently lose its phase table --
    # deleting the evidence for the demotion at the moment it becomes relevant.
    bonf = 0.05 / max(1, 2 * sum(1 for f in fams if f[2].usable))
    phases = tuple((name, tuple(phase_split(
        [o for o in obs if fn(o)], obs, demean, by_date, side)))
        for (name, side, fn), (_, _, p, c, ys, cl) in zip(families(), fams)
        if p.usable and min(c.p_value, c.p_low) < bonf)

    return Result(
        symbols=tuple(syms),
        obs=tuple(obs),
        demean=demean,
        regime=regime,
        baseline=base,
        baseline_years=base_years,
        surv=survivorship(sorted(d for d in grid_dates if d >= start), syms, end),
        fams=tuple(fams),
        folds=tuple(folds),
        imp=imp,
        groups=grp,
        stab=tuple(stability(obs, demean, sens_rows, regime)),
        inter=tuple(interactions(obs, demean)),
        edge_tables=tuple(
            (title, tuple(edges(obs, demean, kind, share)),
             screening_test(obs, demean, kind, share))
            for title, (kind, share) in EDGE_KINDS.items()
        ),
        cal=cal,
        swing=swing_rank(obs, demean),
        phases=phases,
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


def bonferroni(r: Result) -> tuple[float, int]:
    """(corrected threshold, families tested). N rules in TWO directions is 2N tests.

    This function used to divide by N. But every family is judged twice -- once on
    ``p_value`` for an EDGE and once on ``p_low`` for a NEGATIVE -- and a test you
    are willing to declare on is a test you paid alpha for, whether or not it fired.
    Screening fifteen families two-sidedly is thirty opportunities to find something,
    so the bar is 0.05/30 = 0.00167, not 0.05/15 = 0.00333.

    The measured consequence on this archive is not cosmetic: at 0.0033 three
    families cleared a corrected bar in one direction or the other; at 0.00167 only
    ``vwap_slope`` (p 0.0002) still does, and ``zone_buy`` (p.low 0.0072) and
    ``net_churn`` (p.low 0.0164) drop to the naive tier.

    Returns the corrected threshold and the FAMILY count. The test count is twice
    the second element, and every line that prints one prints the other beside it --
    "1 of 15 clears" and "2 are significantly negative" were previously two counts
    quoted to two different standards inside a single sentence.
    """
    tested = sum(1 for f in r.fams if f[2].usable)
    return 0.05 / max(1, 2 * tested), tested


def verdict(p: Perf, c: Control, years: dict[str, Perf],
            bonf: float, cl: Cluster) -> tuple[str, int, int]:
    """EDGE / weak / NEGATIVE / weak NEGATIVE / no edge / SUPPRESSED, and the year count.

    Lifted out of :func:`report` so that the text report and the board's section 98
    cannot drift into disagreeing about what a family was judged to be.

    THREE gates, and the SAME three in both directions. That symmetry is the fix, not
    a tidy-up: this function used to require ``p_value < bonf`` (0.0033) to say EDGE
    while accepting ``p_low < 0.05`` to say NEGATIVE, with no corrected branch on the
    lower tail at all -- a fifteen-fold cheaper bar for a negative claim than a
    positive one -- and its own docstring asserted that "the lower tail is tested with
    the same two thresholds as the upper one", which was simply not true of the code
    beneath it. The gates:

    1. **Direction and persistence.** Excess of the claimed sign, and >=60% of years
       with n>=10 agreeing with it. Unchanged.
    2. **The date-matched control**, at the Bonferroni bar for the strong tier and at
       the naive 0.05 for the weak one -- now applied to ``p_low`` exactly as to
       ``p_value``, and now against a bar corrected for 2N tests (:func:`bonferroni`).
    3. **The symbol-block bootstrap** (:func:`clustered`), REQUIRED for either strong
       tier. Gate 2 assumes the rows are exchangeable within a date, which prices the
       cross-section and not the fact that a handful of symbols supply most of the
       rows AND most of the effect. A family whose clustered 95% interval spans zero
       has not been separated from three stocks having a good run, and does not print
       EDGE or NEGATIVE no matter what gate 2 says.

    On this archive gate 3 is what decides the headline. ``vwap_slope`` clears gate 2
    at p 0.0002 and fails gate 3 -- its excess is +0.69% with a clustered interval of
    roughly [-0.25%, +1.67%], because three of thirty-six names carry 87% of it.

    ``zone_buy`` is the mirror case and the one the board itself trades: a negative
    excess, negative in 4 of its 5 years, whose lower-tail control p (0.0072) clears
    the naive bar but not the corrected one, while its clustered interval DOES
    separate from zero. It lands in "weak NEGATIVE", and the report spells out that
    the clustering supports the sign and the corrected bar does not.

    Returns ``(verdict, years with positive excess, years with n>=10)``.
    """
    if not p.usable:
        return "SUPPRESSED", 0, 0
    yrs = [q for q in years.values() if q.n >= 10]
    pos = sum(1 for q in yrs if q.excess_mean > 0)
    up = p.excess_mean > 0 and bool(yrs) and pos / len(yrs) >= 0.6
    dn = p.excess_mean < 0 and bool(yrs) and (len(yrs) - pos) / len(yrs) >= 0.6
    # Gate 3, stated once and identically for both tiers.
    tight = cl.p_value < CLUSTER_P and not cl.spans_zero
    if up and c.p_value < bonf and tight:
        return "EDGE", pos, len(yrs)
    if dn and c.p_low < bonf and tight:
        return "NEGATIVE", pos, len(yrs)
    if up and c.p_value < 0.05:
        return "weak", pos, len(yrs)
    if dn and c.p_low < 0.05:
        return "weak NEGATIVE", pos, len(yrs)
    return "no edge", pos, len(yrs)


def report(r: Result) -> str:
    """The whole study as text. Every claim carries its sample size beside it."""
    L: list[str] = []
    add = L.append
    add("=" * 100)
    add("SWING QUANTUM BACKTEST -- spec sections 93-104 + 114")
    add("=" * 100)
    add(f"universe        {len(r.symbols)} symbols, liquidity-stratified, selection-filtered "
        f"(see SELECTION below)")
    add(f"                {', '.join(r.symbols)}")
    add(f"window          {START} -> {END}   grid every {GRID_STRIDE} sessions, zones every {ZONE_STRIDE}")
    add(f"observations    {len(r.obs):,} stock-days over {len({o.date for o in r.obs})} decision dates")
    add(f"outcome         entry at the NEXT session's OPEN on ADJUSTED bars; horizon {PRIMARY} sessions")
    add(f"                secondary horizons {HORIZONS}; target/stop {TARGET:+.0%}/{-STOP:.0%}, fixed, never tuned")
    add(f"runtime         {r.seconds:.0f}s")
    for e in r.errors:
        add(f"  ! {e}")
    add("")
    add("SELECTION -- what the universe filter does, in the direction it actually does it.")
    add(f"  A symbol qualifies on three clauses: >=700 sessions, first session <= {FIRST_BY}, last")
    add(f"  session >= {LAST_BY}. Only the middle one selects anything that matters.")
    add("    survivorship (last session): removes 3 symbols, ALL of which have zero price bars and")
    add("      are dropped by the panel builder regardless. Measured impact on this file: 0.00%.")
    add("    >=700 sessions: a measurement floor (a trailing percentile needs history), not a bet.")
    add("    start date: removes the symbols listed after the window opened. Measured below.")
    add("")
    add("  MEASURED THIS RUN, all three groups on the SAME grid dates, the same horizon and the")
    add("  same next-open entry on adjusted bars. Every figure names the universe it belongs to,")
    add("  because the version of this block that quoted numbers from memory compared the excluded")
    add("  names against a 'qualifying universe' that meant two different things thirty lines apart.")
    add(f"    {'group':<52}{'syms':>6}{'n':>9}{'mean':>10}{'median':>10}{'win':>8}")
    for sv in r.surv:
        add(f"    {sv.label:<52}{sv.symbols:>6}{sv.n:>9,}{sv.mean:>10.2%}"
            f"{sv.median:>10.2%}{sv.win_rate:>8.1%}")
    add("    (the count in the label is how many symbols pass the session filter; the syms column")
    add("     is how many of those have price bars to measure a forward return on.)")
    if len(r.surv) >= 2:
        ex_, po = r.surv[0], r.surv[1]
        add(f"  -> the EXCLUDED names did better: {ex_.median - po.median:+.2%} at the median and")
        add(f"     {ex_.mean - po.mean:+.2%} at the MEAN, against the pool they were dropped from.")
        add("     The mean gap is the one that matters and the one previously left out: quoting")
        add("     the median alone understates this selection by an order of magnitude, because")
        add("     late listings in this market carry the right tail.")
    add(f"  The study's own baseline ({r.baseline.n:,} rows) is a FOURTH number and not comparable")
    add("  to the three above -- it is the same 36 symbols after the panel builder's data")
    add(f"  requirements, and it reads mean {r.baseline.mean:+.2%} / median {r.baseline.median:+.2%} "
        f"/ win {r.baseline.win_rate:.1%}.")
    add("  So this universe is selection-DEPRESSED, not inflated. An earlier version of this file")
    add("  claimed the opposite. Either way it does not touch the comparisons: every excess column")
    add("  below is taken WITHIN this universe against the same-day mean of the same symbols,")
    add("  which is why the buy-and-hold baseline on identical dates is compulsory.")

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
        f"{'EXCESS':>9}{'vs REST':>9}{'rand':>9}{'p':>9}{'p.low':>9}{'PF':>7}"
        f"{'MFE':>8}{'MAE':>8}{'cov':>7}")
    for name, side, p, c, _, _cl in r.fams:
        if p.n == 0:
            add(f"  {name:<26}{side:>6}{0:>7}   (never fired -- the rule is unreachable on this archive)")
            continue
        if not p.usable:
            add(f"  {name:<26}{side:>6}{p.n:>7}   SUPPRESSED: below the {MIN_OBS}-observation floor, "
                f"no rate reported")
            continue
        add(f"  {name:<26}{side:>6}{p.n:>7}{p.win_rate:>8.1%}{p.mean:>9.2%}{p.median:>9.2%}"
            f"{p.excess_mean:>9.2%}{p.excess_lfo:>9.2%}{c.mean:>9.2%}{c.p_value:>9.4f}"
            f"{c.p_low:>9.4f}{p.profit_factor:>7.2f}"
            f"{p.mfe_mean:>8.2%}{p.mae_mean:>8.2%}{p.coverage:>7.0%}")
    add("")
    add("  EXCESS is the date-demeaned edge: the family's return minus the mean of EVERY stock in")
    add("  the universe on the same day. On a strong market day every stock looks accumulated, so")
    add("  this column -- not `mean` -- is the one that says whether the rule knew anything.")
    add("  vs REST is the same excess taken against the same day's NON-MEMBERS. EXCESS puts the")
    add("  family inside its own benchmark, which shrinks it toward zero in proportion to cov -- at")
    add("  ~50% coverage by about half. vs REST is the number with that removed, and it is the")
    add("  larger of the two in BOTH directions: it makes the good families look better and the")
    add("  bad ones look worse. Neither column is the 'real' one; EXCESS is what a long-only book")
    add("  earns over the index, vs REST is what the RULE knew relative to what it passed over.")
    add(f"  p is the share of {CONTROL_DRAWS:,} date-matched random draws that matched or BEAT the")
    add("  family; p.low is the share that matched or fell BELOW it. The draws are k-of-N subsets")
    add("  of each day's universe taken WITHOUT replacement, which is what makes them a null for")
    add("  'pick k stocks today' rather than a null with the finite-population correction missing.")
    add("  cov is the share of the universe the family holds on its own firing dates.")
    add("  A `short` side is an AVOIDANCE signal, sign-flipped so 'right' reads positive: NEPSE")
    add("  has no short selling, so a profitable short row is not a tradeable strategy.")
    add(f"  Compare every excess win rate against the BASELINE's {b.excess_win_rate:.1%}, not against")
    add("  50%: forward returns are right-skewed, so most stocks lose to the same-day mean.")

    add("")
    add("  SYMBOL CONCENTRATION AND THE CLUSTERED TEST -- the p column above assumes the rows")
    add("  are exchangeable within a date. They are not. One symbol contributes dozens of rows,")
    add("  its 20-day windows overlap, and its idiosyncratic run appears in every one of them.")
    add(f"  {'family':<26}{'side':>6}{'n':>7}{'syms':>6}{'agree':>8}{'top3':>8}"
        f"{'excess':>9}{'clustered 95% CI':>22}{'clus p':>8}  note")
    for name, side, p, _, _, cl in r.fams:
        if not p.usable:
            continue
        note = "SPANS ZERO" if cl.spans_zero else "separates from zero"
        if cl.top3_share > 1.0:
            note += f"; the other {cl.symbols - 3} symbols are net NEGATIVE"
        elif abs(cl.top3_share) >= 0.5:
            note += f"; {cl.top3_share:.0%} of it is 3 names"
        add(f"  {name:<26}{side:>6}{p.n:>7}{cl.symbols:>6}"
            f"{f'{cl.agree}/{cl.symbols}':>8}{cl.top3_share:>8.0%}{cl.excess:>9.2%}"
            f"{f'[{cl.lo:+.2%}, {cl.hi:+.2%}]':>22}{cl.p_value:>8.3f}  {note}")
    add("")
    add("  syms is how many distinct symbols the family fired on; agree is how many of them")
    add("  carry the family's OWN sign in total excess; top3 is the share of the family's whole")
    add("  excess carried by its three biggest contributors. Above 100% the remaining names are")
    add("  net NEGATIVE and the family IS those three stocks; a very large percentage means the")
    add("  family's total is near zero and the ratio has little to say -- read `agree` instead.")
    add("  The CI is a symbol-block bootstrap")
    add(f"  ({CLUSTER_DRAWS:,} resamples of the symbols, with replacement) and clus p is one-sided")
    add("  in the family's own direction -- the SAME statistic for a positive family and a")
    add(f"  negative one, judged against the same {CLUSTER_P:.2f}. A CI that spans zero means the")
    add("  effect has not been separated from a few stocks having a run, and it blocks both")
    add("  strong verdicts below no matter how small the date-matched p is.")

    add("")
    add("  section 93 EXPECTED VALUE and section 97 LABELS, per family:")
    for name, side, p, _, _, _cl in r.fams:
        if not p.usable:
            continue
        add(f"    {name} ({side}, n={p.n})")
        add(f"      win {p.win_rate:.1%} / loss {p.loss_rate:.1%}   avg win {p.avg_win:+.2%} "
            f"(median {p.median_win:+.2%}) / avg loss {p.avg_loss:.2%} (median {p.median_loss:.2%})")
        add(f"      EV {p.expected_value:+.2%}   expectancy {p.expectancy:+.2f}R   payoff {p.payoff:.2f}   "
            f"max fav {p.max_mfe:+.1%} / max adv {p.max_mae:+.1%}   max DD {p.max_drawdown:.1%}")
        add(f"      time to target {p.time_to_target:.1f} sessions ({p.target_rate:.0%} got there), "
            f"time to invalidation {p.time_to_invalidation:.1f} ({p.invalidation_rate:.0%}), "
            f"average hold {p.avg_hold:.1f}")
        add("      labels: " + ", ".join(f"{k} {v}" for k, v in p.labels[:6]))

    add("")
    add("-" * 100)
    add("PER-YEAR TABLES (mandatory) -- a single aggregate hides a regime failure")
    add("-" * 100)
    for name, side, p, _, ys, _cl in r.fams:
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
    add("  'extreme'/'missing' are the mean per-day shift, in the feature's own cross-day standard")
    add("  deviations, after removing the day's single largest trade / a random 10% of its rows.")
    lb = [s for s in r.stab if s.lookback_sensitivity]
    if lb:
        add("  lookback sensitivity (1 - corr between the 7D and 15D definition of the same idea):")
        add("    " + "   ".join(f"{s.feature}={s.lookback_sensitivity:.3f}" for s in
                                sorted(lb, key=lambda s: -s.lookback_sensitivity)[:8]))

    add("")
    add("-" * 100)
    add("SECTION 101 -- FEATURE INTERACTIONS")
    add("-" * 100)
    add(f"  {'pattern (tercile cuts)':<58}{'n':>7}{'excess':>10}{'additive':>10}{'lift':>9}")
    for it in r.inter:
        if it.suppressed:
            add(f"  {it.name:<58}{it.n:>7}   SUPPRESSED: below the {MIN_OBS}-observation floor")
            continue
        add(f"  {it.name:<58}{it.n:>7}{it.excess:>10.2%}{it.additive:>10.2%}{it.lift:>9.2%}")
    add("  'lift' is the conjunction minus the sum of its parts. Near zero = the interaction is")
    add("  nothing more than its ingredients arriving on the same day.")
    add("  Terciles, not quintiles: the spec's five-way conjunction at the quintile cut fires on")
    add("  zero stock-days, and an untestable pattern is worse than a weak one.")

    add("")
    add("-" * 100)
    add(f"SECTIONS 94-96 -- BROKER EDGE (no rate reported below {MIN_OBS} occurrences)")
    add("-" * 100)
    shares = sorted(o.pair_share for o in r.obs)
    add(f"  dominant-pair share of 15-session volume across all {len(shares):,} stock-days: "
        f"median {history.percentile(shares, 50):.1%}, p90 {history.percentile(shares, 90):.1%}, "
        f"max {shares[-1]:.1%} (section 96 floor is {PAIR_MIN_SHARE:.1%})")
    for title, table, sc in r.edge_tables:
        live = [e for e in table if not e.suppressed]
        add(f"  section {title}: {len(table)} candidates, {len(live)} above the floor, "
            f"{len(table) - len(live)} SUPPRESSED for sample size")
        if not live:
            add("    nothing reportable -- every candidate is below the floor. That is the result.")
            continue
        add(f"    {'key':<26}{'n':>6}{'pos':>7}{'mean ex':>10}{'median':>9}{'consist':>9}"
            f"{'lead':>7}{'syms':>6}{'top sym':>9}{'5d':>8}{'10d':>8}{'20d':>8}")
        for e in live[:8]:
            d = e.decay + (0.0,) * 3
            add(f"    {e.key:<26}{e.n:>6}{e.positive_rate:>7.0%}{e.mean:>10.2%}{e.median:>9.2%}"
                f"{e.consistency:>9.0%}{e.lead_time:>7.1f}{e.symbols:>6}{e.top_symbol_share:>9.0%}"
                f"{d[0]:>8.2%}{d[1]:>8.2%}{d[2]:>8.2%}")
        if len(live) > 8:
            add(f"    ... and {len(live) - 8} more")
        best = live[0]
        add(f"    BEST-OF-N CONTROL: {sc.candidates} candidates screened, {sc.groups} above the "
            f"floor. Shuffling which observation lands in which group, keeping the group sizes,")
        add(f"    produces a best group at least as good as {best.key}'s {sc.best:+.2%} in "
            f"p = {sc.p_value:.4f} of {sc.draws:,} random partitions.")
        add(f"    DATE-MATCHED, dealing each random group the same number of rows PER DATE as the "
            f"real one: p = {sc.p_matched:.4f}.")
        gap = abs(sc.p_matched - sc.p_value)
        add(f"    -> date-matching {'MATTERS here' if gap > 0.02 else 'changes little here'} "
            f"({sc.p_value:.4f} -> {sc.p_matched:.4f}, {gap:+.4f}): the pooled null leaves the")
        add("       date clustering inside a group in place, so any gap is the part of this table")
        add("       that was reading a calendar rather than a broker.")
        conc = best.top_symbol_share >= 0.5 or best.symbols <= 2
        add(f"    -> {'this table is a sorting artefact' if sc.p_value > 0.10 else 'the top of this table survives the shuffle control'}"
            f"; {best.key} is positive in {best.consistency:.0%} of years, spread over "
            f"{best.symbols} stock(s) with {best.top_symbol_share:.0%} on the busiest.")
        if sc.p_value <= 0.10 and conc:
            add("       BUT the shuffle control does not remove SYMBOL clustering, and this one is")
            add("       concentrated in one or two names -- that is one stock's run wearing a")
            add("       broker's number, not evidence that the broker knew anything.")

    add("")
    add("-" * 100)
    add("SECTION 114 -- PROBABILISTIC OUTPUT")
    add("-" * 100)
    add("  " + r.cal.note)
    if r.cal.buckets:
        add("  The walk-forward score is min-max scaled into [0, 1] inside each test block and then")
        add("  read as if it were P(positive 20-day return). A real probability would need a fitted")
        add("  link (isotonic or Platt) on out-of-sample scores; this is the test of whether the raw")
        add("  score already behaves like one, and the answer is what decides the wording above.")
        add(f"  {'predicted':>11}{'realised':>11}{'n':>8}")
        for pr, re_, n in r.cal.buckets:
            add(f"  {pr:>11.2f}{re_:>11.2f}{n:>8}")
        lo_b, hi_b = r.cal.buckets[0][1], r.cal.buckets[-1][1]
        add(f"  bottom bucket wins {lo_b:.1%} of the time, top bucket {hi_b:.1%}: the WALK-FORWARD")
        add(f"  FITTED COMPOSITE {'at least RANKS' if hi_b - lo_b > 0.05 else 'does not even RANK'} "
            f"(spread {hi_b - lo_b:+.1%}).")
    add("")
    add("  THE BOARD'S OWN SWING SCORE, tested separately -- it is NOT the score above.")
    add("  Everything in the block above measures the fitted composite of feature percentiles that")
    add("  walk_forward() produces. The 0-100 `swing score` the board prints comes out of zones.py")
    add("  and shares none of its inputs. Attributing one score's flatness to the other is a claim")
    add("  about a number nobody measured, so here is the number.")
    add("  " + r.swing.note)
    if r.swing.buckets:
        add(f"  {'score':>9}{'win rate':>11}{'excess':>10}{'n':>8}")
        for sc_, wr, exc, n_ in r.swing.buckets:
            add(f"  {sc_:>9.1f}{wr:>11.1%}{exc:>10.2%}{n_:>8}")
        add("  'excess' is date-demeaned, so it is the cross-sectional statement: did a higher score")
        add("  pick a better stock THAT DAY. A monotone column would be a ranking; a flat one is not.")
    add("")
    best = max((f for f in r.fams if f[2].usable), key=lambda f: f[2].excess_mean, default=None)
    if best:
        add(probabilistic(f"best family by date-demeaned excess: {best[0]} ({best[1]})",
                          best[2], r.cal))
    add("")
    add("=" * 100)
    add("VERDICT")
    add("=" * 100)
    # Three states, not two. Screening fifteen families and reporting whichever
    # ones clear p<0.05 is the error that manufactures a finding out of noise, so
    # the corrected threshold is what earns the word EDGE and the naive one only
    # earns "weak". Both are shown; neither is chosen after looking.
    bonf, tested = bonferroni(r)
    strong, weak, bad, weak_bad = [], [], [], []
    add(f"  THE BARS, stated once, because two counts quoted to two different standards inside one")
    add("  sentence is how this section previously read. Three gates, the SAME three in each")
    add("  direction:")
    add("    1. direction: excess of the claimed sign, agreeing in >=60% of years with n>=10")
    add(f"    2. the date-matched control, at {bonf:.5f} for a strong verdict and at 0.0500 for a")
    add(f"       weak one. {bonf:.5f} is 0.05 Bonferroni-corrected for {tested} families tested in")
    add(f"       TWO directions = {2 * tested} tests. It used to be 0.05/{tested} = "
        f"{0.05 / max(1, tested):.5f}, which paid for")
    add("       the upper tail only; and the lower tail had no corrected branch at all, so a")
    add("       NEGATIVE cost fifteen times less evidence than an EDGE.")
    add(f"    3. the symbol-block bootstrap, one-sided p below {CLUSTER_P:.2f} AND a 95% interval")
    add("       that does not span zero. REQUIRED for EDGE and for NEGATIVE, identically.")
    add("  'weak' / 'weak NEGATIVE' clear gate 2's uncorrected 0.05 only, or clear the corrected")
    add(f"  bar but fail gate 3 -- and screening {2 * tested} tests at 0.05 produces about "
        f"{2 * tested * 0.05:.1f} of them on")
    add("  data with nothing in it. 'no edge' means indistinguishable from chance, which is NOT")
    add("  what a NEGATIVE is: a NEGATIVE is measurably worse than the same-day universe.")
    add("")
    for n, s, p, c, ys, cl in r.fams:
        v, pos, ny = verdict(p, c, ys, bonf, cl)
        if v == "SUPPRESSED":
            add(f"  {n:<26}{'SUPPRESSED':<14} n={p.n}, below the {MIN_OBS}-observation floor")
            continue
        if v == "EDGE":
            strong.append(n)
        elif v == "weak":
            weak.append(n)
        elif v == "NEGATIVE":
            bad.append(n)
        elif v == "weak NEGATIVE":
            weak_bad.append(n)
        tail = f"p.low {c.p_low:.4f}" if "NEGATIVE" in v else f"p {c.p_value:.4f}"
        add(f"  {n:<26}{v:<14} excess {p.excess_mean:+.2%} (vs rest {p.excess_lfo:+.2%}), "
            f"control {tail}, clustered p {cl.p_value:.3f}"
            f"{' (CI spans zero)' if cl.spans_zero else ''}, positive in {pos}/{ny} years")
    add("")
    add(f"  {len(strong)} of {tested} testable families reach EDGE -- all three gates, control p "
        f"below {bonf:.5f}"
        f"{': ' + ', '.join(strong) if strong else '.'}")
    add(f"  {len(bad)} reach NEGATIVE -- all three gates, lower-tail control p below the SAME "
        f"{bonf:.5f}"
        f"{': ' + ', '.join(bad) if bad else '.'}")
    add(f"  {len(weak)} are 'weak' and {len(weak_bad)} 'weak NEGATIVE' -- the uncorrected 0.05 only, "
        f"or the corrected bar without gate 3"
        f"{': ' + ', '.join(weak + weak_bad) if weak + weak_bad else '.'}")
    live_f = [f for f in r.fams if f[2].usable]
    old_bar = 0.05 / max(1, tested)
    add("  CONTROL-P COUNTS ONLY (no direction test, no clustered gate), so that anything")
    add("  quoting an older number can be reconciled against this run:")
    add(f"    at the corrected {bonf:.5f}: "
        f"{sum(1 for f in live_f if f[3].p_value < bonf)} upper tail, "
        f"{sum(1 for f in live_f if f[3].p_low < bonf)} lower tail")
    add(f"    at the OLD one-direction {old_bar:.5f}: "
        f"{sum(1 for f in live_f if f[3].p_value < old_bar)} upper tail, "
        f"{sum(1 for f in live_f if f[3].p_low < old_bar)} lower tail")
    add(f"    at the naive 0.05000: "
        f"{sum(1 for f in live_f if f[3].p_value < 0.05)} upper tail, "
        f"{sum(1 for f in live_f if f[3].p_low < 0.05)} lower tail")

    for n, s, p, c, ys, cl in r.fams:
        v = verdict(p, c, ys, bonf, cl)[0]
        if "NEGATIVE" not in v:
            continue
        yrs = [(y, q) for y, q in sorted(ys.items()) if q.n >= 10]
        add("")
        add(f"  {n.upper()} IS NOT NEUTRAL AND MUST NOT BE READ AS 'UNPROVEN'.")
        add(f"    n {p.n:,}   excess {p.excess_mean:+.2%} mean, {p.excess_median:+.2%} median, "
            f"{p.excess_lfo:+.2%} against the same day's non-members")
        add(f"    it beat the same-day universe {p.excess_win_rate:.1%} of the time "
            f"(baseline {b.excess_win_rate:.1%})")
        add(f"    negative in {sum(1 for _, q in yrs if q.excess_mean <= 0)}/{len(yrs)} years: "
            + ", ".join(f"{y} {q.excess_mean:+.2%}" for y, q in yrs))
        add(f"    the date-matched control puts the chance of an excess this negative at "
            f"{c.p_low:.4f} on {CONTROL_DRAWS:,} draws --")
        add(f"      {'BELOW' if c.p_low < bonf else 'above'} the corrected {bonf:.5f} and "
            f"{'below' if c.p_low < 0.05 else 'above'} the naive 0.05, so it is "
            f"{'NEGATIVE' if c.p_low < bonf else 'weak NEGATIVE'} on gate 2")
        add(f"    the symbol-block bootstrap puts it at {cl.p_value:.3f} with a 95% interval of "
            f"[{cl.lo:+.2%}, {cl.hi:+.2%}] over {cl.symbols} symbols,")
        add(f"      {cl.symbols - cl.agree} of which are net POSITIVE -- so clustering "
            f"{'SUPPORTS' if cl.p_value < CLUSTER_P and not cl.spans_zero else 'does NOT support'} "
            f"the sign. The two gates")
        add("      can disagree and here they are reported separately rather than averaged into a")
        add("      single word: which one you weight is a judgement, hiding one of them is not.")
        if n.startswith("zone"):
            add(f"    and this is the LEAST forgiving sample in the file to fail on: zones are "
                f"emitted every {ZONE_STRIDE} sessions and the horizon is {PRIMARY}, so these")
            add("    observations do not overlap at all and the p-value needs no discount.")

    for name, phs in r.phases:
        fam = next(f for f in r.fams if f[0] == name)
        p, c_, ys_, cl = fam[2], fam[3], fam[4], fam[5]
        v = verdict(p, c_, ys_, bonf, cl)[0]
        add("")
        add(f"  {name.upper()} CLEARED THE DATE-MATCHED CONTROL at the corrected {bonf:.5f} "
            f"(p {min(c_.p_value, c_.p_low):.4f}).")
        add(f"  Its verdict is {v}. Three things shrink it, and on this archive the first one is")
        add("  decisive:")
        add(f"    SYMBOL CONCENTRATION -- it fired on {cl.symbols} symbols, {cl.agree} of which "
            f"carry its own sign, and its")
        add(f"      top three carry {cl.top3_share:.0%} of the entire excess. Resampling SYMBOLS "
            f"rather than rows gives")
        add(f"      {cl.excess:+.2%} with a 95% interval of [{cl.lo:+.2%}, {cl.hi:+.2%}] and a "
            f"one-sided p of {cl.p_value:.3f}.")
        add(f"      The interval {'SPANS ZERO' if cl.spans_zero else 'excludes zero'}, so gate 3 "
            f"{'blocks' if cl.spans_zero or cl.p_value >= CLUSTER_P else 'passes'} the strong "
            f"verdict. The date-matched p of")
        add(f"      {min(c_.p_value, c_.p_low):.4f} is the p of 'could a random pick that day have "
            f"done this', and it is not")
        add("      the p of 'is there an effect here'. Those are different questions and this file")
        add("      now prints both.")
        add(f"    OVERLAP -- the grid fires every {GRID_STRIDE} sessions and the horizon is "
            f"{PRIMARY}, so the {p.n:,} rows above carry about {p.n // max(1, PRIMARY // GRID_STRIDE):,}")
        add("    rows' worth of independent information. Re-measured on each non-overlapping phase:")
        add(f"      {'phase':>7}{'n':>7}{'excess':>10}{'p':>9}")
        for ph in phs:
            add(f"      {ph.phase:>7}{ph.n:>7}{ph.excess:>10.2%}{ph.p_value:>9.4f}")
        neg = [ph.phase for ph in phs if ph.excess <= 0]
        add(f"      -> {len(neg)} of {len(phs)} phases have a NEGATIVE excess"
            f"{' (' + ', '.join(str(x) for x in neg) + ')' if neg else ''}. A result that survives")
        add("      on some phases and inverts on others has not been demonstrated on this sample.")
        lo_c, hi_c = ROUND_TRIP
        add(f"    COST -- every number in this file is GROSS. A NEPSE round trip is "
            f"{lo_c:.1%}-{hi_c:.1%}, so this excess of {p.excess_mean:+.2%}")
        add(f"      nets {p.excess_mean - lo_c:+.2%} to {p.excess_mean - hi_c:+.2%} per trade before "
            f"slippage on a market where the median stock-day loses to the mean.")
        grp_of = {f: g for g, fs in GROUPS.items() for f in fs}.get(name)
        if grp_of:
            add(f"    WHAT IT IS -- this family fires on the `{name}` feature, which section 104")
            add(f"      classifies in the {grp_of.upper()} group.")
            if grp_of == "price":
                add("      That makes it a PRICE-MOMENTUM result, not a floorsheet one: vwap_slope is")
                add("      3-session mean VWAP over 15-session mean VWAP, a price ratio that needs no")
                add("      broker column at all. It does not validate the flow engine this file tests;")
                add("      the one family that cleared the bar is the one that reads the least flow.")
            add("      Per-year excess above shows where it lives; a family that is one or two years")
            add("      is a regime, not an edge.")
    add("")
    add("  Anything not marked EDGE should not be traded, and no threshold in this file was moved")
    add("  to make one appear.")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# the machine-readable summary -- writer and parser, deliberately side by side
# ---------------------------------------------------------------------------
#
# WHY this file exists at all. :func:`report` writes 34 KB of prose to
# ``backtest.txt`` and nobody opens it. Meanwhile the board prints an entry zone,
# a target and a 0-100 score for 481 symbols, and the measurement saying the buy signal
# is significantly worse than random lives only in that unopened file. A board that shows
# the target and hides the verdict is not neutral, it is advertising. So the verdict has
# to reach the board -- and the board is rebuilt in 22 minutes while this study costs
# ~490 s, so it cannot be recomputed per build. One small tab-separated digest,
# written when the study runs and read once when the board is built, is the whole
# mechanism.
#
# Storage rule: plain ``.txt``, ``key<TAB>value``. No JSON, no CSV. Repeated keys
# (``family``, ``fold``, ``stability``, ``importance``, ``group_importance``) carry a
# row of values after the key; every other key carries exactly one.
#
# Writer and parser are in this module, next to each other, for the same reason
# :mod:`store` keeps its own pair together: a format whose reader lives elsewhere
# drifts, and a silently mis-parsed verdict is worse than no verdict.

#: File name beside ``backtest.txt`` in ``Master_data/swing_quantam/``.
SUMMARY = "backtest_summary.txt"


def summary_path() -> str:
    return os.path.join(loader.OUT, SUMMARY)


class FamilyRow(NamedTuple):
    """One section 98 rule family, as the board reads it back."""

    name: str
    side: str
    n: int
    win: float
    mean: float
    median: float
    excess: float
    control_p: float
    years_positive: int
    years: int
    verdict: str
    excess_lfo: float = 0.0   # against the same-day non-members
    control_p_low: float = 0.0  # the lower tail -- how a NEGATIVE verdict is earned
    # The symbol-block bootstrap. Defaults keep a digest written before the clustering
    # audit parseable; `cluster_symbols == 0` is how a renderer tells "not measured"
    # from "measured and wide", which are very different things to print.
    cluster_symbols: int = 0
    cluster_top3: float = 0.0
    cluster_lo: float = 0.0
    cluster_hi: float = 0.0
    cluster_p: float = 1.0

    @property
    def usable(self) -> bool:
        """False when the family was below the sample floor -- then no rate is real."""
        return self.verdict != "SUPPRESSED"

    @property
    def clustered(self) -> bool:
        """True when the clustered interval spans zero -- a few symbols carry it.

        Named for what a reader should conclude, not for the arithmetic: the row is
        not separated from a handful of stocks having a run. False when the bootstrap
        was never run (an old digest), which is why callers must check
        ``cluster_symbols`` before quoting this.
        """
        return self.cluster_lo <= 0.0 <= self.cluster_hi


class EdgeRow(NamedTuple):
    """One section 94/95/96 edge claim that cleared the sample floor."""

    table: str
    key: str
    n: int
    positive_rate: float
    mean: float
    median: float
    consistency: float
    lead_time: float
    symbols: int
    top_symbol_share: float
    decay: tuple[float, float, float]

    @property
    def clustered(self) -> bool:
        """One or two stocks carry the whole row -- a false positive's signature.

        The shuffle control removes the market effect but NOT symbol clustering: a broker
        whose occurrences are one stock's good run scores exactly like a broker with a real
        edge. This is the column that tells them apart, and it is why section 95 -- which is
        ONE broker on ONE stock by construction -- can never clear it.
        """
        return self.symbols <= 2 or self.top_symbol_share >= 0.5


class EdgeTable(NamedTuple):
    """The best-of-N control for a screened table -- the number that kills most of them."""

    title: str
    candidates: int
    live: int
    best: float
    p_value: float
    groups: int
    draws: int = 0          # 1/draws is the quantum of p_value; 0 means an old digest
    p_matched: float = 1.0  # the same control with the reshuffle done WITHIN each date

    @property
    def artefact(self) -> bool:
        return self.p_value > 0.10


class Summary(NamedTuple):
    """``backtest_summary.txt`` parsed back.

    ``meta`` is every single-value key; the tuples are the repeated-key tables. The
    accessors return None rather than 0.0 for a key that is not there, because a
    missing metric rendered as zero is a lie and this whole artefact exists to stop
    one being told.
    """

    meta: dict[str, str]
    families: tuple[FamilyRow, ...]
    folds: tuple[tuple[str, str, str, int, int, float, float, float], ...]
    stability: tuple[tuple[str, float, float, float, float, float], ...]
    importance: tuple[tuple[str, float, float], ...]
    groups: tuple[tuple[str, float], ...]
    edge_tables: tuple[EdgeTable, ...]
    edges: tuple[EdgeRow, ...]
    labels: tuple[tuple[str, str, int], ...]  # (family, label, count)
    #: (family, phase, n, excess, p) for families that cleared the corrected bar
    phases: tuple[tuple[str, int, int, float, float], ...] = ()
    #: (label, symbols, n, mean, median, win) -- the universe filter, measured
    surv: tuple[tuple[str, int, int, float, float, float], ...] = ()

    def num(self, key: str) -> float | None:
        try:
            return float(self.meta[key])
        except (KeyError, ValueError):
            return None

    def get(self, key: str, default: str = "") -> str:
        return self.meta.get(key) or default

    def family(self, name: str) -> FamilyRow | None:
        return next((f for f in self.families if f.name == name), None)

    # Sections 94-96 are matched on the LEADING NUMBER, not the full title. The titles are
    # EDGE_KINDS' keys ("94 broker", "95 broker-stock"); keying a renderer off that exact
    # wording means rewording one silently blanks a board section.
    def edge_table(self, section: int) -> EdgeTable | None:
        return next((t for t in self.edge_tables if t.title.startswith(f"{section} ")), None)

    def edges_in(self, section: int) -> tuple[EdgeRow, ...]:
        return tuple(e for e in self.edges if e.table.startswith(f"{section} "))

    def labels_for(self, family: str) -> tuple[tuple[str, int], ...]:
        return tuple((lab, n) for f, lab, n in self.labels if f == family)

    def label_families(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(f for f, _, _ in self.labels))


def _cell(v: object) -> str:
    """A tab or a newline would silently reshape a row; nothing else needs escaping."""
    return str(v).replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()


def _f(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        return 0.0


def _i(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        return 0


def _perf_lines(label: str, p: Perf) -> list[str]:
    """Every scalar field of a :class:`Perf`, as ``label.field<TAB>value``.

    Generated from ``Perf._fields`` rather than hand-listed: a metric added upstream
    then reaches the board without a second edit, and -- more to the point -- cannot
    go missing from it without anyone noticing.
    """
    out = []
    for f in p._fields:
        v = getattr(p, f)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        out.append(f"{label}.{f}\t{v:.6g}" if isinstance(v, float) else f"{label}.{f}\t{v}")
    return out


def write_summary(r: Result, leakage: tuple[str, str, int] | None = None,
                  ex_dates: int = -1, path: str | None = None) -> str:
    """Write the board-readable digest of ``r`` beside the human report.

    ``leakage`` is ``(symbol, cut date, rows proved identical)`` from
    :func:`leakage_check` and ``ex_dates`` the count of corporate actions inside the
    window -- both are computed by :func:`_demo`, not by :func:`run`, so they are
    passed in rather than recomputed. Absent, section 100 on the board says the guard
    was not re-proved by this run instead of implying that it was.
    """
    bonf, tested = bonferroni(r)
    ics = [f.ic for f in r.folds]
    lines: list[str] = [
        "# swing_quantam backtest summary -- key<TAB>value, written by backtest.write_summary()",
        "# Read with backtest.read_summary(). Rebuild with:",
        '#   python -c "from swing_quantam import backtest; backtest._demo()"',
        f"run_date\t{time.strftime('%Y-%m-%d')}",
        f"runtime_seconds\t{r.seconds:.0f}",
        f"universe\t{len(r.symbols)}",
        f"universe_symbols\t{_cell(', '.join(r.symbols))}",
        f"observations\t{len(r.obs)}",
        f"decision_dates\t{len({o.date for o in r.obs})}",
        f"start\t{START}",
        f"end\t{END}",
        f"horizon\t{PRIMARY}",
        f"horizons\t{', '.join(str(h) for h in HORIZONS)}",
        f"entry_lag\t{ENTRY_LAG}",
        f"target\t{TARGET}",
        f"stop\t{STOP}",
        f"grid_stride\t{GRID_STRIDE}",
        f"zone_stride\t{ZONE_STRIDE}",
        f"min_obs\t{MIN_OBS}",
        f"control_draws\t{CONTROL_DRAWS}",
        f"families_tested\t{tested}",
        f"bonferroni_p\t{bonf:.4f}",
    ]
    lines += _perf_lines("baseline", r.baseline)

    # The universe filter, MEASURED. Carried as three labelled rows rather than as a
    # sentence, because the sentence this replaces compared the excluded names against
    # an unnamed "qualifying universe" that meant two different populations.
    for i, sv in enumerate(r.surv):
        lines.append("\t".join(("survivorship", _cell(sv.label), str(sv.symbols), str(sv.n),
                                f"{sv.mean:.6g}", f"{sv.median:.6g}", f"{sv.win_rate:.6g}")))
        if i == 0:
            lines += [f"survivorship.excluded_symbols\t{sv.symbols}",
                      f"survivorship.excluded_mean\t{sv.mean:.6g}",
                      f"survivorship.excluded_median\t{sv.median:.6g}",
                      f"survivorship.excluded_win\t{sv.win_rate:.6g}"]
        elif i == 1:
            lines += [f"survivorship.pool_symbols\t{sv.symbols}",
                      f"survivorship.pool_mean\t{sv.mean:.6g}",
                      f"survivorship.pool_median\t{sv.median:.6g}",
                      f"survivorship.pool_win\t{sv.win_rate:.6g}"]
    if len(r.surv) >= 2:
        lines += [f"survivorship.median_gap\t{r.surv[0].median - r.surv[1].median:.6g}",
                  f"survivorship.mean_gap\t{r.surv[0].mean - r.surv[1].mean:.6g}"]

    # The board prints an entry zone, so the family the board itself would trade gets
    # its whole Perf written out -- section 93 is about THAT rule, not about an average
    # of fifteen. The rest travel as the compact section 98 table below.
    strong, weak, bad, weak_bad = [], [], [], []
    fam_lines: list[str] = []
    for name, side, p, c, ys, cl in r.fams:
        v, pos, ny = verdict(p, c, ys, bonf, cl)
        if v == "EDGE":
            strong.append(name)
        elif v == "weak":
            weak.append(name)
        elif v == "NEGATIVE":
            bad.append(name)
        elif v == "weak NEGATIVE":
            weak_bad.append(name)
        # A suppressed family gets blanks, not zeros. MIN_OBS exists because a rate off
        # nine events is a number-shaped opinion, and a 0.0000 in a data file is quoted
        # as a measurement by whoever reads it next.
        cells = ["", "", "", "", ""] if v == "SUPPRESSED" else [
            f"{p.win_rate:.4f}", f"{p.mean:.4f}", f"{p.median:.4f}",
            f"{p.excess_mean:.4f}", f"{c.p_value:.4f}",
        ]
        extra = ["", ""] if v == "SUPPRESSED" else [f"{p.excess_lfo:.4f}", f"{c.p_low:.4f}"]
        # Six significant digits, not four decimals. An interval bound of -0.00003 is
        # a SEPARATED interval; rounded to -0.0000 it parses back as -0.0 and reads as
        # spanning zero, which would silently invert a NEGATIVE verdict on the board.
        clus = ["", "", "", "", ""] if v == "SUPPRESSED" else [
            str(cl.symbols), f"{cl.top3_share:.6g}", f"{cl.lo:.6g}", f"{cl.hi:.6g}",
            f"{cl.p_value:.6g}",
        ]
        fam_lines.append("\t".join(["family", _cell(name), side, str(p.n)] + cells
                                   + [str(pos), str(ny), v] + extra + clus))
        if name == "zone_buy":
            lines += _perf_lines("zone_buy", p)
            lines += [f"zone_buy.side\t{side}", f"zone_buy.control_p\t{c.p_value:.4f}",
                      f"zone_buy.control_p_low\t{c.p_low:.4f}",
                      f"zone_buy.verdict\t{v}", f"zone_buy.years_positive\t{pos}",
                      f"zone_buy.years\t{ny}",
                      f"zone_buy.years_negative\t{ny - pos}",
                      f"zone_buy.cluster_symbols\t{cl.symbols}",
                      f"zone_buy.cluster_excess\t{cl.excess:.4f}",
                      f"zone_buy.cluster_lo\t{cl.lo:.4f}",
                      f"zone_buy.cluster_hi\t{cl.hi:.4f}",
                      f"zone_buy.cluster_p\t{cl.p_value:.4f}",
                      f"zone_buy.cluster_top3\t{cl.top3_share:.4f}",
                      # The per-year excesses in full, because "negative in 5 of 5 years" is
                      # the sentence the board has to be able to write without re-running.
                      "zone_buy.year_excess\t" + _cell(", ".join(
                          f"{y} {q.excess_mean:+.2%}" for y, q in sorted(ys.items())
                          if q.n >= 10))]
    lines += [
        f"families_edge\t{len(strong)}",
        f"families_edge_names\t{_cell(', '.join(strong))}",
        f"families_weak\t{len(weak)}",
        f"families_weak_names\t{_cell(', '.join(weak))}",
        f"families_negative\t{len(bad)}",
        f"families_negative_names\t{_cell(', '.join(bad))}",
        f"families_weak_negative\t{len(weak_bad)}",
        f"families_weak_negative_names\t{_cell(', '.join(weak_bad))}",
        # Both counts come from the SAME bar now, and the bar travels with them so no
        # renderer can put "1 of 15 clears the corrected threshold" and "2 are
        # significantly negative" in one sentence again without saying which is which.
        f"tests\t{2 * tested}",
        f"cluster_draws\t{CLUSTER_DRAWS}",
        f"cluster_p_bar\t{CLUSTER_P}",
    ]
    lines += fam_lines

    # --- section 99 ---------------------------------------------------------
    lines.append(f"walkforward.folds\t{len(r.folds)}")
    if r.folds:
        lines += [
            f"walkforward.mean_oos_ic\t{_mean(ics):.4f}",
            f"walkforward.folds_positive\t{sum(1 for i in ics if i > 0)}",
            f"walkforward.top_decile\t{_mean([f.top_decile for f in r.folds]):.4f}",
            f"walkforward.bottom_decile\t{_mean([f.bottom_decile for f in r.folds]):.4f}",
        ]
        for f in r.folds:
            lines.append("\t".join(("fold", f"{f.train[0]}-{f.train[1]}", f.validate[0], f.test[0],
                                    str(f.n_train), str(f.n_test), f"{f.ic:.4f}",
                                    f"{f.top_decile:.4f}", f"{f.bottom_decile:.4f}")))

    # --- section 100 --------------------------------------------------------
    lines += [f"leakage.pit_lookback\t{PIT_LOOKBACK}", f"leakage.pit_min\t{PIT_MIN}"]
    if leakage:
        lines += [f"leakage.symbol\t{leakage[0]}", f"leakage.cut\t{leakage[1]}",
                  f"leakage.rows_identical\t{leakage[2]}"]
    if ex_dates >= 0:
        lines.append(f"leakage.ex_dates_in_window\t{ex_dates}")

    # --- sections 103 and 104 -----------------------------------------------
    lines.append(f"stability.features\t{len(r.stab)}")
    for s in r.stab[:8]:
        lines.append("\t".join(("stability", s.feature, f"{s.ic_overall:.4f}",
                                f"{s.year_sign_agreement:.4f}", f"{s.dist_drift:.4f}",
                                f"{s.extreme_sensitivity:.4f}", f"{s.missing_sensitivity:.4f}")))
    lines += [f"importance.features\t{len(r.imp)}", "importance.shap\tnot implemented"]
    for f, (pm, ab) in sorted(r.imp.items(), key=lambda kv: -kv[1][0])[:8]:
        lines.append("\t".join(("importance", f, f"{pm:.4f}", f"{ab:.4f}")))
    for g, d in sorted(r.groups.items(), key=lambda kv: -kv[1]):
        lines.append("\t".join(("group_importance", g, f"{d:.4f}")))

    # --- sections 94-96: the screened broker tables --------------------------
    # The SUPPRESSED count is carried deliberately. "10 candidates, 0 above the floor" is
    # the finding for section 96, and a board that renders an empty panel instead reads as
    # "nothing was looked for" rather than "everything looked at was too thin to report".
    lines.append(f"pair_min_share\t{PAIR_MIN_SHARE}")
    if r.obs:
        shares = sorted(o.pair_share for o in r.obs)
        lines += [
            f"pair_share.median\t{history.percentile(shares, 50):.6g}",
            f"pair_share.p90\t{history.percentile(shares, 90):.6g}",
            f"pair_share.max\t{shares[-1]:.6g}",
            f"pair_share.n\t{len(shares)}",
        ]
    for title, table, sc in r.edge_tables:
        live = [e for e in table if not e.suppressed]
        lines.append("\t".join(("edge_table", _cell(title), str(len(table)), str(len(live)),
                                f"{sc.best:.4f}", f"{sc.p_value:.4f}", str(sc.groups),
                                str(sc.draws), f"{sc.p_matched:.4f}")))
        for e in live[:8]:
            d = tuple(e.decay) + (0.0,) * 3
            lines.append("\t".join(("edge", _cell(title), _cell(e.key), str(e.n),
                                    f"{e.positive_rate:.4f}", f"{e.mean:.4f}", f"{e.median:.4f}",
                                    f"{e.consistency:.4f}", f"{e.lead_time:.4f}",
                                    str(e.symbols), f"{e.top_symbol_share:.4f}",
                                    f"{d[0]:.4f}", f"{d[1]:.4f}", f"{d[2]:.4f}")))

    # --- section 97: outcome labels, per family ------------------------------
    for nm, pf in [("baseline (buy-and-hold)", r.baseline)] + [(f[0], f[2]) for f in r.fams]:
        if not pf.usable:
            continue
        for lab, cnt in pf.labels:
            lines.append("\t".join(("label", _cell(nm), _cell(lab), str(cnt))))

    # --- section 114 --------------------------------------------------------
    lines += [
        f"calibration.calibrated\t{'yes' if r.cal.calibrated else 'no'}",
        f"calibration.slope\t{r.cal.slope:.4f}",
        f"calibration.mean_abs_error\t{r.cal.mean_abs_error:.4f}",
        f"calibration.note\t{_cell(r.cal.note)}",
        f"calibration.buckets\t{len(r.cal.buckets)}",
    ]
    if r.cal.buckets:
        lines += [f"calibration.bottom_bucket_win\t{r.cal.buckets[0][1]:.4f}",
                  f"calibration.top_bucket_win\t{r.cal.buckets[-1][1]:.4f}"]

    # The BOARD'S OWN score, which is a different number from the one above and used to be
    # reported as if the one above had measured it. Both travel, under names that say which
    # is which, so no renderer can attribute one score's result to the other again.
    lines += [
        f"swing_score.n\t{r.swing.n}",
        f"swing_score.ic\t{r.swing.ic:.4f}",
        f"swing_score.spread_win\t{r.swing.spread_win:.4f}",
        f"swing_score.spread_excess\t{r.swing.spread_excess:.4f}",
        f"swing_score.buckets\t{len(r.swing.buckets)}",
        f"swing_score.ranks\t{r.swing.ranks}",
        f"swing_score.note\t{_cell(r.swing.note)}",
    ]
    if r.swing.buckets:
        lines += [f"swing_score.bottom_bucket_win\t{r.swing.buckets[0][1]:.4f}",
                  f"swing_score.top_bucket_win\t{r.swing.buckets[-1][1]:.4f}",
                  f"swing_score.bottom_bucket_excess\t{r.swing.buckets[0][2]:.4f}",
                  f"swing_score.top_bucket_excess\t{r.swing.buckets[-1][2]:.4f}"]

    # --- the fragility of anything that cleared the bar ----------------------
    lines += [f"round_trip_low\t{ROUND_TRIP[0]}", f"round_trip_high\t{ROUND_TRIP[1]}"]
    for name, phs in r.phases:
        for ph in phs:
            lines.append("\t".join(("phase", _cell(name), str(ph.phase), str(ph.n),
                                    f"{ph.excess:.4f}", f"{ph.p_value:.4f}")))
    lines += [f"error\t{_cell(e)}" for e in r.errors]

    p = path or summary_path()
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return p


def read_summary(path: str | None = None) -> Summary | None:
    """Parse the summary back. **None when the backtest has never been run.**

    None is a real answer and every caller has to print it as one. A board that just
    omits its backtest sections reads as "this was considered and found irrelevant",
    which is the exact opposite of what an unrun study means.
    """
    p = path or summary_path()
    if not os.path.isfile(p):
        return None
    meta: dict[str, str] = {}
    fams: list[FamilyRow] = []
    folds: list[tuple] = []
    stab: list[tuple] = []
    imp: list[tuple] = []
    grp: list[tuple] = []
    etabs: list[EdgeTable] = []
    erows: list[EdgeRow] = []
    labs: list[tuple] = []
    phs: list[tuple] = []
    surv: list[tuple] = []
    with open(p, "r", encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            ln = ln.rstrip("\r\n")
            if not ln or ln.startswith("#"):
                continue
            c = ln.split("\t")
            k = c[0]
            if k == "family" and len(c) >= 12:
                # Trailing cells were added by later audits; a digest written before one
                # parses back with its defaults rather than not at all.
                fams.append(FamilyRow(c[1], c[2], _i(c[3]), _f(c[4]), _f(c[5]), _f(c[6]),
                                      _f(c[7]), _f(c[8]), _i(c[9]), _i(c[10]), c[11],
                                      _f(c[12]) if len(c) > 12 else 0.0,
                                      _f(c[13]) if len(c) > 13 else 0.0,
                                      _i(c[14]) if len(c) > 14 else 0,
                                      _f(c[15]) if len(c) > 15 else 0.0,
                                      _f(c[16]) if len(c) > 16 else 0.0,
                                      _f(c[17]) if len(c) > 17 else 0.0,
                                      _f(c[18]) if len(c) > 18 else 1.0))
            elif k == "fold" and len(c) >= 9:
                folds.append((c[1], c[2], c[3], _i(c[4]), _i(c[5]), _f(c[6]), _f(c[7]), _f(c[8])))
            elif k == "stability" and len(c) >= 7:
                stab.append((c[1], _f(c[2]), _f(c[3]), _f(c[4]), _f(c[5]), _f(c[6])))
            elif k == "importance" and len(c) >= 4:
                imp.append((c[1], _f(c[2]), _f(c[3])))
            elif k == "group_importance" and len(c) >= 3:
                grp.append((c[1], _f(c[2])))
            elif k == "edge_table" and len(c) >= 7:
                etabs.append(EdgeTable(c[1], _i(c[2]), _i(c[3]), _f(c[4]), _f(c[5]), _i(c[6]),
                                       _i(c[7]) if len(c) > 7 else 0,
                                       _f(c[8]) if len(c) > 8 else 1.0))
            elif k == "edge" and len(c) >= 14:
                erows.append(EdgeRow(c[1], c[2], _i(c[3]), _f(c[4]), _f(c[5]), _f(c[6]),
                                     _f(c[7]), _f(c[8]), _i(c[9]), _f(c[10]),
                                     (_f(c[11]), _f(c[12]), _f(c[13]))))
            elif k == "label" and len(c) >= 4:
                labs.append((c[1], c[2], _i(c[3])))
            elif k == "phase" and len(c) >= 6:
                phs.append((c[1], _i(c[2]), _i(c[3]), _f(c[4]), _f(c[5])))
            elif k == "survivorship" and len(c) >= 7:
                surv.append((c[1], _i(c[2]), _i(c[3]), _f(c[4]), _f(c[5]), _f(c[6])))
            else:
                meta[k] = c[1] if len(c) > 1 else ""
    return Summary(meta, tuple(fams), tuple(folds), tuple(stab), tuple(imp), tuple(grp),
                   tuple(etabs), tuple(erows), tuple(labs), tuple(phs), tuple(surv))


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

    # -- the control must sample WITHOUT replacement, and the bar must be resolvable --
    # Degenerate on purpose: if the family IS the whole pool then a without-replacement
    # draw has no freedom at all and must reproduce the family exactly on every draw.
    # With replacement it cannot -- that is what the audited version did, and it made the
    # null ~30% too wide at the coverage these families actually run at.
    fake = [Obs("A", "2024-01-01", (), (), (0.0, 0.0, x), 0.0, 0.0, 0, 0, 0, 0.0, 0,
                (0, 0), 0.0, None) for x in (-0.10, 0.00, 0.25)]
    c_all = control(fake, {"2024-01-01": fake}, draws=200)
    assert abs(c_all.mean - _mean([o.ret for o in fake])) < 1e-12 and c_all.p_value == 1.0, (
        f"control() is sampling WITH replacement: an exhaustive draw returned mean "
        f"{c_all.mean} / p {c_all.p_value} instead of the family's own mean and p=1.0")
    # ...and a strict subset must still be free: the middle of three is beaten by 2 of 3.
    c_one = control(fake[1:2], {"2024-01-01": fake}, draws=2000)
    assert 0.60 < c_one.p_value < 0.74, f"a 1-of-3 draw is not uniform, got p={c_one.p_value}"
    # A p-value quantised to 1/draws must be finer than the bar it is compared against,
    # or the corrected threshold is reachable only at exactly zero.
    # The bar is over 2N: every family is judged on both tails, so N rules is 2N tests.
    assert 1.0 / CONTROL_DRAWS <= 0.05 / (2 * len(families())) / 3, (
        f"CONTROL_DRAWS={CONTROL_DRAWS} cannot resolve the Bonferroni bar "
        f"{0.05 / (2 * len(families())):.5f} -- raise it")

    # -- the leave-family-out excess is the identity excess/(1 - coverage), per date --
    # Checked here rather than on the study because the aggregate mixes dates with
    # different coverages and the identity only holds date by date.
    fdm = {"2024-01-01": (0.0, 0.0, _mean([o.ret for o in fake]))}
    one = perf("member", fake[:1], fdm, "long", {"2024-01-01": fake})
    assert abs(one.excess_lfo - (fake[0].ret - _mean([o.ret for o in fake[1:]]))) < 1e-12, (
        f"excess vs non-members is {one.excess_lfo}, not the member minus the rest")
    assert abs(one.excess_lfo * (1.0 - one.coverage) - one.excess_mean) < 1e-12, (
        "the leave-one-out excess does not equal excess / (1 - coverage) on a single date")

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

    # ...and the guard is MUTATION-TESTED, because a guard that passes on correct code
    # is indistinguishable from a guard that passes on everything -- which is what this
    # one did for two of the three leak classes it was believed to cover. The smallest
    # symbol in the universe keeps the two extra rebuilds cheap.
    small = syms[-1]
    for nm in _MUTATIONS:
        t = time.time()
        miss = _mutation_survives(nm, small, "2025-06-30")
        assert not miss, miss
        print(f"  mutation {nm} -> CAUGHT on {small} ({time.time() - t:.0f}s)")

    # -- the study ------------------------------------------------------------
    print(f"running the study over {len(syms)} symbols, {START} -> {END} ...")
    r = run(syms)

    assert r.obs, "no observations at all"
    assert 0.0 <= r.baseline.win_rate <= 1.0, "a win rate outside [0, 1]"
    assert r.baseline.n >= 1000, f"baseline only has {r.baseline.n} observations"

    # The baseline IS the universe, so its date-demeaned excess must vanish. If
    # this drifts, the demean is being taken over the wrong set and every EXCESS
    # column in the report is quietly wrong.
    assert abs(r.baseline.excess_mean) < 1e-9, (
        f"the universe does not demean to zero ({r.baseline.excess_mean}) -- the control is broken")
    # ...and so must its leave-family-out excess: buy-and-hold has no non-members.
    assert abs(r.baseline.excess_lfo) < 1e-9, (
        f"the baseline's leave-family-out excess is {r.baseline.excess_lfo}, not zero")
    assert r.swing.note, "the board's own swing score must always carry a measured verdict"
    assert r.baseline.coverage > 0.99, "buy-and-hold must hold the whole universe by definition"
    per_date = Counter(o.date for o in r.obs)
    assert _mean([float(v) for v in per_date.values()]) >= 10, (
        f"only {_mean([float(v) for v in per_date.values()]):.1f} stocks per decision date -- "
        "the cross-section is too thin to demean against")

    years = {o.year for o in r.obs}
    assert set(r.baseline_years) == years, (
        f"the per-year table misses {years - set(r.baseline_years)}")
    for name, side, p, c, ys, cl in r.fams:
        assert 0.0 <= p.win_rate <= 1.0, f"{name}: win rate {p.win_rate}"
        if p.usable:
            assert c.draws > 0, f"{name}: the random control is empty"
            assert set(ys) <= years and ys, f"{name}: per-year table broken"
            # The clustered test must actually have run, and it must be measuring the
            # same excess the control is -- if these two ever disagree, one of them is
            # demeaning against a different universe.
            assert cl.symbols >= 2, f"{name}: the symbol bootstrap saw {cl.symbols} symbols"
            assert abs(cl.excess - p.excess_mean) < 1e-9, (
                f"{name}: clustered excess {cl.excess} != reported excess {p.excess_mean}")
            assert cl.lo <= cl.excess <= cl.hi or cl.symbols < 5, (
                f"{name}: the bootstrap interval [{cl.lo}, {cl.hi}] excludes its own estimate")

    # The universe filter, measured. All three groups must be non-empty or the
    # SELECTION block prints a comparison against nothing.
    assert len(r.surv) == 3, f"survivorship returned {len(r.surv)} groups"
    for sv in r.surv:
        assert sv.symbols > 0 and sv.n > 0, f"survivorship group {sv.label!r} is empty"
    for _, table, sc in r.edge_tables:
        for e in table:
            if e.n < MIN_OBS:
                assert e.suppressed and e.positive_rate == 0.0, (
                    f"{e.key}: a rate was reported off {e.n} occurrences")
        assert 0.0 <= sc.p_value <= 1.0, "the best-of-N control returned a non-probability"

    assert r.cal.note, "section 114 must always state a calibration verdict"
    if not r.cal.calibrated:
        assert "NOT CALIBRATED" in r.cal.note, "an uncalibrated score must say so in words"

    text = report(r)

    # Nothing under the floor may print a percentage anywhere in the report.
    for name, side, p, _, _, _cl in r.fams:
        if 0 < p.n < MIN_OBS:
            line = next(ln for ln in text.split("\n")
                        if ln.strip().startswith(name) and "SUPPRESSED" in ln)
            assert "%" not in line, f"{name}: a rate leaked into the report off {p.n} observations"
    for it in r.inter:
        if it.suppressed and it.n:
            line = next(ln for ln in text.split("\n") if it.name in ln and "SUPPRESSED" in ln)
            assert "%" not in line, f"{it.name}: a rate leaked off {it.n} observations"

    print(text)

    path = os.path.join(loader.OUT, "backtest.txt")
    os.makedirs(loader.OUT, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print(f"\nwritten: {path}")

    # -- the board's copy, and proof it survives the round trip ---------------
    spath = write_summary(r, leakage=(probe_sym, "2025-06-30", n_cmp), ex_dates=fakes)
    s = read_summary()
    assert s is not None, "the summary was written and then could not be read back"
    assert len(s.families) == len(r.fams), f"{len(s.families)} families read back of {len(r.fams)}"
    bonf, tested = bonferroni(r)
    assert abs(bonf - 0.05 / (2 * tested)) < 1e-12, (
        f"the corrected bar {bonf} is not 0.05 over {2 * tested} two-sided tests")
    for (name, side, p, c, ys, cl), got in zip(r.fams, s.families):
        want, pos, ny = verdict(p, c, ys, bonf, cl)
        assert got.name == name and got.verdict == want, f"{name}: {got.verdict!r} != {want!r}"
        assert (got.years_positive, got.years) == (pos, ny), name
        # Every strong verdict must carry the clustered gate that earned it, in the
        # digest as well as in the report -- a board that prints EDGE without it is
        # back to quoting a p-value that assumed 1,472 independent rows.
        if want in ("EDGE", "NEGATIVE"):
            assert got.cluster_symbols and not got.clustered and got.cluster_p < CLUSTER_P, (
                f"{name}: {want} written without a separating clustered interval")
        if got.usable:
            assert abs(got.excess - p.excess_mean) < 5e-5, name
            assert got.cluster_symbols == cl.symbols, name
            assert abs(got.cluster_p - cl.p_value) < 5e-5, name
        else:
            # A rate off fewer than MIN_OBS occurrences must not survive as a number.
            assert got.win == 0.0 and got.excess == 0.0, f"{name}: a suppressed rate was written"
    # Every key section 93/98/114 renders from must be present, or the board shows a
    # blank cell that reads as zero -- which is the failure this artefact exists to stop.
    for key in ("run_date", "universe", "observations", "decision_dates", "horizon",
                "target", "stop", "families_tested", "bonferroni_p",
                "baseline.n", "baseline.win_rate", "baseline.mean", "baseline.median",
                "baseline.profit_factor", "baseline.max_drawdown",
                "calibration.calibrated", "calibration.note"):
        assert s.get(key), f"the summary is missing {key}"
    assert s.num("baseline.n") == r.baseline.n
    assert s.family("zone_buy") is not None, "the board's own signal is not in the summary"

    # Sections 94-96: the SUPPRESSED count has to survive, because for section 96 it is the
    # entire finding. A table that round-trips as "0 candidates" instead of "10 candidates,
    # 0 above the floor" turns a measured negative into a blank.
    assert len(s.edge_tables) == len(r.edge_tables), "an edge table was lost"
    for (title, table, sc), got in zip(r.edge_tables, s.edge_tables):
        assert got.title == title and got.candidates == len(table), title
        assert got.live == sum(1 for e in table if not e.suppressed), title
        assert abs(got.p_value - sc.p_value) < 5e-5, title
        n = int(title.split()[0])
        assert s.edge_table(n) is got, f"section {n} is not reachable by its number"
        # Only rows above the floor may carry numbers at all.
        assert len(s.edges_in(n)) == min(8, got.live), title
    for e in s.edges:
        assert e.n >= MIN_OBS, f"{e.key}: a suppressed row reached the summary"
        assert 0.0 <= e.top_symbol_share <= 1.0 and e.symbols >= 1, e.key

    # Section 97: labels must sum back to each family's own n, or the breakdown on the board
    # is of a different sample than the row above it.
    assert s.labels, "no outcome labels were written"
    assert len(s.surv) == 3, f"the survivorship table did not round-trip ({len(s.surv)} rows)"
    for nm, pf in [("baseline (buy-and-hold)", r.baseline)] + [(f[0], f[2]) for f in r.fams]:
        if not pf.usable:
            continue
        got_labs = s.labels_for(nm)
        assert got_labs == pf.labels, nm
        assert sum(v for _, v in got_labs) == pf.n, f"{nm}: labels sum to != n"
    print(f"written: {spath} ({len(s.families)} families, {len(s.folds)} folds, "
          f"{len(s.stability)} stability rows, {len(s.importance)} importance rows, "
          f"{len(s.edge_tables)} edge tables / {len(s.edges)} rows above the floor, "
          f"{len(s.labels)} label counts)")


if __name__ == "__main__":
    _demo()
