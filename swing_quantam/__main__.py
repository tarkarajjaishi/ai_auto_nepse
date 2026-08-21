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
* The market-wide functions are deliberately NOT called per symbol. They all take a
  *symbol list*, so invoking one inside a symbol's build turns that build into a full
  market scan — 593 of them per run. Sections 69-71 are market facts (on a session they
  are the SAME numbers in every detail file), so :func:`main` runs ``hist.market_pass``
  **once** before the loop and hands the result to every ``build_symbol`` via ``market=``;
  that argument defaults to None, so a direct single-symbol call still works and simply
  reports those sections as not computed. Sections 38-39 (``network.broker_totals`` /
  ``affinity`` / ``stock_rotation``) are not market facts — they are per-symbol answers
  that happen to need the market — so they still carry an explicit "not computed" row and
  the reason, rather than silently missing.
* ``--upto`` is threaded into the single ``load_last`` call and nothing else reads the
  filesystem, so point-in-time is enforced in one place instead of sixty. ``zones`` and
  ``timeframes`` are handed ``history=ses`` for the same reason.
* Sections 93-104 and 114 are MEASUREMENTS of the sections above, not more of them, and the
  study behind them costs ~470 s — three times a whole 593-symbol build. It is never run
  here. :func:`main` reads the small ``backtest_summary.txt`` digest once (same threading
  pattern as 69-71) and hands it to every ``build_symbol``. Unlike the market pass, a
  MISSING digest is reported rather than skipped: those sections still render and say the
  study has not been run, because a section that quietly vanishes reads as "considered and
  found irrelevant" — the exact opposite of a null result. Sections 107, 108 and 115 are
  the spec's own honesty text and render unconditionally.

MEASURED COST (this machine, warm page cache, HISTORY=120, sections 4-92 all live):
``python -m swing_quantam --limit 10`` takes **11 s (1.1 s per symbol)**, plus a flat
**~45 s** for the sections 69-71 market pass that every run does once up front — measured
at 40-46 s over six dates, and it does not grow with the number of symbols built. The whole
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
# Sections 93-104 and 114 are MEASUREMENTS of the sections above, not more of them. The
# study behind them costs ~470 s and is never run here; this import only brings in the
# parser for the small digest it leaves behind. Guarded like the two above so a missing
# module degrades to "not run" instead of taking the whole build down.
try:  # pragma: no cover
    from . import backtest as bt  # type: ignore
except ImportError:
    bt = None  # type: ignore


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

#: Every money figure in sections 69-71 carries this. The market pass reads the prebuilt
#: broker_flow tables, whose amount columns are summed from the floorsheet's *recorded*
#: amount rather than being recomputed here — see loader.flow_rows.
#:
#: This used to say the recorded amount was unreliable. It is not: measured across the whole
#: archive it agrees with quantity x rate on every row. The real caveat is only that these are
#: another program's sums, rounded on write, so they need not match to the last paisa.
_INDICATIVE = "summed by build_broker_flow and rounded on write — exact to the rupee, not the paisa"


def _market_sections(mp: hist.MarketPass) -> list[Section]:
    """Sections 69-71 rendered from ONE ``hist.market_pass`` — see :func:`build_symbol`.

    These are facts about the SESSION, not about the symbol: on a given day every one of
    the 593 detail files gets the identical numbers here. That is exactly why the pass is
    run once in :func:`main` and threaded in, instead of being computed per symbol.
    """
    src = (f"One session ({mp.date}), whole market, from the prebuilt broker_flow tables in "
           f"{mp.seconds:.0f}s — {mp.active} of {mp.covered} symbols traded"
           + (f", {mp.skipped} had no data at this date." if mp.skipped else "."))

    s69 = [
        Row("session", mp.date),
        Row("transactions", mp.trades, "market-wide"),
        Row("volume", mp.volume, "shares"),
        Row("turnover", mp.turnover, _INDICATIVE),
        Row("active stocks", mp.active, f"of {mp.covered} with floorsheet data"),
        Row("active brokers", mp.brokers,
            "near-constant at ~90 — a participation check, not a signal"),
        Row("broker concentration hhi", mp.broker_hhi, "gross quantity across brokers"),
        Row("buyer concentration hhi", mp.buyer_hhi, "buy quantity across brokers"),
        Row("seller concentration hhi", mp.seller_hhi, "sell quantity across brokers"),
        Row("top broker", mp.top_broker, "by gross quantity"),
        Row("top broker share", mp.top_broker_share, "of market gross quantity"),
        Row("top net buyer", mp.top_net_buyer, "by net quantity"),
        Row("top net buyer share", mp.top_net_buyer_share, "its net quantity / market volume"),
        Row("net buying brokers", mp.net_buyers, f"of {mp.brokers}"),
        Row("net selling brokers", mp.net_sellers, f"of {mp.brokers}"),
        Row("stocks accumulating", mp.accumulating, "section 69's market accumulation"),
        Row("stocks distributing", mp.distributing, "section 69's market distribution"),
    ] + [
        Row(f"#{i} broker", b.broker,
            f"gross {b.gross_qty:,} ({b.gross_share:.1%}), net {b.net_qty:+,} "
            f"({b.net_share:+.2%} of volume), {b.symbols} stocks")
        for i, b in enumerate(mp.ranking, 1)
    ]

    s70 = [
        Row("stocks with positive net broker flow", mp.accumulating),
        Row("stocks with negative net broker flow", mp.distributing),
        Row("stocks flat", mp.flat, "tilt inside +/-0.01"),
        Row("flow breadth", mp.breadth, "positive / (positive + negative)"),
        Row("flow breadth pct", mp.breadth_pct),
        Row("broker flow breadth", mp.broker_breadth,
            f"{mp.net_buyers} of {mp.net_buyers + mp.net_sellers} brokers ended the session net long"),
        Row("turnover breadth", mp.turnover_breadth,
            "share of market turnover in positive-tilt stocks — " + _INDICATIVE),
        Row("volume breadth", mp.volume_breadth, "share of market volume in positive-tilt stocks"),
    ]

    if mp.sectors:
        covered = sum(s.volume for s in mp.sectors) / mp.volume if mp.volume else 0.0
        s71 = [
            Row("sectors", len(mp.sectors)),
            Row("symbols mapped", mp.mapped, f"of {mp.requested} in the archive"),
            Row("mapped share of market volume", covered, "how much of the session the rows below cover"),
        ]
        for s in sorted(mp.sectors, key=lambda x: -x.volume):
            s71 += [
                Row(f"{s.sector} volume", s.volume,
                    f"{s.active}/{s.symbols} active, {s.trades:,} transactions, "
                    f"turnover {s.turnover:,.0f} ({_INDICATIVE})"),
                Row(f"{s.sector} net tilt", s.net_tilt,
                    f"breadth {s.breadth:.2f} — {s.positive} positive, {s.negative} negative"),
                Row(f"{s.sector} concentration", s.concentration,
                    f"buying HHI across {s.brokers} brokers; top is broker {s.top_broker} "
                    f"at {s.top_broker_share:.1%}"),
                Row(f"{s.sector} rotation", s.rotation,
                    f"{s.volume_share:.2%} of mapped volume this session vs {s.prior_share:.2%} "
                    "over the prior 20"),
            ]
        n71 = (f"Sector is EXTERNAL (Master_data/sectors.txt) and never a floorsheet field. It maps "
               f"{mp.mapped} of {mp.requested} symbols into {len(mp.sectors)} sectors, so the "
               f"{mp.requested - mp.mapped} unmapped ones are in no row below. Sector buying and selling "
               "are not shown: inside a sector every share bought is a share sold, so both are just "
               "sector volume. Rotation shares are of the mapped universe and therefore sum to zero.")
    else:
        s71 = [Row("sectors", 0,
                   "Master_data/sectors.txt is missing or empty — nothing to aggregate")]
        n71 = ("Section 71 needs an external symbol -> sector map at Master_data/sectors.txt "
               "(tab-separated, header symbol<TAB>sector). Sector is never inferred from trades.")

    return [
        Section(69, "Market-wide floorsheet analysis", tuple(s69),
                src + " Market-wide net broker flow is not reported: summed across all brokers it is "
                      "identically zero every session — every share bought is a share sold — so the "
                      "distribution across brokers and stocks is reported instead."),
        Section(70, "Market flow breadth", tuple(s70),
                "A stock's own net broker flow is zero by construction, so \"positive\" means the flow "
                "TILT: the top net buyer's share of volume minus the top net seller's. Measured below "
                "0.5 on every session sampled over fourteen months (0.375-0.539) — read breadth against "
                "its own history, not against 50%."),
        Section(71, "Sector flow", tuple(s71), n71),
    ]


# ── 93-115: what the sections above are actually worth ─────────────────────────────────
#
# Sections 82-92 print an entry zone, a target, a stop and a 0-100 score. The measurement
# of whether any of that has ever paid was already computed — by :mod:`swing_quantam.backtest`,
# into a 34 KB report at Master_data/swing_quantam/backtest.txt that no reader opens. A board
# that shows the target and keeps the verdict in a file nobody reads is not neutral; it is
# advertising. So the verdict is rendered here, beside the signal, in the reader's own view.
#
# The study costs ~470 s and the full board build is 22 minutes, so it is NEVER re-run here.
# main() reads one small tab-separated digest once and threads it in, exactly as it does for
# the sections 69-71 market pass.

#: What a reader sees when the study has never been run. The command is spelled out because
#: "no data" with no way to get it is a shrug, and — more importantly — because a section
#: that is silently OMITTED reads as "this was considered and found irrelevant", which is the
#: opposite of what an unrun study means. Every backtest-derived section renders either way.
_NOT_RUN = ('not run; build it with `python -c "from swing_quantam import backtest; '
            'backtest._demo()"`')

_NO_SUMMARY = "there is no Master_data/swing_quantam/backtest_summary.txt on disk"

#: Section 107, the spec's own list. Floorsheet records EXECUTED transactions and nothing
#: else, so everything here is outside what the data can reach — not merely unmeasured.
_S107: tuple[tuple[str, str], ...] = (
    ("actual investor identity", "NOT knowable from the floorsheet"),
    ("investor account", "NOT knowable from the floorsheet"),
    ("client identity behind a broker", "NOT knowable from the floorsheet"),
    ("investor cost basis", "NOT knowable from the floorsheet"),
    ("investor holdings", "NOT knowable from the floorsheet"),
    ("why a transaction happened", "NOT knowable from the floorsheet"),
    ("actual profit or loss", "NOT knowable from the floorsheet"),
    ("unexecuted orders", "NOT knowable from the floorsheet"),
    ("full order book", "NOT knowable from the floorsheet"),
    ("hidden liquidity", "NOT knowable from the floorsheet"),
    ("future bids and offers", "NOT knowable from the floorsheet"),
    ("news", "NOT knowable from the floorsheet"),
    ("fundamentals", "NOT knowable from the floorsheet"),
    ("corporate announcements", "NOT knowable from the floorsheet"),
    ("sentiment outside executed transactions", "NOT knowable from the floorsheet"),
    ("broker activity", "is NOT investor identity"),
    ("broker buying", "is NOT guaranteed accumulation"),
    ("broker selling", "is NOT guaranteed distribution"),
    ("pattern", "is NOT manipulation"),
    ("high concentration", "is NOT insider activity"),
    ("a repeated broker pair", "is NOT wrongdoing"),
)

#: Section 108, verbatim in substance: the sentences this system may and may not produce.
_S108: tuple[tuple[str, str], ...] = (
    ('"Broker 58 had net buying."', "CAN say"),
    ('"Net buying increased over 3D/7D."', "CAN say"),
    ('"Broker breadth expanded."', "CAN say"),
    ('"Volume concentration increased."', "CAN say"),
    ('"Executed volume clustered around Rs X."', "CAN say"),
    ('"The current pattern historically resembles prior accumulation setups."', "CAN say"),
    ('"The model identifies a candidate entry zone."', "CAN say"),
    ('"Historical backtest shows X% favourable outcomes for this setup."', "CAN say"),
    ('"Investor X bought."', "CANNOT say"),
    ('"Broker 58 is definitely smart money."', "CANNOT say"),
    ('"This broker is an insider."', "CANNOT say"),
    ('"This is manipulation."', "CANNOT say"),
    ('"The price will definitely rise."', "CANNOT say"),
    ('"The stock will definitely reach Rs X."', "CANNOT say"),
    ('"There are hidden buy orders at Rs X."', "CANNOT say"),
)

#: Section 115. Every note says where THIS package stops, because the spec's rule is only
#: useful next to an honest statement of what the engine does and does not do for you.
_S115: tuple[tuple[str, str, str], ...] = (
    ("position sizing", "must be SEPARATE from signal generation",
     "nothing in this package sizes a position; section 88's invalidation is a flow/price "
     "level, not a risk-managed stop"),
    ("risk per trade", "must be predefined",
     "decided before the trade, not after an entry zone has been seen"),
    ("maximum portfolio exposure", "must be controlled", "not modelled anywhere in this package"),
    ("correlated positions", "must be considered",
     "NEPSE sectors move together — several names from one sector is one position, not several"),
    ("slippage and transaction costs", "must be included in backtests",
     "the study behind sections 93-104 measures GROSS returns entered at the next session's "
     "open and includes NEITHER, so every return above is optimistic by the round trip"),
    ("partial exits", "should be modelled",
     "section 86 gives two profit levels; it does not model an exit schedule across them"),
    ("gap risk", "CANNOT be eliminated",
     "NEPSE's circuit is +/-15% — a stop level is a level, not a fill"),
    ("execution at the desired zone", "CANNOT be guaranteed by floorsheet data",
     "the floorsheet records what traded, never what was resting"),
)


def _num(s, key: str, fmt: str = "{:+.2%}") -> str:
    """A summary number, formatted — or the words "not computed". Never a blank.

    A missing metric that renders as an empty cell reads as a zero, and this whole
    block of sections exists to stop a null result being read as a positive one.
    """
    v = s.num(key)
    return "not computed" if v is None else fmt.format(v)


def _edge_rows(s, section: int, gone: str, why: str) -> list[Row]:
    """Sections 94, 95 and 96 — they are the same shape three times over.

    Screen N candidates, floor most of them out for sample size, then ask whether the
    survivors beat the best of N RANDOM regroupings. Two things must reach the board and
    both are easy to lose:

    * The SUPPRESSED count. "10 candidates, 0 above the floor" IS section 96's result. An
      empty panel in its place reads as "nothing was looked for".
    * The symbol-clustering columns. The shuffle control removes the market effect but not
      symbol clustering, so a broker whose occurrences are one stock's good run scores
      exactly like one with a real edge. ``broker 62 on UMHL`` — n 49, 82% positive, +4.08%
      excess, positive in 100% of years, and every occurrence on ONE stock — is the most
      useful row in the study precisely because it shows what a false positive looks like.
      It is labelled, never dropped.
    """
    if s is None:
        return [Row("historical edge", gone, why)]
    t = s.edge_table(section)
    if t is None:
        return [Row("historical edge", "not computed",
                    "the last backtest run recorded no table for this section")]
    floor = s.get("min_obs", "30")
    rows = [Row("candidates screened", t.candidates,
                f"{t.live} above the {floor}-occurrence floor, {t.candidates - t.live} "
                f"SUPPRESSED for sample size")]
    if t.live == 0:
        rows.append(Row("result",
                        f"nothing reportable — all {t.candidates} candidates are below the "
                        f"{floor}-occurrence floor",
                        "that IS the result, not a gap: a hit rate off fewer occurrences "
                        "than that is a number-shaped opinion, so no percentage is printed"))
        return rows
    for e in s.edges_in(section):
        note = (f"n {e.n}, {e.positive_rate:.0%} positive, median {e.median:+.2%}, positive in "
                f"{e.consistency:.0%} of years, lead {e.lead_time:.1f} sessions, "
                f"{e.symbols} stock(s) with {e.top_symbol_share:.0%} on the busiest, "
                f"decay 5d {e.decay[0]:+.2%} / 10d {e.decay[1]:+.2%} / 20d {e.decay[2]:+.2%}")
        if e.clustered:
            note += (" — CLUSTERED: one stock's run wearing a broker's number, NOT evidence "
                     "the broker knew anything")
        rows.append(Row(e.key, f"{e.mean:+.2%}", note))
    rows.append(Row("best-of-N control", f"p {t.p_value:.2f}",
                    f"keeping the group sizes and shuffling which observation lands in which "
                    f"group produces a best group at least as good as {t.best:+.2%} in "
                    f"{t.p_value:.0%} of random partitions -> "
                    + ("this table is a SORTING ARTEFACT" if t.artefact
                       else "the top of this table survives the shuffle control, but the "
                            "control does NOT remove symbol clustering — read the stock "
                            "counts above")))
    return rows


def _evidence_sections(s, upto: str | None = None) -> list[Section]:
    """Sections 93, 98-100, 103, 104, 107, 108, 114 and 115 from one backtest digest.

    ``s`` is a ``backtest.Summary`` or None. None is a first-class outcome, not an error:
    every backtest-derived section still renders and says so in words. The three honesty
    sections (107, 108, 115) are the spec's own text and never depend on a run at all.

    ``upto`` is the point-in-time cut, and it matters here for a reason that is easy to
    miss: the study is dated. It was RUN on one date over data ending on another, and both
    are normally later than a rebuild's cut. Printing "0 of 15 families have an edge" into
    a rebuild dated before the study measured it is look-ahead — the same look-ahead
    ``--upto`` exists to prevent — and it is the kind that would quietly flatter a backtest
    OF this board, because a reader (or a strategy) could act at date D on a verdict that
    did not exist until later. So a study that postdates the cut is withheld and said to be
    withheld, exactly like one that was never run.

    The numbers are otherwise reproduced exactly as measured. The rules behind the entry
    zones this board prints do not clear their controls, and softening that here would
    defeat the only reason the block exists.
    """
    why, gone = _NO_SUMMARY, _NOT_RUN
    if s is not None and upto and max(s.get("end", "9999-99-99"),
                                      s.get("run_date", "9999-99-99")) > upto:
        gone = f"not available at {upto}"
        why = (f"the backtest was run {s.get('run_date', 'at an unrecorded date')} over data "
               f"ending {s.get('end', 'unrecorded')} — both AFTER this point-in-time cut of "
               f"{upto}, so quoting it here would be look-ahead")
        s = None
    absent = s is None

    # ── 93: expected value, for the rule the board itself would trade ──────────────────
    if absent:
        s93 = [Row("expected value", gone, why)]
        n93 = ("Sections 82-92 above print an entry zone, a target and a stop. Whether following "
               "them has ever paid is NOT knowable from this file until the backtest is run.")
    else:
        zb = s.family("zone_buy")
        s93 = [
            Row("measured rule", "zone_buy",
                "the board's own BUY ZONE / STRONG BUY ZONE signal, backtested exactly as "
                "sections 84-88 emit it"),
            Row("observations", _num(s, "zone_buy.n", "{:,.0f}"),
                f"stock-days, of {s.get('observations', '?')} in the study"),
            Row("win rate", _num(s, "zone_buy.win_rate", "{:.1%}"),
                f"loss rate {_num(s, 'zone_buy.loss_rate', '{:.1%}')} — compare with the "
                f"baseline's {_num(s, 'baseline.win_rate', '{:.1%}')}, not with 50%"),
            Row("average favourable movement", _num(s, "zone_buy.mfe_mean"),
                f"median {_num(s, 'zone_buy.mfe_median')} — the best price reached inside the "
                f"{s.get('horizon', '?')}-session horizon"),
            Row("average adverse movement", _num(s, "zone_buy.mae_mean"),
                f"median {_num(s, 'zone_buy.mae_median')} — the worst price reached"),
            Row("expected value", _num(s, "zone_buy.expected_value"),
                "win rate x average win minus loss rate x average loss, per trade, gross"),
            Row("expectancy", _num(s, "zone_buy.expectancy", "{:+.2f}R"),
                "the same arithmetic per unit of risk: expected return per 1.0 of average loss"),
            Row("payoff", _num(s, "zone_buy.payoff", "{:.2f}"), "average win / average loss"),
            Row("profit factor", _num(s, "zone_buy.profit_factor", "{:.2f}"),
                f"baseline {_num(s, 'baseline.profit_factor', '{:.2f}')}"),
            Row("max drawdown", _num(s, "zone_buy.max_drawdown", "{:.1%}"),
                f"non-overlapping holding periods; baseline "
                f"{_num(s, 'baseline.max_drawdown', '{:.1%}')}"),
            Row("date-demeaned excess", _num(s, "zone_buy.excess_mean"),
                f"median {_num(s, 'zone_buy.excess_median')}, "
                f"{_num(s, 'zone_buy.excess_lfo')} against the same day's NON-members "
                f"(the board's own signal is inside the universe mean it is compared with, "
                f"which flatters it), positive in "
                f"{s.get('zone_buy.years_positive', '?')}/{s.get('zone_buy.years', '?')} years "
                f"-> {s.get('zone_buy.verdict', 'not computed')}"),
            Row("is it worse than random?",
                "not computed" if s.num("zone_buy.control_p_low") is None else
                ("YES — significantly negative"
                 if (s.num("zone_buy.control_p_low") or 1.0) < 0.05 else
                 "not distinguishable from chance"),
                f"lower-tail control p {_num(s, 'zone_buy.control_p_low', '{:.4f}')} — the share "
                f"of {s.get('control_draws', '?')} date-matched random draws that did as badly as "
                f"the rule or worse. Upper tail {_num(s, 'zone_buy.control_p', '{:.4f}')}. "
                f"Per-year excess: {s.get('zone_buy.year_excess', 'not computed')}"),
            Row("beat the same-day universe", _num(s, "zone_buy.excess_win_rate", "{:.1%}"),
                f"of the time, against the baseline's "
                f"{_num(s, 'baseline.excess_win_rate', '{:.1%}')} on the same dates"),
            Row("baseline mean", _num(s, "baseline.mean"),
                f"median {_num(s, 'baseline.median')} — buy-and-hold EVERY stock-day on the "
                f"same dates. The median is negative while the mean is positive: a thin tail "
                f"carries this market."),
        ]
        n93 = (f"Measured over {s.get('observations', '?')} stock-days on "
               f"{s.get('universe', '?')} symbols, {s.get('start', '?')} to {s.get('end', '?')}, "
               f"study run {s.get('run_date', 'unknown')}. Entry is the NEXT session's open on "
               f"corporate-action ADJUSTED bars. A positive expected value here is mostly the "
               f"market rising over the window — the only column that says the RULE knew "
               f"anything is the date-demeaned excess, and for zone_buy it is negative"
               + ("." if (s.num("zone_buy.control_p_low") or 1.0) >= 0.05 else
                  f", significantly so. This is not 'unproven': the excess is "
                  f"{_num(s, 'zone_buy.excess_mean')} mean and "
                  f"{_num(s, 'zone_buy.excess_median')} median, "
                  f"{_num(s, 'zone_buy.excess_lfo')} against the stocks it passed over, it beat "
                  f"the same-day universe only "
                  f"{_num(s, 'zone_buy.excess_win_rate', '{:.1%}')} of the time, it was negative "
                  f"in {s.get('zone_buy.years_negative', '?')} of "
                  f"{s.get('zone_buy.years', '?')} years, and the chance of a random date-matched "
                  f"selection doing this badly is "
                  f"{_num(s, 'zone_buy.control_p_low', '{:.4f}')}. Zones fire every "
                  f"{s.get('zone_stride', '?')} sessions against a "
                  f"{s.get('horizon', '?')}-session horizon, so these observations do not overlap "
                  f"and that p-value needs no discount. The BUY ZONE this board prints is, on "
                  f"this evidence, worse than picking at random from the same day's universe."))

    # ── 94-96: the screened broker tables ─────────────────────────────────────────────
    s94 = _edge_rows(s, 94, gone, why)
    s95 = _edge_rows(s, 95, gone, why)
    s96 = _edge_rows(s, 96, gone, why)
    if s is not None and s.num("pair_share.max") is not None:
        # The floor is unreachable BY CONSTRUCTION on this archive, and the only honest way
        # to present a vacuous section is to print the distribution that makes it vacuous.
        s96.insert(0, Row("dominant-pair share of 15-session volume",
                          f"median {_num(s, 'pair_share.median', '{:.1%}')}, "
                          f"p90 {_num(s, 'pair_share.p90', '{:.1%}')}, "
                          f"max {_num(s, 'pair_share.max', '{:.1%}')}",
                          f"measured across {s.get('pair_share.n', '?')} stock-days; the "
                          f"section 96 floor is {_num(s, 'pair_min_share', '{:.1%}')}, so the "
                          f"floor is barely reachable and this section is close to vacuous "
                          f"by construction rather than negative"))

    # ── 97: outcome labels ────────────────────────────────────────────────────────────
    if absent:
        s97 = [Row("outcome labels", gone, why)]
    elif s.labels:
        s97 = []
        for fam in s.label_families():
            labs = s.labels_for(fam)
            total = sum(v for _, v in labs)
            s97.append(Row(fam, labs[0][0],
                           f"n {total:,} — " + ", ".join(f"{k} {v}" for k, v in labs)))
    else:
        s97 = [Row("outcome labels", "not computed",
                   "the last backtest run recorded no label counts")]

    # ── 98: the rule families, each against its own control ───────────────────────────
    if absent:
        s98 = [Row("backtesting", gone, why)]
        n98 = ("No rule on this board has been measured against a baseline or a control in this "
               "build. Until the study is run, treat every signal above as untested.")
    else:
        s98 = [
            Row("run date", s.get("run_date", "unknown"),
                f"the study took {s.get('runtime_seconds', '?')}s and is NOT re-run per board "
                f"build — these numbers are as old as this date"),
            Row("universe", s.get("universe", "?"),
                "symbols, liquidity-stratified. The universe filter's survivorship clause is a "
                "no-op — it drops 3 symbols that have no price bars at all. What it really "
                "excludes is 69 symbols listed after the window opened, and those returned MORE "
                "than the ones kept, so this universe is selection-DEPRESSED at the median, not "
                "flattered. Every comparison below is within it, against the same symbols on the "
                "same dates, so the level cancels either way"),
            Row("observations", s.get("observations", "?"),
                f"stock-days over {s.get('decision_dates', '?')} decision dates"),
            Row("window", f"{s.get('start', '?')} -> {s.get('end', '?')}",
                f"grid every {s.get('grid_stride', '?')} sessions, zones every "
                f"{s.get('zone_stride', '?')}"),
            # STOP is stored as a positive magnitude and is a LOSS; printing it unsigned
            # would read as a second profit target.
            Row("horizon", f"{s.get('horizon', '?')} sessions",
                f"target {_num(s, 'target', '{:+.0%}')} / stop "
                f"{'not computed' if s.num('stop') is None else format(-s.num('stop'), '.0%')}"
                f", chosen once, applied to every family, never tuned"),
            Row("baseline", f"win {_num(s, 'baseline.win_rate', '{:.1%}')}, "
                            f"mean {_num(s, 'baseline.mean')}, "
                            f"median {_num(s, 'baseline.median')}",
                f"n {_num(s, 'baseline.n', '{:,.0f}')}, profit factor "
                f"{_num(s, 'baseline.profit_factor', '{:.2f}')}, max drawdown "
                f"{_num(s, 'baseline.max_drawdown', '{:.1%}')} — buy-and-hold every stock-day "
                f"on the same dates"),
        ]
        for f in s.families:
            if not f.usable:
                s98.append(Row(f.name, "SUPPRESSED",
                               f"n {f.n}, below the {s.get('min_obs', '30')}-observation floor "
                               f"— no rate is reported off a sample that small"))
                continue
            tail = (f"lower-tail p {f.control_p_low:.4f}" if f.verdict == "NEGATIVE"
                    else f"control p {f.control_p:.4f}")
            s98.append(Row(f.name, f.verdict,
                           f"n {f.n:,}, win {f.win:.1%}, mean {f.mean:+.2%}, "
                           f"median {f.median:+.2%}, EXCESS {f.excess:+.2%} "
                           f"(vs same-day non-members {f.excess_lfo:+.2%}), {tail}, "
                           f"positive in {f.years_positive}/{f.years} years"))
        s98 += [
            Row("families with a demonstrated edge",
                f"{s.get('families_edge', '0')} of {s.get('families_tested', '0')}",
                f"the corrected bar: control p below {s.get('bonferroni_p', '?')} "
                f"(0.05 Bonferroni-corrected for the number screened)"
                + (f" — {s.get('families_edge_names')}" if s.get("families_edge_names") else "")),
            Row("families clearing the uncorrected 0.05 only", s.get("families_weak", "0"),
                (s.get("families_weak_names") or "none")
                + " — which is about what screening this many rules produces on data with no "
                  "signal in it"),
            Row("families that are significantly NEGATIVE", s.get("families_negative", "0"),
                (s.get("families_negative_names") or "none")
                + " — measurably WORSE than a date-matched random pick, which is a different "
                  "finding from 'no edge' and must not be read as 'unproven'"),
        ]
        for name, ph, n, exc, pv in s.phases:
            s98.append(Row(f"{name}: non-overlapping phase {ph}", f"{exc:+.2%}",
                           f"n {n:,}, control p {pv:.4f} — the grid overlaps its own horizon, so "
                           f"the full-sample p above is over-confident; every phase is shown "
                           f"because reporting the best one would be the screening error this "
                           f"study exists to avoid"))
        n98 = ("EXCESS is the date-demeaned edge: the family's return minus the mean of EVERY "
               "stock in the universe on the SAME day. On a strong market day every stock looks "
               "accumulated, so that column — not `mean` — is the one that says whether the rule "
               "knew anything. A family is inside that mean, which shrinks its own excess toward "
               "zero in proportion to how much of the universe it holds, so the figure against "
               "the same-day NON-members is given beside it. `control p` is the share of "
               f"{s.get('control_draws', '?')} date-matched random draws that matched or "
               "beat the family; the draws are subsets of each day's universe taken WITHOUT "
               "replacement, so the null is 'pick this many stocks today' and carries the "
               "finite-population correction. A `short` family is an AVOIDANCE signal, "
               "sign-flipped so that 'right' reads positive: NEPSE has no short selling.")

    # ── 99: walk-forward validation ───────────────────────────────────────────────────
    if absent:
        s99 = [Row("walk-forward validation", gone, why)]
    else:
        s99 = [
            Row("scheme", "expanding train -> one validate year -> one test year -> roll",
                "strict calendar order; a test year is never touched before it is tested"),
            Row("folds", s.get("walkforward.folds", "0")),
        ]
        for tr, va, te, ntr, nte, ic, top, bot in s.folds:
            s99.append(Row(f"train {tr} / validate {va} / test {te}", ic,
                           f"n train {ntr:,}, n test {nte:,}, out-of-sample rank IC {ic:+.3f}, "
                           f"top decile {top:+.2%}, bottom decile {bot:+.2%}, "
                           f"spread {top - bot:+.2%}"))
        s99 += [
            Row("mean out-of-sample rank IC", _num(s, "walkforward.mean_oos_ic", "{:+.3f}"),
                f"{s.get('walkforward.folds_positive', '0')} of "
                f"{s.get('walkforward.folds', '0')} folds positive; the IC is against the "
                f"DATE-DEMEANED forward return, so a rising market cannot inflate it"),
            Row("whole-history fit", "never used",
                "weights are fitted on the train block only, the feature subset is chosen on "
                "the validate block, and backtest.importance() refuses any model that did not "
                "come out of a fold"),
        ]

    # ── 100: data leakage controls ────────────────────────────────────────────────────
    s100 = [
        Row("information allowed at decision date D", "everything through D, nothing at or after it",
            "spec section 100"),
        Row("future volume, prices, broker flow and outcomes", "excluded"),
        Row("future normalisation statistics", "excluded",
            "every normalised feature is a trailing percentile of its OWN prior history"
            + ("" if absent else f" — lookback {s.get('leakage.pit_lookback', '?')} sessions, "
                                 f"minimum {s.get('leakage.pit_min', '?')} prior observations")),
        Row("future percentiles", "excluded",
            "a value enters its own ranking window only AFTER it has been scored, so nothing is "
            "ranked against itself and appending later data cannot move an earlier row"),
        Row("future labels", "excluded",
            "the section 97 outcome labels read the forward path only, and are never computed "
            "where a feature is built"),
        Row("entry price", "the OPEN of the session AFTER D",
            "the floorsheet for D is only complete once D has closed, so entering at D's close "
            "would be buying on information that arrived at the same instant"),
        Row("point-in-time on this board", "--upto is threaded into the single load_last call",
            "nothing else in this build reads the filesystem, so there is exactly one place to "
            "get it wrong"),
    ]
    if absent:
        s100.append(Row("how it is tested", gone, why))
    else:
        ex = s.get("leakage.ex_dates_in_window")
        s100.append(Row("outcome bars", "corporate-action ADJUSTED",
                        (f"{ex} ex-dates fall inside the study window; raw bars would book each "
                         f"one as a fake 12-20% loss" if ex else
                         "raw bars would book every bonus/rights ex-date as a fake loss")))
        if s.get("leakage.symbol"):
            s100.append(Row("how it is tested",
                            f"{s.get('leakage.symbol')} rebuilt with the archive truncated at "
                            f"{s.get('leakage.cut')}",
                            f"{s.get('leakage.rows_identical')} observations came back "
                            f"byte-identical in features, percentiles AND zone output — a single "
                            f"forward-looking line anywhere in the panel fails this"))
        else:
            s100.append(Row("how it is tested", "not re-proved by the last run",
                            "backtest.leakage_check() exists but this run recorded no result "
                            "from it"))

    # ── 103: feature stability ────────────────────────────────────────────────────────
    if absent:
        s103 = [Row("feature stability", gone, why)]
    elif s.stability:
        s103 = [Row("features measured", s.get("stability.features", "?"),
                    f"the top {len(s.stability)} by |rank IC| are listed")]
        s103 += [Row(f, ic,
                     f"year sign agreement {agree:.0%}, distribution drift {drift:.2f} sd, "
                     f"sensitivity to the day's largest trade {ex_s:.3f} sd, to a dropped 10% "
                     f"of rows {ms:.3f} sd")
                 for f, ic, agree, drift, ex_s, ms in s.stability]
    else:
        s103 = [Row("feature stability", "not computed",
                    "the last backtest run recorded no stability table")]

    # ── 104: feature importance ───────────────────────────────────────────────────────
    s104 = [
        Row("source", "walk-forward TEST blocks only",
            "spec section 104 forbids importance taken from a leaked or whole-history model"),
        Row("SHAP", "not implemented",
            "it needs a differentiable or tree model and a background distribution; an "
            "approximation called SHAP would be worse than none"),
    ]
    if absent:
        s104.append(Row("feature importance", gone, why))
    else:
        if s.importance:
            s104 += [Row(f, pm, f"permutation {pm:+.4f}, ablation {ab:+.4f}")
                     for f, pm, ab in s.importance]
        else:
            s104.append(Row("per-feature importance", "not computed",
                            "the last run produced no walk-forward model to measure one on"))
        if s.groups:
            s104 += [Row(f"group: {g}", d,
                         "ablation change in out-of-sample IC — positive means removing the "
                         "whole concept HURT the model") for g, d in s.groups]
        else:
            s104.append(Row("feature-group importance", "not computed",
                            "the last run produced no walk-forward model to measure one on"))

    # ── 114: probabilistic output ─────────────────────────────────────────────────────
    s114 = [Row("what the scores on this board are", "RANKING measures, NOT probabilities",
                "spec section 114: before calibration, do not call a score a probability")]
    if absent:
        s114.append(Row("calibration", gone,
                        why + " — so the scores above are uncalibrated AND untested"))
    else:
        # TWO DIFFERENT SCORES, and this section used to conflate them. Everything keyed
        # on `calibration.*` measures backtest._score() — the walk-forward FITTED composite
        # of feature percentiles. The 0-100 "swing score" in section 90 comes out of
        # zones.py and shares none of its inputs. This board printed the composite's
        # flatness under the heading "does the swing score rank? NO", which asserted a
        # measurement of the board's own number that nobody had taken. Each row below now
        # names which score it is about, and the swing score has its own measurement.
        s114 += [
            Row("fitted composite: calibrated", s.get("calibration.calibrated", "not computed"),
                "the walk-forward composite of feature percentiles, NOT the swing score in "
                "section 90 — 'no' means the number ranks but does not predict"),
            Row("fitted composite: reliability slope", _num(s, "calibration.slope", "{:.2f}"),
                "1.00 would mean the score can be read as P(positive return over the horizon)"),
            Row("fitted composite: mean absolute error",
                _num(s, "calibration.mean_abs_error", "{:.1%}"),
                "how far the implied probability sits from the realised rate"),
            Row("fitted composite: bottom bucket win rate",
                _num(s, "calibration.bottom_bucket_win", "{:.1%}"),
                f"of {s.get('calibration.buckets', '?')} equal buckets of the WALK-FORWARD "
                f"FITTED score"),
            Row("fitted composite: top bucket win rate",
                _num(s, "calibration.top_bucket_win", "{:.1%}")),
            Row("fitted composite: verdict", s.get("calibration.note", "not computed")),
            # The rank/no-rank rule lives in backtest.swing_rank() and travels as a word.
            # Re-deriving it from the two bucket numbers here is how the two surfaces drift
            # into disagreeing, which is the whole reason this row was wrong before.
            Row("THE BOARD'S OWN SWING SCORE (section 90)",
                {"yes": "ranks", "BACKWARDS": "ranks BACKWARDS — higher scored WORSE",
                 "no": "does NOT rank", "untested": "not measured by the last run",
                 "": "not measured by the last run"}.get(
                    s.get("swing_score.ranks"), s.get("swing_score.ranks")),
                "measured directly, on every scored stock-day — no fold is needed because "
                "the score is a fixed formula from zones.py with nothing fitted in it"),
            Row("swing score: rank IC", _num(s, "swing_score.ic", "{:+.3f}"),
                f"against the date-demeaned {s.get('horizon', '?')}-session return, over "
                f"{s.get('swing_score.n', '?')} scored stock-days"),
            Row("swing score: top minus bottom bucket",
                _num(s, "swing_score.spread_win", "{:+.1%}"),
                f"on win rate; {_num(s, 'swing_score.spread_excess', '{:+.2%}')} on "
                f"date-demeaned excess, which is the cross-sectional statement"),
            Row("swing score: verdict", s.get("swing_score.note", "not measured by the last run")),
        ]

    # ── 107, 108, 115: the spec's honesty sections. Static, always rendered. ───────────
    s107 = [Row(k, v) for k, v in _S107]
    s108 = [Row(k, v) for k, v in _S108]
    s115 = [Row(k, v, n) for k, v, n in _S115]

    return [
        Section(93, "Expected value", tuple(s93), n93),
        Section(94, "Historical broker edge", tuple(s94),
                "One broker ID pooled across every stock it was the dominant net buyer in. A "
                "broker is not an investor and not a client (section 107), and the shuffle "
                "control below does not remove symbol clustering — a broker riding one good "
                "stock scores like a broker with an edge."),
        Section(95, "Broker-stock historical edge", tuple(s95),
                "One broker on ONE stock, so every row here is a single symbol by construction "
                "and the clustering column reads 100% for all of them. That is exactly why the "
                "best row in this table is the clearest example of a false positive in the "
                "whole study, not the best finding in it."),
        Section(96, "Broker-pair historical edge", tuple(s96),
                "The busiest ordered (buyer, seller) pair over 15 sessions. Pair reciprocity "
                "was tested in this repository and died — it is a trade-count proxy."),
        Section(97, "Outcome labels", tuple(s97),
                "Every label is a statement about the forward path and NOTHING else, so a "
                "label cannot leak into a feature. \"no resolution\" means neither the target "
                "nor the stop was touched inside the horizon, which on this market is the "
                "single most common outcome."),
        Section(98, "Backtesting", tuple(s98), n98),
        Section(99, "Walk-forward validation", tuple(s99),
                "Fitting on the whole history is the error that makes every backtest in this "
                "repository look good before it is walked forward. Train, validate, test, roll — "
                "and never use future floorsheet data to compute a past signal."),
        Section(100, "Data leakage controls", tuple(s100),
                "At decision date D the model may use only information available through D. This "
                "repository has already published a factor that turned out to be nothing but a "
                "full-sample ranking, which is why the guard below is tested rather than asserted."),
        Section(103, "Feature stability", tuple(s103),
                "'year sign agreement' is the share of years whose IC keeps the overall sign — "
                "under about 70% the feature is not stable enough to trade however good the "
                "pooled IC looks. Sensitivities are the mean per-day shift in the feature's own "
                "cross-day standard deviations."),
        Section(104, "Feature importance", tuple(s104),
                "Permutation shuffles one feature's column inside the test block and measures the "
                "fall in out-of-sample rank IC; ablation drops it from the model and re-scores."),
        Section(107, "Important limitations", tuple(s107),
                "The floorsheet records EXECUTED transactions and nothing else. Everything in the "
                "first block is outside what the data can reach — not merely unmeasured here."),
        Section(108, "What the system can and cannot say", tuple(s108),
                "The difference is not tone, it is evidence: the CAN list restates what was "
                "recorded, the CANNOT list asserts identity, intent or the future."),
        Section(114, "Probabilistic output", tuple(s114),
                "The 0-100 scores in sections 90-92 are weighted sums whose weights have not been "
                "fitted or validated out of sample. They order symbols; they do not state odds."),
        Section(115, "Risk management", tuple(s115),
                "Position sizing is deliberately NOT this engine's job. Section 88's invalidation "
                "level is where the flow thesis breaks, not a risk-managed stop, and no number on "
                "this board tells you how much to buy."),
    ]


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

    The number on the row is the CONTRIBUTION — that is the part that sums to the
    score, which is the whole point of printing it — and the row is NAMED as a
    contribution. It used to be named after the bare quantity instead, so a
    weighted component sat on the board under the name of the raw measurement it
    was derived from, in different units. Measured on the shipped board:

    * section 80 printed "confirming families 0.05" for a count of 1. Weight 0.35
      spread over 7 families makes every count land on a multiple of 1/20, and
      contribution == "independent count" / 20 EXACTLY on all 472 symbols carrying
      both (ratio 20.0, zero variance). A count cannot be 0.05.
    * section 80's "contradiction severity" was -0.282374 where section 81's was
      +28.2374 — one quantity, opposite sign, 100x apart, on all 462 symbols
      carrying both, and neither carried a unit.
    * section 80 printed "confirmation strength" twice, 0.651278 as the raw field
      and 0.227947 as this row.
    * section 77 printed "gap" 0.100204 and "short-vs-long score gap" 0.025051.

    Section 76 was never wrong here only because its reason names ("3D bearish")
    happen not to collide with any field name — the same latent bug, unfired.
    """
    return [Row(f"{r.name} contribution", r.contribution,
                f"value {r.value:.4g} x weight {r.weight:g}, normalised {r.normalised:.4g}")
            for r in rs]


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


def build_symbol(symbol: str, upto: str | None = None, history: int = HISTORY,
                 market: hist.MarketPass | None = None, backtest=None) -> store.Detail | None:
    """Assemble every spec section for one symbol. None when there is too little history.

    ``upto`` is the point-in-time guard and is the only date this function trusts;
    ``history`` is how many sessions of context to read (see :data:`HISTORY`).

    ``market`` is the once-per-build ``hist.market_pass`` result that sections 69-71 are
    rendered from. It defaults to None so a single-symbol call still works — it just says
    those sections were not computed, rather than kicking off a 593-symbol scan inside one
    symbol's build.

    ``backtest`` is the once-per-build ``backtest.read_summary()`` digest behind sections
    93-104 and 114 — the same thread-it-in pattern, for the same reason. It differs from
    ``market`` in one way: a missing digest is a REPORTABLE state rather than a reason to
    skip, so when it is None this function reads the file itself (one small ``.txt``,
    free) and, if that is absent too, the sections render and say the study has not run.
    Silently omitting them would read as "considered and found irrelevant".
    """
    symbol = symbol.upper()
    if backtest is None and bt is not None:
        backtest = bt.read_summary()
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
    # loader.load_last drops any session that kept no trades, so it silently narrows the
    # range it was asked for. Re-derive what it was asked for — sessions() is the same
    # directory listing it already used — to say how many were dropped.
    dates = [d for d in loader.sessions(symbol) if not upto or d <= upto]
    skipped = len(dates[-history:] if history > 0 else dates) - len(ses)

    counts = {loader.VALID: 0, loader.WARNING: 0}
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
        # There was a "status INVALID" row here and it could never be anything but 0 —
        # not rarely, never. A session is INVALID exactly when it kept no trades, and
        # loader.load_last drops every session with no trades before returning, so no
        # INVALID session can reach this count. Measured on the shipped board: 0 on 481
        # of 481 symbols, sd exactly 0, one distinct value ever written, while a gate
        # that reads as if it is watching for unusable sessions. The count it was
        # standing in front of is the one below: sessions that ARE in the archive for
        # this symbol and were skipped, which is the fact a reader needs and which
        # nothing on the board said. The other three constant-looking columns in this
        # section are kept because they CAN fire and are simply not firing here:
        # "status WARNING" is non-zero on this board, and "mean quality score" is below
        # 100 on one symbol, which proves a session can score under 100 and therefore
        # that "last session score" is not pinned either.
        Row("sessions skipped", skipped,
            "dates in this symbol's floorsheet archive, inside the loaded range, that "
            "parsed to no usable trade at all and were dropped before any figure on this "
            "board was computed" if skipped else "every date in the loaded range yielded trades"),
        Row("last session status", last.quality.status),
        Row("last session score", last.quality.score),
        Row("last session rows total", last.quality.rows_total),
        Row("last session rows kept", last.quality.rows_kept),
        Row("mean quality score", sum(s.quality.score for s in ses) / len(ses)),
    ]
    q_rows += [Row(f"defect: {k}", v, "sessions affected") for k, v in sorted(seen.items())]
    # Dealer rows are NOT a defect and are deliberately not in the list above. They are
    # reported because a dealer trades its own book while a member broker executes for
    # clients, so it should never be counted as just one more broker without saying so.
    dealer_rows = sum(s.quality.dealer_rows for s in ses)
    if dealer_rows:
        dealer_sessions = sum(1 for s in ses if s.quality.dealer_rows)
        q_rows.append(Row("dealer rows", dealer_rows,
                          f"on {dealer_sessions} of {len(ses)} sessions — NEPSE's dealer account "
                          f"(D01) on one side. Real trades, counted in volume, but a dealer is "
                          f"NOT a member broker: it inflates every broker count and breadth "
                          f"figure below by one wherever it traded"))
    add(4, "Data quality layer", q_rows,
        "Quality is per session; WARNING sessions were still used, with the defect named above. "
        "A dealer row is not a defect — see the note on that row. There is no INVALID count: an "
        "INVALID session is one that kept no trades and the loader drops those before this "
        "section sees them, so it could only ever have read 0. \"sessions skipped\" is how many "
        "went that way.")
    if last.quality.status != loader.VALID:
        warnings.append(f"the {last.date} session is quality {last.quality.status} "
                        f"(score {last.quality.score:.1f}) — every headline number is built on it")
    if skipped:
        warnings.append(f"{skipped} session(s) in the loaded range parsed to no usable trade and "
                        f"were dropped — the {len(ses)} sessions below are not a contiguous range")
    dented = counts.get(loader.WARNING, 0)
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
    # These rank by NET quantity. Sections 24 and 28 rank by GROSS BUY quantity, over all trades
    # and over large trades respectively. Three different questions, and every one of them used to
    # be called "top buyer share", so the board printed 0.00525 and 0.9895 for the same window and
    # both were correct. Measured on the shipped board: the net-ranked and gross-ranked winners are
    # DIFFERENT brokers in 205 of 480 symbols at 30D, and the two shares differ by up to 0.995
    # (BOKD86KA, 3D). The arithmetic was never wrong — the labels were. Keep the word NET here and
    # the word GROSS there, and a reader cannot collapse them into one figure.
    for w in loader.WINDOWS:
        b_rows += [Row(f"{w}D top NET buyer", sf[w].top_buyer, "the broker with the largest net position"),
                   Row(f"{w}D top NET buyer, share of volume", sf[w].top_buyer_share,
                       "that broker's NET buying / window volume — not its gross buying")]
        # SIGN CONVENTION: a net share is signed, and the sign is the side. Section 11's
        # buyer share is positive and section 17's "accumulator #1 net share" is the same
        # positive number (measured: same broker on 481 of 481 symbols, identical value on
        # 481 of 481). This row was the one place that broke it — it emitted the magnitude,
        # so section 12's "top NET seller, share of volume" was positive on 481 of 481
        # (0.0101 to 0.9819) while section 18's "distributor #1 net share" was its exact
        # negation on 481 of 481, same broker, neither row saying so. Negated here to match.
        s_rows += [Row(f"{w}D top NET seller", sf[w].top_seller, "the broker with the largest net position"),
                   Row(f"{w}D top NET seller, share of volume", -sf[w].top_seller_share,
                       "SIGNED: that broker's NET selling / window volume, negative because it "
                       "is selling — not its gross selling")]
    b_rows += _top_rows(buyers, ("broker", "buy_qty", "buy_amt", "buy_trades", "buy_max"), prefix="30D buyer #")
    s_rows += _top_rows(sellers, ("broker", "sell_qty", "sell_amt", "sell_trades", "sell_max"), prefix="30D seller #")
    add(11, "Buyer analysis", b_rows,
        "Broker IDs, not client IDs — a broker is not an investor. Ranked by NET quantity; section "
        "24's \"buy top1\" and section 28's largest gross buyer rank by GROSS buying and routinely "
        "name a different broker.")
    add(12, "Seller analysis", s_rows,
        "Broker IDs, not client IDs — a broker is not an investor. Ranked by NET quantity; section "
        "24's \"sell top1\" ranks by GROSS selling and routinely names a different broker. Net "
        "shares on this board are SIGNED and the sign is the side, so this section's share is "
        "negative; it is the same broker and the same figure as section 18's \"distributor #1 net "
        "share\", which is where the full ranking lives.")

    add(13, "Net broker flow",
        _by_window(lambda w: sf[w], only=("volume", "turnover", "trades", "brokers", "dealers",
                                          "gross_qty", "net_qty",
                                          "net_buyers", "net_sellers", "neutral")),
        "\"dealers\" is how many of \"brokers\" are NEPSE's dealer account rather than a member "
        "broker executing for clients. It is included in every count and breadth figure here, so "
        "it is named rather than left to be assumed away.")

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

    # The row that used to sit here, "{w}D imbalance" = net / gross, was not an imbalance. Every
    # share bought is a share sold, so gross qty is EXACTLY 2 x volume (asserted below) and the
    # numerator is the positive side only — making the ratio non-negative by construction and
    # capped at 0.5. Measured across the shipped board: 1,924 (symbol, window) pairs, range
    # [0.00525, 0.50000], never once negative, so a "buy/sell imbalance" that could not indicate
    # net selling. Worse, it was section 16's flow quality halved — equal to fq/2 in 1,924 of
    # 1,924 pairs. That is the SAME duplicate the board already had removed once between two of
    # its columns (see the note above BOARD_COLUMNS); this is the detail-section half of it.
    #
    # Section 16 keeps the canonical quantity. What goes here instead is the tilt between the top
    # net buyer and the top net seller, which is genuinely signed: measured over the same 1,924
    # pairs it ranges [-0.6953, +0.6494] with 989 negative, 848 positive and 87 flat.
    i_rows: list[Row] = []
    for w in loader.WINDOWS:
        f_ = sf[w]
        i_rows += [
            Row(f"{w}D net qty", f_.net_qty, "shares that changed hands net between brokers"),
            Row(f"{w}D gross qty", f_.gross_qty, "buy + sell, so exactly 2x volume — every share "
                "is bought once and sold once"),
            Row(f"{w}D top-buyer minus top-seller tilt", f_.top_buyer_share - f_.top_seller_share,
                "SIGNED: negative means the largest single net actor is a seller"),
            Row(f"{w}D buyer-seller ratio",
                f_.net_buyers / f_.net_sellers if f_.net_sellers else 0.0,
                "headcount of net buyers / net sellers"),
        ]
    add(15, "Buy/sell imbalance", i_rows,
        "There is NO stock-level buy/sell imbalance and there cannot be one: buy == sell == volume "
        "on every stock every day. The net-to-gross ratio that used to sit here was half of section "
        "16's flow quality under a second name, and could never go negative. The signed figure is "
        "the top-buyer/top-seller tilt.")
    add(16, "Flow quality / net-to-gross",
        [Row(f"{w}D flow quality", sf[w].flow_quality,
             "net qty / volume — the CANONICAL net-to-gross figure on this board") for w in loader.WINDOWS],
        "The share of volume that changed hands NET between brokers, after each broker's own two-way "
        "churn cancels. 1.0 means every share moved in one direction; near 0 means churn. This is the "
        "only place this quantity is reported — section 15 points here rather than restate it.")

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
                    prefix="30D distributor #"),
        "\"net share\" is SIGNED and negative here because these brokers are net sellers — the "
        "same convention as section 17's accumulators, where it is positive. \"distributor #1\" is "
        "the same broker and the same figure as section 12's \"top NET seller\" (measured: same "
        "broker on 481 of 481 symbols); this section carries the ranking, section 12 the headline.")
    add(19, "Persistence score", _by_window(lambda w: flow.persistence(sd[-w:], window=w)),
        "\"positive pct\" / \"negative pct\" / \"neutral pct\" are day counts over \"days\" and "
        "sum to 1. They were each printed a second time in this same section as \"positive "
        "persistence\" / \"negative persistence\" — identical on 1,924 of 1,924 window-rows, both "
        "pairs — and those duplicate rows are gone. \"persistence\" is the DOMINANT side's share "
        "and is a different number from either.")

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
    add(26, "Pareto analysis", _by_window(lambda w: st.pareto[w]),
        "\"buy volume\" and \"buy turnover\" are the GROSS BUY side — every broker's buy quantity "
        "and buy amount ranked against the window's volume and turnover. They were called "
        "\"volume\" and \"turnover\", which read as the two-sided figures they are not: the top "
        "bucket printed BELOW the same window's section 24 \"broker top1\" on 485 board rows "
        "(worst MAKAR 30D, 0.0674 against 0.3723) because a broker can be large on gross activity "
        "and small on buying alone. The buckets rank only the brokers with a positive weight on "
        "that side, so \"top 20%\" is 20% of the brokers who bought, not 20% of \"brokers\". "
        "There is no top-1% bucket: the largest broker count in the universe is 91 and "
        "ceil(n x 0.01) is 1 for every n up to 100, so it could only ever be the single largest "
        "broker — section 24's \"buy top1\", which it equalled on 1,924 of 1,924 rows.")

    add(27, "Large-trade analysis",
        _rows(st.thresholds, prefix="threshold ")
        + _by_window(lambda w: st.large_value[w],
                     only=("threshold", "trades", "trade_pct", "volume", "volume_pct", "turnover",
                           "turnover_pct", "largest_qty", "largest_amt", "top10_pct", "top50_pct",
                           "top100_pct", "buyers", "sellers", "vwap", "vwap_premium", "rate_p10", "rate_p90")),
        "Cutoffs are this stock's own history (rupee basis). The share-count basis is an inverse-price "
        "proxy and is reported under section 29, not used for the cutoff.")
    # Hand-written rather than _by_window because two things have to be said per row that a
    # field-name-derived label cannot say. First, this section's "top buyer" is the largest GROSS
    # buyer of large prints — a different question from section 11's top NET buyer, and the two
    # named different brokers in 205 of 480 symbols. Second, when a window held no large trades at
    # all, the concentration of a set with nothing in it is UNDEFINED, not zero: it is emitted as
    # None (the store writes a blank) with the reason on the row, instead of a 0 that reads as
    # "perfectly diffuse". Measured on the shipped board: 224 such rows across 79 symbols, and
    # every one of them was the empty set.
    lc_rows: list[Row] = []
    for w in loader.WINDOWS:
        lv = st.large_value[w]
        empty = "" if lv.trades else "no large trade in this window — undefined, not zero"
        lc_rows += _rows(lv, prefix=f"{w}D ",
                         only=("trades", "volume_pct", "turnover_pct", "net_qty", "net_share",
                               "persistence"))
        lc_rows += [
            Row(f"{w}D large-buyer hhi", lv.buyer_hhi, empty or "concentration of GROSS buying "
                "across brokers on large prints"),
            Row(f"{w}D large-seller hhi", lv.seller_hhi, empty or "concentration of GROSS selling "
                "across brokers on large prints"),
            Row(f"{w}D largest GROSS buyer", lv.top_buyer,
                empty or "most large-print buying — NOT section 11's top net buyer"),
            Row(f"{w}D largest GROSS buyer, share of large buying", lv.top_buyer_share if lv.trades else None,
                empty or "its gross buy quantity / large-trade volume"),
            Row(f"{w}D largest GROSS seller", lv.top_seller,
                empty or "most large-print selling — NOT section 12's top net seller"),
            Row(f"{w}D largest GROSS seller, share of large selling", lv.top_seller_share if lv.trades else None,
                empty or "its gross sell quantity / large-trade volume"),
        ]
    add(28, "Large-trade conviction",
        lc_rows
        + [Row("large emergence", st.frag_change.large_emergence),
           Row("large disappearance", st.frag_change.large_disappearance)],
        "Shares here are of LARGE-TRADE volume and rank brokers by GROSS side quantity. Sections "
        "11/12 rank by NET quantity over all trades — the two name a different broker more often "
        "than not. A blank means the window held no large trades, so the figure is undefined.")

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
    add(41, "Broker network analysis",
        _rows(network.network(win[w30], pm),
              only=("nodes", "edges", "undirected_edges", "density", "mean_degree", "max_degree",
                    "concentration", "clustering")),
        "No centrality row. The \"central\" broker here was the largest WEIGHTED degree, and an "
        "edge's quantity is added to both of its endpoints, so that is just the largest gross "
        "trader: it equalled section 9's \"broker #1\" on 481 of 481 symbols and its share "
        "equalled section 24's \"30D broker top1\" on 481 of 481. Read those. It was not replaced "
        "with betweenness or eigenvector centrality — on a graph this dense (median density "
        "0.133, mean degree 17 over a median 75 brokers) eigenvector centrality tracks weighted "
        "degree back to the same column, and neither has a hypothesis behind it here.")
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
        # The " z" suffix is section 51's convention and these are the same kind of
        # number, so they carry the same name shape. Without it this section printed
        # "volume", "turnover", "net_flow", "trade_frequency" and "top_pair_share" over
        # Z-SCORES, colliding with the raw quantities those names hold everywhere else
        # on the board: "49 volume" == "51 volume z" exactly on all 481 symbols, ranged
        # [-1.368, 17.283] with 368 of 481 negative, and "top_pair_share" — a share —
        # left [-1, 1] on 84 symbols. A reader scanning for a volume saw a z-score.
        + [Row(f"{c.name} z", c.z, c.reason) for c in an.components]
        + [_seq("flagged", an.flagged)],
        "Every row here is a Z-SCORE against this symbol's own prior sessions, "
        "point-in-time — never the raw quantity the name would otherwise mean.")

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
        add(57, "Price-flow correlation",
            [Row("n", corr.n, f"FIXED {hist.CORR_WINDOW}-session lookback, minus one spent "
                 f"differencing — NOT the {len(ses)} sessions loaded in section 4")]
            + _rows(corr, only=("price_flow", "volume_price", "turnover_price", "sensitivity", "regime")),
            f"Deliberately the last {hist.CORR_WINDOW} sessions on EVERY symbol, so n is the same "
            "everywhere: this reads as \"how has flow tracked price lately\", and a 120-session "
            "correlation is not comparable with a 32-session one. Not evidence of causation or of edge.")
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

    # ── 69-71: market-level, so computed ONCE per build in main() and handed in here ───
    if market is None:
        add(69, "Market-wide floorsheet analysis", [Row("computed", False, _NOT_COMPUTED)],
            "Sections 69-71 are market-level and are built by a market-wide pass, not per symbol.")
    else:
        out += _market_sections(market)

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
            + _reasons(tf.alignment.reasons),
            "\"persistence days\" is the LOOKBACK — how many prior decision dates \"persistence\" "
            "was measured over, from --history — not a count of days this alignment held. It is "
            "the same lookback section 81's contradiction persistence uses, disclosed once here.")
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
            "one piece of evidence, not six. There are 7 families in all, so \"confirming families "
            "contribution\" is 0.35 x count / 7 and the count itself is \"independent count\" "
            "above. The \"... contribution\" rows are the parts of \"confidence\": each is the raw "
            "value in its own note times its weight, and they sum to confidence / 100.")
        add(81, "Signal contradiction",
            _rows(tf.evidence, only=("contradiction_count", "contradiction_severity",
                                     "contradiction_persistence"))
            + [Row(d.name, d.signed, f"{d.family}/{d.kind}, value {d.value:.4g}")
               for d in tf.evidence.contradictions],
            "Contradictions are reported, never netted away against the confirmations above. "
            "\"contradiction severity\" is on a 0-100 scale; section 80 carries the same quantity "
            "as a signed contribution to confidence, in that row's own units. \"contradiction "
            "persistence\" is measured over the same prior decision dates as section 76's "
            "persistence — see \"persistence days\" there for how many; it is one lookback, not "
            "two, and this section no longer restates it.")
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

    # ── 93-115: the measurement of everything above, plus the spec's honesty sections ──
    # Always appended, and always after 92, so the file stays in section order. These do
    # not depend on the symbol at all — the study is cross-sectional — but they belong in
    # every detail file, because that is where a reader sees the entry zone.
    out += _evidence_sections(backtest, upto=upto)

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
        # "brokers" counts NEPSE's dealer account as one more broker, and so do "breadth",
        # "hhi" and "top_broker_share" beside it. Section 13 discloses that per window in
        # the detail file; the board carried the inflated count with no companion column,
        # so the disclosure never reached the one file most readers open. Measured: 5 of
        # 481 rows affected — CBL, LGIL, PIC, SIC and SIL, each brokers=51 of which one is
        # D01. Blank/0 everywhere else, which is the honest answer there.
        "dealers": get.get((13, "30D dealers")),
        "top_broker": get.get((11, "30D top NET buyer")),
        "top_broker_share": get.get((11, "30D top NET buyer, share of volume")),
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
    "breadth", "hhi", "large_pct", "brokers", "dealers", "top_broker", "top_broker_share",
    "anomaly", "regime", "quality", "alignment", "conflict", "age", "entry_low", "entry_high",
    "target", "stop", "warnings", "date",
]
# `dealers` sits next to `brokers` because it qualifies it: it is how many of that count are
# NEPSE's dealer account rather than a member broker. `date` stays last — store.write_board
# forces it there and a column inserted after it would be moved anyway.


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

    # Sections 69-71 are MARKET facts: on a given session they are the same numbers in
    # every one of the 593 detail files. Compute them once, here, and thread them in —
    # per symbol this would be 593 market scans. Over the whole archive, not just `syms`,
    # because "the market" does not mean "the symbols this run happens to build". It reads
    # the prebuilt broker_flow tables, so it is ~45 s whatever `syms` is.
    market = hist.market_pass(loader.symbols(), upto=a.upto)
    if not market.date:
        print("market pass found no data — sections 69-71 will say so", file=sys.stderr)
        market = None
    else:
        print(f"market pass: session {market.date}, {market.active} active symbols, "
              f"{market.brokers} brokers, {len(market.sectors)} sectors, "
              f"{market.skipped} skipped, in {market.seconds:.0f}s", flush=True)

    # Sections 93-104 and 114 are MEASUREMENTS, cross-sectional and symbol-independent, so
    # like the market pass they are read once and threaded in. Unlike it, this is a single
    # small .txt read rather than a 45 s scan — and the study that produced it takes ~470 s
    # and is deliberately NEVER run from here.
    summary = bt.read_summary() if bt is not None else None
    if summary is None:
        print("no backtest summary on disk — sections 93-104/114 will say the study has not "
              "been run", file=sys.stderr)
    else:
        print(f"backtest summary: study run {summary.get('run_date', '?')}, "
              f"{len(summary.families)} rule families, "
              f"{summary.get('families_edge', '?')} of {summary.get('families_tested', '?')} "
              f"with a demonstrated edge", flush=True)

    started = time.time()
    rows: list[dict] = []
    thin: list[str] = []
    failed: list[tuple[str, str]] = []
    shown_traceback = False

    for i, sym in enumerate(syms, 1):
        t0 = time.time()
        try:
            d = build_symbol(sym, upto=a.upto, history=a.history, market=market,
                             backtest=summary)
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

    # Sections 69-71 come from the once-per-build market pass, threaded in — never from
    # the symbol. A 25-symbol slice and a shallow build keep this a self-check rather
    # than a 45-second market scan plus a third full build.
    assert [s.n for s in d.sections if s.n in (69, 70, 71)] == [69], \
        "without a market pass, 69 must stand alone with its not-computed note"
    mp = hist.market_pass(loader.symbols()[:25])
    md = build_symbol(sym, history=40, market=mp)
    assert md is not None
    assert [s.n for s in md.sections if s.n in (69, 70, 71)] == [69, 70, 71], \
        f"market sections missing: {[s.n for s in md.sections]}"
    assert [s.n for s in md.sections] == sorted(s.n for s in md.sections), "sections out of order"
    m69 = next(s for s in md.sections if s.n == 69)
    assert dict((x.metric, x.value) for x in m69.rows)["session"] == mp.date
    assert all(s.rows and s.note for s in md.sections if s.n in (69, 70, 71))

    # Sections 93-115 must be present on EVERY build, run or not. The failure this guards
    # against is silent omission: a section that is simply missing reads as "considered and
    # found irrelevant", which is the opposite of "measured, and it does not work".
    want = [93, 94, 95, 96, 97, 98, 99, 100, 103, 104, 107, 108, 114, 115]
    assert [s.n for s in d.sections if s.n >= 93] == want, [s.n for s in d.sections if s.n >= 93]

    # ...and the same when the study has never been run. Every backtest-derived section must
    # say so in words, and the three honesty sections must render regardless.
    off = {s.n: s for s in _evidence_sections(None)}
    assert sorted(off) == want, sorted(off)
    assert all(s.rows and s.title and s.note for s in off.values()), "an empty evidence section"
    for n in (93, 94, 95, 96, 97, 98, 99, 100, 103, 104, 114):
        assert any("not run" in str(x.value) for x in off[n].rows), \
            f"section {n} hides a missing backtest instead of reporting it"
    for n in (107, 108, 115):
        assert not any("not run" in str(x.value) for x in off[n].rows), \
            f"section {n} is the spec's own text and must not depend on a backtest run"
    assert len(off[107].rows) == len(_S107) and len(off[108].rows) == len(_S108)
    assert sum(1 for x in off[108].rows if x.value == "CANNOT say") == 7

    # A DATED study must not leak into a point-in-time rebuild that predates it. The
    # look-ahead sweep above only catches values that are bare dates; the study's window
    # end hides inside a longer string, so the rule is asserted directly here too.
    if bt is not None:
        live = bt.read_summary()
        if live is not None:
            old = {s.n: s for s in _evidence_sections(live, upto="2000-01-01")}
            for n in (93, 94, 95, 96, 97, 98, 99, 103, 104, 114):
                assert any("not available at 2000-01-01" in str(x.value) for x in old[n].rows), \
                    f"section {n} quoted a study from the future into a rebuild dated before it"
            # ...and it must still be THERE, saying so, not silently dropped.
            assert sorted(old) == want, sorted(old)
            assert len(old[107].rows) == len(_S107), "the spec sections must not depend on a cut"
            # With no cut at all the same digest renders its real, measured verdict.
            now = {s.n: s for s in _evidence_sections(live)}
            assert not any("not available" in str(x.value) for x in now[98].rows)
            assert any(x.metric == "zone_buy" for x in now[98].rows), "section 98 lost its rules"

            # The two things sections 94-96 exist to say, asserted rather than hoped for.
            for n in (94, 95, 96):
                head = now[n].rows[0] if n != 96 else now[n].rows[1]
                assert "candidates screened" == head.metric, (n, head.metric)
                assert "SUPPRESSED" in head.note, f"section {n} dropped the suppressed count"
            # Section 95 is one broker on one stock BY CONSTRUCTION, so every reported row
            # must carry the clustering label. If one ever does not, the label is not firing.
            body = [x for x in now[95].rows if x.metric not in
                    ("candidates screened", "best-of-N control", "result", "historical edge")]
            assert body, "section 95 reported no rows at all"
            assert all("CLUSTERED" in x.note for x in body), \
                "a single-symbol broker-stock row was presented without its clustering caveat"

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
