"""Scrape merolagani's floorsheet into Master_data/floorsheet/<SYMBOL>/<DATE>.txt.

Uses merolagani.com/Floorsheet.aspx — the site-wide, date-filtered floorsheet, which
serves 500 rows per page for every symbol at once (~114 pages on a busy session). The
per-company page shows the same rows 100 at a time, so scraping it symbol by symbol would
mean roughly five times the requests for identical data.

It is an ASP.NET WebForms page, so every request replays __VIEWSTATE / __EVENTVALIDATION
from the previous response, and paging posts a hidden page number plus the pager button.

    python fetch_floorsheet_merolagani.py              # last 365 days
    python fetch_floorsheet_merolagani.py 2025-01-01   # explicit start
    python fetch_floorsheet_merolagani.py 2026-08-17 --force   # re-scrape a done date

Completed dates are recorded in Master_data/floorsheet/_merolagani.txt, so an interrupted
run resumes and a daily re-run only fetches the new session.
"""

import http.cookiejar
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from fetch_ohlc import MASTER, NPT, folder

URL = "https://merolagani.com/Floorsheet.aspx"
OUT = MASTER / "floorsheet"
DONE = OUT / "_merolagani.txt"
HEADER = "buyer\tseller\tquantity\trate\tamount\ttransaction"  # the date is the filename

SEARCH = "ctl00$ContentPlaceHolder1$lbtnSearchFloorsheet"
PAGER = "ctl00$ContentPlaceHolder1$PagerControl1"
DATE_FIELD = "ctl00$ContentPlaceHolder1$txtFloorsheetDateFilter"

HIDDEN = re.compile(r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', re.I)
HIDDEN_ALT = re.compile(r'<input[^>]*name="([^"]+)"[^>]*type="hidden"[^>]*value="([^"]*)"', re.I)
ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
TAG = re.compile(r"<[^>]+>")
TOTAL = re.compile(r"Total pages:\s*(\d+)", re.I)


class Merolagani:
    """One cookie session; ASP.NET will not page without it."""

    def __init__(self):
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        self.opener.addheaders = [
            ("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36"),
            ("Accept", "text/html,application/xhtml+xml"),
            ("Referer", URL),
        ]

    def open(self, data=None, tries=6):
        # a heavy session is 400+ sequential posts; one dropped connection would otherwise
        # throw away the whole day's work, so back off and keep trying
        body = urllib.parse.urlencode(data).encode() if data else None
        for attempt in range(tries):
            try:
                with self.opener.open(URL, body, timeout=120) as r:
                    return r.read().decode("utf-8", "replace")
            except (OSError, urllib.error.HTTPError):
                if attempt == tries - 1:
                    raise
                time.sleep(3 * (attempt + 1))

    @staticmethod
    def state(html):
        """__VIEWSTATE and friends must be echoed back on the next post."""
        found = dict(HIDDEN.findall(html))
        found.update(dict(HIDDEN_ALT.findall(html)))
        return {k: v for k, v in found.items() if k.startswith("__")}

    def search(self, html, date):
        form = self.state(html)
        form.update({
            "__EVENTTARGET": SEARCH,
            "__EVENTARGUMENT": "",
            DATE_FIELD: date.strftime("%m/%d/%Y"),
            f"{PAGER}$hdnPCID": "PC1",
            f"{PAGER}$hdnCurrentPage": "0",
        })
        return self.open(form)

    def page(self, html, date, n):
        form = self.state(html)
        form.update({
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            DATE_FIELD: date.strftime("%m/%d/%Y"),
            f"{PAGER}$hdnPCID": "PC1",
            f"{PAGER}$hdnCurrentPage": str(n),
            f"{PAGER}$btnPaging": "",
        })
        return self.open(form)


def parse(html):
    """[(symbol, buyer, seller, qty, rate, amount, transaction)] from one results page."""
    out = []
    for chunk in ROW.findall(html):
        cells = [TAG.sub("", c).replace("&nbsp;", " ").replace(",", "").strip() for c in CELL.findall(chunk)]
        # columns: # | transaction | symbol | buyer | seller | quantity | rate | amount
        if len(cells) < 8 or not cells[1].isdigit():
            continue
        _, txn, symbol, buyer, seller, qty, rate, amount = cells[:8]
        out.append((symbol, buyer, seller, qty, rate, amount, txn))
    return out


def scrape_day(session, date):
    html = session.search(session.open(), date)
    rows = parse(html)
    pages = int(TOTAL.search(html).group(1)) if TOTAL.search(html) else 1
    for n in range(2, pages + 1):
        html = session.page(html, date, n)
        rows += parse(html)
    return rows, pages


def write(date, rows):
    by_symbol = defaultdict(list)
    for symbol, buyer, seller, qty, rate, amount, txn in rows:
        by_symbol[symbol].append(f"{buyer}\t{seller}\t{qty}\t{rate}\t{amount}\t{txn}")
    for symbol, lines in by_symbol.items():
        path = folder("floorsheet", symbol) / f"{date:%Y-%m-%d}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(HEADER + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return len(by_symbol)


def trading_days(start):
    rows = (MASTER / "indices" / "NEPSE" / "1D.txt").read_text(encoding="utf-8").splitlines()[1:]
    return [line.split("\t", 1)[0] for line in rows if line[:10] >= start]


def one_day(day, lock):
    """A whole session start to finish. Each worker needs its own cookie/viewstate chain."""
    date = datetime.strptime(day, "%Y-%m-%d")
    try:
        rows, pages = scrape_day(Merolagani(), date)
    except (OSError, AttributeError, ValueError) as e:
        return f"{day} FAILED: {type(e).__name__} {e}"
    if not rows:
        return f"{day} no rows returned"
    symbols = write(date, rows)
    with lock, DONE.open("a", encoding="utf-8") as f:
        f.write(day + "\n")
    return f"{day} {len(rows):>7,} trades · {symbols:>3} symbols · {pages} pages"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    force = "--force" in sys.argv          # re-scrape dates already marked done
    start = args[0] if args else (datetime.now(NPT) - timedelta(days=365)).strftime("%Y-%m-%d")
    OUT.mkdir(parents=True, exist_ok=True)
    done = set() if force else (set(DONE.read_text(encoding="utf-8").split()) if DONE.exists() else set())
    days = [d for d in trading_days(start) if d not in done]
    print(f"{len(days)} sessions to scrape from merolagani ({len(done)} already done)", flush=True)

    lock = threading.Lock()
    # 4 workers: ~110 page-posts per session, and merolagani is a small site — do not hammer it
    with ThreadPoolExecutor(4) as pool:
        for i, line in enumerate(pool.map(lambda d: one_day(d, lock), days), 1):
            print(f"[{i}/{len(days)}] {line}", flush=True)


if __name__ == "__main__":
    main()
