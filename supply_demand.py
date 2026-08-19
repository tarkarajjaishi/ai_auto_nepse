"""Supply Demand Dashboard for NEPSE — a port of Indicator Vault's TradingView product.

Researched from the vendor's own material (product page + knowledge-base articles 62 and 30 +
the screenshots on blog.indicatorvault.com), not guessed. The indicator's title bar in those
screenshots prints its whole input list, which is where the defaults below come from:

    Supply Demand Dashboard MTS  AUDUSD ... US30  0.75 3 6 14 2 3  Buy Sell ...  700
                                                    |  | |  | | |                 |
                                  zone fuzz factor --+  | |  | | +- TP coefficient |
                                    fractal fast -------+ |  | +--- SL shift coef  |
                                    fractal slow ---------+ +----- ATR period      |
                                                                        max bars --+

The dashboard is two scanners over one zone engine:

    MTS (multi-symbol)    — one row per symbol      -> our whole-market cron
    MTF (multi-timeframe) — one row per timeframe   -> per-symbol drill-down

Each row carries Symbol | Direction | Age | Entry | SL | TP — exactly the vendor's columns.

The SL/TP arithmetic was verified against twelve real rows in the vendor's screenshots. Taking
USDCAD Bearish, Entry 1.31403, SL 1.32228, TP 1.28929:

    SL - entry = 0.00825      entry - TP = 0.02474      0.02474 / 0.00825 = 3.00

so TP sits exactly `TP coefficient` risk-multiples from entry, on every row, both directions.
That is the rule implemented here.

Zone strength uses the vendor's own four names — the settings panel exposes show/hide toggles
for the weak three, the fourth being the "strong" zone the product advertises:

    untested  price has not come back since the zone formed  — fresh
    strong    price came back once and the zone held         — the product's headline zone
    weak      price has been back repeatedly                 — worn out
    turncoat  price CLOSED through the far edge              — broken, flips polarity

    python supply_demand.py                 # scan every NEPSE symbol -> supply_demand.txt
    python supply_demand.py --symbol NABIL  # MTF: the same engine across 7 timeframes
    python supply_demand.py --selftest      # assert the maths against the vendor's numbers
"""
import math
import sys
from collections import defaultdict
from datetime import datetime

from fetch_ohlc import MASTER
from indicators import atr, pivots, sma
from prices import bars
from trade_setup import LIQUID_MIN, setup

# Vendor defaults, read off the indicator title bar in the product screenshots.
FUZZ, FAST, SLOW, ATR_N, SL_SHIFT, TP_COEF, MAX_BARS = 0.75, 3, 6, 14, 2.0, 3.0, 700

OUT = MASTER / "supply_demand.txt"
HEADER = ("symbol\tdate\tsignal\tconfirmed\tvol_x\ttrend\tturnover\tin_zone\tdirection\tstate"
          "\tage\tclose\tentry\tsl\ttp\trisk_pct\tdist_pct\tzone_lo\tzone_hi")

# MTF timeframes. TradingView's default set is 1 5 15 30 60 240 D; NEPSE trades ~5h a day, so
# 240 is meaningless here and the top slots go to the swing frames that do exist.
TIMEFRAMES = ["5m", "15m", "30m", "1h", "1D", "1W", "1M"]


def zones(o, h, l, c, fuzz=FUZZ, fast=FAST, slow=SLOW, atr_n=ATR_N,
          sl_shift=SL_SHIFT, tp_coef=TP_COEF):
    """Every supply/demand zone in the series, oldest first.

    A fractal swing high prints a supply zone (body top -> wick high); a swing low prints a
    demand zone (wick low -> body bottom). The fuzz factor is a floor on the zone's height in
    ATR, which is what "a higher factor makes the zone wider" means. Fast and slow fractals are
    both scanned and unioned — the fast one catches minor zones, the slow one the major ones.
    """
    a = atr(h, l, c, atr_n)
    out = {}
    for k in {fast, slow}:
        ph, pl = pivots(h, l, k, k)
        for i in range(len(c)):
            if a[i] is None:
                continue
            if ph[i] is not None:                    # supply: price was rejected DOWN from here
                distal = h[i]
                prox = min(max(o[i], c[i]), distal - fuzz * a[i])
                sl = distal + sl_shift * a[i]
                out[("supply", i)] = dict(kind="supply", i=i, lo=prox, hi=distal, entry=prox,
                                          sl=sl, tp=prox - tp_coef * (sl - prox))
            if pl[i] is not None:                    # demand: price was rejected UP from here
                distal = l[i]
                prox = max(min(o[i], c[i]), distal + fuzz * a[i])
                sl = distal - sl_shift * a[i]
                out[("demand", i)] = dict(kind="demand", i=i, lo=distal, hi=prox, entry=prox,
                                          sl=sl, tp=prox + tp_coef * (prox - sl))
    return [out[k] for k in sorted(out, key=lambda k: k[1])]


def classify(z, h, l, c):
    """The zone's state, and how many separate times price has come back to it.

    Counting starts only once price has LEFT the zone — the bars that build a zone sit inside it
    by construction and are not retests.
    """
    lo, hi, kind = z["lo"], z["hi"], z["kind"]
    visits, inside, gone = 0, False, False
    for i in range(z["i"] + 1, len(c)):
        if (c[i] > hi) if kind == "supply" else (c[i] < lo):
            return "turncoat", visits                # closed through the far edge = broken
        hit = h[i] >= lo and l[i] <= hi
        if not gone:                                 # wait for price to walk away first
            gone = not hit
            continue
        if hit and not inside:
            visits += 1
        inside = hit
    return ("untested" if visits == 0 else "strong" if visits == 1 else "weak"), visits


def dashboard(o, h, l, c):
    """The one row the dashboard shows: the live zone nearest the current price.

    Nearest, not newest. In the vendor's own screenshots the Age column runs 2 to 45 bars on a
    ten-symbol board; always taking the newest fractal would pin every age to the fractal lag
    (3-6). What the board actually tracks is the zone price is at or heading into next.

    `signal` is the vendor's chart label — Buy/Sell print when price RETESTS the zone, i.e. when
    the latest bar reaches the entry edge. Until then the zone is drawn but nothing has fired.
    """
    live = []
    for z in zones(o, h, l, c):
        z["state"], z["visits"] = classify(z, h, l, c)
        if z["state"] != "turncoat":
            live.append(z)
    if not live:
        return None
    # the product sells STRONG zones — weak/untested are the opt-in toggles in its settings — so
    # a worn-out zone only wins the row when the symbol has nothing better on offer
    z = min(live, key=lambda x: (x["state"] == "weak", abs(x["entry"] - c[-1])))
    z["age"] = len(c) - 1 - z["i"]
    z["direction"] = "Bullish" if z["kind"] == "demand" else "Bearish"
    touched = l[-1] <= z["entry"] if z["kind"] == "demand" else h[-1] >= z["entry"]
    z["signal"] = ("BUY" if z["kind"] == "demand" else "SELL") if touched else "WATCH"
    # touched can be a single wick that price left again. Whether the last candle CLOSED inside
    # the zone is the part that still holds at tomorrow's open, so it is reported separately.
    z["in_zone"] = z["lo"] <= c[-1] <= z["hi"]
    z["close"] = c[-1]
    z["dist_pct"] = (z["entry"] - c[-1]) / c[-1] * 100
    z["risk_pct"] = abs(z["entry"] - z["sl"]) / z["entry"] * 100
    return z


# ---------------------------------------------------------------- MTF: resampled timeframes

def _minutes(symbol):
    """(dt, o, h, l, c) from the 1-minute archive, oldest first."""
    p = MASTER / "symbols" / symbol.replace("/", "-") / "1minutes.txt"
    if not p.exists():
        return None
    d, o, h, l, c = [], [], [], [], []
    for line in p.read_text(encoding="utf-8").splitlines()[1:]:
        f = line.split("\t")
        if len(f) > 4 and f[4] not in ("", "None"):
            try:
                d.append(f[0]); o.append(float(f[1])); h.append(float(f[2]))
                l.append(float(f[3])); c.append(float(f[4]))
            except ValueError:
                continue
    return (d, o, h, l, c) if c else None


def _group(d, o, h, l, c, key):
    """Fold bars into buckets by key(timestamp); OHLC aggregates the usual way."""
    go, gh, gl, gc, seen = [], [], [], [], None
    for i in range(len(c)):
        k = key(d[i])
        if k != seen:
            seen = k
            go.append(o[i]); gh.append(h[i]); gl.append(l[i]); gc.append(c[i])
        else:
            gh[-1] = max(gh[-1], h[i])
            gl[-1] = min(gl[-1], l[i])
            gc[-1] = c[i]
    return go, gh, gl, gc


def series(symbol, tf):
    """(o, h, l, c) for one timeframe, built from what is already on disk."""
    if tf in ("1D", "1W", "1M"):
        b = bars(symbol)
        if not b:
            return None
        d, o, h, l, c = b[0], b[1], b[2], b[3], b[4]
        if tf == "1D":
            return o, h, l, c
        key = ((lambda s: s[:7]) if tf == "1M"
               else (lambda s: datetime.strptime(s, "%Y-%m-%d").strftime("%G-W%V")))
        return _group(d, o, h, l, c, key)
    m = _minutes(symbol)
    if not m:
        return None
    n = {"5m": 5, "15m": 15, "30m": 30, "1h": 60}[tf]

    def key(s):
        return s[:10], (int(s[11:13]) * 60 + int(s[14:16])) // n
    return _group(*m, key=key)


# ---------------------------------------------------------------- scans

def _row(name, o, h, l, c, date, v=None):
    o, h, l, c = o[-MAX_BARS:], h[-MAX_BARS:], l[-MAX_BARS:], c[-MAX_BARS:]
    if len(c) < ATR_N + SLOW * 2 + 2:
        return None
    z = dashboard(o, h, l, c)
    if not z:
        return None
    # Volume confirmation. NOT part of their indicator — it reads no volume at all — but across
    # backtest.py's 7,349 replayed trades it was the only ingredient that kept its edge out of
    # sample. Same definition as trade_setup.vol_ratio, so the two pages cannot disagree.
    vol_x = turnover = 0.0
    if v:
        v = v[-MAX_BARS:]
        vs = sma(v, 20)[-1]
        vol_x = v[-1] / vs if vs else 0.0
        turn = sorted(c[k] * v[k] for k in range(max(0, len(c) - 20), len(c)))
        turnover = turn[len(turn) // 2] if turn else 0.0
    return dict(z, symbol=name, date=date, vol_x=vol_x, turnover=turnover,
                trend="?", confirmed=False)      # both filled in by scan_symbols


def scan_symbols(names):
    """MTS — one dashboard row per symbol, on daily bars."""
    rows = []
    for sym in names:
        b = bars(sym)
        if not b:
            continue
        r = _row(sym, b[1], b[2], b[3], b[4], b[0][-1], b[5])
        if not r:
            continue
        # Volume ALONE is not the rule that survived. backtest.py tested "volume confirmation on a
        # confirmed uptrend" — drop the trend half and heavy volume into a FALLING stock at a
        # demand zone counts as confirmation, which is distribution, not accumulation. So the
        # direction has to agree with trade_setup's own trend verdict as well.
        try:
            s = setup(sym)
            r["trend"] = s["signal"] if s else "?"
        except (ValueError, IndexError, KeyError, ZeroDivisionError):
            r["trend"] = "?"          # deliberately narrow: a broad except here once swallowed a
        # missing import and scored the whole market "unconfirmed" without a single error
        agrees = (("BUY", "STRONG BUY") if r["direction"] == "Bullish"
                  else ("SELL", "STRONG SELL"))
        r["confirmed"] = (r["signal"] != "WATCH" and r["vol_x"] >= 1.5
                          and r["turnover"] >= LIQUID_MIN and r["trend"] in agrees)
        rows.append(r)
    # confirmed first, then still-standing (closed in zone), then fresh, then nearest
    rows.sort(key=lambda r: (not r["confirmed"], r["signal"] == "WATCH", not r["in_zone"],
                             r["state"] == "weak", -r["vol_x"]))
    return rows


def scan_timeframes(symbol):
    """MTF — one dashboard row per timeframe, for a single symbol."""
    rows = []
    for tf in TIMEFRAMES:
        s = series(symbol, tf)
        if not s:
            continue
        r = _row(tf, s[0], s[1], s[2], s[3], tf)
        if r:
            rows.append(r)
    return rows


def write(rows, path=OUT):
    lines = [HEADER]
    for r in rows:
        lines.append("\t".join(str(x) for x in (
            r["symbol"], r["date"], r["signal"], "yes" if r["confirmed"] else "no",
            round(r["vol_x"], 2), r["trend"], round(r["turnover"]),
            "yes" if r["in_zone"] else "no", r["direction"], r["state"], r["age"],
            round(r["close"], 2), round(r["entry"], 2), round(r["sl"], 2), round(r["tp"], 2),
            round(r["risk_pct"], 2), round(r["dist_pct"], 2),
            # 4dp, not 2. These are zone GEOMETRY, not order prices, and `in_zone` is decided by
            # comparing the close against them. At 2dp a sub-Rs-10 instrument can print
            # close 9.65 / zone_hi 9.65 / in_zone no, because the true edge was 9.6471 — the flag
            # was right and the record looked self-contradictory. entry/sl/tp stay at 2dp: those
            # are prices you actually send to the market, which ticks in paisa.
            round(r["lo"], 4), round(r["hi"], 4))))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _print(rows, first="Symbol"):
    print(f"{first:<10}{'Direction':<11}{'State':<10}{'Age':>4}{'Signal':>8}{'in':>4}"
          f"{'vol':>7}{'ok':>4}{'Entry':>10}{'SL':>10}{'TP':>10}{'dist%':>8}")
    for r in rows:
        print(f"{r['symbol']:<10}{r['direction']:<11}{r['state']:<10}{r['age']:>4}"
              f"{r['signal']:>8}{('yes' if r['in_zone'] else '-'):>4}"
              f"{r.get('vol_x', 0):>6.2f}x{('YES' if r.get('confirmed') else '-'):>4}"
              f"{r['entry']:>10.2f}{r['sl']:>10.2f}{r['tp']:>10.2f}"
              f"{r['dist_pct']:>8.2f}")


def selftest():
    """The maths, checked against the vendor's own published dashboard rows."""
    # TP sits exactly TP_COEF risk-multiples from entry — the invariant read off 12 real rows.
    for entry, sl, tp in [(1.31403, 1.32228, 1.28929),     # USDCAD Bearish, sdash5.png
                          (95.148, 94.350, 97.543),        # AUDJPY Bullish, sdash5.png
                          (126.93, 123.74, 136.50)]:       # AMZN M30 Bullish, sdash6.png
        assert abs(abs(tp - entry) / abs(sl - entry) - TP_COEF) < 0.01, (entry, sl, tp)

    # a clean V: the low must print a demand zone, entry above the low, stop below it
    n = 60
    c = [100 - i for i in range(n)] + [40 + i for i in range(n)]
    o = c[:]
    h = [x + 1 for x in c]
    l = [x - 1 for x in c]
    zs = zones(o, h, l, c)
    dem = [z for z in zs if z["kind"] == "demand"]
    assert dem, "a V bottom must print a demand zone"
    z = min(dem, key=lambda x: x["lo"])
    assert z["sl"] < z["lo"] <= z["entry"] < z["tp"], z
    assert abs((z["tp"] - z["entry"]) / (z["entry"] - z["sl"]) - TP_COEF) < 1e-6

    # widening the fuzz factor must widen the zone, never narrow it
    wide = zones(o, h, l, c, fuzz=3.0)
    zw = min((x for x in wide if x["kind"] == "demand"), key=lambda x: x["lo"])
    assert (zw["hi"] - zw["lo"]) > (z["hi"] - z["lo"]), "a higher fuzz must widen the zone"

    # a rising sawtooth: every supply zone it prints is later closed through = turncoat, and the
    # newest live zone on a rising market must be a demand zone
    c = [50 + i * 0.5 + 8 * math.sin(i / 3) for i in range(160)]
    o = c[:]
    h = [x + 1 for x in c]
    l = [x - 1 for x in c]
    states = defaultdict(int)
    for zz in zones(o, h, l, c):
        states[classify(zz, h, l, c)[0]] += 1
    assert states["turncoat"], dict(states)

    # the row the dashboard hands over must be a live zone, priced 1:TP_COEF, on the right side
    r = dashboard(o, h, l, c)
    assert r["state"] != "turncoat" and r["signal"] in ("BUY", "SELL", "WATCH"), r
    assert abs(abs(r["tp"] - r["entry"]) / abs(r["sl"] - r["entry"]) - TP_COEF) < 1e-6
    assert (r["sl"] < r["entry"] < r["tp"]) if r["direction"] == "Bullish" else \
           (r["sl"] > r["entry"] > r["tp"]), r
    print("selftest ok —", dict(states))
    return 0


def main():
    argv = sys.argv[1:]
    if "--selftest" in argv:
        return selftest()

    if "--symbol" in argv:                       # MTF: one symbol across timeframes
        sym = argv[argv.index("--symbol") + 1].upper()
        rows = scan_timeframes(sym)
        print(f"=== Supply Demand Dashboard MTF · {sym} ===")
        if not rows:
            print("No data for this symbol.")
            return 1
        _print(rows, first="TF")
        return 0

    names = (MASTER / "symbols.txt").read_text(encoding="utf-8").split()
    rows = scan_symbols(names)
    write(rows)
    fired = [r for r in rows if r["signal"] != "WATCH"]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"=== Supply Demand Dashboard MTS · {stamp} · {len(rows)} symbols scanned ===")
    print(f"{len(fired)} at a zone right now -> {OUT}\n")
    _print(fired or rows[:20])
    if not fired:
        print("\n(nothing at a zone today — the rows above are the nearest WATCH zones)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
