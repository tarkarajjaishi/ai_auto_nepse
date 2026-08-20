"""Professional multi-indicator trade setup from daily OHLCV — Python only, fed by the .txt archive.

Scores a stock 0-100 on the A+ framework (trend / momentum / trend-strength / volume / price-action),
decides a STRONG SELL .. STRONG BUY signal behind a liquidity gate and the false-signal filters, and
returns concrete risk levels: entry, target-1, target-2, stop-loss (ATR + last-swing based).

Core combination, in the order the framework applies it:
    liquidity -> 200-SMA trend -> 20/50-EMA trend -> support/resistance -> volume -> ADX -> RSI
    -> MACD -> ATR risk -> signal.

    python trade_setup.py            # self-check on a couple of live symbols
"""
from fetch_ohlc import MASTER
from indicators import sma, ema, rsi, macd, atr, adx, pivots, trade_levels
from prices import bars as _adjusted

LIQUID_MIN = 500_000            # Rs median 20-day turnover; below this NEPSE fills are unreliable


def _bars(symbol):
    """(dates, o, h, l, c, v) oldest-last, corporate-action adjusted, or None when the file is
    missing or too short.

    This was a verbatim second copy of prices.raw_bars, which made trade_setup the one consumer
    of the archive reading RAW while backtest, swing_pro, supply_demand and absorb all read
    through prices.bars(). That split mattered most where the two meet: supply_demand builds its
    zones from the adjusted series and then takes its `trend` and `confirmed` columns from here,
    so one row was assembled from two different price series for the same symbol on the same day.

    NB on the numbers: an audit measured 11 signals flipping between the two loaders, but that
    was against the OLD prices.py, which fabricated 67 adjustments out of ordinary limit-downs —
    all of them recent, so they reached the bars this module reads. With the ex-date detector
    fixed, the two loaders now agree on all 334 scorable symbols. This stays a deletion of a
    duplicate parser, not a numeric fix: the genuine adjustments left are 2016-2021 and do not
    reach a 200-bar window, but the next real bonus would have silently broken this module.
    """
    b = _adjusted(symbol)
    return b if b and len(b[4]) >= 60 else None


def _last(series, i):
    return series[i] if i < len(series) and series[i] is not None else None


def setup(symbol):
    b = _bars(symbol)
    if not b:
        return None
    d, o, h, l, c, v = b
    n = len(c)
    i = n - 1
    close = c[i]

    e20, e50, s200 = ema(c, 20), ema(c, 50), sma(c, 200)
    r = rsi(c, 14)
    a = atr(h, l, c, 14)
    mline, msig, mhist = macd(c, 12, 26, 9)
    adx_, pdi, mdi = adx(h, l, c, 14)
    vsma = sma(v, 20)
    ph, pl = pivots(h, l, 5, 5)

    e20v, e50v, s200v = _last(e20, i), _last(e50, i), _last(s200, i)
    rv, av, adxv = _last(r, i), _last(a, i), _last(adx_, i)
    pdiv, mdiv = _last(pdi, i), _last(mdi, i)
    mlv, msv, mhv = _last(mline, i), _last(msig, i), _last(mhist, i)
    mh_prev = _last(mhist, i - 1)
    e50_prev = _last(e50, i - 5)           # is the 50-EMA turning down over the last week?
    vsv = _last(vsma, i)

    vol_ratio = v[i] / vsv if vsv else 0.0
    turn = sorted(c[k] * v[k] for k in range(max(0, n - 20), n))
    med_turnover = turn[len(turn) // 2] if turn else 0.0
    liquid = med_turnover >= LIQUID_MIN

    recent_high = max(h[max(0, n - 20):n])
    recent_low = min(l[max(0, n - 20):n])
    prior_high = max(h[max(0, n - 21):n - 1]) if n > 21 else recent_high
    breakout = close > prior_high          # closed above the prior 20-day high

    # ---------------------------------------------------------------- A+ score (0-100)
    parts = {}
    t = (10 if s200v and close > s200v else 0) \
        + (10 if e50v and s200v and e50v > s200v else 0) \
        + (10 if e20v and e50v and e20v > e50v else 0)
    mo = (10 if rv is not None and rv > 50 else 0) \
        + (10 if mlv is not None and msv is not None and mlv > msv else 0) \
        + (5 if mhv is not None and mh_prev is not None and mhv > mh_prev else 0)
    ts = (10 if adxv is not None and adxv > 25 else 0) \
        + (10 if pdiv is not None and mdiv is not None and pdiv > mdiv else 0)
    vo = (10 if vol_ratio > 1.5 else 0) + (5 if vol_ratio > 2.0 else 0)
    pa = 10 if breakout else 0
    parts.update(trend=t, momentum=mo, strength=ts, volume=vo, price=pa)
    score = t + mo + ts + vo + pa
    grade = ("A+" if score >= 90 else "A" if score >= 80 else "B" if score >= 70
             else "Watch" if score >= 60 else "Avoid")

    # ---------------------------------------------------------------- signal (with filters)
    below200 = s200v is not None and close < s200v
    very_bear = below200 and e50v is not None and s200v is not None and e50v < s200v
    trend_exit = (e50v is not None and close < e50v
                  and e50_prev is not None and e50v < e50_prev)
    sell_warn = (rv is not None and rv < 50) and (mlv is not None and msv is not None and mlv < msv)
    weak_trend = adxv is not None and adxv < 20      # ADX<20 -> whipsaw zone, no long

    if very_bear:
        signal = "STRONG SELL"
    elif trend_exit:
        signal = "SELL"
    elif not liquid:
        signal = "ILLIQUID"                # too thin to trade cleanly, regardless of score
    elif below200 or weak_trend:
        signal = "AVOID"                   # filter 1 (no long below 200SMA) + filter 3 (ADX<20)
    elif score >= 90:
        signal = "STRONG BUY"
    elif score >= 80:
        signal = "BUY"
    elif score >= 60:
        signal = "WATCH"
    elif sell_warn:
        signal = "SELL"
    else:
        signal = "AVOID"

    # ---------------------------------------------------------------- risk levels
    is_long = signal in ("STRONG BUY", "BUY", "WATCH")
    last_pl = next((pl[k] for k in range(n - 1, -1, -1) if pl[k] is not None and pl[k] < close), None)
    stop, tgt1, tgt2, risk_pct = trade_levels(
        "BUY" if is_long else "SELL", close, last_pl if is_long else None, av)
    rr = None
    if stop and tgt2 is not None and close != stop:
        rr = round(abs(tgt2 - close) / abs(close - stop), 1)

    # ---------------------------------------------------------------- when did the buy trigger?
    # a bar is "buyable" when the core uptrend is in place: above 200SMA, EMAs stacked, RSI>50, MACD>sig.
    def bull_at(k):
        vals = (s200[k], e20[k], e50[k], r[k], mline[k], msig[k])
        return (all(x is not None for x in vals)
                and c[k] > s200[k] and e20[k] > e50[k] and e50[k] > s200[k]
                and r[k] > 50 and mline[k] > msig[k])

    buy_date = buy_close = buy_idx = None
    days_active = 0
    if bull_at(i) or is_long:
        j = i
        while j > 0 and bull_at(j - 1):
            j -= 1
        buy_date, buy_close, days_active, buy_idx = d[j], round(c[j], 2), i - j, j

    # ---------------------------------------------------------------- can you still buy now?
    # ideal entry was at the trigger; measure how far price has run since, normalised by ATR.
    still_buy, still_reason, runup = None, "", None
    if is_long and buy_close:
        runup = round((close / buy_close - 1) * 100, 1)
        ext = (close - buy_close) / av if av else 0.0
        if days_active <= 2:
            still_buy, still_reason = True, "fresh signal — right at the ideal entry"
        elif ext <= 2.5:
            still_buy, still_reason = True, f"still in the entry zone ({runup:+.1f}% since trigger, {ext:.1f}× ATR)"
        else:
            still_buy, still_reason = False, f"extended — up {runup:+.1f}% ({ext:.1f}× ATR) since the trigger; wait for a pullback toward the 20-EMA"
    elif is_long:
        still_buy, still_reason = True, "buy conditions in place"
    else:
        still_buy, still_reason = False, "not a buy signal right now"

    # ---------------------------------------------------------------- did the trigger's trade work?
    # Take the entry/T1/T2/SL as they'd have been set ON the trigger bar, then walk forward and see
    # which was reached first — the honest outcome of the 2026-07-31-style signal.
    outcome = None
    if is_long and buy_idx is not None and buy_idx < i:
        se = c[buy_idx]
        s_pl = next((pl[k] for k in range(buy_idx, -1, -1) if pl[k] is not None and pl[k] < se), None)
        s_stop, s_t1, s_t2, _ = trade_levels("BUY", se, s_pl, a[buy_idx] or av)
        fwd = range(buy_idx + 1, i + 1)
        t1_date = next((d[k] for k in fwd if s_t1 and h[k] >= s_t1), None)
        t2_date = next((d[k] for k in fwd if s_t2 and h[k] >= s_t2), None)
        sl_date = next((d[k] for k in fwd if s_stop and l[k] <= s_stop), None)
        peak = max(h[buy_idx + 1:i + 1])
        peak_pct = round((peak / se - 1) * 100, 1)
        if sl_date and (not t1_date or sl_date <= t1_date):
            verdict = "stopped"           # hit the stop before any target = a losing trade
        elif t2_date and (not sl_date or t2_date <= sl_date):
            verdict = "t2"                # reached both targets
        elif t1_date:
            verdict = "t1"                # reached target 1
        else:
            verdict = "open"              # neither target nor stop yet
        outcome = {
            "entry": round(se, 2), "t1": s_t1, "t2": s_t2, "stop": s_stop,
            "t1_date": t1_date, "t2_date": t2_date, "sl_date": sl_date,
            "peak_pct": peak_pct, "verdict": verdict,
        }

    def rnd(x):
        return round(x, 2) if isinstance(x, (int, float)) else None

    return {
        "symbol": symbol, "date": d[i], "close": rnd(close),
        "score": score, "grade": grade, "signal": signal, "parts": parts,
        "ema20": rnd(e20v), "ema50": rnd(e50v), "sma200": rnd(s200v),
        "rsi": rnd(rv), "adx": rnd(adxv), "plus_di": rnd(pdiv), "minus_di": rnd(mdiv),
        "macd": rnd(mlv), "macd_sig": rnd(msv), "macd_hist": rnd(mhv),
        "atr": rnd(av), "vol_ratio": round(vol_ratio, 2), "med_turnover": round(med_turnover),
        "liquid": liquid, "breakout": breakout,
        "resistance": rnd(recent_high), "support": rnd(recent_low),
        "entry": rnd(close), "target1": tgt1, "target2": tgt2, "stop": stop,
        "risk_pct": risk_pct, "rr": rr,
        "buy_date": buy_date, "buy_close": buy_close, "days_active": days_active,
        "still_buy": still_buy, "still_reason": still_reason, "runup": runup,
        "outcome": outcome,
    }


def _demo():
    """One runnable check: the score stays in range and BUY levels are ordered SL < entry < T1 < T2."""
    names = (MASTER / "symbols.txt").read_text(encoding="utf-8").split()[:40]
    checked = 0
    for sym in names:
        s = setup(sym)
        if not s:
            continue
        checked += 1
        assert 0 <= s["score"] <= 100, (sym, s["score"])
        assert s["grade"] in ("A+", "A", "B", "Watch", "Avoid")
        if s["signal"] in ("STRONG BUY", "BUY", "WATCH") and s["stop"] and s["target1"]:
            assert s["stop"] < s["entry"] < s["target1"] <= s["target2"], (sym, s)
        print(f"{sym:8} {s['signal']:11} score {s['score']:>3} ({s['grade']:5}) "
              f"entry {s['entry']}  T1 {s['target1']}  T2 {s['target2']}  SL {s['stop']}  RR {s['rr']}")
    assert checked > 0, "no symbols produced a setup"
    print(f"\nOK — {checked} setups computed, all in range and correctly ordered.")


if __name__ == "__main__":
    _demo()
