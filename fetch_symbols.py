"""Fetch every NEPSE symbol and index from chukul.com, save to symbols.txt / indices.txt."""

import json
import urllib.request
from pathlib import Path

BASE = "https://chukul.com/api/data/v2"
MASTER = Path(__file__).parent / "Master_data"


def get(path):
    req = urllib.request.Request(f"{BASE}/{path}", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def symbols():
    # live-market carries debentures/promoter shares that market-summary drops, so union both
    return {row["symbol"] for row in get("live-market/")} | {
        row["symbol"] for row in get("market-summary/?type=stock")
    }


def indices():
    return {row["symbol"] for row in get("market-summary/?type=index")}


def save(names, filename):
    assert names, f"{filename}: empty response — market API down or shape changed"
    assert all(n and n.strip() == n for n in names), f"{filename}: bad symbol in {names}"
    MASTER.mkdir(exist_ok=True)
    path = MASTER / filename
    path.write_text("\n".join(sorted(names)) + "\n", encoding="utf-8")
    print(f"{len(names):>4} -> {path}")


if __name__ == "__main__":
    save(symbols(), "symbols.txt")
    save(indices(), "indices.txt")
