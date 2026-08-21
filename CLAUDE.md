# chukul_data

## Language rule — CHANGED 2026-08-20

**Python is the BACKEND. Next.js/TypeScript is the FRONTEND. Nothing else.**

This replaces the old "Python only, no exceptions" rule, which forbade JavaScript outright. That
rule is dead — do not follow it, and do not re-add it.

- **Backend — Python only.** Data fetching, the archive, all indicator/scoring/backtest maths,
  and the HTTP API. No Go, Rust, Java, C#, Ruby, PHP. No shell/PowerShell as project logic.
- **Frontend — TypeScript only**, in the Next.js app. No plain JavaScript files, no other
  framework, and **no Python rendering UI any more**.

Streamlit (`ui.py`) is the OLD frontend. It stays live and working until the Next.js app replaces
it page for page — do not delete or break it in the meantime — but **add no new features to it**.
New UI work goes in the Next.js app.

Allowed as before (not programming languages): Markdown, JSON/YAML/TOML/INI config, `.env`,
plain CSV/text data, SQL inside Python.

## Frontend stack — fixed, do not substitute

| Purpose | Technology |
|---|---|
| Framework | Next.js |
| UI | React |
| Language | TypeScript |
| Styling | Tailwind CSS |
| Component system | shadcn/ui + Radix UI |
| Icons | Lucide React |
| Animations | Motion |
| Server state / API data | TanStack Query |
| Client state | Zustand |
| Forms | React Hook Form |
| Validation | Zod |
| Tables | TanStack Table |
| Charts | Apache ECharts |
| Financial charts | Lightweight Charts |
| Authentication | Auth.js |
| Notifications | Sonner |
| Date/time | date-fns |
| Testing | Vitest + React Testing Library + Playwright |
| Code quality | ESLint + Prettier |
| Git hooks | Husky + lint-staged |
| Package manager | pnpm |

If a task seems to need something outside this list, say so rather than reaching for an
alternative. Adding a dependency that duplicates one of these is a defect.

## Storage rule — unchanged

**`.txt` only.** There is no database. Everything the project saves goes into plain `.txt` files.

Not allowed: SQLite or any DB server, `.csv`, `.json`, `.parquet`, `.pkl`, `.xlsx`, `.db`, `.h5`
as storage.

Exception: config files (`.env`, `.toml`/`.yaml` settings) are not data — they stay as they are.
The frontend's own `package.json` / `tsconfig.json` / lockfile are config, not storage.

Write with stdlib `open()`. If a task seems to need a real DB, do it with `.txt` or say it can't
be done. **The API serves JSON over HTTP — that is a wire format, not storage, and is fine.**

## Live data rule — during market hours the WHOLE system is live

**While NEPSE is open, every screen shows the NAASA WebSocket feed, not the stored close.**
Not just the chart, not just one panel — every price, every percentage, every sector tile, on
both surfaces. A screen that shows yesterday's close during a live session is wrong even when the
number itself is accurate, because the reader cannot tell which one they are looking at.

How it works, and the one constraint that shapes all of it:

- **NAASA allows ONE live session per account.** Exactly one process may hold the socket.
  `feed_publisher.py` (systemd `chukul-feed`) is that process. Nothing else may open one —
  whichever connects second evicts the first and the two fight for the rest of the session.
- The publisher writes `Master_data/feed_snapshot.txt` every second. **Everything else reads that
  file.** `ui.py` checks `feed_snap.publishing()` and yields; the API serves it through
  `/api/bar/<symbol>`, `/api/quotes?symbols=`, and `/api/depth`.
- The browser opens **no** socket. It polls those routes at 1s. True push would need a second
  NAASA session, which does not exist.
- Today's bar is built by `live_1d.row()` — the same function that writes it into `1D.txt` — so
  the screen and the archive can never disagree about today.

**Every live number carries its age, and every screen shows it.** `age` and `fresh` travel with
the payload, and a stale snapshot renders as stale rather than as a price. A quote whose own
timestamp is not today is refused outright: the socket keeps yesterday's quote alive past the
close, and printing that as today is the failure this project keeps finding.

When adding a screen: if it shows a price during a session, it polls `/api/quotes` (many
instruments) or `/api/bar` (one), and it labels what it is showing. Never re-derive a live price
in the frontend, and never poll `/api/bars` — that is the whole archive.

## What the numbers mean

Every board reads a PRE-COMPUTED `.txt`, so its numbers are only as fresh as the last time its
script ran. Any UI that shows them must say which session they are from, and warn when the
archive has moved past it — a stale read presented as current is the failure mode this project
keeps hitting. See `ui.py:warn_if_behind_archive` for the rule the Next.js app must reproduce.
