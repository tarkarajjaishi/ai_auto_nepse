"""The two pipeline safety rules that have no other coverage, checked without touching the
network, the VPS, or Master_data.

Both guard against a green run that quietly did damage:

  fetch_swp._rewrite  — corporate_actions.txt and lockin.txt are upserts by FULL REWRITE, and
                        every per-company fetch swallows its own exception. A lapsed SWP cookie
                        therefore produced a short list that overwrote a good archive with a
                        header-only file, while the Cron node still went green.
  deploy.vps_ship     — its only health check was `"active" in stdout` against systemd's
                        is-active, and "active" is a substring of "inactive", so a stopped
                        service reported a successful deploy.

    python test_ops.py
"""
import sys
import tempfile
from pathlib import Path

import fetch_swp

H = "a\tb"


def test_rewrite():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "probe.txt"

        p.write_text(H + "\n" + "\n".join(f"r{i}\t{i}" for i in range(100)) + "\n",
                     encoding="utf-8")
        assert fetch_swp._rewrite(p, H, [f"n{i}\t{i}" for i in range(98)], "probe") is True
        assert len(p.read_text(encoding="utf-8").splitlines()) == 99, "a real refresh must land"

        assert fetch_swp._rewrite(p, H, ["only\t1"], "probe") is False
        assert len(p.read_text(encoding="utf-8").splitlines()) == 99, "archive must survive"

        assert fetch_swp._rewrite(p, H, [], "probe") is False
        assert len(p.read_text(encoding="utf-8").splitlines()) == 99, "header-only refused"

        p.unlink()
        assert fetch_swp._rewrite(p, H, ["first\t1"], "probe") is True, "first run must seed"
    print("  fetch_swp._rewrite  refuses a shrunken overwrite, allows a real one, seeds a new file")


def test_deploy_health():
    src = (Path(__file__).parent / "deploy.py").read_text(encoding="utf-8")
    dead = ("inactive", "activating", "deactivating", "failed", "unknown")
    assert [w for w in dead if "active" in w] == ["inactive"], \
        "the substring test used to read 'inactive' as healthy — keep that documented"
    assert '"active" in ship.stdout' not in src, "the substring health check is back"
    assert 'state == "active"' in src, "is-active must be matched exactly"
    assert "SKIPPED — the push failed" in src, \
        "a failed push must block the VPS ship, or the box runs code GitHub does not have"
    print("  deploy              only exact 'active' is healthy; a failed push blocks the ship")


def main():
    print("ops safety:")
    test_rewrite()
    test_deploy_health()
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
