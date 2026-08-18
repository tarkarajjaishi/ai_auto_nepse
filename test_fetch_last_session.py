"""Self-check for the single-session upserts. No network, no archive needed.

    python test_fetch_last_session.py
"""

import shutil
import tempfile
from pathlib import Path

import fetch_last_session as f
from build_broker_flow import HEADER as FLOW_HEADER
from fetch_ohlc import HEADER


def daily():
    tmp = Path(tempfile.mkdtemp())
    path = tmp / "1D.txt"
    path.write_text(HEADER + "\n"
                    "2026-08-14\t100\t101\t99\t100\t\t\t10\t1000\n"
                    "2026-08-17\t100\t102\t100\t102\t2.0\t2.0\t20\t2000\n",
                    encoding="utf-8")

    # replacing a date keeps the row count and swaps the values
    f.upsert_daily(path, "2026-08-17", ["101", "105", "101", "104", "30", "3000"])
    rows = path.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 3, rows
    assert rows[-1].split("\t")[:5] == ["2026-08-17", "101", "105", "101", "104"], rows[-1]
    # change is re-derived against 14 Aug's close, not against the row it replaced
    assert rows[-1].split("\t")[5] == "4.0", rows[-1]

    # a new date is appended, and re-running changes nothing
    f.upsert_daily(path, "2026-08-18", ["104", "110", "104", "108", "40", "4000"])
    grown = path.read_text(encoding="utf-8").splitlines()
    assert len(grown) == 4 and grown[-1].split("\t")[5] == "4.0", grown[-1]
    f.upsert_daily(path, "2026-08-18", ["104", "110", "104", "108", "40", "4000"])
    assert path.read_text(encoding="utf-8").splitlines() == grown, "not idempotent"

    # ordering and header survive
    assert grown[0] == HEADER
    dates = [r.split("\t")[0] for r in grown[1:]]
    assert dates == sorted(dates), dates

    # a missing file is created from scratch
    fresh = tmp / "new" / "1D.txt"
    f.upsert_daily(fresh, "2026-08-18", ["1", "2", "1", "2", "3", "4"])
    assert fresh.read_text(encoding="utf-8").splitlines()[0] == HEADER
    shutil.rmtree(tmp)


def flow():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "floorsheet" / "AAA").mkdir(parents=True)
    (tmp / "floorsheet" / "AAA" / "2026-08-17.txt").write_text(
        "buyer\tseller\tquantity\trate\tamount\ttransaction\n"
        "1\t2\t100\t10\t1000\tT1\n"
        "1\t3\t50\t10\t500\tT2\n", encoding="utf-8")
    (tmp / "broker_flow").mkdir()
    (tmp / "broker_flow" / "AAA.txt").write_text(
        FLOW_HEADER + "\n"
        "2026-08-14\t9\t1\t0\t1\t1.0\t0.0\t1\n"
        "2026-08-17\t99\t999\t0\t999\t9.0\t0.0\t9\n", encoding="utf-8")
    f.FLOORSHEET, f.FLOW = tmp / "floorsheet", tmp / "broker_flow"

    n = f.upsert_flow("AAA", "2026-08-17")
    rows = (tmp / "broker_flow" / "AAA.txt").read_text(encoding="utf-8").splitlines()
    assert n == 3, n                                   # brokers 1, 2, 3
    assert "\t99\t" not in "\n".join(rows), "stale rows for that date must go"
    assert rows[1].startswith("2026-08-14\t9\t"), "other dates must survive"
    body = [r.split("\t") for r in rows[1:]]
    assert [r[0] for r in body] == sorted(r[0] for r in body), "date ordering"
    buyer = next(r for r in body if r[0] == "2026-08-17" and r[1] == "1")
    assert buyer[2:5] == ["150", "0", "150"], buyer     # bought both lots, sold none

    # a session with no floorsheet file leaves the file alone
    assert f.upsert_flow("AAA", "2026-08-18") == 0
    assert (tmp / "broker_flow" / "AAA.txt").read_text(encoding="utf-8").splitlines() == rows
    shutil.rmtree(tmp)


if __name__ == "__main__":
    daily()
    flow()
    print("ok")
