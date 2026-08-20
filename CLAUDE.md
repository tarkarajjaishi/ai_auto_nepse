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

## What the numbers mean

Every board reads a PRE-COMPUTED `.txt`, so its numbers are only as fresh as the last time its
script ran. Any UI that shows them must say which session they are from, and warn when the
archive has moved past it — a stale read presented as current is the failure mode this project
keeps hitting. See `ui.py:warn_if_behind_archive` for the rule the Next.js app must reproduce.
