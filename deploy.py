"""Deploy: commit + push to GitHub AND ship to the VPS, in one step.

Keeping the two in sync by hand always drifts — the box ends up ahead of the repo. This does
both from one command, so what runs on the VPS is always what is on GitHub.

    python deploy.py                     # commit everything, push, ship both, restart
    python deploy.py -m "fix heatmap"    # with a message
    python deploy.py --no-git            # ship to the VPS only (skip commit/push)
    python deploy.py --no-vps            # commit/push only (skip the VPS)
    python deploy.py --no-web            # skip the ~30s frontend build

Three things run on the box and this ships all of them:

    chukul-api    python -m api              127.0.0.1:8600   ← nginx /api
    chukul-web    node server.js (Next)      127.0.0.1:3101   ← nginx /admin, /_next

The landing page (nepse-landing, 3102) is a separate app and is NOT shipped here — see
DEPLOYMENT.md. chukul-feed is deliberately never restarted: it holds the one NAASA socket the
account is allowed, and a restart mid-session drops the live feed.

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
    """Ship the COMMITTED tree over SSH and restart the service.

    `git archive HEAD`, never the working directory. The previous version tarred
    `git ls-files` -- tracked PATHS, read from disk -- so it shipped whatever happened
    to be dirty at the time. With a second session editing the same tree that is not
    hypothetical: the box was found running an uncommitted `swing_quantam/backtest.py`
    that no commit contained, put there by a deploy that printed "deploy ok". Nothing
    failed and nothing looked wrong; production simply was not what `main` said. That is
    the exact drift this module's docstring says it exists to prevent, and it is the
    fourth silent-success bug found in this file.

    Archiving the commit fixes a second class for free: git streams the tree itself, so
    there is no path list to hand to tar and therefore nothing to whitespace-split. The
    `-z`/NUL dance that a space in `quantam_nepse_landing page/` previously required is
    gone because the shell never sees a filename at all.

    The trade is that uncommitted edits are no longer shipped. That is the point, but it
    is a surprise if unannounced, so a dirty tracked file is listed rather than ignored.
    """
    head = run(["git", "rev-parse", "--short", "HEAD"], capture_output=True).stdout.strip()
    subject = run(["git", "log", "-1", "--pretty=%s"], capture_output=True).stdout.strip()
    listing = run(["git", "ls-tree", "-r", "HEAD", "--name-only", "-z"],
                  capture_output=True).stdout
    n = len([f for f in listing.split("\0") if f])
    if not n:
        print("  HEAD holds no files to ship")
        return False

    # Say what is NOT going, or the improvement reads as files silently going missing.
    dirty = [l for l in run(["git", "status", "--porcelain", "--untracked-files=no"],
                            capture_output=True).stdout.splitlines() if l.strip()]
    if dirty:
        print(f"  {len(dirty)} tracked file(s) modified locally and NOT shipped — the box "
              f"gets {head}, not your working tree. Commit them to ship them:")
        for l in dirty[:6]:
            print(f"      {l.strip()}")
        if len(dirty) > 6:
            print(f"      ... and {len(dirty) - 6} more")

    print(f"  shipping {head} - {subject}")
    arch = subprocess.Popen(["git", "archive", "--format=tar.gz", "HEAD"],
                            cwd=HERE, stdout=subprocess.PIPE)
    ship = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=30", VPS,
         f"cd {APP} && tar xzf - && sudo systemctl restart {API_SERVICE} && sleep 3 && "
         f"systemctl is-active {API_SERVICE}"],
        stdin=arch.stdout, text=True, capture_output=True)
    arch.stdout.close()
    arch_rc = arch.wait()

    # The API is the only Python service a source ship affects — it serves the modules the
    # boards are built from, so shipping without restarting it leaves the terminal running
    # yesterday's Python against today's txt. `chukul` (Streamlit) used to be restarted here
    # too and no longer exists; naming a dead unit makes systemctl fail and the whole deploy
    # report failure.
    states = ship.stdout.split()
    if arch_rc:
        # The archive's own failure has to reach the verdict. A shipment that dropped files
        # used to still print "deploy ok" as long as the unit came back active.
        print(f"  git archive exited {arch_rc} — the shipment is INCOMPLETE")
    print(f"  {n} file(s) shipped · {API_SERVICE}={states[0] if states else '?'}"
          f"{'' if states else '  ' + ship.stderr.strip()}")
    # Exact match. `systemd is-active` answers with one of active / inactive / failed /
    # activating / deactivating, and the substring test this replaces read "inactive" as a
    # healthy deploy — a stopped unit reported success. This is the only end-to-end health
    # check the deploy has.
    # `all(...)` over the list, not states[0] — one unit is shipped today, and the check has
    # to keep covering every unit the day a second one is added. test_ops pins this form.
    return arch_rc == 0 and len(states) == 1 and all(s == "active" for s in states)


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
