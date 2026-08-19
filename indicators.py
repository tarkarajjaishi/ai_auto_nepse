"""Indicator maths in plain Python, shared by the UI and the scanner.

Each function takes a list of prices and returns a list of the same length, padded with
None where the indicator has no value yet — so results line up with the bars they belong to.
"""


def sma(values, n):
    out, run = [None] * len(values), 0.0
    for i, v in enumerate(values):
        run += v
        if i >= n:
            run -= values[i - n]
        if i >= n - 1:
            out[i] = run / n
    return out


def ema(values, n):
    """Seeded with the SMA of the first n bars, as charting platforms do."""
    out = [None] * len(values)
    if len(values) < n:
        return out
    k, prev = 2 / (n + 1), sum(values[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def bollinger(values, n=20, mult=2.0):
    mid = sma(values, n)
    upper, lower = [None] * len(values), [None] * len(values)
    for i in range(n - 1, len(values)):
        window = values[i - n + 1: i + 1]
        mean = mid[i]
        sd = (sum((x - mean) ** 2 for x in window) / n) ** 0.5
        upper[i], lower[i] = mean + mult * sd, mean - mult * sd
    return mid, upper, lower


def rsi(values, n=14):
    """Wilder smoothing."""
    out = [None] * len(values)
    if len(values) <= n:
        return out
    gains = [max(values[i] - values[i - 1], 0) for i in range(1, len(values))]
    losses = [max(values[i - 1] - values[i], 0) for i in range(1, len(values))]
    up, down = sum(gains[:n]) / n, sum(losses[:n]) / n
    out[n] = 100 - 100 / (1 + up / down) if down else 100
    for i in range(n, len(gains)):
        up = (up * (n - 1) + gains[i]) / n
        down = (down * (n - 1) + losses[i]) / n
        out[i + 1] = 100 - 100 / (1 + up / down) if down else 100
    return out


def alma(values, n=21, offset=0.85, sigma=6.0):
    """Arnaud Legoux MA — the 'trend wave' in the SMC scripts."""
    out = [None] * len(values)
    m, s = offset * (n - 1), n / sigma
    weights = [pow(2.718281828459045, -((i - m) ** 2) / (2 * s * s)) for i in range(n)]
    norm = sum(weights)
    for i in range(n - 1, len(values)):
        window = values[i - n + 1: i + 1]
        out[i] = sum(w * v for w, v in zip(weights, window)) / norm
    return out


def pivots(highs, lows, left, right):
    """Confirmed swing points, Pine's ta.pivothigh / ta.pivotlow.

    A pivot is only known `right` bars after it happened — the same lag Pine has, so
    signals drawn from these are historical marks, not live calls.
    """
    ph, pl = [None] * len(highs), [None] * len(lows)
    for i in range(left, len(highs) - right):
        window_h = highs[i - left: i + right + 1]
        window_l = lows[i - left: i + right + 1]
        if highs[i] == max(window_h) and window_h.count(highs[i]) == 1:
            ph[i] = highs[i]
        if lows[i] == min(window_l) and window_l.count(lows[i]) == 1:
            pl[i] = lows[i]
    return ph, pl


def structure(closes, ph, pl):
    """BOS / CHoCH events: price closing through the last confirmed swing.

    Breaking a high while the last break was downward is a Change of Character;
    breaking in the same direction as the prevailing one is a Break of Structure.
    """
    events, last_ph, last_pl, direction = [], None, None, 0
    for i in range(1, len(closes)):
        if ph[i] is not None:
            last_ph = ph[i]
        if pl[i] is not None:
            last_pl = pl[i]
        if last_ph is not None and closes[i] > last_ph >= closes[i - 1]:
            events.append((i, last_ph, "CHoCH" if direction == -1 else "BOS", "up"))
            direction, last_ph = 1, None
        elif last_pl is not None and closes[i] < last_pl <= closes[i - 1]:
            events.append((i, last_pl, "CHoCH" if direction == 1 else "BOS", "down"))
            direction, last_pl = -1, None
    return events


def macd(values, fast=12, slow=26, signal=9):
    f, s = ema(values, fast), ema(values, slow)
    line = [(a - b) if a is not None and b is not None else None for a, b in zip(f, s)]
    seed = [v for v in line if v is not None]
    sig_tail = ema(seed, signal)
    sig = [None] * (len(line) - len(sig_tail)) + sig_tail
    hist = [(a - b) if a is not None and b is not None else None for a, b in zip(line, sig)]
    return line, sig, hist


def atr(highs, lows, closes, n=14):
    """Average True Range, Wilder-smoothed — used as a fallback stop distance."""
    out = [None] * len(closes)
    if len(closes) <= n:
        return out
    tr = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    run = sum(tr[1:n + 1]) / n
    out[n] = run
    for i in range(n + 1, len(closes)):
        run = (run * (n - 1) + tr[i]) / n
        out[i] = run
    return out


def adx(highs, lows, closes, n=14):
    """Wilder's ADX with +DI / -DI. Returns (adx, plus_di, minus_di), each None-padded.

    ADX measures trend STRENGTH (not direction); +DI vs -DI gives the direction. A rising
    ADX above 25 with +DI over -DI is the classic 'strong up-trend' reading.
    """
    L = len(closes)
    adx_out, pdi, mdi = [None] * L, [None] * L, [None] * L
    if L <= 2 * n:
        return adx_out, pdi, mdi
    tr, plus_dm, minus_dm = [0.0] * L, [0.0] * L, [0.0] * L
    for i in range(1, L):
        up, dn = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        plus_dm[i] = up if (up > dn and up > 0) else 0.0
        minus_dm[i] = dn if (dn > up and dn > 0) else 0.0
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    atr_s = sum(tr[1:n + 1]); sp = sum(plus_dm[1:n + 1]); sm = sum(minus_dm[1:n + 1])
    p = 100 * sp / atr_s if atr_s else 0.0
    m = 100 * sm / atr_s if atr_s else 0.0
    pdi[n], mdi[n] = p, m
    dx = [(n, 100 * abs(p - m) / (p + m) if (p + m) else 0.0)]
    for i in range(n + 1, L):
        atr_s = atr_s - atr_s / n + tr[i]
        sp = sp - sp / n + plus_dm[i]
        sm = sm - sm / n + minus_dm[i]
        p = 100 * sp / atr_s if atr_s else 0.0
        m = 100 * sm / atr_s if atr_s else 0.0
        pdi[i], mdi[i] = p, m
        dx.append((i, 100 * abs(p - m) / (p + m) if (p + m) else 0.0))
    if len(dx) >= n:
        prev = sum(d for _, d in dx[:n]) / n
        adx_out[dx[n - 1][0]] = prev
        for idx, d in dx[n:]:
            prev = (prev * (n - 1) + d) / n
            adx_out[idx] = prev
    return adx_out, pdi, mdi


def trade_levels(signal, price, pivot, atr_now):
    """Stop and two targets for a signal.

    The stop sits at the swing the signal came from — the level that would invalidate it —
    and falls back to 1.5x ATR when that pivot is on the wrong side of price (it can be,
    once price has run past it). Targets are 1R and 2R from the current price, so the
    reward is always stated against the risk actually being taken.
    """
    if not price or not atr_now:
        return None, None, None, None
    if signal == "BUY":
        stop = pivot if (pivot and pivot < price) else price - 1.5 * atr_now
        risk = price - stop
        if risk <= 0:
            return None, None, None, None
        return round(stop, 2), round(price + risk, 2), round(price + 2 * risk, 2), round(risk / price * 100, 2)
    stop = pivot if (pivot and pivot > price) else price + 1.5 * atr_now
    risk = stop - price
    if risk <= 0:
        return None, None, None, None
    return round(stop, 2), round(price - risk, 2), round(price - 2 * risk, 2), round(risk / price * 100, 2)
