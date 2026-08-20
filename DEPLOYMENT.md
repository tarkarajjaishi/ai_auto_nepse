# chukul_data — what's built & what runs on the VPS

NEPSE market archive, a Streamlit analysis app, and a Next.js admin terminal in front of a
read-only Python API. Every stored artifact is a plain `.txt` under `Master_data/` (no
database). This file is the operational map: what runs, which external services it talks to,
and how the VPS is set up.

Live at **https://ai.tarkarajjaishi.com.np** (HTTP Basic Auth, user `tarka`).

| URL | Serves | Process |
|---|---|---|
| `/` | the Streamlit app | `chukul` — `streamlit run ui.py`, `127.0.0.1:8501` |
| `/admin` | the Next.js terminal | `chukul-web` — `node server.js`, `127.0.0.1:3101` |
| `/api/…` | read-only JSON | `chukul-api` — `python -m api`, `127.0.0.1:8600` |

Same origin on purpose: the browser already holds Basic Auth for this realm, so the API
inherits the wall instead of needing a second one, and no hostname is baked into the JS
bundle. **`/_next` must be proxied too** — Next serves its JS and CSS from there, not from
under `/admin`, and proxying only `/admin` yields a styleless page that never hydrates.

The frontend computes nothing. Every number on a page came from a Python function that read
the archive; `web/src/lib/api.ts` is the only file in `web/` that makes a network call, so
that rule stays checkable by reading one file.

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
| Frontend | `~/chukul-web` — the built Next.js standalone tree, ~27 MB. Previous bundle kept at `~/chukul-web.old` for a one-command rollback. |
| Node | `v24.19.0` LTS at `/opt/nodejs`, symlinked to `/usr/local/bin/node`. Installed from the official tarball with its SHASUMS256 verified. |
| Services | `chukul` (8501, `MemoryMax=1200M`) · `chukul-api` (8600, `MemoryMax=256M`, measured at 23 MB) · `chukul-web` (3101, `MemoryMax=512M`, ~93 MB). All `Restart=always`, all bound to 127.0.0.1. |
| Web | nginx `ai.tarkarajjaishi` vhost: 80→443, self-signed cert, **Basic Auth** (`/etc/nginx/.htpasswd-ai`) at server level so it covers all three. `/admin` + `/_next` → 3101, `/api` → 8600, `/` → 8501 with WebSocket headers. |
| DNS | Cloudflare `ai.tarkarajjaishi.com.np` → proxied, SSL mode **Full** |
| Data | `Master_data/` partial archive (~3 GB): 1D + 1m bars, indices, broker_flow, report txts. Historical `floorsheet/` (~3.5 GB) not transferred — disk. |
| Secrets | `naasa_login/session.txt`, `swp_login/session.txt` live only in `Master_data/` on the box (gitignored) |

**This box is shared and it is tight.** 3.8 GB RAM with ~350 MB free and ~2.7 GB of swap
already in use, 2 cores, and it also runs k3s, buildkit, postgres, redis and three other
sites (`churchnepal`, `nepalidriver.com`, `padma`). Do not `pnpm install` or `next build`
here — it will OOM something that is currently serving. The frontend is built on the dev
machine and shipped as a runnable tree; the box needs only the `node` binary.

**Manage**

```bash
ssh ubuntu@202.51.70.101
sudo systemctl restart chukul chukul-api chukul-web
journalctl -u chukul-web -n 50 -f
curl -s localhost:8600/api/health          # is the API reading today's archive?
curl -so /dev/null -w '%{http_code}\n' localhost:3101/admin
```

**Rollback the frontend** (the swap keeps the last bundle):

```bash
ssh ubuntu@202.51.70.101 \
  'rm -rf ~/chukul-web.bad && mv ~/chukul-web ~/chukul-web.bad &&
   mv ~/chukul-web.old ~/chukul-web && sudo systemctl restart chukul-web'
```

**Editing the nginx vhost** — always back it up and validate before reloading; four other
sites share this nginx, and `reload` is graceful where `restart` drops them all:

```bash
sudo cp /etc/nginx/sites-available/ai.tarkarajjaishi{,.bak.$(date +%F-%H%M%S)}
sudo nginx -t && sudo systemctl reload nginx
```

`nginx -t` prints `protocol options redefined for 0.0.0.0:443` warnings from the other
vhosts. They are pre-existing and unrelated — look for `test is successful`.

**Deploy — always does GitHub *and* the VPS together** (`deploy.py`), so the box never drifts
ahead of the repo:

```bash
python deploy.py -m "what changed"
```

It stages + commits + pushes to `origin`, then tars the **tracked source** (never
`Master_data/`, so the archive and saved logins stay on the box) over SSH and restarts
`chukul` **and** `chukul-api` — the API imports the same modules the Streamlit app does, so
restarting only one leaves the terminal serving yesterday's Python against today's `.txt`,
which renders perfectly and is wrong. Then it builds the frontend locally and ships that.

`--no-git` ships only; `--no-vps` pushes only; `--no-web` skips the ~30 s frontend build.

**The frontend bundle must contain real files, never pnpm symlinks.** pnpm's default
`node_modules` is a symlink farm into `.pnpm`, and on Windows those links carry MSYS paths
(`/c/Tarkaproject/...`). `next build` copies that shape straight into `.next/standalone`, so
the bundle runs on the machine that built it and dies on the box with `Cannot find module
'next'`. Dereferencing at ship time does **not** save you — `tar -h` cannot follow an MSYS
path and silently *skips* what it cannot follow, which yields a bundle quietly missing
`@swc/helpers` instead. `nodeLinker: hoisted` in `web/pnpm-workspace.yaml` is what prevents
it (it belongs there, not in `.npmrc`: pnpm 11 ignores `node-linker` in `.npmrc` without a
word). `deploy.py` refuses to swap a bundle containing any symlink, and `test_ops.py` guards
both halves.

Manual one-file shortcut, if ever needed:

```bash
tar czf - ui.py | ssh ubuntu@202.51.70.101 "cd ~/chukul_data && tar xzf - && sudo systemctl restart chukul"
```

---

## Repo rules

- **Python is the backend, TypeScript is the frontend** — and nothing else. The old
  "Python only, no exceptions" rule is retired; see `CLAUDE.md` for the current stack and
  `ARCHITECTURE.md` for why the split exists.
- **`.txt` only** for storage — stdlib `open()`, no DB/CSV/JSON/xlsx as data stores.
- `Master_data/` is regenerated by the fetch scripts and never committed.
- `web/` computes nothing. If a number reaches the screen, Python produced it.
