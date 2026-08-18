"""Daily archive refresh. Run after the 15:00 NPT close — that is when the floorsheet
holds the full session, so today's minute bars come out exact rather than truncated.

    python daily_update.py

Steps run in order; a failing step is reported but does not stop the rest, since each
later step still works off whatever is already on disk. Exits non-zero if any failed,
so a scheduler can alert on it. Every run appends to update_log.txt.
"""

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from fetch_ohlc import NPT

HERE = Path(__file__).parent
LOG = HERE / "update_log.txt"

STEPS = [
    ("symbol + index lists", [sys.executable, "fetch_symbols.py"]),
    ("daily bars", [sys.executable, "fetch_ohlc.py"]),
    ("minute archive", [sys.executable, "fetch_intraday.py"]),
    ("today's exact minutes", [sys.executable, "fetch_minutes_today.py"]),
    ("instrument types", [sys.executable, "naasa.py"]),
    ("floorsheet", [sys.executable, "fetch_floorsheet_merolagani.py"]),
    ("broker flow table", [sys.executable, "build_broker_flow.py"]),
    # last steps, so both reflect everything fetched above
    ("signal scan", [sys.executable, "scan.py"]),
    ("volume spike screen", [sys.executable, "volume_spike.py"]),
]


def run(label, cmd):
    started = time.time()
    try:
        p = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, timeout=7200)
        tail = (p.stdout or p.stderr).strip().splitlines()[-1:] or [""]
        return p.returncode == 0, time.time() - started, tail[0][:120]
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, time.time() - started, f"{type(e).__name__}: {e}"[:120]


def main():
    stamp = datetime.now(NPT).strftime("%Y-%m-%d %H:%M")
    lines = [f"=== {stamp} NPT"]
    print(lines[0])

    failed = []
    for label, cmd in STEPS:
        ok, secs, note = run(label, cmd)
        line = f"  {'ok  ' if ok else 'FAIL'} {label:22} {secs:6.0f}s  {note}"
        print(line, flush=True)
        lines.append(line)
        if not ok:
            failed.append(label)

    summary = "  all steps ok" if not failed else f"  FAILED: {', '.join(failed)}"
    print(summary)
    with LOG.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines + [summary, ""]) + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
