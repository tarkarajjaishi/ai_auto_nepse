"""Whether the two saved broker logins still work. READ ONLY — status, never credentials.

No password, cookie or session token is returned by anything in this module. The files live on
the box (`Master_data/{naasa,swp}_login.txt` and the matching `_session.txt`) and stay there; what
crosses the wire is three booleans, a timestamp and a sentence.

**There is deliberately no sign-in path here.** Signing in means accepting a broker password over
a surface whose only gate is one shared Basic Auth password, and `api/__main__.py` has no write
verb at all. That is the same reasoning `api/account.py` applies to placing orders, and it is a
decision for a human rather than a side effect of adding a status page.

Probing is opt-in (`?probe=1`). A NAASA probe is a full OAuth login — several HTTP calls, up to
30s each — so doing it on every page load would make the Account page feel broken. Without a
probe this reports what is on disk, which is fast and still useful: "no login saved" and "the
session file is three weeks old" are both answers.
"""
import time

from fetch_ohlc import MASTER

import naasa
import swp

_CACHE = {}          # name -> (checked_at, payload). A probe is expensive; 60s is plenty.
_TTL = 60


def _files(login_name, session_name):
    """(login saved?, session saved?, session age in seconds or None) — never the contents."""
    login = MASTER / login_name
    sess = MASTER / session_name
    age = None
    if sess.exists():
        age = int(time.time() - sess.stat().st_mtime)
    return login.exists(), sess.exists(), age


def _probe_swp():
    """Does SWP work — following the same path the real code does, not a stricter one.

    fetch_swp._session() reuses the cached cookie and, when it has lapsed, **re-logs in from the
    saved credentials**. Testing only the cached cookie therefore reports "not working" for a
    system that heals itself on the next run, which is a false alarm dressed as a diagnosis. So
    this mirrors that path: stale cookie plus saved credentials is a working integration, and it
    says which of the two answered.
    """
    cookie = swp.load_session()
    if cookie:
        try:
            info = swp.session_info(cookie)
            return True, "Session valid — %s companies visible." % info.get("companies", "?")
        except Exception:
            pass                       # lapsed cookie is normal; the real code re-logins here too
    email, password = swp.load_credentials()
    if not (email and password):
        return False, ("The cached session has lapsed and no credentials are saved, so nothing "
                       "can renew it. Sign in on the Streamlit app under SmartWealthPro with "
                       "Remember me.")
    cookie = swp.login_password(email, password)
    swp.save_session(cookie)
    info = swp.session_info(cookie)
    return True, ("The cached session had lapsed; re-logged in from the saved credentials — %s "
                  "companies visible. The daily jobs do exactly this, so it needed no action."
                  % info.get("companies", "?"))


def _probe_naasa():
    """A real account read. This is the only honest test: NAASA's session can be present and
    rejected, which is indistinguishable from a good one until you use it."""
    from . import account
    rows = account.holdings()
    return True, "Login works — %d holding(s) visible." % len(rows.get("rows", []))


def _status(name, login_name, session_name, prober, probe):
    have_login, have_session, age = _files(login_name, session_name)
    out = {"configured": have_login, "session_saved": have_session, "session_age_s": age,
           "probed": False, "ok": None, "detail": ""}
    if not have_login:
        out["detail"] = ("No login saved on the server. It is stored on the box, not in your "
                         "browser, which is why signing in here would not help.")
        return out
    if not probe:
        out["detail"] = "Login saved. Not tested — press Test to check it against the broker."
        return out

    hit = _CACHE.get(name)
    if hit and time.time() - hit[0] < _TTL:
        return {**out, **hit[1]}
    try:
        ok, detail = prober()
    except Exception as e:                 # any failure IS the answer here, whatever its type
        ok, detail = False, "%s: %s" % (type(e).__name__, str(e)[:220])
    result = {"probed": True, "ok": ok, "detail": detail}
    _CACHE[name] = (time.time(), result)
    return {**out, **result}


def status(probe=False):
    """Both integrations at once — the Account page shows them side by side."""
    return {
        "naasa": _status("naasa", naasa.CREDS_FILE.name, "naasa_session.txt",
                         _probe_naasa, probe),
        "swp": _status("swp", swp.CREDS_FILE.name, swp.SESSION_FILE.name, _probe_swp, probe),
        "sign_in_here": False,      # the frontend states plainly that it cannot sign you in
    }
