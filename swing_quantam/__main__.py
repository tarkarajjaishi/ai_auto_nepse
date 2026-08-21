"""Run every analysis module over the real archive and write the board — the integration layer.

WHY this module exists, and why it is the only one allowed to be boring:

Every other module in this package computes one slice of ``swing_floorsheet_quantam.md``
and knows nothing about symbols, files or the passage of time. This one is the single
place that decides *which* sessions get loaded, loads them **once**, and hands the same
list to all of them. That is not a style preference — it is the entire cost of the build.
A 593-symbol run that re-read the floorsheet per section would read the archive sixty
times over; ``loader.load_last(sym, HISTORY)`` is called exactly once per symbol here and
every section is a pure function of that list.

Four consequences of "load once" that are easy to get wrong:

* ``hist.daily()`` does its own I/O. Calling it would double the read cost, so
  :func:`_daystats` assembles the identical ``DayStat`` list from the sessions we already
  hold, out of ``history``'s own public pieces. :func:`_demo` asserts the two agree —
  if ``hist.daily`` ever changes shape, the self-check fails instead of the board
  quietly diverging from the module it is supposed to be reporting.
* The expensive shared quantity is not the file read, it is the per-session per-broker
  aggregate. ``flow.all_series()`` builds every broker's daily series in one pass, so
  ``build_symbol`` calls it once and takes ``flow.stock_days`` and section 68's
  ``hist.broker_flow_series`` out of that one result; ``flow.ranking`` is called twice,
  not five times. Those three shortcuts alone took the worst symbol from 11.2 s to 5.8 s,
  and ``_demo`` asserts each still equals the function it stands in for.
* The market-wide functions are deliberately NOT called per symbol.
  ``hist.market_flow``/``sector_flow`` (spec 69-71) and ``network.broker_totals`` /
  ``affinity`` / ``stock_rotation`` (spec 38-39) all take a *symbol list*; invoking them
  inside one symbol's build turns it into a full market scan. Those sections appear in
  the output with an explicit "not computed" row and the reason, rather than silently
  missing.
* ``--upto`` is threaded into the single ``load_last`` call and nothing else reads the
  filesystem, so point-in-time is enforced in one place instead of sixty. ``zones`` and
  ``timeframes`` are handed ``history=ses`` for the same reason.

MEASURED COST (this machine, warm page cache, HISTORY=120, sections 4-92 all live):
``python -m swing_quantam --limit 10`` takes **11 s (1.1 s per symbol)**. The whole
archive — 593 symbols, of which 481 build and 112 are too thin — takes **1,339 s, i.e.
22 minutes at 2.26 s per symbol attempted / 2.78 s per symbol built**. Do not size a run
off the ten-symbol figure: it is optimistic because cost tracks TRADE count, not symbol
count, and the tail is heavy — the median symbol finishes in ~2 s while AHPC's 62,627
trades take 5.8 s and the worst take 15 s. Reading the 120 floorsheet files is only
~0.3 s of any of that; re-aggregating trades per broker is the real cost, which is why
the sharing above matters and why re-reading per section would not merely be slower, it
would be unusable.

Output goes to ``Master_data/swing_quantam/`` via :mod:`swing_quantam.store` — one
``<SYMBOL>.txt`` per symbol plus ``board.txt``. Plain ``.txt``, no database, and the
board's ``date`` column is mandatory and last so the staleness check can answer
"I cannot tell" instead of "not stale".

Honesty rules carried through from the spec (sections 2, 107, 108): a score always
travels with its component reasons, contradictions go in ``warnings`` and are never
hidden behind the verdict, and a proxy is labelled a proxy. Nothing here interprets —
it reports what the modules computed.
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback

# ``history`` is aliased because ``history`` is also the name of build_symbol's
# session-count parameter, and the module is used all through that function.
from . import brokers, features, flow, loader, network, store, structure
from . import history as hist
from .store import Row, Section

# Sections 74-92 (multi-timeframe alignment and the zone/score engine) live in two
# sibling modules that may not exist yet. Absent, the build still produces sections
# 4-73 and says "NOT SCORED" rather than inventing a number.
try:  # pragma: no cover - depends on which modules have landed
    from . import timeframes  # type: ignore
except ImportError:
    timeframes = None  # type: ignore
try:  # pragma: no cover
    from . import zones  # type: ignore
except ImportError:
    zones = None  # type: ignore


#: Below this many sessions the build is refused. 30 is ``max(loader.WINDOWS)``: with
#: fewer sessions the "30D" column is silently a shorter window, every cross-window
#: comparison (acceleration, reversal, breadth trend, concentration dynamics) degenerates
#: into comparing a window against itself, and the output reads as real analysis of a
#: stock that has barely traded. Refusing is the honest answer.
MIN_SESSIONS = max(loader.WINDOWS)

#: How much history to load per symbol. The decision windows only need 30; the historical
#: layer (spec 50-73) wants at least ``min_obs=60`` observations before it will emit a
#: z-score or a regime, so 120 gives every one of those functions room to answer while
#: keeping the per-symbol read count bounded.
HISTORY = 120

#: How many sessions of history the anomaly baseline and the "previous window" comparisons
#: need before sections 34/35/43 can say anything. Two full 30D windows.
PREV_WINDOW = MIN_SESSIONS

#: The score at which the section 61/62 proxies call a day flagged. Not chosen here — it is
#: ``hist.absorption_like``/``exhaustion_like``'s own default ``threshold``, restated so the
#: "days scored >= N" row cannot drift away from the series it is counting.
PROXY_FLAG = 60.0

_SCALAR = (str, int, float, bool)


# ── rendering helpers ──────────────────────────────────────────────────────────────────
#
# Every section is "take a NamedTuple the module returned and print its fields". These
# three helpers are that, so a section is one line and adding a metric upstream shows up
# here for free.


def _rows(obj, prefix: str = "", only: tuple[str, ...] = ()) -> list[Row]:
    """Flatten a NamedTuple to Rows, recursing into nested NamedTuples.

    Containers (the per-broker dicts, the full daily series) are skipped on purpose:
    they are not metrics and dumping them would bury the ones that are. Where a
    container matters it gets an explicit hand-written row in its section.
    """
    out: list[Row] = []
    for f in (only or obj._fields):
        v = getattr(obj, f, None)
        name = prefix + f.replace("_", " ")
        if hasattr(v, "_fields"):
            out += _rows(v, name + " ")
        elif v is None or isinstance(v, _SCALAR):
            out.append(Row(name, v))
    return out


def _by_window(make, only: tuple[str, ...] = ()) -> list[Row]:
    """``make(w)`` for every decision window, rows prefixed ``3D ``/``7D ``/…"""
    out: list[Row] = []
    for w in loader.WINDOWS:
        o = make(w)
        if o is not None:
            out += _rows(o, prefix=f"{w}D ", only=only)
    return out


def _top_rows(items, only: tuple[str, ...], prefix: str = "#") -> list[Row]:
    """Rank a handful of per-broker/per-pair records into numbered row groups."""
    out: list[Row] = []
    for i, o in enumerate(items, 1):
        out += _rows(o, prefix=f"{prefix}{i} ", only=only)
    return out


def _seq(metric: str, values, limit: int = 8, note: str = "") -> Row | None:
    """One row for a short sequence. None when there is nothing, so ``add`` drops it."""
    if not values:
        return None
    return Row(metric, ", ".join(store._fmt(v) for v in list(values)[:limit]), note)


def _daystats(sessions) -> list[hist.DayStat]:
    """``hist.daily()`` for sessions we already hold — see the module docstring.

    Assembled from ``history``'s own public functions, not reimplemented; ``_demo``
    asserts it is byte-identical to ``hist.daily`` on the real archive.
    """
    out: list[hist.DayStat] = []
    for s in sessions:
        f = brokers.stock_flow(brokers.day(s))
        qtys = sorted(t.quantity for t in s.trades)
        out.append(
            hist.DayStat(
                date=s.date,
                volume=f.volume,
                turnover=f.turnover,
                trades=f.trades,
                vwap=s.vwap,
                brokers=f.brokers,
                net_buyers=f.net_buyers,
                net_sellers=f.net_sellers,
                avg_trade=f.volume / f.trades if f.trades else 0.0,
                median_trade=hist.percentile(qtys, 50),
                flow_quality=f.flow_quality,
                top_buyer_share=f.top_buyer_share,
                top_seller_share=f.top_seller_share,
                tilt=f.top_buyer_share - f.top_seller_share,
                concentration=f.top_buyer_share + f.top_seller_share,
                profile=hist.price_profile(s),
            )
        )
    return out


#: Daily metrics the historical layer (spec 50-52) is built over.
_HIST_METRICS = (
    ("volume", lambda d: float(d.volume)),
    ("turnover", lambda d: d.turnover),
    ("trades", lambda d: float(d.trades)),
    ("vwap", lambda d: d.vwap),
    ("tilt", lambda d: d.tilt),
    ("concentration", lambda d: d.concentration),
    ("flow quality", lambda d: d.flow_quality),
)

_NOT_COMPUTED = "market-wide: takes a symbol list, would turn one symbol's build into a full market scan"


# ── the scoring layer (sections 74-92) ─────────────────────────────────────────────────


def _zone(label, z) -> list[Row]:
    """A ``zones.Zone`` as two STABLE metric names.

    The engine's own zone name is dynamic prose ("Partial Profit Zone"); keying the board
    off it would break the moment that string is reworded. The name and the basis travel
    as notes instead, so the reason a level exists is never separated from the level.
    """
    if z is None:
        return []
    tail = f" [{z.open_ended}]" if getattr(z, "open_ended", "") else ""
    return [Row(f"{label} low", z.low, z.name), Row(f"{label} high", z.high, z.basis + tail)]


def _score(s) -> list[Row]:
    """A ``zones.Score`` with every component that produced it — spec section 105."""
    rows = [Row("score", s.score), Row("weights are provisional", s.weights_are_provisional)]
    for c in s.components:
        rows.append(Row(c.name, c.value, c.note))
        rows.append(Row(f"{c.name} contribution", c.contribution, f"weight {c.weight}"))
    return rows


def _reasons(rs) -> list[Row]:
    """``timeframes.Reason`` records — the weighted components behind a score.

    Spec section 105: a score is never shown without the parts that made it.
    """
    return [Row(r.name, r.contribution, f"value {r.value:.4g}, normalised {r.normalised:.4g}, "
                                        f"weight {r.weight:g}") for r in rs]


def _optional(mod, sessions, days):
    """Call a sibling module's ``analyse`` if it has landed. Never fabricates.

    Used only for ``timeframes``, whose API does not exist yet; the call shape is the
    house convention (``structure.analyse(sessions, …)``). If the module is there but
    does not answer to it, that is reported as a warning naming the exception — a
    scoring module that silently returns nothing is exactly the "quietly reports
    success" failure this build refuses to have.
    """
    if mod is None:
        return None, None
    fn = getattr(mod, "analyse", None)
    if fn is None:
        return None, f"{mod.__name__} has no analyse(); sections not built"
    for args in ((sessions,), (sessions, days)):
        try:
            return fn(*args), None
        except TypeError:
            continue
        except Exception as exc:  # the module is there but blew up — say so, loudly
            return None, f"{mod.__name__}.analyse failed: {type(exc).__name__}: {exc}"
    return None, f"{mod.__name__}.analyse signature not understood by the builder"


# ── the build ──────────────────────────────────────────────────────────────────────────


def build_symbol(symbol: str, upto: str | None = None, history: int = HISTORY) -> store.Detail | None:
    """Assemble every spec section for one symbol. None when there is too little history.

    ``upto`` is the point-in-time guard and is the only date this function trusts;
    ``history`` is how many sessions of context to read (see :data:`HISTORY`).
    """
    symbol = symbol.upper()
    ses = loader.load_last(symbol, history, upto=upto)
    if len(ses) < MIN_SESSIONS:
        return None

    last = ses[-1]
    win = {w: ses[-w:] for w in loader.WINDOWS}
    prev = ses[-2 * PREV_WINDOW : -PREV_WINDOW]  # the window before the current 30D
    days = _daystats(ses)
    agg = {w: brokers.window(win[w]) for w in loader.WINDOWS}
    sf = {w: brokers.stock_flow(agg[w]) for w in loader.WINDOWS}
    w30 = MIN_SESSIONS
    # ONE pass for every daily flow series in the symbol. The per-session broker aggregate
    # is the expensive part and all_series already shares it, so this single call replaces
    # flow.stock_days() plus one history.broker_flow_series() per broker in section 68 —
    # each of which would otherwise re-aggregate all 120 sessions from scratch. _demo
    # asserts the two shortcuts still equal the functions they stand in for.
    fseries = flow.all_series(ses)
    sd = fseries[None]  # == flow.stock_days(ses): the stock's own daily net-flow series
    st = structure.analyse(win[w30], baseline=ses)
    pm = network.pairs(win[w30])
    bstock = network.broker_stock(win[w30])
    # ranking() re-aggregates the whole window internally, so rank each side ONCE at the
    # deepest limit any section needs and slice it — five calls became two.
    rank_acc = flow.ranking(win[w30], side="accumulation", limit=5)
    rank_dist = flow.ranking(win[w30], side="distribution", limit=5)
    # Both are read by two sections apiece (6+64, 10+65), so compute per window once.
    pstat = {w: features.price(win[w]) for w in loader.WINDOWS}
    prof = {w: features.profile(win[w]) for w in loader.WINDOWS}

    out: list[Section] = []
    warnings: list[str] = []

    def add(n: int, title: str, rows, note: str = "") -> None:
        rows = tuple(r for r in rows if r is not None)
        if rows:
            out.append(Section(n, title, rows, note))

    # ── 4: data quality ────────────────────────────────────────────────────────────────
    counts = {loader.VALID: 0, loader.WARNING: 0, loader.INVALID: 0}
    for s in ses:
        counts[s.quality.status] = counts.get(s.quality.status, 0) + 1
    seen: dict[str, int] = {}
    for s in ses:
        for w in s.quality.warnings:
            key = w.split(" ", 1)[1] if w[:1].isdigit() else w  # drop the leading count
            seen[key] = seen.get(key, 0) + 1
    q_rows = [
        Row("sessions loaded", len(ses)),
        Row("first session", ses[0].date),
        Row("last session", last.date),
        Row("status VALID", counts.get(loader.VALID, 0)),
        Row("status WARNING", counts.get(loader.WARNING, 0)),
        Row("status INVALID", counts.get(loader.INVALID, 0)),
        Row("last session status", last.quality.status),
        Row("last session score", last.quality.score),
        Row("last session rows total", last.quality.rows_total),
        Row("last session rows kept", last.quality.rows_kept),
        Row("mean quality score", sum(s.quality.score for s in ses) / len(ses)),
    ]
    q_rows += [Row(f"defect: {k}", v, "sessions affected") for k, v in sorted(seen.items())]
    add(4, "Data quality layer", q_rows,
        "Quality is per session; WARNING sessions were still used, with the defect named above.")
    if last.quality.status != loader.VALID:
        warnings.append(f"the {last.date} session is quality {last.quality.status} "
                        f"(score {last.quality.score:.1f}) — every headline number is built on it")
    dented = counts.get(loader.WARNING, 0) + counts.get(loader.INVALID, 0)
    if dented > len(ses) // 2:
        warnings.append(f"{dented} of {len(ses)} loaded sessions are WARNING/INVALID quality")

    # ── 5-10: price, volume, turnover, VWAP, executed-price profile ────────────────────
    add(5, "Basic market statistics", _rows(features.stats(last)),
        "Executed transactions only — the floorsheet records no orders, no bids, no asks.")
    add(6, "Price analysis", _by_window(lambda w: pstat[w]))
    add(7, "Volume analysis", _by_window(lambda w: features.volume(win[w])))
    add(8, "Turnover analysis", _by_window(lambda w: features.turnover(win[w])))

    vset = features.vwap_set(ses)
    top_bs = sorted(bstock.values(), key=lambda b: b.buy_qty + b.sell_qty, reverse=True)[:5]
    v_rows = [Row(f"{k} vwap" if k != "sessions" else "sessions", v) for k, v in vset.items()]
    v_rows.append(Row("last trade vs 30D vwap",
                      features.vwap_distance(last.trades[-1].rate, vset.get("30d", 0.0)),
                      "normalised distance (price - vwap) / vwap"))
    v_rows += _top_rows(top_bs, ("broker", "buy_vwap", "sell_vwap", "buy_qty", "sell_qty"), prefix="broker #")
    add(9, "VWAP analysis", v_rows, "Broker VWAPs are over the 30D window, by gross activity.")

    p_rows = _by_window(lambda w: prof[w])
    p_rows.append(_seq("30D hvn", prof[w30].hvn, note="high-volume nodes"))
    p_rows.append(_seq("30D lvn", prof[w30].lvn, note="low-volume nodes"))
    add(10, "Volume-at-price / executed price profile", p_rows,
        "The profile is of EXECUTED prices only — it is not an order-book depth profile.")

    # ── 11-16: broker aggregates, net flow, normalisation, imbalance, flow quality ─────
    buyers = sorted(agg[w30].values(), key=lambda b: b.buy_qty, reverse=True)[:5]
    sellers = sorted(agg[w30].values(), key=lambda b: b.sell_qty, reverse=True)[:5]
    b_rows: list[Row] = []
    s_rows: list[Row] = []
    for w in loader.WINDOWS:
        b_rows += [Row(f"{w}D top buyer", sf[w].top_buyer),
                   Row(f"{w}D top buyer share", sf[w].top_buyer_share)]
        s_rows += [Row(f"{w}D top seller", sf[w].top_seller),
                   Row(f"{w}D top seller share", sf[w].top_seller_share)]
    b_rows += _top_rows(buyers, ("broker", "buy_qty", "buy_amt", "buy_trades", "buy_max"), prefix="30D buyer #")
    s_rows += _top_rows(sellers, ("broker", "sell_qty", "sell_amt", "sell_trades", "sell_max"), prefix="30D seller #")
    add(11, "Buyer analysis", b_rows, "Broker IDs, not client IDs — a broker is not an investor.")
    add(12, "Seller analysis", s_rows, "Broker IDs, not client IDs — a broker is not an investor.")

    add(13, "Net broker flow",
        _by_window(lambda w: sf[w], only=("volume", "turnover", "trades", "brokers", "gross_qty", "net_qty",
                                          "net_buyers", "net_sellers", "neutral")))

    n_rows: list[Row] = []
    for r in rank_acc[:3]:
        bd = agg[w30].get(r.broker)
        if bd:
            n_rows += [Row(f"net buyer {r.broker} {k}", v) for k, v in brokers.shares(bd, sf[w30]).items()]
    for r in rank_dist[:3]:
        bd = agg[w30].get(r.broker)
        if bd:
            n_rows += [Row(f"net seller {r.broker} {k}", v) for k, v in brokers.shares(bd, sf[w30]).items()]
    add(14, "Flow normalisation", n_rows,
        "Every figure is a share of THIS stock's own 30D volume/turnover — raw quantities are "
        "not comparable across stocks.")

    i_rows: list[Row] = []
    for w in loader.WINDOWS:
        f_ = sf[w]
        i_rows += [
            Row(f"{w}D net qty", f_.net_qty),
            Row(f"{w}D gross qty", f_.gross_qty),
            Row(f"{w}D imbalance", f_.net_qty / f_.gross_qty if f_.gross_qty else 0.0,
                "net / gross quantity"),
            Row(f"{w}D buyer-seller ratio",
                f_.net_buyers / f_.net_sellers if f_.net_sellers else 0.0),
        ]
    add(15, "Buy/sell imbalance", i_rows)
    add(16, "Flow quality / net-to-gross",
        [Row(f"{w}D flow quality", sf[w].flow_quality) for w in loader.WINDOWS],
        "1.0 means every share that changed hands moved in one direction; near 0 means churn.")

    # ── 17-21: accumulation, distribution, persistence, acceleration, reversal ─────────
    add(17, "Accumulation", _by_window(lambda w: flow.accumulation(sd[-w:]),
                                       only=("label", "days", "total_days", "qty", "amt", "pct", "intensity",
                                             "peak", "consistency", "streak", "max_streak", "change",
                                             "accel", "decel", "reversal", "breadth", "quality"))
        + _top_rows(rank_acc,
                    ("broker", "net_share", "net_qty", "days", "positive_days", "streak", "phase", "persistence"),
                    prefix="30D accumulator #"),
        "\"Net buying\" is a fact; \"accumulation\" is the inference this section names, not proves.")
    add(18, "Distribution", _by_window(lambda w: flow.distribution(sd[-w:]),
                                       only=("label", "days", "total_days", "qty", "amt", "pct", "intensity",
                                             "peak", "consistency", "streak", "max_streak", "change",
                                             "accel", "decel", "reversal", "breadth", "quality"))
        + _top_rows(rank_dist,
                    ("broker", "net_share", "net_qty", "days", "positive_days", "streak", "phase", "persistence"),
                    prefix="30D distributor #"))
    add(19, "Persistence score", _by_window(lambda w: flow.persistence(sd[-w:], window=w)))

    acc = flow.acceleration(sd)
    add(20, "Flow acceleration", _rows(acc))
    rev = flow.reversal(sd)
    add(21, "Flow reversal", _rows(rev))

    # ── 22-32: consensus, breadth, concentration, trade structure ──────────────────────
    add(22, "Broker consensus", _by_window(lambda w: st.consensus[w]))
    add(23, "Accumulation / distribution breadth",
        _by_window(lambda w: st.breadth[w])
        + _rows(st.breadth_trend)
        + [_seq("trend series", st.breadth_trend.series)])
    add(24, "Concentration", _by_window(lambda w: st.concentration[w]),
        "HHI is computed over the WINDOW aggregate, never averaged from daily HHIs.")
    add(25, "Concentration dynamics",
        _rows(st.conc_dynamics) + [_seq("hhi series", st.conc_dynamics.hhi_series)],
        "Read 30D -> 15D -> 7D -> 3D: the series runs from the longest window to the newest.")
    add(26, "Pareto analysis", _by_window(lambda w: st.pareto[w]))

    add(27, "Large-trade analysis",
        _rows(st.thresholds, prefix="threshold ")
        + _by_window(lambda w: st.large_value[w],
                     only=("threshold", "trades", "trade_pct", "volume", "volume_pct", "turnover",
                           "turnover_pct", "largest_qty", "largest_amt", "top10_pct", "top50_pct",
                           "top100_pct", "buyers", "sellers", "vwap", "vwap_premium", "rate_p10", "rate_p90")),
        "Cutoffs are this stock's own history (rupee basis). The share-count basis is an inverse-price "
        "proxy and is reported under section 29, not used for the cutoff.")
    add(28, "Large-trade conviction",
        _by_window(lambda w: st.large_value[w],
                   only=("trades", "volume_pct", "turnover_pct", "net_qty", "net_share", "persistence",
                         "buyer_hhi", "seller_hhi", "top_buyer", "top_buyer_share", "top_seller",
                         "top_seller_share"))
        + [Row("large emergence", st.frag_change.large_emergence),
           Row("large disappearance", st.frag_change.large_disappearance)])

    add(29, "Trade-size distribution",
        _by_window(lambda w: st.sizes[w])
        + [Row(f"{w}D shares-basis {f}", getattr(st.sizes_shares[w], f))
           for w in loader.WINDOWS for f in ("median", "p90", "p99", "cv")],
        "Rupee basis is the cross-sectionally safe one; the share-count figures are shown because the "
        "spec asks for them, but a share-count clip metric is price in disguise.")
    add(30, "Broker trade-size signature",
        _top_rows(sorted(st.signatures.values(), key=lambda b: b.trades, reverse=True)[:5],
                  ("broker", "trades", "median", "p90", "p99", "max", "cv", "skew", "large_ratio",
                   "small_ratio", "consistency"), prefix="broker #"))
    add(31, "Transaction fragmentation", _by_window(lambda w: st.fragmentation[w]))
    add(32, "Transaction fragmentation change",
        _rows(st.frag_change) + [_seq("series", st.frag_change.series)])

    # ── 33-49: participation, rotation, pairs, network, anomaly ────────────────────────
    add(33, "Broker participation", _rows(network.participation(win[w30])),
        "participation pct is None unless a market-wide broker universe is supplied.")
    # Sections 34/35/43 compare the current 30D window against the one before it, so they
    # need two full windows. At exactly MIN_SESSIONS there is only one; say so per section
    # rather than letting the panels vanish, which would read as "nothing to report".
    thin = [Row("computed", False, f"needs {2 * PREV_WINDOW} sessions, have {len(ses)}")]
    if prev:
        add(34, "New / returning / exiting brokers",
            _rows(network.churn(prev, win[w30], ses[: -2 * PREV_WINDOW])))
        add(35, "Broker rotation", _rows(network.rotation(prev, win[w30])))
    else:
        add(34, "New / returning / exiting brokers", thin)
        add(35, "Broker rotation", thin)
    add(36, "Broker rank stability", _rows(network.rank_stability(win[w30])))

    add(37, "Broker x stock matrix",
        _top_rows(sorted(bstock.values(), key=lambda b: abs(b.net_qty), reverse=True)[:5],
                  ("broker", "buy_qty", "sell_qty", "net_qty", "net_amt", "trades", "buy_vwap", "sell_vwap",
                   "volume_pct", "turnover_pct", "cum_net", "persistence", "consistency"), prefix="broker #"),
        "One symbol only. Affinity needs the market-wide book and is left blank here — see section 38.")
    add(38, "Broker-stock affinity", [Row("computed", False, _NOT_COMPUTED)])
    add(39, "Broker stock rotation", [Row("computed", False, _NOT_COMPUTED)])

    top_pairs = sorted(pm.values(), key=lambda p: p.qty, reverse=True)[:5]
    add(40, "Broker x broker analysis",
        [Row("pairs", len(pm)), Row("pair concentration", network.pair_concentration(pm), "HHI of pair quantity")]
        + _top_rows(top_pairs, ("buyer", "seller", "trades", "qty", "turnover", "vwap", "days",
                                "qty_share", "reciprocal_qty"), prefix="pair #"),
        "A recurring pair is a trade-count artefact as often as a relationship — reciprocity was "
        "tested in this repo and died.")
    add(41, "Broker network analysis", _rows(network.network(win[w30], pm)))
    drift = network.centrality_drift(win[w30])
    add(42, "Network centrality over time", _rows(drift))
    if prev:
        shift = network.pair_shift(network.pairs(prev), pm)
        add(43, "Broker-pair persistence",
            _rows(shift, only=("appeared", "disappeared", "continuing", "turnover_rate"))
            + _top_rows(top_pairs, ("buyer", "seller", "days", "persistence", "recurrence"), prefix="persistent #"))
    else:
        add(43, "Broker-pair persistence", thin)
    add(44, "Repeated transaction patterns",
        [Row(f"{r.kind} {r.key}", r.count, r.detail) for r in network.repeats(win[w30])[:10]])

    sq = network.sequence(last)
    add(45, "Sequence analysis",
        _rows(sq, only=("trades", "consecutive_buyer", "consecutive_seller", "buyer_switch", "seller_switch",
                        "both_switch", "longest_buyer_run", "longest_seller_run", "up_steps", "down_steps",
                        "flat_steps", "price_drift", "size_early", "size_late", "size_trend")),
        f"One session ({last.date}) only — adjacency across a day boundary is meaningless.")
    add(46, "Contract / sequence analysis",
        _rows(sq, only=("trades", "reordered", "top_bigram_count", "repeated_bigrams"))
        + [_seq("top bigram", sq.top_bigram)],
        "Order comes from the contract number, which had to be sorted — the file order is not the "
        "execution order.")
    add(47, "Same-broker side flag",
        _rows(network.self_trades(win[w30]), only=("count", "quantity", "amount", "pct_transactions",
                                                   "pct_volume", "pct_turnover", "sessions_with", "session_pct")),
        "Same broker on both sides means one member firm, not necessarily one client.")
    cyc = network.cycles(win[w30], pair_map=pm)
    add(48, "Circular-pattern candidates",
        [Row("candidates", len(cyc))]
        + [Row(f"cycle #{i}", "->".join(str(b) for b in c.brokers),
               f"len {c.length}, {c.qty_share:.4f} of volume on {c.days} days")
           for i, c in enumerate(cyc[:3], 1)],
        "CANDIDATES only. A cycle in broker IDs is not evidence of intent.")
    an = network.anomaly(last, ses[:-1])
    add(49, "Anomaly detection",
        _rows(an, only=("date", "score", "baseline_sessions"))
        + [Row(c.name, c.z, c.reason) for c in an.components]
        + [_seq("flagged", an.flagged)],
        "z-scores are against this symbol's own prior sessions, point-in-time.")

    # ── 50-73: baselines, z-scores, price-flow relationships, regimes ──────────────────
    series = {name: [pick(d) for d in days] for name, pick in _HIST_METRICS}
    base_rows: list[Row] = []
    z_rows: list[Row] = []
    pct_rows: list[Row] = []
    for name, _ in _HIST_METRICS:
        b = hist.baseline(series[name])
        if b:
            base_rows += _rows(b, prefix=f"{name} ",
                               only=("n", "mean", "median", "sd", "p10", "p50", "p90", "p99", "skew", "kurtosis"))
        p = hist.pit(series[name], len(days) - 1)
        if p:
            z_rows.append(Row(f"{name} z", p.z, f"vs {p.base.n} prior sessions"))
            pct_rows.append(Row(f"{name} percentile", p.pct, f"vs {p.base.n} prior sessions"))
        else:
            z_rows.append(Row(f"{name} z", None, "too few prior sessions"))
    add(50, "Historical statistics", base_rows, f"Over the {len(days)} loaded sessions, ending {last.date}.")
    add(51, "Z-score", z_rows, "Point-in-time: the baseline excludes the day being scored.")
    add(52, "Percentile analysis", pct_rows, "Point-in-time: the baseline excludes the day being scored.")

    shape = hist.flow_shape(series["tilt"])
    if shape:
        add(53, "Flow sharpness", _rows(shape, only=("n", "mean", "median", "sd", "sharpness")))
        add(54, "Flow skew", _rows(shape, only=("skew", "kurtosis", "pos_ratio", "neg_ratio",
                                                "sign_consistency", "consistency")))
        add(55, "Flow drawdown", _rows(shape, only=("cum", "peak", "drawdown", "max_drawdown", "trough_i",
                                                    "days_since_trough", "recovery", "recovery_speed")))

    dv = hist.divergences(days)
    add(56, "Price-flow divergence",
        _rows(dv, only=("price_trend", "flow_trend", "flow_sign", "volume_trend", "concentration_trend"))
        + [Row("pattern", p) for p in dv.patterns],
        "Trends are signs over the last 7 sessions, not a fitted model.")
    corr = hist.correlations(days)
    if corr:
        add(57, "Price-flow correlation", _rows(corr),
            "Correlation over one symbol's recent history — not evidence of causation or of edge.")
    lag = hist.lag_scan(days)
    if lag:
        add(58, "Flow-price lag",
            _rows(lag, only=("n", "best_lag", "best_corr"))
            + [Row(f"corr at lag {k}", v) for k, v in lag.corrs]
            + [Row(f"year {y} best lag", lg, f"corr {c:.4f}") for y, lg, c in lag.by_year],
            "The best lag is the best of 11 tried — the per-year column is there because a "
            "full-sample best lag is fitted by construction.")
    el = hist.elasticity(days)
    if el:
        add(59, "Volume-price elasticity", _rows(el))
    fe = hist.flow_efficiency(days)
    if fe:
        add(60, "Flow efficiency", _rows(fe))

    for n, title, fn, what in ((61, "Absorption-like proxy", hist.absorption_like, "absorption"),
                               (62, "Exhaustion-like proxy", hist.exhaustion_like, "exhaustion")):
        ser = fn(days)
        cur = ser[-1] if ser else None
        flagged = sum(1 for x in ser if x is not None and x.score >= PROXY_FLAG)
        rows = [Row("score", cur.score if cur else None),
                Row("persistence", cur.persistence if cur else None),
                Row("prior frequency", cur.prior_frequency if cur else None),
                Row("days scored >= 60", flagged, f"of {len(ser)} sessions")]
        if cur:
            rows += [Row(f"part: {k}", v) for k, v in cur.parts]
        add(n, title, rows,
            f"PROXY. This is a floorsheet pattern that RESEMBLES {what}; the floorsheet cannot "
            "confirm it, because it records no order book.")

    liq = hist.executed_liquidity(days)
    cur_liq = liq[-1] if liq else None
    if cur_liq:
        add(63, "Executed-liquidity proxy", _rows(cur_liq),
            "PROXY built from executed trades only — it is not spread, depth or resting size.")

    add(64, "Price clustering",
        _by_window(lambda w: pstat[w],
                   only=("levels", "clustering", "concentration", "dispersion", "freq_price",
                         "freq_price_share", "heavy_price", "heavy_price_share"))
        + _rows(days[-1].profile, prefix="last session ",
                only=("distinct", "dominant", "dominant_share", "hhi", "entropy", "dispersion")))
    add(65, "Price range utilisation",
        _by_window(lambda w: prof[w],
                   only=("low", "high", "range_utilisation", "vwap_position", "poc", "poc_position",
                         "low_third", "mid_third", "high_third")))

    asym = hist.price_asymmetry(last)
    add(66, "Buyer/seller price asymmetry",
        _top_rows(sorted(asym.values(), key=lambda a: a.buy_qty + a.sell_qty, reverse=True)[:5],
                  ("broker", "buy_qty", "sell_qty", "buy_vwap", "sell_vwap", "spread", "qty_ratio",
                   "turnover_ratio"), prefix="broker #"),
        f"One session ({last.date}).")
    fqp = hist.flow_quality_by_price(win[w30])
    add(67, "Broker flow quality by price",
        _top_rows(sorted(fqp.values(), key=lambda q: abs(q.quality), reverse=True)[:5],
                  ("broker", "buy_below", "buy_near", "buy_above", "sell_below", "sell_near",
                   "sell_above", "quality"), prefix="broker #"),
        "\"Below\"/\"above\" is relative to that session's VWAP — it is not a measure of skill.")

    c_rows: list[Row] = []
    for r in rank_acc[:3]:
        # fseries[b] is history.broker_flow_series(ses, b) already computed — see above.
        bs = hist.flow_shape([d.flow for d in fseries.get(r.broker, ())])
        if bs:
            c_rows += _rows(bs, prefix=f"broker {r.broker} ",
                            only=("n", "mean", "sd", "sharpness", "sign_consistency", "consistency",
                                  "cum", "max_drawdown"))
    add(68, "Flow consistency score", c_rows,
        "Over the full loaded history for the top 30D net buyers. A persistent net buyer is the "
        "normal state of a broker, not a finding.")

    add(69, "Market-wide floorsheet analysis", [Row("computed", False, _NOT_COMPUTED)],
        "Sections 69-71 are market-level and are built by a market-wide pass, not per symbol.")

    regs = hist.regimes(days)
    cur_reg = regs[-1] if regs else None
    labelled = [r for r in regs if r is not None]
    r_rows = ([Row("current regime", cur_reg.label), Row("tilt", cur_reg.tilt),
               Row("tilt z", cur_reg.tilt_z), Row("volume z", cur_reg.volume_z),
               _seq("tags", cur_reg.tags)] if cur_reg else
              [Row("current regime", None,
                   f"{len(days)} sessions is too short a point-in-time baseline to classify one")])
    r_rows.append(Row("sessions classified", len(labelled), f"of {len(regs)}"))
    add(72, "Historical regime classification", r_rows, "Point-in-time: each day is classified on its own past.")

    if labelled:
        tr = hist.transitions(regs)
        add(73, "Regime transitions",
            _rows(tr, only=("runs",))
            + [Row(f"{a} -> {b}", v, "transitions observed")
               for (a, b), v in sorted(tr.counts.items(), key=lambda kv: -kv[1])[:8]]
            + [Row(f"mean duration {k}", v, "sessions") for k, v in sorted(tr.mean_duration.items())],
            "Transition counts are over this symbol's own history only.")

    # ── 74-81: multi-timeframe alignment ───────────────────────────────────────────────
    signal, score, confidence = "NOT SCORED", None, "none"
    reasons: list[str] = []

    tf, tf_err = _optional(timeframes, ses, days)
    if tf_err:
        warnings.append(tf_err)
    if tf is not None:
        add(74, "Multi-timeframe system",
            _by_window(lambda w: tf.windows.get(w),
                       only=("purpose", "days", "date", "direction", "score", "flow_mean",
                             "positive_days", "negative_days", "neutral_days", "persistence",
                             "accumulation_pct", "distribution_pct", "breadth_pct",
                             "buy_concentration", "sell_concentration", "volume", "turnover",
                             "vwap", "brokers")),
            "3D pressure, 7D signal, 15D trend, 30D context — the only decision windows.")
        add(75, "Multi-timeframe comparison",
            [Row(p.name, p.label, f"{p.short_direction} vs {p.long_direction}, "
                                  f"delta {p.delta:+.4f}, {'agree' if p.agree else 'DISAGREE'}")
             for p in tf.comparison.pairs]
            + _rows(tf.comparison.accel, prefix="accel ")
            + [Row(f"{w}D vwap", v) for w, v in sorted(tf.comparison.vwap.items())])
        add(76, "Multi-timeframe alignment score",
            _rows(tf.alignment, only=("bullish", "bearish", "neutral", "windows", "weighted",
                                      "weighted_score", "score", "strength", "direction",
                                      "persistence", "persistence_days"))
            + _reasons(tf.alignment.reasons))
        add(77, "Timeframe conflict score",
            _rows(tf.conflict, only=("score", "conflicted", "label", "short_direction",
                                     "long_direction", "short_score", "long_score", "gap"))
            + [_seq("disagreeing", tf.conflict.disagreeing)]
            + _reasons(tf.conflict.reasons))
        add(78, "Signal freshness",
            _rows(tf.freshness, only=("date", "signal", "signal_age", "days_since_accumulation",
                                      "days_since_distribution", "days_since_flow_reversal",
                                      "days_since_concentration_spike",
                                      "days_since_participation_expansion",
                                      "days_since_large_trade_emergence", "accumulation_run",
                                      "distribution_run", "accumulation_active", "distribution_active"))
            + [Row(e.name, e.date, f"{e.age} sessions ago, level {e.level:+.4f}, "
                                   f"{'active' if e.active else 'lapsed'}") for e in tf.freshness.events],
            "Cut-offs are percentiles of this stock's own history, not fixed thresholds.")
        add(79, "Signal decay",
            _rows(tf.decay, only=("days", "day1", "peak_day", "latest", "rate", "half_life",
                                  "persistence", "recovery", "reacceleration", "label")))
        add(80, "Signal confirmation",
            _rows(tf.evidence, only=("thesis", "confirmation_count", "confirmation_strength",
                                     "independent_count", "families", "net", "confidence", "label"))
            + [Row(d.name, d.signed, f"{d.family}/{d.kind}, value {d.value:.4g}")
               for d in tf.evidence.confirmations]
            + _reasons(tf.evidence.reasons),
            "Confirmations are counted by INDEPENDENT family — six correlated flow metrics are "
            "one piece of evidence, not six.")
        add(81, "Signal contradiction",
            _rows(tf.evidence, only=("contradiction_count", "contradiction_severity",
                                     "contradiction_persistence", "contradiction_days"))
            + [Row(d.name, d.signed, f"{d.family}/{d.kind}, value {d.value:.4g}")
               for d in tf.evidence.contradictions],
            "Contradictions are reported, never netted away against the confirmations above.")
        # Spec rule: a contradiction is surfaced next to the verdict, not buried in a panel.
        warnings += [f"{d.name} contradicts the {tf.evidence.thesis} thesis "
                     f"({d.family}/{d.kind}, {d.signed:+.3f})" for d in tf.evidence.contradictions]
    elif timeframes is None:
        warnings.append("sections 74-81 (multi-timeframe alignment, conflict and signal freshness) "
                        "are not built yet — cross-window agreement is absent, not zero")

    # ── 82-92: zones, entries, exits and the three scores ──────────────────────────────
    zn = None
    if zones is not None:
        try:
            # `history=ses` is what keeps "load once" true: zones re-derives its bands from
            # the sessions we already hold instead of reading the archive a second time.
            zn = zones.zones(symbol, upto=upto, history=ses, lookback=len(ses))
        except Exception as exc:
            warnings.append(f"zones.zones failed: {type(exc).__name__}: {exc} — this symbol is "
                            "NOT SCORED, not neutral")

    if zn is not None:
        signal, confidence = str(zn.signal), str(zn.confidence)
        score = zn.swing_score.score
        reasons = [str(r) for r in zn.reasons]
        warnings += [str(w) for w in zn.warnings]

        add(82, "Buy zone engine",
            [Row("price", zn.price, "last executed VWAP"), Row("sessions", zn.sessions),
             Row("bands", len(zn.bands)), Row("repaired", zn.repaired)]
            + _top_rows(sorted(zn.bands, key=lambda b: b.volume_share, reverse=True)[:5],
                        ("low", "high", "volume", "volume_share", "tilt", "demand", "supply",
                         "churn", "brokers"), prefix="band #"),
            "Zones are built from where volume actually changed hands, not from chart geometry.")
        add(83, "Three entry types",
            [r for e in zn.entries for r in
             ([Row(f"{e.kind} qualified", e.qualified, e.zone.name)]
              + [Row(f"{e.kind}: {c.name}", c.passed, c.note) for c in e.conditions])],
            "An entry is qualified only when every one of its conditions passed; the failing "
            "conditions are listed rather than hidden.")
        add(84, "Value / accumulation buy zone", _zone("accumulation", zn.accumulation)
            + _zone("entry", zn.entry) + _zone("confirmation", zn.confirmation))
        add(85, "Sell / distribution zone", _zone("distribution", zn.distribution))
        add(86, "Profit-taking zones", _zone("profit 1", zn.profit1) + _zone("profit 2", zn.profit2))
        add(87, "Exit types", [Row(e.kind, e.triggered, e.note) for e in zn.exits])
        add(88, "Invalidation / stop-loss zone", _zone("invalidation", zn.invalidation),
            "Invalidation is a flow/price level, not a risk-managed stop — position sizing is "
            "not this engine's job.")
        add(89, "Entry -> exit map",
            [Row("price", zn.price)]
            + [Row(f"{lab} band", f"{z.low:g} - {z.high:g}", z.name)
               for lab, z in (("invalidation", zn.invalidation), ("accumulation", zn.accumulation),
                              ("entry", zn.entry), ("confirmation", zn.confirmation),
                              ("profit 1", zn.profit1), ("profit 2", zn.profit2),
                              ("distribution", zn.distribution)) if z is not None],
            "The ladder in price order, low to high.")
        add(90, "Swing score", _score(zn.swing_score),
            "Provisional weights: the score is a weighted sum whose weights have NOT been fitted "
            "or validated out of sample." if zn.swing_score.weights_are_provisional else "")
        add(91, "Entry score", _score(zn.entry_score))
        add(92, "Exit score", _score(zn.exit_score))
    elif zones is None:
        warnings.append("sections 82-92 (the zone/score engine) are not built yet — this symbol "
                        "is NOT SCORED, not neutral")

    if score is None:
        reasons.append(f"sections 4-73 computed over {len(ses)} sessions ending {last.date}; "
                       "no score exists because the scoring layer did not produce one")

    return store.Detail(
        symbol=symbol,
        session=last.date,
        signal=signal,
        score=score,
        confidence=confidence,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        sections=tuple(out),
    )


def board_row(d: store.Detail) -> dict:
    """The one-line summary of a Detail. ``date`` is the session the numbers are FROM."""
    get = {(s.n, r.metric): r.value for s in d.sections for r in s.rows}
    return {
        "symbol": d.symbol,
        "signal": d.signal,
        "score": d.score,
        "confidence": d.confidence,
        "volume": get.get((5, "volume")),
        "turnover": get.get((5, "turnover")),
        "vwap": get.get((5, "vwap")),
        "net_qty": get.get((13, "30D net qty")),
        "flow_quality": get.get((16, "30D flow quality")),
        "persistence": get.get((19, "30D persistence")),
        "direction": get.get((19, "30D direction")),
        "phase": get.get((20, "phase")),
        "reversal": get.get((21, "kind")),
        "consensus": get.get((22, "30D consensus")),
        "breadth": get.get((23, "30D accumulation")),
        "hhi": get.get((24, "30D broker hhi")),
        "large_pct": get.get((27, "30D turnover pct")),
        "brokers": get.get((33, "active")),
        "top_broker": get.get((11, "30D top buyer")),
        "top_broker_share": get.get((11, "30D top buyer share")),
        "anomaly": get.get((49, "score")),
        "regime": get.get((72, "current regime")),
        "alignment": get.get((76, "score")),
        "conflict": get.get((77, "score")),
        "age": get.get((78, "signal age")),
        "quality": get.get((4, "last session status")),
        # Zone levels come from section 84/86/88 under STABLE metric names — see _zone().
        "entry_low": get.get((84, "entry low")),
        "entry_high": get.get((84, "entry high")),
        "target": get.get((86, "profit 1 low")),
        "stop": get.get((88, "invalidation high")),
        "warnings": len(d.warnings),
        "date": d.session,
    }


# `imbalance` was dropped after the first full build: measured across all 481 rows it was
# flow_quality / 2 exactly (ratio sd 3.7e-6, pure rounding), so the board carried one number in
# two columns and a reader counting evidence would have counted it twice. flow_quality is the
# name kept because it is the same quantity as volume_spike.py's net_churn, which is what the
# scores actually weight. Section 15 still reports it per window in the detail, where it is
# labelled "net / gross quantity" rather than presented as a second independent measure.
BOARD_COLUMNS = [
    "symbol", "signal", "score", "confidence", "volume", "turnover", "vwap", "net_qty",
    "flow_quality", "persistence", "direction", "phase", "reversal", "consensus",
    "breadth", "hhi", "large_pct", "brokers", "top_broker", "top_broker_share", "anomaly",
    "regime", "quality", "alignment", "conflict", "age", "entry_low", "entry_high", "target",
    "stop", "warnings", "date",
]


# ── CLI ────────────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m swing_quantam",
        description="Build the swing_quantam board and per-symbol detail into Master_data/swing_quantam/.",
        epilog="python -m swing_quantam --selfcheck runs this module's self-check instead of a build.",
    )
    ap.add_argument("symbols", nargs="*", help="symbols to build; default is every symbol in the archive")
    ap.add_argument("--limit", type=int, default=0, help="build only the first N symbols (a fast pass)")
    ap.add_argument("--upto", default=None, metavar="YYYY-MM-DD",
                    help="point-in-time rebuild: never read a session after this date")
    ap.add_argument("--history", type=int, default=HISTORY,
                    help=f"sessions of context to load per symbol (default {HISTORY})")
    a = ap.parse_args(argv if argv is not None else sys.argv[1:])

    syms = [s.upper() for s in a.symbols] or loader.symbols()
    # Only a run over the WHOLE archive is entitled to replace the board. A --limit pass or a
    # named-symbol pass is a partial view, and letting it rewrite board.txt silently drops the
    # production board to a handful of rows that render as a perfectly healthy small board.
    full_run = not a.symbols and a.limit <= 0
    if a.limit > 0:
        syms = syms[: a.limit]
    if not syms:
        print("no symbols in the archive — nothing to build", file=sys.stderr)
        return 1

    started = time.time()
    rows: list[dict] = []
    thin: list[str] = []
    failed: list[tuple[str, str]] = []
    shown_traceback = False

    for i, sym in enumerate(syms, 1):
        t0 = time.time()
        try:
            d = build_symbol(sym, upto=a.upto, history=a.history)
        except Exception as exc:
            failed.append((sym, f"{type(exc).__name__}: {exc}"))
            print(f"[{i:>4}/{len(syms)}] {sym:<12} FAILED  {type(exc).__name__}: {exc}", flush=True)
            if not shown_traceback:  # one traceback, not 593 — enough to actually debug
                traceback.print_exc()
                shown_traceback = True
            continue
        if d is None:
            thin.append(sym)
            print(f"[{i:>4}/{len(syms)}] {sym:<12} skipped (fewer than {MIN_SESSIONS} sessions)", flush=True)
            continue
        store.write_detail(d)
        rows.append(board_row(d))
        print(f"[{i:>4}/{len(syms)}] {sym:<12} {d.signal:<16} "
              f"{'' if d.score is None else format(d.score, '.1f'):>5}  "
              f"{len(d.sections):>2} sections  {time.time() - t0:.1f}s", flush=True)

    elapsed = time.time() - started
    print(f"\nbuilt {len(rows)}, skipped {len(thin)} (too little history), failed {len(failed)} "
          f"in {elapsed:.1f}s ({elapsed / max(1, len(syms)):.2f}s per symbol)")
    if failed:
        print("failures:")
        for sym, why in failed:
            print(f"  {sym:<12} {why}")

    if not rows:
        # A board written from nothing would read as "everything is fine, nothing to see".
        print("produced nothing — refusing to write an empty board", file=sys.stderr)
        return 1

    try:
        path = store.write_board(BOARD_COLUMNS, rows, allow_shrink=full_run)
    except ValueError as exc:
        # The detail files this run wrote are still on disk and still correct; only the board
        # index was left alone. Say exactly that, so nobody reruns in a panic.
        print(f"\nboard NOT rewritten: {exc}", file=sys.stderr)
        print(f"the {len(rows)} detail file(s) this run produced were written and are current.",
              file=sys.stderr)
        return 1
    print(f"wrote {path} ({len(rows)} rows, session {max(r['date'] for r in rows)})")
    return 0


def _demo() -> None:
    """Self-check: the shared-session shortcut must agree with the module it replaces."""
    syms = loader.symbols()
    assert syms, "no floorsheet archive"
    sym = "NABIL" if "NABIL" in syms else syms[0]
    ses = loader.load_last(sym, 40)
    assert len(ses) >= MIN_SESSIONS, f"{sym}: only {len(ses)} sessions"

    # The one piece of hist.daily() this module reimplements. If it drifts, every
    # section 50-73 number silently stops matching the module it claims to come from.
    assert _daystats(ses) == hist.daily(sym, 40), "_daystats has drifted from hist.daily"

    # The two other "compute it once" shortcuts, guarded the same way: build_symbol reads
    # both out of a single flow.all_series() pass instead of calling these per broker.
    fs = flow.all_series(ses)
    assert fs[None] == flow.stock_days(ses), "all_series[None] is no longer flow.stock_days"
    b = max(k for k in fs if k is not None)
    assert [d.flow for d in fs[b]] == hist.broker_flow_series(ses, b), \
        "all_series[b].flow is no longer hist.broker_flow_series"

    # Full HISTORY, not a cheap 40: the historical layer needs 60+ observations before it
    # emits a z-score or a regime, so a shallow build never exercises those branches and the
    # board-column check below could not tell a typo'd key from a legitimately thin symbol.
    d = build_symbol(sym)
    assert d is not None and d.session == loader.sessions(sym)[-1]
    assert d.sections and [s.n for s in d.sections] == sorted(s.n for s in d.sections), "sections out of order"
    assert all(s.rows for s in d.sections), "a section with no rows should not have been added"
    assert d.reasons, "a signal without reasons is a black box"
    # The two must never disagree: a symbol reading NOT SCORED while carrying a number, or
    # carrying a verdict with no number behind it, is the fabricated score this forbids.
    assert (d.score is None) == (d.signal == "NOT SCORED"), f"{d.signal!r} vs score {d.score!r}"
    if zones is None:
        assert d.score is None, "must not fabricate a score without the zone engine"

    # Point-in-time really cuts — and not just the header. Sections 45/46/49/66/72/78 all
    # print dates of their own; any one of them reading past `upto` is look-ahead that would
    # score a rebuild on information the decision date did not have.
    cut = loader.sessions(sym)[-10]
    pit = build_symbol(sym, upto=cut)
    assert pit is not None and pit.session <= cut, f"{pit.session} is past {cut}"
    ahead = [(s.n, r.metric, r.value) for s in pit.sections for r in s.rows
             if isinstance(r.value, str) and len(r.value) == 10 and r.value[4] == "-" and r.value > cut]
    assert not ahead, f"rows dated past {cut}: {ahead[:5]}"

    r = board_row(d)
    assert r["date"] == d.session and r["symbol"] == d.symbol
    assert set(BOARD_COLUMNS) == set(r), set(BOARD_COLUMNS) ^ set(r)
    # Every board column must actually resolve. A typo in a (section, metric) key returns
    # None silently and ships a column of blanks that looks like "no data".
    blank = [c for c, v in r.items() if v is None]
    assert not blank, f"board columns resolved to nothing: {blank}"

    # PROXY_FLAG restates a default that lives in another module; pin it.
    import inspect
    assert inspect.signature(hist.absorption_like).parameters["threshold"].default == PROXY_FLAG

    print(f"__main__ ok — {sym}: {len(d.sections)} sections, "
          f"{sum(len(s.rows) for s in d.sections)} rows, signal {d.signal}")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        _demo()
    else:
        sys.exit(main())
