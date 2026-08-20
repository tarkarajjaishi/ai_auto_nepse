# Architecture — Python backend, Next.js frontend

Decided 2026-08-20. This supersedes the "Python only" rule that previously governed the whole
repo, and it is the reason `ui.py` is now legacy rather than the product.

## The split

```
  Master_data/*.txt          the archive — plain text, no database, unchanged
        |
        v
  Python  (unchanged)        prices · indicators · supply_demand · swing_pro · trade_setup
        |                    backtest · operator_* · volume_spike · scan · fetch_*
        |                    ALL maths and ALL data access stays here
        v
  api/  (new, Python)        read-only HTTP/JSON over the same functions
        |
        v
  web/  (new, TypeScript)    Next.js app — every screen, every chart, every interaction
```

**The rule that keeps this honest: the frontend computes nothing.** No indicator, no score, no
gate, no R-multiple is ever recalculated in TypeScript. If a number appears on screen it came
from a Python function over the archive. This project has already been bitten repeatedly by the
same quantity being computed in two places and drifting — three different "measured" edge values
shipped at once, and five modules read prices raw while the rest read them adjusted. A second
implementation in another language would be that failure mode with a language barrier on top.

The API is a thin projection of functions that already exist. It does not own logic.

## Why not keep Streamlit

Streamlit re-runs the whole script on every interaction and owns the render loop, so the UI
budget was spent fighting it: a Plotly chart that resolved to 0px inside a flex item, a fragment
wrapper that broke the height chain, four separate attempts at one heatmap, and a poll interval
that had to be traded off against a component re-mount flashing black. None of that is design
work. React gives the render control the app needs, and the listed stack is the standard set for
it.

## Frontend stack

See the table in `CLAUDE.md` — that is the authoritative list. Two entries deserve a note:

- **Apache ECharts** for analytics (heatmaps, treemaps, distributions, scorecards).
- **Lightweight Charts** for price. It is TradingView's own candle renderer and is the right tool
  for the Chart page; ECharts is not a substitute for it and neither replaces the other.

## Layout of the repo after this change

```
  chukul_data/
    *.py                 backend: maths, fetchers, scripts  (unchanged)
    api/                 the HTTP layer (Python)
    web/                 the Next.js app (TypeScript)
    ui.py                LEGACY Streamlit — still live, frozen for new features
    Master_data/         the .txt archive (never in git)
```

## Migration stance

`ui.py` stays running and correct until the Next.js app replaces it screen for screen. It is the
reference implementation for behaviour — including the parts that were hard-won:

- a board must state its session and warn when the archive has moved past it
- "AVOID" must not render in the success colour (it starts with "A")
- a sidebar entry with no matching body draws a blank page and raises nothing
- a money call must never be reachable from anything on a timer
- 0.00x volume means *did not trade*, not *dry*

Port the behaviour, not just the look.

## Current target

`https://ai.tarkarajjaishi.com.np/admin` — the admin/terminal surface, sidebar-driven. The public
landing page comes later.
