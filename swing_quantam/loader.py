"""Floorsheet reading and the data-quality layer — spec sections 1-4.

Every other module in this package codes against the types defined here. Nothing
else in the package touches the filesystem for floorsheet rows.

Two hard-won rules are baked in and must not be relaxed:

1. Numeric columns are parsed with ``int(float(x))``. The 2022-era files (and some
   2025 ones) write quantities as ``50.0``; a plain ``int()`` raises and, if the
   caller skips bad rows quietly, a whole session yields zero trades. That looked
   in practice like NABIL having 227 usable sessions out of 1056 — a 79% silent
   loss that reads as "the symbol didn't trade back then".
2. The recorded ``amount`` column is unreliable across eras, so ``Trade.amount``
   is always recomputed as ``quantity * rate``. The recorded value is kept as
   ``source_amount`` purely so the spec's ``amount ~= quantity x rate`` check has
   something to compare against.

Pure stdlib on purpose: this runs on a RAM-starved VPS beside the stdlib-only API.
"""

from __future__ import annotations

import os
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


def symbols() -> list[str]:
    """Every symbol with at least one floorsheet session, sorted."""
    if not os.path.isdir(FLOORSHEET):
        return []
    return sorted(
        d for d in os.listdir(FLOORSHEET) if os.path.isdir(os.path.join(FLOORSHEET, d))
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
        try:
            buyer = int(_num(parts[0]))
            seller = int(_num(parts[1]))
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

        trades.append(Trade(buyer, seller, qty, rate, amount, contract, src_amount))

    total = len(lines)
    kept = len(trades)
    if unparsed:
        warnings.append(f"{unparsed} unparsable rows")
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

    return Session(symbol, date, tuple(trades), Quality(status, score, total, kept, tuple(warnings)))


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

    * The money columns are summed from the floorsheet's **recorded** ``amount``,
      which is unreliable in the 2022-era files. Quantities are trustworthy;
      treat ``buy_amount``/``sell_amount`` as indicative and recompute from raw
      trades (``quantity * rate``) whenever the figure actually matters.
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
                        int(float(p[1])),
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

    print(f"loader ok — {sym}: {len(dates)} sessions, {ratio:.1%} parsed, latest {dates[-1]}")
    print(f"  {s.date}: {len(s.trades)} trades, {s.volume:,} shares, vwap {s.vwap:.2f}, {s.quality.status}")


if __name__ == "__main__":
    _demo()
