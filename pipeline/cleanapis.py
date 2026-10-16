"""CleanAPIs side of the pipeline: sign in with Google, then mint an API key.

Do NOT use the /register form, at any price. It carries a Cloudflare Turnstile
widget whose token the backend rejects unconditionally -- a genuine token minted
by real Chrome driven over CDP is rejected exactly like a garbage string, with
the same message ("Security verification failed. Please complete the CAPTCHA.")
and no account is ever created. Every browser configuration was tried; the
failure is server-side. OAuth is the only working door.

The key itself is displayed exactly once, inside a <code>/<pre> element on the
keys page. The listing table shows redacted rows (cc_xxxxxxxx----), so scraping
the table yields nothing usable.
"""
from __future__ import annotations

import re
import time

from playwright.sync_api import Page

from . import google_oauth
from .config import Config


def _log(msg: str) -> None:
    print(f"[cleanapis] {msg}", flush=True)


# New keys are rendered into the "your new API key" banner. Scan the broadest
# sensible set of nodes because the exact element has changed between deploys.
KEY_JS = """() => {
  for (const el of document.querySelectorAll(
      'code, pre, input, textarea, span, div, p, strong, b')) {
    const t = (el.value || el.textContent || '').trim();
    if (/^cc_[A-Za-z0-9]{20,}$/.test(t)) return t;
  }
  return null;
}"""


def open_signup(page: Page, cfg: Config) -> None:
    """Land on the referral page and enter the OAuth door.

    The Google link is located by its href, not its label: `/auth/google/redirect`
    is stable, while the visible text varies by page state and language
    ("Sign up with Google", "Continue with Google", ...).
    """
    _log(f"opening {cfg.login_url()}")
    page.goto(cfg.login_url(), wait_until="domcontentloaded")
    page.wait_for_timeout(2500)

    def google_link_clickable(wait_ms: int) -> bool:
        """Poll for the OAuth anchor; the SPA renders it slightly late."""
        for _ in range(max(1, wait_ms // 1500)):
            try:
                loc = page.locator('a[href*="/auth/google/redirect"]')
                if loc.count() > 0 and loc.first.is_visible():
                    return True
            except Exception:
                pass
            page.wait_for_timeout(1500)
        return False

    if google_link_clickable(9_000):
        pass                                            # already visible
    else:
        for name in (r"Get Started", r"Get started", r"Sign up|Sign in|Log in"):
            try:
                loc = page.get_by_role("link", name=re.compile(name, re.I))
                if loc.count() > 0:
                    loc.first.click(timeout=15_000)
                    _log(f"clicked {name!r}")
                    page.wait_for_timeout(3000)
                    break
            except Exception:
                continue
        google_link_clickable(9_000)

    try:
        loc = page.locator('a[href*="/auth/google/redirect"]')
        if loc.count() > 0:
            loc.first.click(timeout=15_000)
            _log("clicked the Google OAuth link")
            page.wait_for_timeout(4500)
            return
    except Exception as exc:
        _log(f"Google link click failed: {type(exc).__name__}")

    if google_oauth.is_google(page):
        return

    # Last resort: navigate to the OAuth entry directly. A pending client-side
    # navigation can interrupt the goto, so settle first and tolerate the error --
    # the interruption usually means the browser landed on /register, which also
    # carries the same OAuth link.
    _log("no Google link found; trying the OAuth entry point directly")
    for attempt in range(2):
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15_000)
            page.goto(f"{cfg.base_url}/auth/google/redirect", wait_until="domcontentloaded")
            break
        except Exception as exc:
            _log(f"goto attempt {attempt + 1} interrupted: {type(exc).__name__}")
            page.wait_for_timeout(3000)
    page.wait_for_timeout(4000)


def is_signed_in(page: Page) -> bool:
    """True once the browser is on a CleanAPIs page that is not login/register."""
    url = page.url or ""
    if google_oauth.is_google(page):
        return False
    if "cleanapis.com" not in url:
        return False
    return not any(seg in url for seg in ("/login", "/register"))


def sign_in(page: Page, email: str, password: str, cfg: Config) -> bool:
    """Full Google OAuth sign-in from the referral landing page."""
    open_signup(page, cfg)
    if is_signed_in(page):
        _log("already signed in (session carried over)")
        return True
    if not google_oauth.is_google(page):
        _log(f"expected Google, but ended on {page.url[:90]}")
        return False
    if not google_oauth.full_google_login(page, email, password, cfg.max_consent_rounds):
        _log(f"Google step failed at {page.url[:90]}")
        return False

    # OAuth has handed control back to the site; give the session cookie a moment
    # to settle before judging, and say exactly where we landed if it is not the
    # dashboard -- "sign-in failed" without the URL is unactionable.
    for _ in range(6):
        if is_signed_in(page):
            _log(f"signed in: {page.url[:90]}")
            return True
        page.wait_for_timeout(2000)
    try:
        body = re.sub(r"\s+", " ", page.inner_text("body"))[:220]
    except Exception:
        body = ""
    _log(f"landed on {page.url[:110]}")
    if body:
        _log(f"page says: {body}")
    return False


def create_key(page: Page, name: str, cfg: Config) -> str | None:
    """Create a key named `name` and return the cc_... value, or None."""
    captured: dict[str, str] = {}

    def on_response(resp):
        if resp.request.method in ("POST", "PUT", "PATCH") and "cleanapis.com" in resp.url:
            try:
                body = resp.text()
            except Exception:
                return
            m = re.search(cfg.key_pattern, body)
            if m:
                captured.setdefault("key", m.group(0))

    page.on("response", on_response)
    page.goto(cfg.base_url + cfg.keys_path, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)

    clicked = False
    for name_re in (r"Create API Key", r"Create Key", r"New Key", r"Generate Key"):
        try:
            btn = page.get_by_role("button", name=re.compile(name_re, re.I))
            if btn.count() > 0:
                btn.first.click(timeout=20_000)
                _log(f"clicked {name_re!r}")
                clicked = True
                break
        except Exception:
            continue
    if not clicked:
        _log("no create-key button found")
        return None
    page.wait_for_timeout(2500)

    for sel in ("input#name", "input[name=name]", "input[placeholder*='name' i]",
                "form input[type=text]"):
        el = page.query_selector(sel)
        if el and el.is_visible():
            el.fill(name)
            _log(f"key name = {name}")
            break
    page.wait_for_timeout(400)

    for name_re in (r"Create Key", r"Create", r"Generate", r"Save"):
        try:
            btn = page.get_by_role("button", name=re.compile(rf"^{name_re}$", re.I))
            if btn.count() > 0:
                btn.first.click(timeout=20_000)
                break
        except Exception:
            continue
    page.wait_for_timeout(4500)

    for _ in range(20):
        try:
            found = page.evaluate(KEY_JS)
        except Exception:
            found = None
        if found:
            _log(f"key captured from the banner ({len(found)} chars)")
            return found
        if captured.get("key"):
            return captured["key"]
        time.sleep(1)
    return captured.get("key")
