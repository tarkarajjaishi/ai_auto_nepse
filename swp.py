"""SmartWealthPro (app.smartwealthpro.com) — authenticated data API.

Classic ASP.NET MVC login: `POST /Account/Login` with an anti-forgery token (no captcha). The auth
cookie is HttpOnly, which only blocks *browser JS* — a Python cookie jar keeps and reuses it fine,
so the app logs in with the user's own email + password exactly like [[naasa.py]]. The password is
never stored beyond the optional remembered credentials the user opts into.

API: `https://app.smartwealthpro.com/api/*` → clean `{responseCode, result}` JSON while the session
is valid; once it lapses the endpoints return the page-shell HTML instead (we detect that and ask
the user to sign in again).
"""
import http.cookiejar
import json
import re
import urllib.parse
import urllib.request

from fetch_ohlc import MASTER

BASE = "https://app.smartwealthpro.com"
API = BASE + "/api"
SESSION_FILE = MASTER / "swp_session.txt"
CREDS_FILE = MASTER / "swp_login.txt"


def _opener():
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [("User-Agent", "Mozilla/5.0")]
    return op, jar


def login_password(email, password):
    """Log in with email + password and return the session cookie string. Raises on bad login."""
    op, jar = _opener()
    page = op.open(BASE + "/account/login", timeout=25).read().decode("utf-8", "replace")
    m = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', page)
    token = m.group(1) if m else ""
    data = urllib.parse.urlencode({
        "__RequestVerificationToken": token, "UserName": email, "Password": password,
        "RememberMe": "true", "ReturnUrl": "/"}).encode()
    body = op.open(urllib.request.Request(
        BASE + "/Account/Login", data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}), timeout=25
    ).read().decode("utf-8", "replace")
    cookies = {c.name: c.value for c in jar}
    # success = an auth cookie was set (anything other than the anti-forgery one). A wrong password
    # re-renders the login form and sets no auth cookie.
    auth = [n for n in cookies if "antiforgery" not in n.lower()]
    if not auth or ('id="loginForm"' in body and 'name="Password"' in body):
        raise RuntimeError("SWP login failed — check email/password")
    return "; ".join("%s=%s" % (n, v) for n, v in cookies.items())


def _authed(cookie, path, timeout=25):
    """GET an /api endpoint with the session; JSON dict/list, or raise if the session lapsed."""
    op, _ = _opener()
    req = urllib.request.Request(API + path, headers={
        "Cookie": cookie, "Accept": "application/json", "X-Requested-With": "XMLHttpRequest"})
    body = op.open(req, timeout=timeout).read().decode("utf-8", "replace")
    if body.lstrip()[:1] in ("<", ""):        # page shell / empty → session gone
        raise RuntimeError("SWP session expired — sign in again")
    return json.loads(body)


def _rows(j):
    """Pull the row list out of `{result:{data:[…]}}`, `{result:[…]}`, or a bare list/obj."""
    r = j.get("result", j) if isinstance(j, dict) else j
    if isinstance(r, dict):
        return r.get("data") or []
    return r if isinstance(r, list) else []


def companies(cookie):
    """symbol ↔ company map (used as a lightweight signed-in check too)."""
    return _rows(_authed(cookie, "/GetAutoCompleteCompanies"))


def dividends(cookie, items=50):
    """Corporate-action calendar (bonus/cash, book-closure dates), newest first."""
    return _rows(_authed(cookie, "/GetDividends?companyId=0&fiscalYearId=0&sectorId=0&pageNo=1"
                                 "&itemsPerPage=%d" % items))


def dividends_for(cookie, company_id, items=6):
    """That one company's dividend history (companyId=0 collapsed to 1 row server-side, so the
    book-close calendar must be rebuilt per company)."""
    return _rows(_authed(cookie, "/GetDividends?companyId=%s&fiscalYearId=0&sectorId=0&pageNo=1"
                                 "&itemsPerPage=%d" % (company_id, items)))


def auctions(cookie):
    """Auction calendar."""
    return _rows(_authed(cookie, "/GetAuctions?companyId=0"))


def ownership(cookie, company_id):
    """GetOwnershipStructure — a TOP-LEVEL object (no `result` wrapper): promoterShareCount,
    promoterPercent, publicShareCount (live free float in shares), publicPercent."""
    return _authed(cookie, "/GetOwnershipStructure?companyId=%s" % company_id)


def lockin(cookie, items=100):
    """IPO promoter lock-in (currently locked), with unlock date + remaining days."""
    return _rows(_authed(cookie, "/NewsAndCorporateAction/GetIpoLockedInPeriod?sectorId=0"
                                 "&companyId=0&status=locked&pageNo=1&itemsPerPage=%d" % items))


def session_info(cookie):
    """{'companies': N} if the session works — a cheap validity probe. Raises if lapsed."""
    return {"companies": len(companies(cookie))}


# ------------------------------------------------------------------ session / creds persistence
def save_session(cookie):
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text((cookie or "").strip() + "\n", encoding="utf-8")


def load_session():
    return SESSION_FILE.read_text(encoding="utf-8").strip() if SESSION_FILE.exists() else ""


def clear_session():
    SESSION_FILE.unlink(missing_ok=True)


def save_credentials(email, password):
    """Remember the login for silent auto sign-in (plain text, gitignored, local only)."""
    CREDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CREDS_FILE.write_text("%s\n%s\n" % (email, password), encoding="utf-8")


def load_credentials():
    if not CREDS_FILE.exists():
        return None, None
    lines = CREDS_FILE.read_text(encoding="utf-8").splitlines()
    return (lines[0].strip(), lines[1].strip()) if len(lines) >= 2 else (None, None)


def clear_credentials():
    CREDS_FILE.unlink(missing_ok=True)
