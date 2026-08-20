"""Deploy: commit + push to GitHub AND ship to the VPS, in one step.

Keeping the two in sync by hand always drifts — the box ends up ahead of the repo. This does
both from one command, so what runs on the VPS is always what is on GitHub.

    python deploy.py                     # commit everything, push, ship, restart
    python deploy.py -m "fix heatmap"    # with a message
    python deploy.py --no-git            # ship to the VPS only (skip commit/push)
    python deploy.py --no-vps            # commit/push only (skip the VPS)

Ships the tracked source only — Master_data/ (the archive and the saved logins) never leaves
the box, and the VPS keeps its own copy.
"""
import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
VPS = "ubuntu@202.51.70.101"
APP = "~/chukul_data"
SERVICE = "chukul"


def run(cmd, **kw):
    """Run a command, echoing it; returns the CompletedProcess (never raises)."""
    print(f"  $ {cmd if isinstance(cmd, str) else ' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=HERE, shell=isinstance(cmd, str), text=True, **kw)


def git_sync(message):
    """Stage everything, commit if there is anything to commit, then push to origin."""
    changed = run(["git", "status", "--porcelain"], capture_output=True).stdout.strip()
    if changed:
        print(f"  {len(changed.splitlines())} file(s) changed")
        run(["git", "add", "-A"])
        if run(["git", "commit", "-m", message]).returncode:
            return False
    else:
        print("  nothing to commit — repo already clean")
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                 capture_output=True).stdout.strip() or "main"
    return run(["git", "push", "origin", branch]).returncode == 0


def vps_ship():
    """Tar the tracked source over SSH and restart the service."""
    files = run(["git", "ls-files"], capture_output=True).stdout.split()
    files = [f for f in files if not f.startswith("Master_data/")]
    if not files:
        print("  no tracked files to ship")
        return False
    tar = subprocess.Popen(["tar", "czf", "-"] + files, cwd=HERE, stdout=subprocess.PIPE)
    ship = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=30", VPS,
         f"cd {APP} && tar xzf - && sudo systemctl restart {SERVICE} && sleep 3 && "
         f"systemctl is-active {SERVICE}"],
        stdin=tar.stdout, text=True, capture_output=True)
    tar.stdout.close(), tar.wait()
    state = ship.stdout.strip()
    print(f"  {len(files)} file(s) shipped · service {state or ship.stderr.strip()}")
    # Exact match. `systemd is-active` answers with one of active / inactive / failed /
    # activating / deactivating, and the substring test this replaces read "inactive" as a
    # healthy deploy — a stopped unit reported success. This is the only end-to-end health
    # check the deploy has.
    return state == "active"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-m", "--message", help="commit message")
    ap.add_argument("--no-git", action="store_true", help="skip commit/push")
    ap.add_argument("--no-vps", action="store_true", help="skip the VPS")
    a = ap.parse_args()
    msg = a.message or f"Update {datetime.now():%Y-%m-%d %H:%M}"

    ok = True
    if not a.no_git:
        print("GitHub:")
        ok &= git_sync(msg)
    if not a.no_vps:
        # Never ship code that did not reach GitHub. This used to run unconditionally, so a
        # rejected push — non-fast-forward, expired token, no network — still restarted the VPS
        # on source nobody else has, which is precisely the drift the docstring above says this
        # script exists to prevent.
        if not ok:
            print("VPS:  SKIPPED — the push failed, so shipping now would leave the box ahead "
                  "of the repo. Fix the push and re-run, or use --no-git to ship deliberately.")
        else:
            print("VPS:")
            ok &= vps_ship()
    print("deploy ok" if ok else "deploy FINISHED WITH ERRORS")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
