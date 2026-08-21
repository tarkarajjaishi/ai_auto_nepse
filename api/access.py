"""Access requests: the landing page's "Request For Access" form.

The one place in this project where a STRANGER writes to the archive. Everything else here
either reads a .txt or re-runs a script the project already runs on a timer, so the rules are
different and worth stating:

  * The file is a module constant. Nothing a caller sends ever reaches a path, a filename or a
    command — the only thing a request can do is add one line to `access_requests.txt`.
  * Every field is validated and CAPPED before it is written. A form on a public page is a
    stranger's text box pointed at our disk.
  * The row is escaped, not merely joined. The archive's format is tab-separated with a header
    (see backtest.txt, corporate_actions.txt), and a name containing a tab or a newline would
    otherwise write a row that no longer parses as one row — or as three. The escape is
    percent-encoding of exactly four characters, so ordinary names stay readable in the file.
  * Appends only. There is no rewrite path, so the failure that `fetch_swp._rewrite` guards
    against — a bad run replacing a good archive — cannot happen here.

Storage is .txt because that is the project rule, and it fits: this is a list of leads, written
once and read in order.
"""
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from fetch_ohlc import MASTER

# NEPAL time, like every other stamp in this archive. A lead is followed up by a person in
# Kathmandu, so a UTC timestamp would need mental arithmetic every time it is read.
NPT = timezone(timedelta(hours=5, minutes=45))

# A CONSTANT, never built from a request. See the module docstring.
PATH = MASTER / "access_requests.txt"

FIELDS = ("received_at", "full_name", "country_code", "phone",
          "whatsapp_code", "whatsapp", "place", "email", "source_ip")

# Caps, because the writer is the public. 80 is generous for a name or a city; 254 is the
# maximum length of an email address in RFC 5321.
MAX = {"full_name": 80, "place": 80, "email": 254, "phone": 20, "whatsapp": 20,
       "country_code": 6, "whatsapp_code": 6}

# Practical, not RFC 5322. The full grammar accepts addresses no mail server will take and is a
# well-known way to write an unreadable regex; this asks for the shape every real address has —
# a local part, one @, and a dotted domain — and lets the confirmation email be the real proof.
_EMAIL = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$")
_DIAL = re.compile(r"^\+\d{1,4}$")
_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)

# E.164 allows at most 15 digits including the country code; 6 is shorter than any real national
# number. Separators are stripped before counting, so "980-123 4567" is accepted as typed.
_SEP = re.compile(r"[\s\-().]")
_DIGITS = re.compile(r"^\d{6,15}$")

_lock = threading.Lock()

# Rate limit, per source address. Deliberately in-process: nginx does the shaping (a public POST
# is a public POST) and this is the backstop for the case where the API is reached directly. A
# dict of lists is enough for a form that should see a handful of submissions a day, and it is
# pruned on every call so it cannot grow without bound.
#
# 20/hour, not 5. Nepal's mobile networks are heavily CGNAT'd — a great many real visitors share
# one public address — so a tight per-IP cap does not stop a script, it silently drops leads
# from the exact audience this page is for. The cap that matters against automation is the 8 KB
# body limit and the field validation; this one only stops a loop.
WINDOW = 3600
BURST = 20
_seen: dict[str, list[float]] = {}


def _clean(value, cap):
    """One field as the archive will store it: no control characters, collapsed spaces, capped.

    Control characters are removed rather than escaped. They cannot be typed into a form by a
    person and are only ever present when something is being attempted.
    """
    if not isinstance(value, str):
        return ""
    text = "".join(ch for ch in value if ch.isprintable() or ch == " ")
    return re.sub(r"\s+", " ", text).strip()[:cap]


def validate(payload):
    """(clean, error). `error` is a message meant for the person filling the form.

    Server side because the browser's copy of these rules is a convenience, not a control: the
    POST can be sent without ever loading the page.
    """
    if not isinstance(payload, dict):
        return None, "malformed request"

    name = _clean(payload.get("full_name"), MAX["full_name"])
    if len(name) < 2 or not _LETTER.search(name):
        return None, "Please enter your full name."

    place = _clean(payload.get("place"), MAX["place"])
    if len(place) < 2 or not _LETTER.search(place):
        return None, "Please enter where you currently live."

    email = _clean(payload.get("email"), MAX["email"]).lower()
    if not _EMAIL.match(email):
        return None, "Please enter a valid email address."

    out = {"full_name": name, "place": place, "email": email}

    for code_key, num_key, label in (("country_code", "phone", "phone number"),
                                     ("whatsapp_code", "whatsapp", "WhatsApp number")):
        code = _clean(payload.get(code_key), MAX[code_key])
        if not _DIAL.match(code):
            return None, "Please choose a country code for your %s." % label
        digits = _SEP.sub("", _clean(payload.get(num_key), MAX[num_key]))
        if not _DIGITS.match(digits):
            return None, "Please enter a valid %s." % label
        out[code_key], out[num_key] = code, digits

    return out, None


def allowed(ip):
    """False once one address has sent BURST requests inside WINDOW seconds."""
    now = time.time()
    with _lock:
        for key in [k for k, v in _seen.items() if not v or v[-1] < now - WINDOW]:
            del _seen[key]
        hits = [t for t in _seen.get(ip, []) if t > now - WINDOW]
        if len(hits) >= BURST:
            _seen[ip] = hits
            return False
        hits.append(now)
        _seen[ip] = hits
        return True


def _esc(value):
    """Percent-encode ONLY what would break a tab-separated line.

    Four characters, so a name or a city is written literally and the file stays readable. `%`
    goes first or the escape is ambiguous — a literal "50%" would otherwise round-trip as a tab.
    """
    return (str(value).replace("%", "%25").replace("\t", "%09")
            .replace("\r", "%0D").replace("\n", "%0A"))


def _unesc(value):
    return (value.replace("%09", "\t").replace("%0D", "\r")
            .replace("%0A", "\n").replace("%25", "%"))


def record(clean, ip=""):
    """Append one validated request. Returns the row as stored."""
    row = dict(clean)
    row["received_at"] = datetime.now(NPT).strftime("%Y-%m-%d %H:%M:%S")
    row["source_ip"] = _clean(ip, 45)

    line = "\t".join(_esc(row.get(f, "")) for f in FIELDS)
    with _lock:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        fresh = not PATH.exists() or PATH.stat().st_size == 0
        # Append, never rewrite: a partial write can only ever cost the newest row, and the
        # 'a' mode plus one write() call keeps a single line atomic on both platforms.
        with open(PATH, "a", encoding="utf-8", newline="\n") as fh:
            if fresh:
                fh.write("\t".join(FIELDS) + "\n")
            fh.write(line + "\n")
    return row


def read_all():
    """Every request, NEWEST FIRST — which is the order anyone reading a lead list wants.

    Rows whose column count does not match the header are skipped rather than guessed at: a
    short row means the file was edited by hand, and inventing values for the missing columns
    would put a made-up phone number in front of somebody.
    """
    if not PATH.exists():
        return []
    lines = PATH.read_text(encoding="utf-8").splitlines()
    if not lines:
        return []
    head = lines[0].split("\t")
    out = []
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != len(head):
            continue
        out.append({k: _unesc(v) for k, v in zip(head, parts)})
    out.reverse()
    return out


def demo():
    """python -m api.access — round-trips a row that would break a naive tab file."""
    import tempfile
    from pathlib import Path as _P

    global PATH
    keep = PATH
    try:
        with tempfile.TemporaryDirectory() as tmp:
            PATH = _P(tmp) / "access_requests.txt"

            good = {"full_name": "Tarka Raj\tJaishi", "place": "Kathmandu\nNepal",
                    "country_code": "+977", "phone": "980-123 4567",
                    "whatsapp_code": "+977", "whatsapp": "9801234567",
                    "email": "  Someone@Example.COM "}
            clean, err = validate(good)
            assert err is None, err
            assert clean["phone"] == "9801234567", clean["phone"]
            assert clean["email"] == "someone@example.com", clean["email"]
            # the tab and the newline survive validation as ordinary spaces
            assert "\t" not in clean["full_name"] and "\n" not in clean["place"]

            record(clean, ip="203.0.113.9")
            record(clean, ip="203.0.113.9")
            rows = read_all()
            assert len(rows) == 2, rows
            assert rows[0]["email"] == "someone@example.com"
            assert rows[0]["source_ip"] == "203.0.113.9"
            assert PATH.read_text(encoding="utf-8").splitlines()[0].startswith("received_at\t")

            # a value that DOES carry a tab must not become two columns
            PATH.unlink()
            record({"full_name": "a\tb", "place": "c", "country_code": "+1", "phone": "1234567",
                    "whatsapp_code": "+1", "whatsapp": "1234567", "email": "a@b.co"})
            assert read_all()[0]["full_name"] == "a\tb", "escape did not round-trip"
            assert len(PATH.read_text(encoding="utf-8").splitlines()) == 2, "row split in two"

            for bad, why in (
                ({**good, "email": "not-an-email"}, "email"),
                ({**good, "email": "a@b"}, "dotless domain"),
                ({**good, "phone": "12"}, "too short"),
                ({**good, "phone": "1234567890123456"}, "too long"),
                ({**good, "country_code": "977"}, "no plus"),
                ({**good, "full_name": " "}, "blank name"),
                ({**good, "place": ""}, "blank place"),
                ({**good, "full_name": "12345"}, "digits only"),
            ):
                assert validate(bad)[1], "accepted a bad %s" % why

            _seen.clear()
            assert all(allowed("198.51.100.1") for _ in range(BURST))
            assert not allowed("198.51.100.1"), "rate limit never trips"
            assert allowed("198.51.100.2"), "rate limit is not per-address"
    finally:
        PATH = keep
    print("access demo ok")


if __name__ == "__main__":
    demo()
