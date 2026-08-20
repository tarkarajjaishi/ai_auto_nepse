"""Scan every symbol's latest session and list which ones are flagging BUY or SELL.

Writes Master_data/scan.txt — one row per symbol, newest session, with the state of each
signal that fed the decision, so a flag can always be traced back to why:

    symbol  date  close  change_pct  volume  trend  structure  swing  swing_age  broker_net
    signal  badge  badge_age  badge_since  stop  target1  target2  risk_pct

    trend       close above/below the ALMA wave
    structure   direction of the last BOS / CHoCH break
    swing       last confirmed pivot at STRUCTURE sensitivity (7) — BUY at a low, SELL at a high
    swing_age   sessions since that pivot was confirmable (it is never same-day news)
    broker_net  biggest single broker's net, as a share of the session's volume
    signal      the VERDICT: BUY/SELL when trend and structure agree, else WATCH
    badge       the chart's BUY/SELL badge, at SWING sensitivity (10) — never WATCH
    badge_age   sessions since that badge printed; badge_since is its date
    stop … risk_pct   indicators.trade_levels() for the BADGE: the stop sits at the pivot that
                would invalidate it, targets at 1R and 2R, risk as a % of price

**Three different things wear the word "signal" in this project, so they are named apart here.**
`signal` is the verdict (trend AND structure agree). `badge` is the chart's last pivot mark — the
one the Streamlit Scanner shows in its own "signal" column. `swing` is that same pivot idea at the
structure sensitivity, kept only because existing readers index it positionally. A screen showing
one under the other's name tells the trader a different rule fired than actually did.

BUY needs trend and structure both up; SELL needs both down. Anything else is WATCH.
Run it from daily_update.py after the fetches, or on its own:

    python scan.py
"""

from pathlib import Path

from fetch_ohlc import MASTER
from prices import ex_dates
from indicators import alma, atr, pivots, structure, trade_levels

OUT = MASTER / "scan.txt"
BARS = 250
WAVE, SENS = 21, 7
# The badge runs at its OWN sensitivity, matching the Streamlit sidebar's "Swing sensitivity"
# default of 10. Reusing SENS=7 here would put a different rule on screen under the same name.
SIG_SENS = 10
HEADER = ("symbol\tdate\tclose\tchange_pct\tvolume\ttrend\tstructure\tswing\tswing_age"
          "\tbroker_net\tsignal\tbadge\tbadge_age\tbadge_since"
          "\tstop\ttarget1\ttarget2\trisk_pct")


def read_bars(symbol, limit=BARS):
    """Archive rows, newest last — corporate-action adjusted for SYMBOLS.

    The prices here feed alma / pivots / structure, and a raw ex-date gap is a -50% bar that
    never traded: it invents a swing low and a trend break out of arithmetic. Adjusted in place
    rather than via prices.bars() because the callers read these rows POSITIONALLY (r[4] close,
    r[2] high, r[3] low, r[0] date). Indices carry no corporate actions and are left alone.
    """
    for kind in ("symbols", "indices"):
        path = MASTER / kind / symbol.replace("/", "-") / "1D.txt"
        if not path.exists():
            continue
        rows = [l.split("\t") for l in path.read_text(encoding="utf-8").splitlines()[1:]][-limit:]
        rows = [r for r in rows if len(r) > 4 and r[4] not in ("", "None")]
        if kind != "symbols" or not rows:
            return rows
        try:
            dates = [r[0] for r in rows]
            closes = [float(r[4]) for r in rows]
            opens = [float(r[1]) for r in rows]
        except (ValueError, IndexError):
            return rows                       # unparseable tail: raw beats crashing the scan
        for i, fac in reversed(ex_dates(dates, closes, opens)):
            for j in range(i):
                for col in (1, 2, 3, 4):      # open, high, low, close — volume is not a price
                    try:
                        rows[j][col] = str(float(rows[j][col]) * fac)
                    except (ValueError, IndexError):
                        pass
        return rows
    return []


def top_broker_share(symbol, date):
    """Largest single broker's net position that session, as a share of volume."""
    path = MASTER / "broker_flow" / f"{symbol.replace('/', '-')}.txt"
    if not path.exists():
        return ""
    best, volume = 0.0, 0.0
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        f = line.split("\t")
        if f[0] != date:
            continue
        volume += float(f[2])
        best = max(best, float(f[4]))
    return f"{best / volume:.3f}" if volume else ""


def scan(symbol):
    rows = read_bars(symbol)
    if len(rows) < WAVE + 2 * max(SENS, SIG_SENS) + 2:
        return None
    date = rows[-1][0]
    close = [float(r[4]) for r in rows]
    high = [float(r[2]) for r in rows]
    low = [float(r[3]) for r in rows]

    wave = alma(close, WAVE)
    trend = "up" if wave[-1] is not None and close[-1] >= wave[-1] else "down"

    ph, pl = pivots(high, low, SENS, SENS)
    events = structure(close, ph, pl)
    struct = f"{events[-1][2]}-{events[-1][3]}" if events else "none"
    struct_dir = events[-1][3] if events else ""

    swing, age = "", ""
    for i in range(len(close) - 1, -1, -1):
        if ph[i] is not None or pl[i] is not None:
            swing = "SELL" if ph[i] is not None else "BUY"
            age = len(close) - 1 - i          # bars since the pivot bar itself
            break

    if trend == "up" and struct_dir == "up":
        signal = "BUY"
    elif trend == "down" and struct_dir == "down":
        signal = "SELL"
    else:
        signal = "WATCH"

    # The chart's badge, and the levels that belong to IT rather than to the verdict. A
    # pivot low prints BUY, a pivot high SELL, and it stands until the next one replaces it
    # -- so every symbol is on one side or the other and `badge` is never WATCH.
    sh, sl = pivots(high, low, SIG_SENS, SIG_SENS)
    badge, badge_age, badge_since, pivot_px = "", "", "", None
    for i in range(len(close) - 1, -1, -1):
        if sh[i] is not None or sl[i] is not None:
            badge = "SELL" if sh[i] is not None else "BUY"
            pivot_px = sh[i] if sh[i] is not None else sl[i]
            badge_age = len(close) - 1 - i
            badge_since = rows[i][0]
            break
    if not badge:          # no pivot in the window yet -- fall back to the side of the wave
        badge = "BUY" if trend == "up" else "SELL"

    atr_now = next((v for v in reversed(atr(high, low, close)) if v is not None), None)
    stop, t1, t2, risk_pct = trade_levels(badge, close[-1], pivot_px, atr_now)

    return "\t".join("" if v is None else str(v) for v in (
        symbol, date, rows[-1][4], rows[-1][6] or "", rows[-1][7] or "",
        trend, struct, swing, age, top_broker_share(symbol, date), signal,
        badge, badge_age, badge_since, stop, t1, t2, risk_pct,
    ))


# Column positions resolved from HEADER, never counted by hand. `signal` used to be the
# last field and main() read it as one; appending the badge and its levels moved it, and
# the verdict tally started counting risk percentages. It raised rather than lying, but a
# name lookup means the next append cannot reach this code at all.
_COL = {name: i for i, name in enumerate(HEADER.split("\t"))}


def field(row, name):
    """One named field out of a finished row."""
    return row.split("\t")[_COL[name]]


def main():
    names = (MASTER / "symbols.txt").read_text(encoding="utf-8").split()
    lines, counts = [], {"BUY": 0, "SELL": 0, "WATCH": 0}
    for name in names:
        row = scan(name)
        if row:
            lines.append(row)
            counts[field(row, "signal")] += 1

    # buys first, then sells, each ordered by the day's move
    order = {"BUY": 0, "SELL": 1, "WATCH": 2}
    lines.sort(key=lambda r: (order[field(r, "signal")], -float(field(r, "change_pct") or 0)))
    OUT.write_text(HEADER + "\n" + "\n".join(lines) + "\n", encoding="utf-8")

    print(f"scanned {len(lines)} symbols -> {OUT}")
    print(f"  BUY {counts['BUY']}   SELL {counts['SELL']}   WATCH {counts['WATCH']}")
    for line in lines[:10]:
        f = line.split("\t")
        print(f"  {f[10]:5} {f[0]:10} {f[1]}  close={f[2]:>9}  {f[3]:>6}%  trend={f[5]:4} {f[6]}")


if __name__ == "__main__":
    main()
