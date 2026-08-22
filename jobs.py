"""Run a board's rebuild script — once at a time, across every process on the box.

There are three things that can start a rebuild: the systemd timer, the Cron page's manual run,
and the API's rebuild endpoint. On 2026-08-20 two of them fired the whole pipeline in the same
minute, because the only guard that existed was a `threading.Lock` inside the Streamlit process
and a systemd unit cannot see one. The archive survived it, but two concurrent writers to the
same .txt files is the corruption every overwrite guard in this project exists to prevent.

So the lock is a FILE, created with O_EXCL, and it is the only mechanism that works between
processes that do not know about each other.

Two rules that are not negotiable:

1. **`SCRIPTS` is an allow-list, and the client never names a path.** The API takes a board name
   and looks the script up here. Anything that lets a caller choose what to execute is a remote
   shell with extra steps, however well the caller is authenticated.
2. **Nothing here imports naasa or touches an order.** A rebuild runs analysis over files on
   disk; it must not be able to reach a money call, and `test_ops` checks that it cannot.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).parent
MASTER = HERE / "Master_data"
LOCK = MASTER / "pipeline.lock"

# board name -> the script that rebuilds it. The keys match api/tables.py BOARDS so a caller can
# ask for the thing it is looking at; "pipeline" is the whole nightly chain, in order.
SCRIPTS: dict[str, tuple[str, ...]] = {
    "scan": ("scan.py",),
    "volume_spike": ("volume_spike.py",),
    "operator_scan": ("operator_scan.py",),
    "operator_now": ("operator_now.py",),
    "operator_verdict": ("operator_verdict.py",),
    "supply_demand": ("supply_demand.py",),
    "swing_pro": ("swing_pro.py",),
    "swing_master": ("swing_master.py",),
    "master_signal": ("master_signal.py",),
    "backtest": ("backtest.py",),
    # A shim, not the package: this runs scripts as [python, <script>] and cannot spell
    # `-m swing_quantam`. A top-level swing_quantam.py would shadow the package directory.
    "swing_quantam": ("build_swing_quantam.py",),
    # Same script, second artefact: a FULL run of the package writes brokers.txt after
    # board.txt (see swing_quantam/__main__.main). Listed by name so the freshness half of
    # test_ops can account for it — a board nothing declares is a board nobody can tell is
    # stale — and so the console's rebuild button targets the right script.
    "swing_quantam_brokers": ("build_swing_quantam.py",),
    "pipeline": ("daily_update.py", "scan.py", "volume_spike.py", "fetch_swp.py",
                 "operator_scan.py", "supply_demand.py", "swing_pro.py", "backtest.py",
                 "build_swing_quantam.py"),
}

# What `chukul-update.service` actually runs at 15:15 NPT. Keep this equal to the unit's
# ExecStart list -- they are two definitions of one pipeline, and test_ops checks that every
# board is accounted for by one of them.
NIGHTLY = SCRIPTS["pipeline"]

# Boards whose script is NOT in that chain, so they only change when somebody rebuilds them.
#
# This exists because the Cron page told the reader boards refresh on their own. That is true of
# the pipeline and false of these four, and an unattended board that reports itself "current"
# against a moving archive is the failure this project keeps finding. Each entry says what is
# true, not why -- the reason these sit outside the nightly chain is not recorded anywhere, and
# inventing one would be worse than admitting it.
MANUAL = {
    "operator_now": "not in chukul-update.service; only changes when rebuilt here or on the box",
    "operator_verdict": "not in chukul-update.service; the four-test verdict is rebuilt on demand",
    "master_signal": "not in chukul-update.service; rebuild it to re-score today's candidates",
    "swing_master": "not in chukul-update.service; it sizes master_signal's rows for your book",
}


def auto(board):
    """Does this board rebuild itself overnight?"""
    scripts = SCRIPTS.get(board)
    return bool(scripts) and board not in MANUAL and all(x in NIGHTLY for x in scripts)


# A rebuild that has held the lock this long is assumed dead — the pipeline's own worst case is
# ~40 minutes of CPU, so three hours is generous rather than tight.
STALE_AFTER = 3 * 3600

_state: dict = {"running": None, "started": 0.0, "last": None, "log": [], "lock": threading.Lock()}


def _pid_alive(pid: int) -> bool:
    """Is this pid still running? Used only to break a lock left by a killed process."""
    if pid <= 0:
        return False
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid],
                             capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def holder():
    """(pid, started, what) of whoever holds the lock, or None. Never raises."""
    try:
        raw = LOCK.read_text(encoding="utf-8").split("\t")
        return int(raw[0]), float(raw[1]), raw[2].strip()
    except (OSError, ValueError, IndexError):
        return None


def acquire(what: str):
    """Take the lock, or return None if someone else has it.

    O_CREAT|O_EXCL is atomic: two processes racing here cannot both win, which a "check then
    write" cannot promise. A lock whose owner is gone, or which is older than STALE_AFTER, is
    broken rather than left to block the box forever.
    """
    MASTER.mkdir(parents=True, exist_ok=True)
    held = holder()
    if held:
        pid, started, _ = held
        if (not _pid_alive(pid)) or (time.time() - started) > STALE_AFTER:
            try:
                LOCK.unlink()
            except OSError:
                pass
        else:
            return None
    elif LOCK.exists():
        # The file is there but says nothing readable, so no process can be shown to own it.
        # Left alone it blocks every rebuild on the box forever and no code path can clear it:
        # holder() returns None, so the stale check above never runs, and O_EXCL then fails
        # because the file exists. A corrupt lock is a broken lock.
        try:
            LOCK.unlink()
        except OSError:
            pass
    try:
        fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("%d\t%f\t%s\n" % (os.getpid(), time.time(), what))
    return LOCK


def release():
    """Drop the lock if this process owns it. Someone else's lock is never removed."""
    held = holder()
    if held and held[0] == os.getpid():
        try:
            LOCK.unlink()
        except OSError:
            pass


def busy():
    """What is running right now, or None — readable from any process."""
    held = holder()
    if not held:
        return None
    pid, started, what = held
    return {"pid": pid, "what": what, "seconds": round(time.time() - started, 1),
            "mine": pid == os.getpid()}


def run(board: str, timeout=3 * 3600):
    """Run one board's scripts to completion, holding the lock. Returns a result dict.

    Blocking on purpose — the caller decides whether to background it. Every script runs even if
    an earlier one failed, matching the systemd unit's `-` prefixes: each step works off whatever
    is already on disk, so one flaky fetch must not silently skip every analysis below it.
    """
    scripts = SCRIPTS.get(board)
    if not scripts:
        raise KeyError(board)
    if acquire(board) is None:
        return {"board": board, "ok": False, "skipped": True, "busy": busy(),
                "message": "another rebuild is already running"}
    started, steps = time.time(), []
    try:
        for script in scripts:
            t0 = time.time()
            try:
                p = subprocess.run([sys.executable, script], cwd=str(HERE), capture_output=True,
                                   text=True, timeout=timeout)
                tail = (p.stdout or p.stderr or "").strip().splitlines()[-12:]
                steps.append({"script": script, "code": p.returncode,
                              "seconds": round(time.time() - t0, 1), "tail": tail})
            except subprocess.TimeoutExpired:
                steps.append({"script": script, "code": -1,
                              "seconds": round(time.time() - t0, 1),
                              "tail": ["timed out after %ds" % timeout]})
    finally:
        release()
    out = {"board": board, "ok": all(s["code"] == 0 for s in steps), "skipped": False,
           "seconds": round(time.time() - started, 1), "steps": steps}
    _state["last"] = out
    return out


def start(board: str):
    """Run in the background and return immediately. `status()` reports progress."""
    if board not in SCRIPTS:
        raise KeyError(board)
    with _state["lock"]:
        if _state["running"]:
            return {"started": False, "reason": "this process is already running %s"
                    % _state["running"], "busy": busy()}
        if busy():
            return {"started": False, "reason": "another process holds the rebuild lock",
                    "busy": busy()}
        _state.update(running=board, started=time.time())

    def worker():
        try:
            run(board)
        finally:
            with _state["lock"]:
                _state["running"] = None

    threading.Thread(target=worker, daemon=True).start()
    return {"started": True, "board": board}


def status():
    """Everything a screen needs: what is running anywhere, and how the last run went."""
    return {"running": _state["running"],
            "seconds": round(time.time() - _state["started"], 1) if _state["running"] else None,
            "busy": busy(), "last": _state["last"], "boards": sorted(SCRIPTS),
            # per board: does it refresh overnight, and if not, why the screen must say so
            "auto": {b: auto(b) for b in SCRIPTS},
            "manual": MANUAL}


def demo():
    """Self-check: the lock is exclusive, survives across "processes", and cannot be stolen."""
    global LOCK, MASTER
    import tempfile

    real_lock, real_master = LOCK, MASTER
    try:
        with tempfile.TemporaryDirectory() as d:
            MASTER = Path(d)
            LOCK = MASTER / "pipeline.lock"

            assert busy() is None, "nothing should be running yet"
            assert acquire("scan") is not None, "first acquire must win"
            assert acquire("scan") is None, "the SECOND acquire must lose -- this is the whole point"
            b = busy()
            assert b and b["what"] == "scan" and b["mine"] is True, b

            release()
            assert busy() is None, "release must clear it"
            assert acquire("other") is not None, "and the lock is then free"
            release()

            # a lock held by a process that no longer exists must not block the box forever
            LOCK.write_text("999999\t%f\tghost\n" % time.time(), encoding="utf-8")
            assert acquire("scan") is not None, "a dead owner's lock must be broken"
            release()

            # nor may an ancient one, even if that pid happens to be alive again
            LOCK.write_text("%d\t%f\tancient\n" % (os.getpid(), time.time() - STALE_AFTER - 1),
                            encoding="utf-8")
            assert acquire("scan") is not None, "a lock older than STALE_AFTER must be broken"
            release()

            # a live lock owned by SOMEONE ELSE is never removed by release()
            LOCK.write_text("%d\t%f\ttheirs\n" % (os.getpid() + 1 if _pid_alive(os.getpid() + 1)
                                                  else os.getpid(), time.time()), encoding="utf-8")
            before = LOCK.read_text(encoding="utf-8")
            release()
            if int(before.split("\t")[0]) != os.getpid():
                assert LOCK.exists() and LOCK.read_text(encoding="utf-8") == before, \
                    "release() must never drop a lock this process does not own"
            LOCK.unlink(missing_ok=True)

            # garbage never raises into a caller
            LOCK.write_text("not a lock at all", encoding="utf-8")
            assert holder() is None
            assert acquire("scan") is not None, "an unreadable lock is a broken lock"
            release()

            # every board is classified, and the two answers cannot both be true
            for board in SCRIPTS:
                if board == "pipeline":
                    continue
                assert auto(board) != (board in MANUAL), board
            assert auto("scan") is True, "scan.py is in the nightly chain"
            assert auto("backtest") is True, "backtest.py was added to the nightly chain"
            assert auto("master_signal") is False, "master_signal.py is not in it"
            assert auto("nonsense") is False, "an unknown board rebuilds itself never"

            # the allow-list is the only way to name a script
            for bad in ("../../etc/passwd", "naasa.py", "", "rm -rf"):
                try:
                    run(bad)
                except KeyError:
                    pass
                else:
                    raise AssertionError("run(%r) should have been refused" % bad)
    finally:
        LOCK, MASTER = real_lock, real_master
    print("jobs demo ok")


if __name__ == "__main__":
    demo()
