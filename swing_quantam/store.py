"""The on-disk format for everything this package produces — writer and reader together.

Storage rule for this project: plain ``.txt``, no database, no JSON/CSV/parquet as
storage. Everything lands in ``Master_data/swing_quantam/``, its own folder.

Two artefacts:

``board.txt``
    One row per symbol, tab-separated with a header — the same shape every other
    board in this repo uses, so ``api.tables.read()`` serves it for free. The
    ``date`` column is **mandatory and last**: four boards have shipped here
    without one, and every single one reported itself fresh forever, because a
    board with no date makes the staleness check answer "not stale" instead of
    "I cannot tell". That is CLAUDE.md's named failure mode and it is not
    repeating on this board.

``<SYMBOL>.txt``
    The per-symbol detail behind the board row — the spec's numbered sections,
    each with its own metrics, note and caveats. The UI renders one collapsible
    panel per section, so the file is written in section order and read back in
    the same order.

Writer and reader live in the same module on purpose. A format with its parser in
another file drifts; this way a change to one is a change to both.
"""

from __future__ import annotations

import os
from typing import NamedTuple

from .loader import OUT


class Row(NamedTuple):
    metric: str
    value: object
    note: str = ""


class Section(NamedTuple):
    n: int  # spec section number, so the UI can print "§19"
    title: str
    rows: tuple[Row, ...]
    note: str = ""  # one-line caveat shown under the section heading


class Detail(NamedTuple):
    symbol: str
    session: str
    signal: str
    score: float | None
    confidence: str
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    sections: tuple[Section, ...]


def _fmt(v: object) -> str:
    """Numbers keep enough precision to be re-read, but not so much they lie."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        if v != v:  # NaN
            return ""
        # Snap floating-point dust to zero. `1.0 - pos - neg` and a sum of signed shares
        # land on +-1.1e-16, and %.6g prints that verbatim — so a share of days shipped as
        # "-1.11022e-16" on 26 rows, a negative share in scientific notation. Measured on
        # the whole board there is a five-order gap between the dust (all < 1e-13) and the
        # smallest real value (7.4e-07), so this cannot swallow a measurement.
        if abs(v) < 1e-12:
            v = 0.0
        return f"{v:.6g}"
    return str(v)


def _esc(s: str) -> str:
    """Tabs and newlines would break the format; nothing else needs escaping."""
    return str(s).replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()


def detail_path(symbol: str) -> str:
    return os.path.join(OUT, f"{symbol.upper()}.txt")


def board_path() -> str:
    return os.path.join(OUT, "board.txt")


def write_detail(d: Detail) -> str:
    os.makedirs(OUT, exist_ok=True)
    out: list[str] = [
        "# swing_quantam detail",
        f"symbol\t{_esc(d.symbol)}",
        f"session\t{_esc(d.session)}",
        f"signal\t{_esc(d.signal)}",
        f"score\t{_fmt(d.score)}",
        f"confidence\t{_esc(d.confidence)}",
    ]
    # Reasons and warnings are repeated keys rather than one delimited field: a
    # reason is free text and any delimiter chosen here would eventually appear
    # inside one.
    out += [f"reason\t{_esc(r)}" for r in d.reasons]
    out += [f"warning\t{_esc(w)}" for w in d.warnings]

    for s in d.sections:
        out.append("")
        out.append(f"## {s.n}\t{_esc(s.title)}")
        if s.note:
            out.append(f"note\t{_esc(s.note)}")
        for r in s.rows:
            line = f"{_esc(r.metric)}\t{_fmt(r.value)}"
            if r.note:
                line += f"\t{_esc(r.note)}"
            out.append(line)

    path = detail_path(d.symbol)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    return path


def read_detail(symbol: str) -> Detail | None:
    """Parse a detail file back. Returns None when the symbol has not been built."""
    path = detail_path(symbol)
    if not os.path.isfile(path):
        return None

    head: dict[str, str] = {}
    reasons: list[str] = []
    warnings: list[str] = []
    sections: list[Section] = []
    cur_n, cur_title, cur_note, cur_rows = 0, "", "", []

    def flush() -> None:
        if cur_title:
            sections.append(Section(cur_n, cur_title, tuple(cur_rows), cur_note))

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            ln = ln.rstrip("\r\n")
            if not ln or ln.startswith("# "):
                continue
            if ln.startswith("## "):
                flush()
                p = ln[3:].split("\t", 1)
                try:
                    cur_n = int(p[0])
                except ValueError:
                    cur_n = 0
                cur_title = p[1] if len(p) > 1 else ""
                cur_note, cur_rows = "", []
                continue
            p = ln.split("\t")
            key = p[0]
            val = p[1] if len(p) > 1 else ""
            note = p[2] if len(p) > 2 else ""
            if not cur_title:
                if key == "reason":
                    reasons.append(val)
                elif key == "warning":
                    warnings.append(val)
                else:
                    head[key] = val
            elif key == "note" and not cur_rows and not cur_note:
                cur_note = val
            else:
                cur_rows.append(Row(key, val, note))
    flush()

    score: float | None
    try:
        score = float(head.get("score", ""))
    except ValueError:
        score = None

    return Detail(
        symbol=head.get("symbol", symbol.upper()),
        session=head.get("session", ""),
        signal=head.get("signal", ""),
        score=score,
        confidence=head.get("confidence", ""),
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        sections=tuple(sections),
    )


def prune_excluded(excluded: set[str]) -> list[str]:
    """Delete detail files for symbols this engine no longer analyses. Returns the names.

    Narrowing the universe leaves the previous build's reports on disk, and a report that
    is no longer rebuilt is a stale read presented as current — `/api/swingquantam/NABILP`
    happily served a full 103-section analysis from a build that will never run again.
    Measured after the mutual-fund/promoter exclusion: 69 such files.

    Deliberately narrow. It removes ONLY symbols in the excluded set — a deliberate policy
    decision recorded in instruments.txt — and never a symbol that merely failed or was
    skipped this run, because "the build did not reach it today" is not the same as "this
    is not analysed", and treating them alike would delete real reports on a bad night.
    Callers gate it on a FULL run, the same gate that lets the board shrink.
    """
    if not excluded or not os.path.isdir(OUT):
        return []
    gone = []
    for f in sorted(os.listdir(OUT)):
        if not f.endswith(".txt") or f in ("board.txt", "brokers.txt", "probability.txt"):
            continue
        if f[:-4] in excluded:
            os.remove(os.path.join(OUT, f))
            gone.append(f[:-4])
    return gone


def board_rows() -> int:
    """How many data rows the board on disk currently has. 0 when there is none."""
    path = board_path()
    if not os.path.isfile(path):
        return 0
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return max(0, sum(1 for ln in fh if ln.strip()) - 1)


def write_board(columns: list[str], rows: list[dict], allow_shrink: bool = False) -> str:
    """Write board.txt. ``date`` is forced last — see the module docstring.

    Refuses to shrink the board unless told to. A `--limit 10` test run rewrites the
    same file as the full 593-symbol build, so the production board silently drops
    from 481 rows to 10 and the page renders 10 symbols looking perfectly healthy —
    nothing errors, nothing is stale, the count is just quietly wrong. That is the
    same failure the archive writer guards with its never-shrinks rule, and it has
    already happened once here. A full run passes ``allow_shrink=True`` because it is
    entitled to replace the board; a partial run is not.
    """
    cols = [c for c in columns if c != "date"] + ["date"]
    missing = [r for r in rows if not r.get("date")]
    if missing:
        raise ValueError(
            f"{len(missing)} board rows have no date. A board without a date reports "
            "itself fresh forever — refusing to write it."
        )
    had = board_rows()
    if not allow_shrink and had > len(rows):
        raise ValueError(
            f"refusing to shrink the board from {had} rows to {len(rows)}. This looks "
            "like a partial run overwriting a full one. Re-run without --limit and "
            "without an explicit symbol list, or pass allow_shrink=True deliberately."
        )
    os.makedirs(OUT, exist_ok=True)
    lines = ["\t".join(cols)]
    for r in rows:
        lines.append("\t".join(_esc(_fmt(r.get(c))) for c in cols))
    path = board_path()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def _demo() -> None:
    # Redirect OUT to a scratch dir for the whole self-check. The first version of this wrote a
    # one-row board to the REAL Master_data/swing_quantam/board.txt and then deleted it, so
    # merely running the self-check destroyed the production board — and the hourly health check
    # runs the self-checks. A test that damages what it is testing is worse than no test.
    global OUT
    import shutil
    import tempfile

    real_out = OUT
    OUT = tempfile.mkdtemp(prefix="swing_quantam_selfcheck_")
    try:
        _run_demo()
    finally:
        shutil.rmtree(OUT, ignore_errors=True)
        OUT = real_out


def _run_demo() -> None:
    d = Detail(
        symbol="TEST",
        session="2026-08-20",
        signal="WATCH / BUILDING",
        score=61.5,
        confidence="medium",
        reasons=("7D net buying on 5 of 7 sessions", "3D flow accelerating"),
        warnings=("concentration rising into the move", "15D/30D conflict"),
        sections=(
            Section(5, "Basic market statistics", (Row("transactions", 459), Row("volume", 36097)),
                    "Executed transactions only — the floorsheet records no orders."),
            Section(19, "Persistence score", (Row("positive days", 19, "of 30"), Row("persistence", 0.6333)),),
        ),
    )
    p = write_detail(d)
    back = read_detail("TEST")
    assert back is not None
    assert back.symbol == "TEST" and back.session == "2026-08-20"
    assert back.signal == "WATCH / BUILDING"
    assert back.score == 61.5 and back.confidence == "medium"
    assert back.reasons == d.reasons, back.reasons
    assert back.warnings == d.warnings, back.warnings
    assert len(back.sections) == 2
    assert back.sections[0].n == 5 and back.sections[0].note.startswith("Executed")
    assert [r.metric for r in back.sections[0].rows] == ["transactions", "volume"]
    assert back.sections[1].rows[0].note == "of 30"
    # A section with no note must not swallow its first metric as one.
    assert back.sections[1].note == ""
    assert [r.metric for r in back.sections[1].rows] == ["positive days", "persistence"]

    two = [{"symbol": "TEST", "date": "2026-08-20", "signal": "WATCH", "score": 61.5},
           {"symbol": "TEST2", "date": "2026-08-20", "signal": "NEUTRAL", "score": 40.0}]
    bp = write_board(["symbol", "date", "signal", "score"], two)
    header = open(bp, encoding="utf-8").readline().rstrip("\n").split("\t")
    assert header[-1] == "date", f"date must be last, got {header}"
    assert board_rows() == 2, board_rows()

    try:
        write_board(["symbol", "date"], [{"symbol": "X"}])
    except ValueError:
        pass
    else:
        raise AssertionError("a dateless board row must be refused, not written")

    # The shrink guard: a partial run must not silently replace a fuller board. This is not
    # hypothetical — a `--limit 10` verification pass cut a real 481-row board to 6.
    try:
        write_board(["symbol", "date", "signal", "score"], two[:1])
    except ValueError as exc:
        assert "shrink" in str(exc), exc
    else:
        raise AssertionError("a partial board must not be allowed to shrink a fuller one")
    assert board_rows() == 2, "the refused write must leave the existing board untouched"

    # ...but a deliberate full run may replace it with fewer rows.
    write_board(["symbol", "date", "signal", "score"], two[:1], allow_shrink=True)
    assert board_rows() == 1

    print(f"store ok — round-trips detail ({len(back.sections)} sections, "
          f"{len(back.reasons)} reasons, {len(back.warnings)} warnings), refuses a dateless "
          "board, and refuses to shrink one")


if __name__ == "__main__":
    _demo()
