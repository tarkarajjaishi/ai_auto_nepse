"""HTTP/JSON projection of the analysis modules, for the Next.js frontend.

This layer owns NO logic. Every number it returns comes from a function that already exists and
is already tested — `prices.bars`, `swing_pro.analyse`, `supply_demand.dashboard`, and the
pre-computed `.txt` boards. It parses a request, calls the thing, and serialises the answer.

That constraint is the point. This repo has twice shipped the same quantity computed in two
places and drifting: three different "measured" out-of-sample edges at once, and five modules
reading prices raw while the rest read them adjusted. Re-deriving anything here — or in
TypeScript — would be that failure with a process boundary added. If a value is not available
from an existing function, add it to that module and expose it, do not recompute it here.

Stdlib only, no framework: `http.server` is enough for a read-only localhost API behind nginx,
and it keeps the backend dependency list where it is.

    python -m api            # serve on 127.0.0.1:8600
"""
