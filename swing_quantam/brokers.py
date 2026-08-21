"""Broker aggregates, net flow and flow normalisation — spec sections 11-16.

This module is the contract the rest of the package aggregates on top of. It
answers, for a broker over one session or one window: how much did it buy, how
much did it sell, at what average price, and how big is that relative to the
stock itself.

The normalisation in :class:`StockFlow` is the part that matters. Raw net flow is
not comparable across stocks — 50,000 shares is a rounding error in one name and
a week's volume in another — so every downstream feature reads the *share* fields
rather than the raw ones.

Naming discipline from spec section 2 applies to everything here: these are
execution facts about broker numbers. A broker may act for many clients, so
"broker 58 had net buying" is sayable and "investor X accumulated" is not.
"""

from __future__ import annotations

from typing import Iterable, NamedTuple

from .loader import Session, Trade, is_dealer


class BrokerDay(NamedTuple):
    """One broker's activity in one stock, over a session or a whole window."""

    broker: int
    buy_qty: int
    buy_amt: float
    buy_trades: int
    buy_max: int  # largest single purchase, shares
    sell_qty: int
    sell_amt: float
    sell_trades: int
    sell_max: int  # largest single sale, shares

    # -- section 13: net flow -------------------------------------------------
    @property
    def net_qty(self) -> int:
        return self.buy_qty - self.sell_qty

    @property
    def net_amt(self) -> float:
        return self.buy_amt - self.sell_amt

    # -- section 16: net-to-gross --------------------------------------------
    @property
    def gross_qty(self) -> int:
        return self.buy_qty + self.sell_qty

    @property
    def gross_amt(self) -> float:
        return self.buy_amt + self.sell_amt

    @property
    def trades(self) -> int:
        return self.buy_trades + self.sell_trades

    @property
    def buy_vwap(self) -> float:
        return self.buy_amt / self.buy_qty if self.buy_qty else 0.0

    @property
    def sell_vwap(self) -> float:
        return self.sell_amt / self.sell_qty if self.sell_qty else 0.0

    @property
    def imbalance(self) -> float:
        """Section 15. -1 = pure selling, 0 = balanced, +1 = pure buying."""
        g = self.gross_qty
        return (self.buy_qty - self.sell_qty) / g if g else 0.0

    @property
    def flow_quality(self) -> float:
        """Section 16. |net| / gross. High = directional, low = two-way churn."""
        g = self.gross_qty
        return abs(self.net_qty) / g if g else 0.0

    @property
    def side(self) -> str:
        if self.net_qty > 0:
            return "buyer"
        if self.net_qty < 0:
            return "seller"
        return "neutral"

    def plus(self, other: "BrokerDay") -> "BrokerDay":
        return BrokerDay(
            self.broker,
            self.buy_qty + other.buy_qty,
            self.buy_amt + other.buy_amt,
            self.buy_trades + other.buy_trades,
            max(self.buy_max, other.buy_max),
            self.sell_qty + other.sell_qty,
            self.sell_amt + other.sell_amt,
            self.sell_trades + other.sell_trades,
            max(self.sell_max, other.sell_max),
        )


_EMPTY = (0, 0.0, 0, 0)


def _blank(broker: int) -> BrokerDay:
    return BrokerDay(broker, *_EMPTY, *_EMPTY)


def from_trades(trades: Iterable[Trade]) -> dict[int, BrokerDay]:
    """Aggregate raw trades into per-broker totals.

    A broker that appears on both sides is one entry, not two — that is the whole
    point of net flow. Self-trades (buyer == seller, section 47) contribute to
    both sides and therefore cancel in net, which is the honest treatment.
    """
    out: dict[int, BrokerDay] = {}
    for t in trades:
        b = out.get(t.buyer) or _blank(t.buyer)
        out[t.buyer] = b._replace(
            buy_qty=b.buy_qty + t.quantity,
            buy_amt=b.buy_amt + t.amount,
            buy_trades=b.buy_trades + 1,
            buy_max=max(b.buy_max, t.quantity),
        )
        s = out.get(t.seller) or _blank(t.seller)
        out[t.seller] = s._replace(
            sell_qty=s.sell_qty + t.quantity,
            sell_amt=s.sell_amt + t.amount,
            sell_trades=s.sell_trades + 1,
            sell_max=max(s.sell_max, t.quantity),
        )
    return out


def day(session: Session) -> dict[int, BrokerDay]:
    """Section 11/12/13 — per-broker totals for one session."""
    return from_trades(session.trades)


def window(sessions: Iterable[Session]) -> dict[int, BrokerDay]:
    """The same aggregate summed over several sessions (3D/7D/15D/30D)."""
    out: dict[int, BrokerDay] = {}
    for s in sessions:
        for broker, bd in from_trades(s.trades).items():
            out[broker] = out[broker].plus(bd) if broker in out else bd
    return out


class StockFlow(NamedTuple):
    """Section 14 — the normalised view. Shares, not raw sizes.

    ``top_buyer``/``top_seller`` are the single largest net positions, which the
    breadth and concentration layers need in order to tell "many brokers buying"
    apart from "one broker doing all of it".

    ``dealers`` exists because ``brokers`` — and therefore ``breadth``,
    ``breadth_ratio`` and every concentration figure built on them — counts
    NEPSE's dealer account as one more participant. It is one, but it is not a
    member broker executing for clients, so a reader comparing breadth across
    sessions is told when one is in the count instead of having to know.
    """

    volume: int
    turnover: float
    trades: int
    brokers: int
    net_buyers: int
    net_sellers: int
    neutral: int
    top_buyer: int | None
    top_buyer_share: float  # that broker's net buying / stock volume
    top_seller: int | None
    top_seller_share: float
    gross_qty: int
    net_qty: int  # section 13: total net buying, i.e. shares that changed hands net
    flow_quality: float  # section 16, at stock level: net_qty / volume
    #: How many of ``brokers`` are dealer accounts, not member brokers. Trailing with
    #: a default so the two positional constructions below stay valid.
    dealers: int = 0

    @property
    def breadth(self) -> float:
        """Net buyers as a fraction of participating brokers."""
        return self.net_buyers / self.brokers if self.brokers else 0.0

    @property
    def breadth_ratio(self) -> float:
        return self.net_buyers / self.net_sellers if self.net_sellers else float(self.net_buyers)


def stock_flow(agg: dict[int, BrokerDay]) -> StockFlow:
    """Roll per-broker aggregates up to the stock and normalise.

    Volume is the sum of BUY quantity, not of gross: every share is bought once
    and sold once, so gross double-counts the session.
    """
    if not agg:
        return StockFlow(0, 0.0, 0, 0, 0, 0, 0, None, 0.0, None, 0.0, 0, 0, 0.0)

    volume = sum(b.buy_qty for b in agg.values())
    turnover = sum(b.buy_amt for b in agg.values())
    trades = sum(b.buy_trades for b in agg.values())

    buyers = [b for b in agg.values() if b.net_qty > 0]
    sellers = [b for b in agg.values() if b.net_qty < 0]
    neutral = len(agg) - len(buyers) - len(sellers)

    tb = max(buyers, key=lambda b: b.net_qty, default=None)
    ts = min(sellers, key=lambda b: b.net_qty, default=None)

    gross = sum(b.gross_qty for b in agg.values())
    # Section 15 lists an imbalance "for Stock". There isn't one: every share is
    # bought by somebody and sold by somebody, so stock-level buy == sell == volume
    # and (buy - sell) / (buy + sell) is identically 0.000 on every stock, every
    # day, forever. The same conservation kills any quantity imbalance over a
    # subset picked without an aggressor side, large trades included -- a big print
    # has one buyer and one seller of equal size. Measured on real sessions before
    # this was cut, so it does not come back as a column of zeros pretending to be
    # a signal. Imbalance stays where it is real: per broker, and per broker-stock.
    #
    # What IS non-degenerate at stock level is how that volume is DISTRIBUTED
    # across brokers -- net_qty (shares that changed hands net, after each broker's
    # own two-way churn cancels) over volume. Two sessions with the same volume and
    # very different net_qty are genuinely different sessions.
    pos = sum(b.net_qty for b in buyers)

    return StockFlow(
        volume=volume,
        turnover=turnover,
        trades=trades,
        brokers=len(agg),
        net_buyers=len(buyers),
        net_sellers=len(sellers),
        neutral=neutral,
        top_buyer=tb.broker if tb else None,
        top_buyer_share=(tb.net_qty / volume) if tb and volume else 0.0,
        top_seller=ts.broker if ts else None,
        top_seller_share=(-ts.net_qty / volume) if ts and volume else 0.0,
        gross_qty=gross,
        net_qty=pos,
        flow_quality=(pos / volume) if volume else 0.0,
        dealers=sum(1 for b in agg if is_dealer(b)),
    )


def flow_intensity(bd: BrokerDay, flow: StockFlow) -> float:
    """Section 14 — broker net quantity as a share of the stock's own volume."""
    return bd.net_qty / flow.volume if flow.volume else 0.0


def turnover_flow_intensity(bd: BrokerDay, flow: StockFlow) -> float:
    """Section 14 — the same idea in money terms."""
    return bd.net_amt / flow.turnover if flow.turnover else 0.0


def shares(bd: BrokerDay, flow: StockFlow) -> dict[str, float]:
    """Section 14 — every 'share of the stock' figure for one broker."""
    vol, to, tr = flow.volume, flow.turnover, flow.trades
    return {
        "buy_share": bd.buy_qty / vol if vol else 0.0,
        "sell_share": bd.sell_qty / vol if vol else 0.0,
        "net_share": bd.net_qty / vol if vol else 0.0,
        "turnover_share": bd.gross_amt / (2 * to) if to else 0.0,
        "trade_share": bd.trades / (2 * tr) if tr else 0.0,
        "flow_intensity": flow_intensity(bd, flow),
        "turnover_flow_intensity": turnover_flow_intensity(bd, flow),
    }


def _demo() -> None:
    from . import loader

    syms = loader.symbols()
    sym = "NABIL" if "NABIL" in syms else syms[0]
    ses = loader.load_last(sym, 30)
    assert ses, f"no sessions for {sym}"

    s = ses[-1]
    agg = day(s)
    flow = stock_flow(agg)

    # Every share is bought once and sold once.
    assert sum(b.buy_qty for b in agg.values()) == sum(b.sell_qty for b in agg.values()) == s.volume
    # Net flow across all brokers must cancel exactly.
    assert sum(b.net_qty for b in agg.values()) == 0, "net flow does not net to zero"
    assert abs(sum(b.net_amt for b in agg.values())) < 1.0

    assert flow.volume == s.volume
    assert flow.brokers == len(agg)
    assert flow.net_buyers + flow.net_sellers + flow.neutral == flow.brokers
    assert 0.0 <= flow.top_buyer_share <= 1.0
    assert 0.0 <= flow.flow_quality <= 1.0

    # Guard the reason section 15's stock-level imbalance was cut: it is not that
    # it was small, it is that conservation pins it to exactly zero. If anyone
    # reintroduces it this fails loudly on real data instead of shipping zeros.
    assert sum(b.buy_qty for b in agg.values()) - sum(b.sell_qty for b in agg.values()) == 0
    assert flow.net_qty > 0, "net_qty is the non-degenerate replacement and must be positive"

    # The same conservation, one level up, and the reason section 15 no longer prints net/gross as
    # an "imbalance": gross DOUBLE-COUNTS volume, so net/gross is flow_quality/2 exactly — one
    # number under two names, and non-negative by construction so it can never show net selling.
    # Both halves are asserted here so a re-added ratio fails loudly instead of shipping.
    assert flow.gross_qty == 2 * flow.volume, "gross qty must be exactly twice volume"
    assert abs(flow.net_qty / flow.gross_qty - flow.flow_quality / 2.0) < 1e-12, \
        "net/gross is half of flow quality — it is not an independent measure"

    for b in agg.values():
        assert -1.0 <= b.imbalance <= 1.0
        assert 0.0 <= b.flow_quality <= 1.0
        if b.buy_qty:
            assert b.buy_vwap > 0

    # A window is the sum of its days.
    w = window(ses)
    assert sum(b.buy_qty for b in w.values()) == sum(x.volume for x in ses)
    assert sum(b.net_qty for b in w.values()) == 0

    top = max(w.values(), key=lambda b: b.net_qty)
    print(f"brokers ok — {sym} {s.date}: {flow.brokers} brokers, {flow.net_buyers} net buyers, "
          f"net {flow.net_qty:,} of {flow.volume:,} shares, flow quality {flow.flow_quality:.3f}")
    print(f"  30D top accumulator: broker {top.broker} net {top.net_qty:+,} shares "
          f"({flow_intensity(top, stock_flow(w)):+.2%} of window volume)")


if __name__ == "__main__":
    _demo()
