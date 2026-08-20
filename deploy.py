"""Deploy: commit + push to GitHub AND ship to the VPS, in one step.

Keeping the two in sync by hand always drifts — the box ends up ahead of the repo. This does
both from one command, so what runs on the VPS is always what is on GitHub.

    python deploy.py                     # commit everything, push, ship all three, restart
    python deploy.py -m "fix heatmap"    # with a message
    python deploy.py --no-git            # ship to the VPS only (skip commit/push)
    python deploy.py --no-vps            # commit/push only (skip the VPS)
    python deploy.py --no-web            # skip the ~30s frontend build

Three things run on the box and this ships all of them:

    chukul        streamlit ui.py            127.0.0.1:8501   ← nginx /
    chukul-api    python -m api              127.0.0.1:8600   ← nginx /api
    chukul-web    node server.js (Next)      127.0.0.1:3101   ← nginx /admin, /_next

Ships the tracked source only — Master_data/ (the archive and the saved logins) never leaves
the box, and the VPS keeps its own copy.

The frontend is BUILT HERE and shipped as a runnable tree. The VPS has ~350 MB of RAM free
and already swaps; `pnpm install && pnpm build` there would OOM something that is currently
serving. It needs nothing but the node at /usr/local/bin/node.
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
API_SERVICE = "chukul-api"
WEB_SERVICE = "chukul-web"
WEB_SRC = HERE / "web"
WEB_DEST = "~/chukul-web"


def run(cmd, **kw):
    """Run a command, echoing it; returns the CompletedProcess (never raises)."""
    print(f"  $ {cmd if isinstance(cmd, str) else ' '.join(cmd)}", flush=True)
    kw.setdefault("cwd", HERE)
    return subprocess.run(cmd, shell=isinstance(cmd, str), text=True, **kw)


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
         f"cd {APP} && tar xzf - && sudo systemctl restart {SERVICE} {API_SERVICE} && sleep 3 && "
         f"systemctl is-active {SERVICE} {API_SERVICE}"],
        stdin=tar.stdout, text=True, capture_output=True)
    tar.stdout.close(), tar.wait()
    # Both units answer on their own line; both must be up. The API serves the same modules the
    # Streamlit app does, so shipping ui.py without restarting the API leaves the terminal
    # running yesterday's Python against today's txt.
    states = ship.stdout.split()
    print(f"  {len(files)} file(s) shipped · {SERVICE}={states[0] if states else '?'} "
          f"{API_SERVICE}={states[1] if len(states) > 1 else '?'}"
          f"{'' if states else '  ' + ship.stderr.strip()}")
    # Exact match. `systemd is-active` answers with one of active / inactive / failed /
    # activating / deactivating, and the substring test this replaces read "inactive" as a
    # healthy deploy — a stopped unit reported success. This is the only end-to-end health
    # check the deploy has.
    return len(states) == 2 and all(s == "active" for s in states)


def web_ship():
    """Build the Next.js frontend here, ship the standalone tree, restart it there."""
    if not (WEB_SRC / "package.json").exists():
        print("  no web/ — skipping")
        return True

    # pnpm is a .cmd shim on Windows, so this needs a shell.
    if run("pnpm build", cwd=WEB_SRC).returncode:
        print("  build FAILED — not shipping")
        return False

    standalone = WEB_SRC / ".next" / "standalone"
    if not (standalone / "server.js").exists():
        print("  no .next/standalone/server.js — is output:'standalone' still set?")
        return False

    # `next build` leaves these two out of the standalone tree on purpose, because it cannot know
    # whether a CDN will serve them. Nothing else does the copy, and without it every page loads
    # as unstyled HTML that never hydrates — a 200 that looks like a broken site.
    run(f'cp -r "{WEB_SRC / ".next" / "static"}" "{standalone / ".next" / "static"}"')
    if (WEB_SRC / "public").exists():
        run(f'cp -r "{WEB_SRC / "public"}" "{standalone / "public"}"')

    # Ship into a staging dir and swap, so a half-transferred bundle never becomes the live one.
    tar = subprocess.Popen(["tar", "czf", "-", "."], cwd=standalone, stdout=subprocess.PIPE)
    ship = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=30", VPS,
         f"rm -rf {WEB_DEST}.new && mkdir -p {WEB_DEST}.new && cd {WEB_DEST}.new && tar xzf - && "
         f"test -f server.js && test -d .next/static && "
         # A symlinked node_modules survives the tar and dies on the box with a bare
         # "Cannot find module 'next'". pnpm's nodeLinker:hoisted is what prevents it; if that
         # setting ever gets lost, fail HERE rather than after the swap.
         f"[ $(find . -type l | wc -l) -eq 0 ] && "
         f"cd ~ && rm -rf {WEB_DEST}.old && "
         f"{{ [ -d {WEB_DEST} ] && mv {WEB_DEST} {WEB_DEST}.old || true; }} && "
         f"mv {WEB_DEST}.new {WEB_DEST} && "
         f"sudo systemctl restart {WEB_SERVICE} && sleep 5 && systemctl is-active {WEB_SERVICE}"],
        stdin=tar.stdout, text=True, capture_output=True)
    tar.stdout.close(), tar.wait()
    state = ship.stdout.strip().splitlines()[-1] if ship.stdout.strip() else ""
    print(f"  frontend shipped · {WEB_SERVICE}={state or 'FAILED'}")
    if state != "active":
        print(f"  {ship.stderr.strip()[:400]}")
        print(f"  the previous bundle is still at {WEB_DEST}.old")
    return state == "active"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-m", "--message", help="commit message")
    ap.add_argument("--no-git", action="store_true", help="skip commit/push")
    ap.add_argument("--no-vps", action="store_true", help="skip the VPS")
    ap.add_argument("--no-web", action="store_true", help="skip the frontend build + ship")
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
            if not a.no_web:
                print("Frontend:")
                ok &= web_ship()
    print("deploy ok" if ok else "deploy FINISHED WITH ERRORS")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
