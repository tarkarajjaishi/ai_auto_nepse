# chukul_data — what's built & what runs on the VPS

NEPSE market archive + Streamlit analysis app. Python only; every stored artifact is a
plain `.txt` under `Master_data/` (no database). This file is the operational map: what
the app does, which external services it talks to, and how the VPS is set up.

Live at **https://ai.tarkarajjaishi.com.np** (HTTP Basic Auth, user `tarka`).

---

## Pages (sidebar)

| Page | What it shows |
|---|---|
| **Chart** | TradingView-style candles (1D / 1m) with personal SMC indicators, full-fit layout |
| **Floorsheet** | Per-trade floorsheet for a symbol/date |
| **Broker flow** | Daily net buy/sell per broker for a symbol |
| **Scanner** | Signal scan output (`scan.txt`) |
| **Volume spike** | Unusual-activity screen (`volume_spike.txt`) |
| **Operator radar** | Single-broker accumulation candidates with proof chain (floorsheet + chart + position), earnings backdrop, lock-in risk, and a **Professional trade setup** panel (A+ score, entry/T1/T2/stop, "can you still buy" timing) |
| **NAASA** | Order panel (display-only), **live socket feed** (full Top-5 bid/ask ladder + stock info, sub-second), order book, holdings, collateral |
| **Heatmap** | Our own NEPSE heatmap — sector-first treemap (click to drill into symbols) + index heatmap + index watchlist; works even when the market is closed |
| **Cron** | Data freshness table, **master pipeline** (run-now + auto timer), separate **full-history backfill** section, last-run log |

The selected page persists in the URL (`?page=…`) so refresh/redeploy keeps you where you are.

---

## External integrations

### NAASA Securities (`naasa.py`)
- **Permanent login** — email+password drives the NextAuth/Keycloak browser flow; session
  cookie in `Master_data/naasa_session.txt`, remembered credentials (opt-in) in
  `naasa_login.txt`. Sticky until explicit sign-out.
- **Live WebSocket feed** — `wss://x.naasasecurities.com.np:8006/WebSocket/Connect`
  authorized with `hdnLogin`/`hdnSession` scraped from the old X app after OAuth login.
  Subscribes `25.1!SYMBOL` (quotes, type 75/77) **and** `25.2!SYMBOL` (5-level depth,
  type 76). Frames are `102^` + base64(zlib-deflate). Always on (no toggle); the app
  holds it server-side (`st.cache_resource` daemon thread).
- **Account data over the same session** — order book (`POST /MarketOrder/OrderBook`),
  holdings (`POST /TradeBook/HoldingDataReport`), collateral (`POST /Home/DashboardDetails`),
  batch quotes (`POST /MarketOrder/SpecifiedQuote` — works while the market is closed).
  Sharing ONE X-app login between the socket and these calls is what avoids NAASA's
  one-session-per-account eviction.
- **Caveat** — opening `x.naasasecurities.com.np` in a browser on the same account evicts
  the server feed (and vice-versa). Use a dedicated feed account to avoid it.
- **No order placement** — deliberately display-only; the app never places/cancels orders.

### SmartWealthPro (`swp.py`, `fetch_swp.py`)
- **Permanent login** — classic ASP.NET form POST with anti-forgery token; session in
  `Master_data/swp_session.txt`, remembered credentials in `swp_login.txt`. Sidebar
  expander with Test button. Sessions are short-lived; every fetch re-logs in as needed.
- **`fetch_swp.py`** (in the pipeline) rewrites, in the existing txt formats:
  - `corporate_actions.txt` — latest book-closure per company (per-company dividends loop, ~579)
  - `lockin.txt` — IPO promoter shares still locked (unlock date, supply-shock flag)
  - `fundamentals.txt` — **live free float** refresh (public/promoter/float% only; other
    columns, incl. `paid_up`, untouched — that column is the operator scan's data gate)

### chukul.com / merolagani
- Bars, floorsheet, and the symbol universe come from chukul's public API; floorsheet
  scraping falls back to merolagani. `fetch_symbols.py` **merges** the live symbol list
  (add-only — a new listing is auto-added, a feed gap never drops one).

---

## Data pipeline (Cron page)

**Master pipeline** — one ▶ Run-all button + one master timer (default **15:15 NPT**,
add/remove times in-app, stored in `Master_data/cron_schedule.txt`). Jobs run strictly
one-by-one with colour-highlighted flowchart nodes (pending → running → done/failed):

1. **Last Traded Data** — `fetch_symbols.py` → `fetch_last_session.py` (today's daily
   bars, floorsheet, exact minute bars via `fetch_minutes_today.py`, broker-flow upsert)
   → `scan.py` → `volume_spike.py`
2. **SmartWealthPro → Operator** — `fetch_swp.py` → `operator_scan.py`

Latest-session only (~10 min). Fired times persist in `cron_ran.txt` so a redeploy never
re-fires; a missed time is caught up within 30 min; skips Fri/Sat. The scheduler is an
in-app daemon thread (`cron_scheduler()` in `ui.py`) — **no systemd timer** (the old
`chukul-update.timer` is disabled; don't re-enable it or jobs double-fire).

**Full history backfill** — a separate on-page section (NOT part of the daily run):
- Chart history: `fetch_ohlc.py` → `fetch_intraday.py` (all bars, all symbols, all dates)
- Floorsheet history: `fetch_floorsheet_merolagani.py` → `build_broker_flow.py`

These re-walk the whole archive (minutes to hours) and run in a background thread from
their own Backfill buttons.

---

## VPS setup

| Piece | Detail |
|---|---|
| Host | `ubuntu@202.51.70.101` (`ubuntu-kathmandu-01`, in Nepal — NAASA feeds are local). Key-auth SSH. |
| App | `~/chukul_data`, venv at `.venv` (streamlit, pandas, plotly, websocket-client) |
| Service | systemd **`chukul.service`** → `streamlit run ui.py` on `127.0.0.1:8501`, `MemoryMax=1200M`, auto-restart |
| Web | nginx `ai.tarkarajjaishi` vhost: 80→443, self-signed cert, **Basic Auth** (`/etc/nginx/.htpasswd-ai`), proxy to 8501 with WebSocket headers |
| DNS | Cloudflare `ai.tarkarajjaishi.com.np` → proxied, SSL mode **Full** |
| Data | `Master_data/` partial archive (~3 GB): 1D + 1m bars, indices, broker_flow, report txts. Historical `floorsheet/` (~3.5 GB) not transferred — disk. |
| Secrets | `naasa_login/session.txt`, `swp_login/session.txt` live only in `Master_data/` on the box (gitignored) |

**Manage**

```bash
ssh ubuntu@202.51.70.101
sudo systemctl restart chukul        # restart the app
journalctl -u chukul -n 50           # app logs
```

**Deploy — always does GitHub *and* the VPS together** (`deploy.py`), so the box never drifts
ahead of the repo:

```bash
python deploy.py -m "what changed"
```

It stages + commits + pushes to `origin`, then tars the **tracked source** (never
`Master_data/`, so the archive and saved logins stay on the box) over SSH and restarts the
service. `--no-git` ships only; `--no-vps` pushes only.

Manual one-file shortcut, if ever needed:

```bash
tar czf - ui.py | ssh ubuntu@202.51.70.101 "cd ~/chukul_data && tar xzf - && sudo systemctl restart chukul"
```

---

## Repo rules

- **Python only** — no second language anywhere in the project.
- **`.txt` only** for storage — stdlib `open()`, no DB/CSV/JSON/xlsx as data stores.
- `Master_data/` is regenerated by the fetch scripts and never committed.
