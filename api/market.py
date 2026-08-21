"""Floorsheet, broker flow and the heatmap — every one of them read from the archive.

The Streamlit versions of these three pages call NAASA's live endpoints, so they go blank the
moment the saved session lapses and they cannot show you a closed market at all. These read the
.txt archive instead: they work signed out, they work at midnight, and — like every other board
in this API — they carry the session they are describing so the frontend can say which day it
is showing. `x_quotes` is a better number DURING a session; it is not available for most of the
day, and a heatmap that is blank 18 hours out of 24 is not a heatmap.
"""
import re
from collections import defaultdict

from fetch_ohlc import MASTER

FLOORSHEET = MASTER / "floorsheet"

# Sector (as spelled in sectors.txt) -> the index directory under Master_data/indices/.
#
# Built against sectors.txt's OWN spelling, then matched case- and punctuation-insensitively,
# because the two sources disagree and always have: sectors.txt writes "Hotels and Tourism",
# "Manufacturing and Processing" and "Non Life Insurance" where ui.py's map writes "And" and
# "Non-Life". A plain dict lookup drops exactly those three sectors to the fallback average
# without a word — they would render, they would just quietly stop being the official index.
_SECTOR_INDEX = {
    "Commercial Banks": "BANKINGIND", "Development Banks": "DEVBANKIND",
    "Finance": "FINANCEIND", "Hotels and Tourism": "HOTELIND",
    "Hydro Power": "HYDROPOWIND", "Investment": "INVIDX",
    "Life Insurance": "LIFEINSUIND", "Manufacturing and Processing": "MANUFACTUREIND",
    "Microfinance": "MICROFININD", "Mutual Fund": "MUTUALIND",
    "Non Life Insurance": "NONLIFEIND", "Others": "OTHERSIND", "Tradings": "TRADINGIND",
}


def _key(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


_SECTOR_INDEX_BY_KEY = {_key(k): v for k, v in _SECTOR_INDEX.items()}


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() and abs(f) < 2 ** 53 else f


def _tail_lines(path, want=2, block=4096):
    """The last `want` non-empty lines, without reading the file.

    A 1D.txt runs to ~1,600 rows and the heatmap touches ~290 of them per call; reading them
    whole turns a 40 ms request into a 3-second one.
    """
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            buf = b""
            while size > 0 and buf.count(b"\n") <= want:
                step = min(block, size)
                size -= step
                fh.seek(size)
                buf = fh.read(step) + buf
    except OSError:
        return []
    lines = [l for l in buf.decode("utf-8", "replace").splitlines() if l.strip()]
    return lines[-want:]


def _last_bar(path):
    """{date, close, pct, volume, turnover} from the final row of a 1D.txt, or None.

    `percent_change` is the exchange's own figure and is used as-is. It is computed on RAW
    closes, so on an ex-dividend date it shows the drop the tape actually printed — which is
    what every other terminal shows, and what the archive's own adjusted series deliberately
    does NOT (see prices.ex_dates). Do not "fix" it here; the two numbers answer different
    questions and this page is asking what the tape did today.
    """
    rows = _tail_lines(path, 2)
    if not rows:
        return None
    f = rows[-1].split("\t")
    if len(f) < 9 or not f[0]:
        return None
    open_, high, low = _num(f[1]), _num(f[2]), _num(f[3])
    close, pct, vol, amt = _num(f[4]), _num(f[6]), _num(f[7]), _num(f[8])
    if pct is None and len(rows) > 1:
        prev = _num(rows[-2].split("\t")[4])          # a fresh live bar has no change column yet
        pct = (close - prev) / prev * 100 if close is not None and prev else None
    # `amount` is blank for most of the archive's history — this is the same fallback the
    # volume-spike study settled on rather than treating an empty column as zero turnover.
    turnover = amt if amt else (close * vol if close and vol else 0)
    return {"date": f[0], "open": open_, "high": high, "low": low, "close": close,
            "pct": pct, "volume": vol, "turnover": turnover}


def live_overlay(bar, name):
    """Lay the socket's current quote over a stored bar. Returns (bar, live?).

    The stored bar is already today's — live_1d flushes it every 20s — so this only removes that
    latency. Same numbers, sooner.

    `turnover` is the exception and is left alone when the feed says 0. Indices publish TTV 0
    intraday, and writing that through would replace a real turnover with a claim that nothing
    traded. Unknown is reported as None; the screen prints a dash.
    """
    if not bar:
        return bar, False
    try:
        import feed_snap
        import live_1d
    except Exception:
        return bar, False
    snap = feed_snap.read()
    if not snap["fresh"]:
        return bar, False
    q = snap["quotes"].get(live_1d.feed_name(name.upper()))
    if not q or q.get("ltp") is None:
        return bar, False
    # Only overlay TODAY. The snapshot outlives the session, and a quote from a finished day
    # written over a stored bar is exactly the stale-as-current failure.
    if q.get("stamp") and live_1d.today() and not str(q["stamp"]).startswith(
            "%s/%s/%s" % (live_1d.today()[8:10], live_1d.today()[5:7], live_1d.today()[:4])):
        return bar, False
    out = dict(bar)
    prev = q.get("close")
    out["close"] = q["ltp"]
    for src, dst in (("open", "open"), ("high", "high"), ("low", "low")):
        if q.get(src) is not None:
            out[dst] = q[src]
    out["pct"] = round((q["ltp"] - prev) / prev * 100, 2) if prev else None
    if q.get("volume"):
        out["volume"] = q["volume"]
    ttv = q.get("ttv")
    if ttv:
        out["turnover"] = ttv
    elif q.get("volume") and q.get("avg_price"):
        out["turnover"] = q["volume"] * q["avg_price"]
    else:
        # The feed reports nothing usable. Do not keep the stored figure -- it belongs to a
        # different moment -- and do not write 0, which reads as "nothing traded".
        out["turnover"] = None
    return out, True


# ── floorsheet ──────────────────────────────────────────────────────────────────────────────

def _dir(symbol):
    return FLOORSHEET / symbol.upper().replace("/", "-")


def sessions(symbol):
    """Every floorsheet date on file for a symbol, newest first."""
    d = _dir(symbol)
    return sorted((p.stem for p in d.glob("*.txt")), reverse=True) if d.is_dir() else []


def floorsheet(symbol, date, limit=3000):
    """One session's trades, plus what each broker did net.

    Quantities are parsed with float(), never int(): the older merolagani files write them as
    "50.0", and an int() on that raises — which a bare `except: continue` then swallows one row
    at a time until 79% of the sessions in the archive look empty. That is a real bug this
    project already shipped once.
    """
    p = _dir(symbol) / f"{date}.txt"
    if not p.exists():
        return None
    trades, bought, sold, amount = [], defaultdict(float), defaultdict(float), 0.0
    qty_total, dropped, parsed = 0.0, 0, 0
    for line in p.read_text(encoding="utf-8").splitlines()[1:]:
        f = line.split("\t")
        if len(f) < 6:
            dropped += 1
            continue
        try:
            q, rate, amt = float(f[2]), float(f[3]), float(f[4])
        except ValueError:
            dropped += 1
            continue
        b, s = f[0].strip(), f[1].strip()
        bought[b] += q
        sold[s] += q
        qty_total += q
        amount += amt
        parsed += 1
        if len(trades) < limit:
            trades.append({"buyer": b, "seller": s, "quantity": q, "rate": rate,
                           "amount": amt, "transaction": f[5].strip()})
    total = int(qty_total)
    brokers = sorted(
        ({"broker": b, "bought": bought.get(b, 0.0), "sold": sold.get(b, 0.0),
          "net": bought.get(b, 0.0) - sold.get(b, 0.0)}
         for b in set(bought) | set(sold)),
        key=lambda r: -abs(r["net"]))
    return {
        "symbol": symbol.upper(), "date": date, "trades": trades, "brokers": brokers,
        # `shown` vs `trades_total` so a capped table can SAY it is capped instead of implying
        # the session was small. Silent truncation reads as "that is all of it".
        "shown": len(trades), "trades_total": parsed,
        "totals": {"trades": parsed, "shares": total, "turnover": amount,
                   "avg_trade": (total / parsed) if parsed else None,
                   "brokers": len(brokers), "unparsed_rows": dropped},
    }


def broker_flow(symbol, count=20):
    """Net shares per broker over the last `count` sessions, from the pre-built broker_flow table.

    Falls back to re-reading the raw floorsheet when build_broker_flow.py has not run for this
    symbol, so a fresh listing is not a blank page.
    """
    p = MASTER / "broker_flow" / f"{symbol.upper().replace('/', '-')}.txt"
    by_date = defaultdict(dict)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines()[1:]:
            f = line.split("\t")
            if len(f) < 5:
                continue
            try:
                by_date[f[0]][f[1]] = (float(f[2]), float(f[3]))
            except ValueError:
                continue

    dates = sorted(by_date, reverse=True)[:count] if by_date else sessions(symbol)[:count]
    bought, sold = defaultdict(float), defaultdict(float)
    if by_date:
        for d in dates:
            for broker, (b, s) in by_date[d].items():
                bought[broker] += b
                sold[broker] += s
    else:
        for d in dates:
            fs = floorsheet(symbol, d, limit=0)
            for r in (fs or {}).get("brokers", []):
                bought[r["broker"]] += r["bought"]
                sold[r["broker"]] += r["sold"]

    rows = sorted(({"broker": b, "bought": bought.get(b, 0.0), "sold": sold.get(b, 0.0),
                    "net": bought.get(b, 0.0) - sold.get(b, 0.0)}
                   for b in set(bought) | set(sold)), key=lambda r: -r["net"])
    total_buy = sum(bought.values())
    top5 = sum(sorted(bought.values(), reverse=True)[:5])
    return {
        "symbol": symbol.upper(), "sessions": len(dates),
        "from": dates[-1] if dates else None, "to": dates[0] if dates else None,
        "accumulating": [r for r in rows if r["net"] > 0][:10],
        "distributing": [r for r in rows if r["net"] < 0][-10:][::-1],
        # The one broker metric that survived out-of-sample testing is net_churn; the
        # concentration family turned out to be a liquidity proxy. This number is here to
        # DESCRIBE the session, not as a signal — the frontend must not grade on it.
        "top5_buy_share": (top5 / total_buy * 100) if total_buy else None,
        "source": "broker_flow" if by_date else "floorsheet",
    }


# ── heatmap ─────────────────────────────────────────────────────────────────────────────────

_hm_cache = {"stamp": None, "value": None}


def _sectors():
    p = MASTER / "sectors.txt"
    if not p.exists():
        return {}
    out = {}
    for line in p.read_text(encoding="utf-8").splitlines()[1:]:
        f = line.split("\t")
        if len(f) >= 2 and f[0].strip() and f[1].strip():
            out[f[0].strip()] = f[1].strip()
    return out


def index_names():
    d = MASTER / "indices"
    return sorted(p.name for p in d.iterdir() if p.is_dir()) if d.is_dir() else []


def index_bars(name, limit=500):
    """Daily bars for one index — NOT run through prices.bars(), on purpose.

    That loader corporate-action-adjusts, and an index has no corporate actions: it is already a
    continuous series by construction, so 'adjusting' it could only invent a factor out of an
    ordinary large move. It also reads Master_data/symbols/ only, and indices live in their own
    directory. Two different things that both happen to be OHLC, kept apart.
    """
    p = MASTER / "indices" / name.upper().replace("/", "-") / "1D.txt"
    if not p.exists():
        return None
    out = []
    for line in p.read_text(encoding="utf-8").splitlines()[1:]:
        f = line.split("\t")
        if len(f) < 5 or not f[0] or f[4] in ("", "None"):
            continue
        try:
            out.append({"date": f[0], "open": float(f[1] or f[4]), "high": float(f[2] or f[4]),
                        "low": float(f[3] or f[4]), "close": float(f[4]),
                        "volume": float(f[7]) if len(f) > 7 and f[7] not in ("", "None") else 0.0})
        except ValueError:
            continue
    return out[-limit:] if limit > 0 else out


def indices():
    """Every index — live off the socket while the market is open.

    Turnover is the one field NOT taken from the feed: indices publish TTV 0 intraday, and writing
    that through would replace a real figure with "nothing traded", which is a claim rather than a
    gap. live_overlay() reports it as None and the screen prints a dash.
    """
    base = _indices_stored()
    rows, live, age = [], False, None
    for r in base["rows"]:
        bar, was = live_overlay(r, r["index"])
        rows.append({**r, **bar} if was else r)
        live = live or was
    if live:
        try:
            import feed_snap
            age = feed_snap.read()["age"]
        except Exception:
            age = None
    return {**base, "rows": rows, "live": live, "age": age}


def _indices_stored():
    """The last bar of every index in the archive."""
    d = MASTER / "indices"
    if not d.is_dir():
        return {"session": None, "rows": []}
    rows = []
    for sub in sorted(d.iterdir()):
        bar = _last_bar(sub / "1D.txt") if sub.is_dir() else None
        if bar:
            rows.append({"index": sub.name, **bar})
    return {"session": max((r["date"] for r in rows), default=None), "rows": rows}


def heatmap():
    """Every equity under its sector — live off the socket while the market is open.

    The stored layer below is cached; the live layer is applied on top of it every call. During a
    session that is the difference between a tile showing the previous close and showing the
    market, which CLAUDE.md's live-data rule requires of every screen.
    """
    base = _heatmap_stored()
    try:
        import feed_snap
        import live_1d
    except Exception:
        return base
    snap = feed_snap.read()
    if not snap["fresh"] or not snap["quotes"]:
        return {**base, "live": False, "age": snap["age"]}

    quotes, hit = snap["quotes"], 0
    sectors = []
    for sec in base["sectors"]:
        members, changed = [], False
        for m in sec["symbols"]:
            q = quotes.get(live_1d.feed_name(m["symbol"]))
            if q and q.get("ltp") is not None:
                prev = q.get("close")
                m = {**m, "close": q["ltp"],
                     "pct": round((q["ltp"] - prev) / prev * 100, 2) if prev else m["pct"]}
                if q.get("volume"):
                    m["volume"] = q["volume"]
                    if q.get("avg_price"):
                        m["turnover"] = q["volume"] * q["avg_price"]
                changed = True
                hit += 1
            members.append(m)
        if not changed:
            sectors.append(sec)
            continue
        turnover = sum(m["turnover"] or 0 for m in members)
        # The sector's OWN index is the market's summary of it, so prefer the live index tick.
        # Only when there is no index (or it has not printed) fall back to weighting the members —
        # the same rule the stored layer uses, so the two cannot disagree about what a sector did.
        iq = quotes.get(live_1d.feed_name(sec["index"])) if sec["index"] else None
        if iq and iq.get("ltp") is not None and iq.get("close"):
            pct = round((iq["ltp"] - iq["close"]) / iq["close"] * 100, 2)
            official = True
        else:
            weight = turnover or len(members)
            pct = (sum((m["pct"] or 0) * (m["turnover"] or 1) for m in members) / weight
                   if weight else 0.0)
            official = False
        sectors.append({**sec, "pct": pct, "turnover": turnover, "official": official,
                        "symbols": sorted(members, key=lambda m: -(m["turnover"] or 0))})

    nepse = base["nepse"]
    nq = quotes.get("NEPSE")
    if nepse and nq and nq.get("ltp") is not None:
        prev = nq.get("close")
        nepse = {**nepse, "close": nq["ltp"],
                 "pct": round((nq["ltp"] - prev) / prev * 100, 2) if prev else nepse.get("pct")}
    return {**base, "nepse": nepse, "sectors": sorted(sectors, key=lambda s: -s["turnover"]),
            "live": hit > 0, "age": snap["age"], "live_symbols": hit}


def _heatmap_stored():
    """The archive layer. Cached on the symbols directory's mtime."""
    d = MASTER / "symbols"
    stamp = d.stat().st_mtime if d.is_dir() else None
    if _hm_cache["stamp"] == stamp and stamp is not None:
        return _hm_cache["value"]

    sector_of = _sectors()
    idx = {r["index"]: r for r in indices()["rows"]}
    groups = defaultdict(list)
    for symbol, sector in sector_of.items():
        bar = _last_bar(d / symbol / "1D.txt")
        if bar and bar["close"]:
            groups[sector].append({"symbol": symbol, **bar})

    out = []
    for sector, members in sorted(groups.items()):
        turnover = sum(m["turnover"] or 0 for m in members)
        ticker = _SECTOR_INDEX_BY_KEY.get(_key(sector))
        official = idx.get(ticker, {}).get("pct") if ticker else None
        if official is None:
            # No index for this sector (or it has not printed today) — turnover-weight the
            # members rather than a plain mean, so one untraded scrip at -10% cannot swing it.
            weight = turnover or len(members)
            official = (sum((m["pct"] or 0) * (m["turnover"] or 1) for m in members) / weight
                        if weight else 0.0)
        out.append({
            "sector": sector, "index": ticker, "pct": official, "turnover": turnover,
            "symbols": sorted(members, key=lambda m: -(m["turnover"] or 0)),
            "count": len(members),
            "official": ticker in idx and idx.get(ticker, {}).get("pct") is not None,
        })

    nepse = idx.get("NEPSE", {})
    value = {"session": max((m["date"] for g in out for m in g["symbols"]), default=None),
             "nepse": nepse or None, "sectors": sorted(out, key=lambda s: -s["turnover"]),
             "symbols": sum(len(g["symbols"]) for g in out)}
    _hm_cache.update(stamp=stamp, value=value)
    return value
