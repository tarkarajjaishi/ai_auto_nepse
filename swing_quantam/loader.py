"""Floorsheet reading and the data-quality layer — spec sections 1-4.

Every other module in this package codes against the types defined here. Nothing
else in the package touches the filesystem for floorsheet rows.

Three hard-won rules are baked in and must not be relaxed:

1. Numeric columns are parsed with ``int(float(x))``. The 2022-era files (and some
   2025 ones) write quantities as ``50.0``; a plain ``int()`` raises and, if the
   caller skips bad rows quietly, a whole session yields zero trades. That looked
   in practice like NABIL having 227 usable sessions out of 1056 — a 79% silent
   loss that reads as "the symbol didn't trade back then".
2. **The broker columns are not always numeric.** NEPSE's dealer account trades
   under the code ``D01``, and parsing the broker cell with ``int()`` threw on it
   and dropped the row — BOTH sides of it. See :func:`broker_id` and
   :data:`DEALERS` for the reserved-id scheme that fixes it, and :func:`_demo`
   for the external-oracle check that catches it if it ever comes back.
3. ``Trade.amount`` is recomputed as ``quantity * rate``, and ``source_amount``
   keeps the recorded value for the spec's ``amount ~= quantity x rate`` check.

   The reason is NOT that the recorded column is unreliable — that claim was in
   this docstring for a long time and it is false. Measured over the whole
   archive (9,614,462 trades, 2022-2026): every row has an ``amount``, and not
   one of them disagrees with ``quantity * rate``. The column is exact.

   It is recomputed because it must be DERIVED rather than READ, for two
   reasons that survive the column being correct. First, ``amount`` is the only
   field in the row that is redundant with two others, so reading it throws away
   the one cross-check the floorsheet gives us for free; recomputing turns it
   into an assertion instead (that is what ``amount_mismatch`` counts, and on
   this archive it is always 0 — a non-zero count means the file is corrupt, not
   that the era is old). Second, an ``amount`` that is read cannot be kept
   consistent with a ``quantity`` that this parser has already coerced, so a
   ``50.0`` quantity truncated to ``50`` would silently leave turnover computed
   off a different quantity than volume. Every money figure in this package is
   therefore ``quantity * rate`` of the quantity actually used.

   ``1D.txt``'s ``amount`` column is a DIFFERENT column and genuinely is mostly
   absent (populated on 37 of 27,759 symbol-sessions). Do not conflate the two:
   :func:`bars` does not read it.

Pure stdlib on purpose: this runs on a RAM-starved VPS beside the stdlib-only API.
"""

from __future__ import annotations

import os
import re
from typing import NamedTuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLOORSHEET = os.path.join(ROOT, "Master_data", "floorsheet")
SYMBOLS = os.path.join(ROOT, "Master_data", "symbols")

#: Where every artefact this package produces is written. Its own folder, plain .txt.
OUT = os.path.join(ROOT, "Master_data", "swing_quantam")

#: The only decision windows. Longer history exists for baselines, never for a call.
WINDOWS = (3, 7, 15, 30)

HEADER = ("buyer", "seller", "quantity", "rate", "amount", "transaction")

VALID, WARNING, INVALID = "VALID", "WARNING", "INVALID"


# --------------------------------------------------------------------------
# broker identity — see rule 2 in the module docstring
# --------------------------------------------------------------------------

#: Reserved id floor for non-member participants. Real NEPSE member codes are small
#: positive integers: measured over the WHOLE archive (307,969 files, 69,526,892
#: rows) they run **1 to 101**. Nothing here can collide with one.
DEALER_BASE = 900_000


class _Code(int):
    """A broker id that renders as its NEPSE code instead of its number.

    ``broker`` is an ``int`` everywhere in this package — dict key, sort key, rank,
    Counter key. NEPSE's dealer account is written ``D01``, which is not one, and
    the parser used to throw on it and drop the row. Retyping ``broker`` to ``str``
    would ripple through every module; giving the dealer a reserved integer that
    knows its own label does not.

    An instance IS an int: it hashes, sorts, compares and keys identically to the
    number it carries, so every ``dict[int, ...]``, ``sorted()``, ``max()`` and
    ``Counter`` in the package keeps working untouched. It only differs in how it
    prints — ``str()``, ``repr()`` and every f-string render ``D01``, which means
    ``store._fmt`` and all board output get the real code for free rather than a
    bare sentinel number.

    LOAD-BEARING, and the load rests on one line: ``store._fmt`` ends in ``str(v)``
    for anything that is not None/bool/float, and ``_Code`` is an ``int``, so it
    goes down that branch and renders its label. ``__format__`` is NOT overridden —
    it is inherited from ``int`` — so an explicit numeric format spec prints the
    sentinel instead: ``f"{code}"`` gives ``D01`` but ``f"{code:,}"`` gives
    ``900,001`` and ``f"{code:d}"`` gives ``900001``. Verified clean as shipped:
    ``900001`` appears 0 times across all 483 output files. Anyone who changes
    ``store._fmt``'s int branch, or writes a numeric format spec against a broker
    id anywhere in this package, leaks the sentinel onto the board — override
    ``__format__`` here at that point rather than chasing the call sites.
    """

    def __new__(cls, value: int, label: str) -> "_Code":
        self = super().__new__(cls, value)
        self.label = label
        return self

    def __str__(self) -> str:
        return self.label

    __repr__ = __str__


#: Every non-numeric participant code the archive actually contains. The full scan
#: found exactly one: ``D01``, on 9,315 rows (5,385 buyer-side, 3,930 seller-side)
#: across 82 symbols, confined to 2022-01-25 .. 2023-06-07. Singletons on purpose —
#: one object per code, so identity as well as equality holds.
DEALERS: dict[str, _Code] = {"D01": _Code(DEALER_BASE + 1, "D01")}


def is_dealer(broker: int) -> bool:
    """Is this id a dealer account rather than a member broker?

    A dealer trades its own book; a member broker executes for clients. They are
    genuinely different kinds of participant, so anything that counts brokers,
    measures breadth or reports concentration should say when one is in the mix
    rather than quietly folding it into the broker population.
    """
    return broker >= DEALER_BASE


def broker_id(cell: str) -> int:
    """Parse one broker column into a stable id. Raises ValueError if unrecognised.

    Raising on an *unknown* non-numeric code is deliberate: it is the honest
    outcome, and :func:`load` counts it under its own quality warning rather than
    burying it in "unparsable rows". If NEPSE ever adds a second dealer or a
    special account, that warning is what surfaces it — add it to :data:`DEALERS`
    and the rows come back.
    """
    text = cell.strip()
    code = DEALERS.get(text.upper())
    if code is not None:
        return code
    return int(_num(text))


class Trade(NamedTuple):
    """One executed transaction. Only fields the floorsheet actually records."""

    buyer: int
    seller: int
    quantity: int
    rate: float
    amount: float  # recomputed quantity * rate — see module docstring
    contract: str
    source_amount: float  # as written in the file, for the consistency check only


class Quality(NamedTuple):
    status: str  # VALID | WARNING | INVALID
    score: float  # 0-100
    rows_total: int  # data rows seen in the file
    rows_kept: int  # rows that survived validation
    warnings: tuple[str, ...]
    #: Kept rows with a dealer account on either side. NOT a defect and NOT in
    #: ``warnings`` — it is a composition fact, reported so a dealer never counts
    #: silently as an ordinary member broker. See :func:`is_dealer`.
    dealer_rows: int = 0


class Session(NamedTuple):
    symbol: str
    date: str  # YYYY-MM-DD, taken from the filename
    trades: tuple[Trade, ...]
    quality: Quality

    @property
    def volume(self) -> int:
        return sum(t.quantity for t in self.trades)

    @property
    def turnover(self) -> float:
        return sum(t.amount for t in self.trades)

    @property
    def vwap(self) -> float:
        v = self.volume
        return (self.turnover / v) if v else 0.0


class Bar(NamedTuple):
    """A raw, *unadjusted* daily bar — it lines up with floorsheet rates.

    Corporate-action-adjusted bars are a different series and belong only in
    forward-return labelling; see :mod:`swing_quantam.backtest`.
    """

    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


#: Instrument types this engine does not analyse, by ``Master_data/instruments.txt``
#: ``type``. Every metric here is about who is accumulating a COMPANY, and none of these
#: has that story:
#:
#: - **Mutual Fund** — the unit price tracks NAV, not the order flow this engine reads.
#: - **Index** — not tradeable, and has no floorsheet of its own.
#: - **Bond** — a debenture is a fixed-coupon instrument whose price barely moves, so a
#:   zone ladder drawn on it has targets and stops a fraction of a percent apart. That
#:   made the probability screens read as almost-certainties for structural reasons: the
#:   "most likely to rise" top five were all debentures, on ladders whose whole range was
#:   narrower than a single day's move in an ordinary share.
_SKIP_TYPES = frozenset({"Mutual Fund", "Index", "Bond"})

#: Promoter shares are excluded too. They are typed ``Stock``, so they must be caught by
#: NAME, and the name is the only reliable signal: the suffix is not. `NADEP` and `RRHP`
#: are ordinary stocks that merely end in P, and would be wrongly dropped by a suffix
#: rule, while the exchange spells the real ones four ways — "Promoter Share",
#: "PROMOTER SHARE", "Promotor Share" (sic) and lowercase "promotor". Matching `promot`
#: case-insensitively catches all 121 and spares the two lookalikes; an exact-case
#: "Promoter" test missed 8 of them, NABILP among them.
_SKIP_NAME = "promot"

#: Debenture tickers, for the ones ``instruments.txt`` has never heard of. VALIDATED
#: rather than assumed: it matches 0 of the 492 classified Stocks and 0 of the 45
#: classified Mutual Funds, while matching 48 of the 57 classified Bonds — so on the
#: data we can check it against, it is never wrong in the dangerous direction.
#:
#: There is deliberately NO equivalent for funds. The obvious `MF$` rule looks just as
#: reasonable and is wrong: `NMBMF` is NMB Laghubitta Bittiya Sanstha and `SWMF` is
#: Suryodaya Womi Laghubitta — two real microfinance companies. Same trap as the `P`
#: suffix, where NADEP and RRHP are ordinary firms. A fund ticker cannot be recognised
#: from its spelling, so unclassified fund-like symbols are REPORTED, not guessed at.
#: `B\d+` is included and a BARE trailing B is not, and the difference is measured:
#: `B\d{2,4}$` matches 0 of the 492 Stocks, while `B$` matches 20 of them — ADLB, ANLB,
#: GILB and friends, which are Laghubitta banks, not bonds.
_DEBENTURE = re.compile(r"(D|EB|B)\d{2,4}(-\d{2})?(KA|KHA|GA)?$")

#: Named one by one, because no rule can reach them and a wrong rule is worse than a
#: list. Every classifier is silent on these and every pattern that would catch them
#: also catches a real company — `MF$` would delete NMBMF and SWMF, two microfinance
#: firms, and a bare trailing `D` or `B` would delete the Laghubitta banks.
#:
#: The evidence, measured 2026-08-22 rather than inferred from the ticker:
#:
#: - **Twelve trade between Rs 8.70 and Rs 10.50**, which is fund-unit NAV. That is
#:   decisive here because NOT ONE of the 278 analysed companies trades under Rs 15 —
#:   the cheapest is Rs 181.80 and the 10th percentile is Rs 262. There is no overlap
#:   to be wrong about.
#: - **Four have no daily bars at all** (ADBLB, EBLCP, SBBLPO, SHINED): a bond series,
#:   a convertible line, a promoter line and a debenture, none of which the price feed
#:   carries.
#:
#: Revisit if one of these ever starts trading like a company — `test_ops` checks
#: exactly that and will say so.
_SKIP_SYMBOLS = frozenset({
    # fund units, priced at NAV
    "CSY", "GBIMESY2", "HLICF", "LSH12", "MBLEF", "MNMF1",
    "NMBHF2", "RBBF40", "RSY", "RSY2", "SAEF2", "SEF2",
    # no price history: bond / convertible / promoter / debenture lines
    "ADBLB", "EBLCP", "SBBLPO", "SHINED",
})

_excluded_cache: set[str] | None = None


def excluded() -> set[str]:
    """Symbols this engine ignores entirely: mutual funds, indices, promoter shares.

    Read from ``instruments.txt``, which is the exchange's own classification, rather
    than inferred from the ticker. Returns an empty set when that file is missing — an
    unreadable classifier must not silently empty the universe.
    """
    global _excluded_cache
    if _excluded_cache is not None:
        return _excluded_cache
    out: set[str] = set(_SKIP_SYMBOLS)

    # 1. the exchange's own classification. Keyed BOTH spellings of a range: the file
    #    writes `NMBD87/88` where the floorsheet directory is `NMBD87-88`, and that one
    #    character left 38 board symbols looking unclassified — including every debenture
    #    at the top of the probability screens.
    path = os.path.join(ROOT, "Master_data", "instruments.txt")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.readline().rstrip("\n").split("\t")
            i_sym, i_type, i_name = (head.index("symbol"), head.index("type"),
                                     head.index("name"))
            for line in fh:
                f = line.rstrip("\n").split("\t")
                if len(f) <= max(i_sym, i_type, i_name):
                    continue
                if f[i_type] in _SKIP_TYPES or _SKIP_NAME in f[i_name].lower():
                    out.add(f[i_sym])
                    out.add(f[i_sym].replace("/", "-"))
    except (OSError, ValueError):
        return set()

    # 2. sectors.txt is a second, independent classifier and names a few the first misses.
    try:
        with open(os.path.join(ROOT, "Master_data", "sectors.txt"),
                  encoding="utf-8", errors="replace") as fh:
            head = fh.readline().rstrip("\n").split("\t")
            i_sym, i_sec = head.index("symbol"), head.index("sector")
            for line in fh:
                f = line.rstrip("\n").split("\t")
                if len(f) > max(i_sym, i_sec) and f[i_sec] in _SKIP_TYPES:
                    out.add(f[i_sym])
    except (OSError, ValueError):
        pass

    if os.path.isdir(FLOORSHEET):
        listed = os.listdir(FLOORSHEET)

        # 3. the validated debenture spelling, for instruments no classifier listed.
        #    Spaces stripped first: one ticker really is `NICAD 85-86`.
        out.update(d for d in listed if _DEBENTURE.search(d.replace(" ", "")))

        # 4. promoter lines the classifier never listed — recognised by their PARENT, not
        #    by the suffix. A promoter share only exists where the ordinary share does, so
        #    `X` + P/PO counts only when `X` is itself a traded symbol. Measured against
        #    the classified data: 0 of the 371 known non-promoter Stocks have such a
        #    parent, while 117 of the 121 known promoter lines do. That is what makes it
        #    safe where a bare suffix is not — NADEP and RRHP have no parent, so the two
        #    ordinary companies a suffix rule would have deleted survive.
        traded = set(listed)
        for d in listed:
            for suf in ("PO", "P"):
                if d.endswith(suf) and d[: -len(suf)] in traded:
                    out.add(d)
                    break

    _excluded_cache = out
    return out


def unclassified() -> list[str]:
    """Traded symbols no classifier names, so the build can SAY so rather than assume.

    Everything here is analysed as an ordinary company. Some are not — several are
    evidently fund schemes — but no rule this module can defend distinguishes them from a
    real business by ticker alone, and quietly dropping them on a guess would be the same
    mistake as the `MF$` rule that would have deleted two microfinance companies.
    """
    if not os.path.isdir(FLOORSHEET):
        return []
    known: set[str] = set()
    for name in ("instruments.txt", "sectors.txt"):
        try:
            with open(os.path.join(ROOT, "Master_data", name),
                      encoding="utf-8", errors="replace") as fh:
                fh.readline()
                for line in fh:
                    sym = line.split("\t", 1)[0].strip()
                    if sym:
                        known.add(sym)
                        known.add(sym.replace("/", "-"))
        except OSError:
            pass
    return sorted(set(symbols()) - known)


def symbols() -> list[str]:
    """Every ANALYSABLE symbol with at least one floorsheet session, sorted.

    Excludes mutual funds, indices and promoter shares — see :func:`excluded`. This is
    the one place the universe is decided, so every board built from it inherits the
    same list and no screen can disagree with another about what exists.
    """
    if not os.path.isdir(FLOORSHEET):
        return []
    skip = excluded()
    return sorted(
        d for d in os.listdir(FLOORSHEET)
        if d not in skip and os.path.isdir(os.path.join(FLOORSHEET, d))
    )


def sessions(symbol: str) -> list[str]:
    """Dates available for a symbol, oldest first. The filename *is* the date."""
    d = os.path.join(FLOORSHEET, symbol.upper())
    if not os.path.isdir(d):
        return []
    return sorted(f[:-4] for f in os.listdir(d) if f.endswith(".txt"))


def _num(text: str) -> float:
    """Parse a numeric cell. Tolerates '50.0', spaces and thousands commas."""
    return float(text.strip().replace(",", ""))


def load(symbol: str, date: str) -> Session | None:
    """Read one session. Returns None only when the file does not exist.

    A file that exists but parses to nothing still returns a Session — with an
    INVALID quality and the reasons attached. "No trades" and "we could not read
    it" are different facts and the caller must be able to tell them apart.
    """
    symbol = symbol.upper()
    path = os.path.join(FLOORSHEET, symbol, f"{date}.txt")
    if not os.path.isfile(path):
        return None

    # newline="" is deliberate: these files are a mix of CRLF and LF.
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        raw = fh.read()

    lines = [ln for ln in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n") if ln.strip()]
    if lines and lines[0].lower().startswith("buyer"):
        lines = lines[1:]

    trades: list[Trade] = []
    warnings: list[str] = []
    unparsed = 0
    unknown_code = 0
    dealer_rows = 0
    nonpositive = 0
    amount_mismatch = 0
    seen_contracts: set[str] = set()
    dup_contracts = 0
    seen_rows: set[tuple] = set()
    dup_rows = 0

    for ln in lines:
        parts = ln.split("\t") if "\t" in ln else ln.split()
        if len(parts) < 6:
            unparsed += 1
            continue
        # Broker cells are parsed separately from the numeric ones so an unrecognised
        # participant code is reported as itself instead of hiding inside "unparsable
        # rows" — that conflation is exactly how D01 stayed invisible for so long.
        try:
            buyer = broker_id(parts[0])
            seller = broker_id(parts[1])
        except ValueError:
            unknown_code += 1
            continue
        try:
            qty = int(_num(parts[2]))
            rate = _num(parts[3])
            src_amount = _num(parts[4])
        except ValueError:
            unparsed += 1
            continue
        contract = parts[5].strip()

        if qty <= 0 or rate <= 0 or buyer <= 0 or seller <= 0:
            nonpositive += 1
            continue

        amount = qty * rate
        # Spec section 3: amount ~= quantity x rate, as a consistency check only.
        if src_amount > 0 and abs(src_amount - amount) > max(1.0, amount * 0.001):
            amount_mismatch += 1

        if contract:
            if contract in seen_contracts:
                dup_contracts += 1
            else:
                seen_contracts.add(contract)

        key = (buyer, seller, qty, rate, contract)
        if key in seen_rows:
            dup_rows += 1
        else:
            seen_rows.add(key)

        if is_dealer(buyer) or is_dealer(seller):
            dealer_rows += 1
        trades.append(Trade(buyer, seller, qty, rate, amount, contract, src_amount))

    total = len(lines)
    kept = len(trades)
    if unparsed:
        warnings.append(f"{unparsed} unparsable rows")
    if unknown_code:
        warnings.append(f"{unknown_code} rows with an unrecognised broker code")
    # NOTE: dealer_rows is deliberately NOT a warning. A dealer trade is a real NEPSE
    # trade and this session is not defective for containing one — filing it as a
    # defect would flip clean sessions to WARNING and make the status column lie in
    # the opposite direction. It travels as its own Quality field instead.
    if nonpositive:
        warnings.append(f"{nonpositive} rows with a non-positive quantity/rate/broker")
    if amount_mismatch:
        warnings.append(f"{amount_mismatch} rows where amount != quantity x rate")
    if dup_contracts:
        warnings.append(f"{dup_contracts} duplicate contract numbers")
    if dup_rows:
        warnings.append(f"{dup_rows} duplicate rows")

    score = 100.0 * (kept / total) if total else 0.0
    # A mismatched amount does not cost us a row (we recompute it) but it is still
    # a real defect in the source, so it dents the score rather than the row count.
    if total:
        score -= 10.0 * (amount_mismatch / total)
    score = max(0.0, min(100.0, score))

    if kept == 0:
        status = INVALID
    elif warnings:
        status = WARNING
    else:
        status = VALID

    return Session(symbol, date, tuple(trades),
                   Quality(status, score, total, kept, tuple(warnings), dealer_rows))


def load_last(symbol: str, n: int, upto: str | None = None) -> list[Session]:
    """The most recent ``n`` sessions at or before ``upto``, oldest first.

    ``upto`` is the point-in-time guard: pass a decision date and you cannot
    accidentally read a session that had not happened yet. Every window feature
    in this package goes through here for exactly that reason.
    """
    dates = sessions(symbol)
    if upto:
        dates = [d for d in dates if d <= upto]
    out = []
    for d in dates[-n:] if n > 0 else dates:
        s = load(symbol, d)
        if s is not None and s.trades:
            out.append(s)
    return out


def bars(symbol: str, upto: str | None = None) -> list[Bar]:
    """Raw daily bars from the archive, oldest first, unadjusted.

    Unadjusted on purpose — these are compared against floorsheet rates, which
    are the actual executed prices of the day. Adjusting them would break the
    join at every bonus/rights date.
    """
    path = os.path.join(SYMBOLS, symbol.upper(), "1D.txt")
    if not os.path.isfile(path):
        return []
    out: list[Bar] = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for i, ln in enumerate(fh):
            if i == 0 and ln.lower().startswith("date"):
                continue
            p = ln.rstrip("\n").split("\t")
            if len(p) < 8:
                continue
            try:
                bar = Bar(p[0], float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[7] or 0))
            except ValueError:
                continue
            if upto and bar.date > upto:
                break
            out.append(bar)
    return out


def adjusted_bars(symbol: str, upto: str | None = None) -> list[Bar]:
    """Corporate-action-adjusted bars — the ONLY series valid for forward returns.

    ``bars()`` above is raw because it is compared against floorsheet rates. This
    one is spliced by :mod:`prices`, which detects an ex-date from the gap at the
    OPEN (a restatement does not trade through the gap; a limit-down does). Using
    raw bars to label a trade outcome turns every bonus issue into a fake -30%
    loss, which is why the two readers are deliberately separate names.
    """
    try:
        from prices import bars as _adj
    except ImportError:  # running the package standalone, without the repo root
        return bars(symbol, upto)
    b = _adj(symbol.upper())
    if not b:
        return []
    d, o, h, l, c, v = b
    out = [Bar(d[i], o[i], h[i], l[i], c[i], v[i]) for i in range(len(d))]
    return [x for x in out if x.date <= upto] if upto else out


class FlowRow(NamedTuple):
    """One row of the prebuilt broker_flow table: one broker, one date, one stock."""

    date: str
    broker: int
    bought: int
    sold: int
    buy_amount: float
    sell_amount: float
    trades: int

    @property
    def net(self) -> int:
        return self.bought - self.sold


def flow_rows(symbol: str, upto: str | None = None) -> list[FlowRow]:
    """Fast path: read the prebuilt ``Master_data/broker_flow/<SYM>.txt``.

    ``build_broker_flow.py`` has already collapsed all 308k floorsheet files into
    one file per symbol. Anything that only needs per-broker daily quantities over
    a long window should read this instead of re-parsing the raw archive — it is
    the difference between one file and a thousand.

    Two caveats, both real:

    * The money columns are summed from the floorsheet's **recorded** ``amount``.
      That used to be described here as unreliable in the 2022-era files; it is
      not — the recorded column was measured across the whole archive and agrees
      with ``quantity * rate`` on every row (see rule 3 in the module docstring).
      What is genuinely true is narrower: these are SUMS taken by a different
      program, rounded to one decimal on write, so they are not guaranteed to
      match a total this package recomputes to the last paisa. Cross-check a
      money figure against raw trades when the last decimal matters; do not
      distrust it because of its era.
    * There is no trade-level detail here at all — no rates, no sizes, no
      counterparties. Trade-size, fragmentation, volume-at-price, broker pairs and
      the execution network all still need :func:`load`.

    Returns [] when the table has not been built for this symbol.
    """
    path = os.path.join(ROOT, "Master_data", "broker_flow", f"{symbol.upper()}.txt")
    if not os.path.isfile(path):
        return []
    out: list[FlowRow] = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for i, ln in enumerate(fh):
            if i == 0 and ln.lower().startswith("date"):
                continue
            p = ln.rstrip("\r\n").split("\t")
            if len(p) < 8:
                continue
            if upto and p[0] > upto:
                continue  # not sorted by date alone, so filter rather than break
            try:
                out.append(
                    FlowRow(
                        p[0],
                        # build_broker_flow.py keeps the broker cell verbatim, so this
                        # column carries "D01" too. It used to be int()'d and dropped —
                        # the same bug as in load(), on the fast path.
                        broker_id(p[1]),
                        int(float(p[2])),
                        int(float(p[3])),
                        float(p[5]),
                        float(p[6]),
                        int(float(p[7])),
                    )
                )
            except ValueError:
                continue
    return out


def _demo() -> None:
    """Self-check: the parser must not lose sessions to the float-quantity trap."""
    syms = symbols()
    assert syms, f"no floorsheet archive under {FLOORSHEET}"
    sym = "NABIL" if "NABIL" in syms else syms[0]

    dates = sessions(sym)
    assert dates, f"no sessions for {sym}"

    # The regression that motivates this module: parse EVERY session and demand
    # that almost all of them yield trades. The old bug scored 227/1056 here.
    empty = [d for d in dates if not (load(sym, d) or Session(sym, d, (), Quality(INVALID, 0, 0, 0, ()))).trades]
    ratio = 1 - len(empty) / len(dates)
    assert ratio > 0.95, f"{sym}: only {ratio:.0%} of {len(dates)} sessions parsed ({len(empty)} empty)"

    s = load(sym, dates[-1])
    assert s and s.trades, "latest session is empty"
    assert s.volume == sum(t.quantity for t in s.trades)
    assert s.quality.rows_kept == len(s.trades)
    assert 0 <= s.quality.score <= 100
    # VWAP must sit inside the traded range — the cheapest possible sanity check.
    lo = min(t.rate for t in s.trades)
    hi = max(t.rate for t in s.trades)
    assert lo <= s.vwap <= hi, f"vwap {s.vwap} outside [{lo}, {hi}]"

    # Point-in-time guard really cuts.
    cut = dates[len(dates) // 2]
    assert all(x.date <= cut for x in load_last(sym, 10, upto=cut))
    assert all(b.date <= cut for b in bars(sym, upto=cut))
    assert all(r.date <= cut for r in flow_rows(sym, upto=cut))

    # The prebuilt broker_flow table must agree with the raw archive it was built
    # from. If it drifts, every window feature that takes the fast path is wrong,
    # and it would be wrong quietly.
    fr = [r for r in flow_rows(sym) if r.date == s.date]
    if fr:
        assert sum(r.bought for r in fr) == s.volume, (
            f"broker_flow disagrees with raw floorsheet on {sym} {s.date}: "
            f"{sum(r.bought for r in fr):,} vs {s.volume:,}"
        )

    # Adjusted bars are a different series from raw ones and must not be swapped.
    adj = adjusted_bars(sym, upto=cut)
    assert all(b.date <= cut for b in adj)

    # ── the D01 regression: an EXTERNAL oracle, because no internal check can see it ──
    #
    # This parser silently discarded every trade involving NEPSE's dealer account D01,
    # because the broker cell was int()'d and "D01" is not a number: 9,315 rows over 82
    # symbols, confined to 2022-01-25..2023-06-07.
    #
    # WHY NO EXISTING ASSERT CAUGHT IT, and why a new conservation assert never would:
    # a dropped row removes a buyer AND a seller of exactly equal size, so every
    # internal identity still balances perfectly on a session whose total is wrong.
    # brokers._demo's `sum(net_qty) == 0`, `buy_qty == sell_qty == volume` and
    # `gross_qty == 2 * volume` were all GREEN the entire time. Self-consistency is
    # structurally incapable of detecting an ingest bug — it only proves the rows we
    # kept are coherent with each other, never that we kept all of them.
    #
    # So this compares against something the parser cannot influence: 1D.txt's volume,
    # which is NEPSE's own published daily total. Of the 77 affected sessions with a
    # bar, 77/77 match the floorsheet WITH D01 and 0/77 match it without. Two of the
    # worst cases are asserted here; both are single-session reads, so this is cheap.
    #
    #   NMB50 2023-03-30 — every trade that day involved D01: the engine reported 0
    #                      shares against a true 10,000. A -100% session.
    #   NBL   2023-01-02 — 49,063 reported against a true 56,863.
    for tie_sym, tie_date, official in (("NMB50", "2023-03-30", 10_000),
                                        ("NBL", "2023-01-02", 56_863)):
        ts = load(tie_sym, tie_date)
        if ts is None:
            continue  # this archive slice does not carry the symbol; nothing to prove
        assert ts.volume == official, (
            f"{tie_sym} {tie_date}: floorsheet totals {ts.volume:,} shares but NEPSE "
            f"published {official:,}. Rows are being dropped on ingest — check for a "
            f"broker code the parser cannot read (this is exactly how D01 was lost)."
        )
        bar = next((b for b in bars(tie_sym, upto=tie_date) if b.date == tie_date), None)
        assert bar is not None and int(bar.volume) == ts.volume, (
            f"{tie_sym} {tie_date}: floorsheet {ts.volume:,} vs 1D.txt "
            f"{bar.volume if bar else None} — the external oracle disagrees"
        )
        assert ts.quality.dealer_rows > 0, f"{tie_sym} {tie_date} must show its dealer rows"
        assert any(is_dealer(t.buyer) or is_dealer(t.seller) for t in ts.trades)

    # A dealer must be distinguishable and must print as its real code, not a sentinel.
    d01 = DEALERS["D01"]
    assert is_dealer(d01) and not is_dealer(101), "101 is a real member code, not a dealer"
    assert str(d01) == "D01" and f"{d01}" == "D01", "a dealer must render as D01"
    assert d01 == DEALER_BASE + 1 and sorted([d01, 5])[0] == 5, "a dealer id is still an int"

    print(f"loader ok — {sym}: {len(dates)} sessions, {ratio:.1%} parsed, latest {dates[-1]}")
    print(f"  {s.date}: {len(s.trades)} trades, {s.volume:,} shares, vwap {s.vwap:.2f}, {s.quality.status}")


if __name__ == "__main__":
    _demo()
