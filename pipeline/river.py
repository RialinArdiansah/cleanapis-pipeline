"""River AI (console.river.ai) sign-up + API-key harvest.

River's Console has no signup HTTP API at all (`/api/auth/signup` ->
`404 Password signup is disabled`); authentication is delegated to WorkOS
AuthKit on `login.console.river.ai`, behind Cloudflare Turnstile and WorkOS
Radar. A real browser driving the hosted UI is the only route.

Two sign-up doors exist and they behave very differently:

* **password form** (`/sign-up`) — AuthKit asks first/last name + email, then a
  password, then it e-mails a 6-digit code. Radar scores the attempt and, when
  the risk is high enough, demands a **phone number** and sends an SMS
  challenge. The phone field only exists on this door.
* **Google OAuth** (`/api/login?provider=GoogleOAuth`) — the identity arrives
  pre-verified from the provider, so there is no password to set, no e-mail
  code, and no phone field on the page. Prefer this door: it is the only one
  with nothing to supply that we do not already have.

Both doors hand back an authorization code to `console.river.ai/auth/callback`.
After that the console calls its own REST API with `Authorization: Bearer
<workos access token>`. The token is short-lived, is never written to
`localStorage`/`sessionStorage`, and is attached only at request time, so we
read it off the wire and then talk to the console API directly:

    POST https://console.river.ai/api/keys              -> mint a key
    GET  https://console.river.ai/api/keys              -> list keys
    GET  https://console.river.ai/api/me                -> identity
    GET  https://console.river.ai/api/billing/account   -> balance

Inference is OpenAI-compatible at https://api.river.ai/v1.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from playwright.sync_api import Page

from .config import Config
from .google_oauth import full_google_login, is_google

KEY_PATTERN = re.compile(r"rv_[A-Za-z0-9_\-]{16,}")

# Screens that mean the phone door slammed shut. Matched against page text.
PHONE_TEXT = (
    "phone number",
    "verify your phone",
    "enter the code sent to your phone",
    "we need to verify your phone",
)


def _log(msg: str) -> None:
    print(f"[river] {msg}", flush=True)


def _visible_text(page: Page) -> str:
    try:
        return page.inner_text("body")[:2000]
    except Exception:
        return ""


def open_signup(page: Page, cfg: Config) -> None:
    """Land on the console and click the real Google door.

    The link's href carries `authorization_session_id` and `signals_id` minted
    for this page load. Those are the values Radar correlates the attempt with,
    so the link is clicked as-rendered instead of being rebuilt by hand.
    """
    _log(f"opening {cfg.river_console}")
    try:
        page.goto(cfg.river_console, wait_until="domcontentloaded",
                  timeout=cfg.nav_timeout_ms)
    except Exception as exc:
        _log(f"initial goto: {type(exc).__name__}")
    page.wait_for_timeout(4000)

    deadline = time.time() + 45
    while time.time() < deadline:
        clicked = page.evaluate(
            """() => {
                const a = [...document.querySelectorAll('a,button')].find(el => {
                    const h = el.getAttribute('href') || '';
                    if (/provider=GoogleOAuth/i.test(h)) return true;
                    return /continue with google|sign up with google|google/i.test(
                        (el.innerText || '').trim()) && el.tagName === 'A';
                });
                if (a) { a.click(); return a.getAttribute('href') || a.innerText.trim(); }
                return null;
            }"""
        )
        if clicked:
            _log(f"clicked Google door: {str(clicked)[:120]}")
            break
        page.wait_for_timeout(700)
    else:
        raise RuntimeError("no Google sign-up link found on the console page")

    # The click can either navigate inline or converge on Google after a hop.
    for _ in range(40):
        page.wait_for_timeout(700)
        url = page.url
        # Strict match: `redirect_uri` contains "callback" on every AuthKit URL,
        # so a substring test here fires immediately and skips the Google drive.
        if url.startswith(f"{cfg.river_console}/auth/callback"):
            _log("already past Google via callback")
            return
        if is_google(page):
            _log("reached Google")
            return
    _log(f"still at {page.url[:110]} after clicking Google door")


def is_on_console(page: Page) -> bool:
    """True once the console app itself has loaded (not AuthKit, not Google)."""
    url = page.url
    if "console.river.ai" not in url or "login.console.river.ai" in url:
        return False
    if "accounts.google.com" in url:
        return False
    # The app shell renders after the code exchange.
    txt = _visible_text(page).lower()
    return any(k in txt for k in ("api key", "api keys", "billing", "sign out", "usage", "logout"))


def phone_gate(page: Page) -> bool:
    """True when AuthKit is demanding a phone number (Radar SMS challenge)."""
    txt = _visible_text(page).lower()
    if "console.river.ai" in page.url and "login.console" not in page.url:
        return False
    return any(k in txt for k in PHONE_TEXT)


def wait_for_console(page: Page, cfg: Config, timeout_s: int = 90) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if phone_gate(page):
            _log("!! phone verification demanded by Radar")
            return False
        if is_on_console(page):
            _log(f"console loaded: {page.url[:90]}")
            return True
        page.wait_for_timeout(1000)
    _log(f"console did not load; url={page.url[:110]} text={_visible_text(page)[:200]!r}")
    return False


class TokenCatcher:
    """Reads the console's bearer token off live requests.

    The token never lands in web storage, so the network is the only place to
    get it. Any authenticated request the console makes reveals it.
    """

    def __init__(self) -> None:
        self.token: str | None = None

    def attach(self, page: Page) -> None:
        def on_request(req):
            if self.token:
                return
            try:
                auth = req.headers.get("authorization") or req.headers.get("Authorization")
            except Exception:
                return
            if auth and auth.startswith("Bearer ") and "console.river.ai" in req.url:
                self.token = auth[7:]
                _log(f"captured console token ({len(self.token)} chars)")

        page.on("request", on_request)


SUBMIT_JS = """() => {
    const b = [...document.querySelectorAll('button')].find(
        el => /^(continue|sign ?up|create account|verify|submit|lanjutkan)$/i.test(
            (el.innerText || '').trim()));
    if (b) { b.click(); return b.innerText.trim(); }
    const any = [...document.querySelectorAll('button[type=submit],button')].find(
        el => !el.disabled && (el.innerText || '').trim());
    if (any) { any.click(); return any.innerText.trim(); }
    return null;
}"""

CLICK_EMAIL_JS = """() => {
    const b = [...document.querySelectorAll('button,a')].find(
        el => /continue with email|sign up with email|use email|with email/i.test(
            (el.innerText || '').trim()));
    if (b) { b.click(); return b.innerText.trim(); }
    return null;
}"""


def open_email_signup(page: Page, cfg: Config) -> None:
    """Enter AuthKit's own signup form.

    This door is reachable even though the console reports
    `password_signup: false`: that flag gates the console's own HTTP endpoint,
    not AuthKit's hosted form, which still renders `Continue with email`.
    """
    url = (f"{cfg.river_auth_host}/sign-up?client_id={cfg.river_client_id}"
           f"&redirect_uri={cfg.river_console}/auth/callback")
    _log(f"opening email door: {url[:90]}")
    page.goto(url, wait_until="domcontentloaded", timeout=cfg.nav_timeout_ms)
    page.wait_for_timeout(4000)

    for _ in range(6):
        clicked = page.evaluate(CLICK_EMAIL_JS)
        if clicked:
            _log(f"clicked '{clicked}'")
            page.wait_for_timeout(3000)
            break
        if page.query_selector("input[name=email], input[type=email]"):
            break
        page.wait_for_timeout(1500)


def fill_signup_identity(page: Page, first: str, last: str, email: str) -> dict:
    """Set the name/email fields via the native setter so React sees the change."""
    res = page.evaluate(
        """(args) => {
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            function set(sel, val) {
                const el = document.querySelector(sel);
                if (!el) return false;
                setter.call(el, val);
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                return true;
            }
            return {
                first: set('input[name="first_name"]', args.first),
                last:  set('input[name="last_name"]', args.last),
                email: set('input[name="email"], input[type="email"]', args.email),
            };
        }""",
        {"first": first, "last": last, "email": email},
    )
    page.wait_for_timeout(1000)
    return res or {}


def fill_signup_password(page: Page, password: str) -> bool:
    """Fill AuthKit's password step. Uses fill() — a click can hit a stale node."""
    try:
        field = page.locator("input[type=password]").first
        field.fill(password, timeout=20000)
    except Exception as exc:
        _log(f"password fill failed: {type(exc).__name__}")
        return False
    page.wait_for_timeout(800)
    got = page.evaluate(SUBMIT_JS)
    _log(f"password step submitted via '{got}'")
    return True


def enter_email_code(page: Page, code: str) -> bool:
    """Type the 6-digit code. AuthKit uses six boxes, or one field."""
    res = page.evaluate(
        """(code) => {
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            const boxes = [...document.querySelectorAll('input')].filter(
                i => i.type !== 'hidden' && i.offsetParent !== null);
            if (boxes.length >= code.length) {
                for (let i = 0; i < code.length; i++) {
                    setter.call(boxes[i], code[i]);
                    boxes[i].dispatchEvent(new Event('input', {bubbles:true}));
                    boxes[i].dispatchEvent(new Event('change', {bubbles:true}));
                }
                return {mode: 'boxes', n: boxes.length};
            }
            const one = document.querySelector(
                'input[name=code],input[type=text],input[inputmode=numeric]');
            if (one) {
                setter.call(one, code);
                one.dispatchEvent(new Event('input', {bubbles:true}));
                return {mode: 'single'};
            }
            return {mode: 'none', n: boxes.length};
        }""",
        code,
    )
    _log(f"email code entry: {res}")
    page.wait_for_timeout(1000)
    got = page.evaluate(SUBMIT_JS)
    if got:
        _log(f"code submitted via '{got}'")
    return True


def solve_phone_gate(page: Page, cfg: Config, sms=None) -> bool:
    """Clear WorkOS Radar's phone challenge, or report that it cannot be cleared.

    Radar has three outcomes and they need different handling:

    * **allow** — no phone screen; nothing to do here.
    * **challenge** — a phone number field. A number is supplied, the SMS code is
      read back through `sms`, and the screen is submitted.
    * **block** — `policy_denied`, which no number clears. Detect it and say so
      rather than filling a form that cannot succeed.

    Returns True only when the screen is gone.
    """
    if not phone_gate(page):
        return True

    if "policy_denied" in page.url or "access blocked" in _visible_text(page).lower():
        _log("Radar BLOCKED the attempt (policy_denied) — no number clears this")
        return False

    if sms is None:
        _log("phone challenge shown but no number provider configured "
             "(river_phone_provider)")
        return False

    num = sms.get_number()
    _log(f"phone challenge: using {num.number} via {num.provider}")
    try:
        # Country selector first: the form validates the local part against it.
        local = num.local()
        _log(f"entering local part {local}")
        filled = page.evaluate(
            """(args) => {
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                function set(sel, val) {
                    const el = document.querySelector(sel);
                    if (!el) return false;
                    setter.call(el, val);
                    el.dispatchEvent(new Event('input', {bubbles:true}));
                    el.dispatchEvent(new Event('change', {bubbles:true}));
                    return true;
                }
                const ok = set('input[type=tel], input[name*=phone i], '
                             + 'input[inputmode=tel], input[autocomplete=tel]', args.local);
                return {ok};
            }""",
            {"local": local},
        )
        _log(f"phone field: {filled}")
        page.wait_for_timeout(800)
        got = page.evaluate(SUBMIT_JS)
        _log(f"phone submitted via '{got}'")
        page.wait_for_timeout(4000)

        if "policy_denied" in page.url or "access blocked" in _visible_text(page).lower():
            _log("Radar blocked after the number was submitted")
            return False

        code = sms.wait_for_code(num, timeout_s=getattr(cfg, "river_sms_timeout_s", 240))
        if not code:
            _log("no SMS code arrived")
            return False
        _log("SMS code received")
        enter_email_code(page, code)   # same six-box / single-field handling
        page.wait_for_timeout(4000)
    finally:
        try:
            sms.release(num)
        except Exception:
            pass

    return not phone_gate(page)


def sign_up_email(page: Page, email: str, password: str, cfg: Config,
                  mail=None, sms=None, first: str = "Rina", last: str = "Puspita") -> bool:
    """E-mail door: identity -> password -> mailed code -> console.

    Handles both Radar *challenge* screens: the mailed code, and — when Radar
    escalates — a phone number with an SMS code. A Radar *block* is terminal for
    this door, so it is reported rather than retried.
    """
    open_email_signup(page, cfg)
    if is_on_console(page):
        return True

    filled = fill_signup_identity(page, first, last, email)
    _log(f"identity filled: {filled}")
    if not filled or not all(filled.values()):
        _log("identity fields missing — form shape changed; aborting this door")
        return False
    got = page.evaluate(SUBMIT_JS)
    _log(f"identity submitted via '{got}'")
    page.wait_for_timeout(4500)

    if "policy_denied" in page.url:
        _log("Radar blocked right after the e-mail step")
        return False
    if not solve_phone_gate(page, cfg, sms=sms):
        return False

    if not fill_signup_password(page, password):
        return False
    page.wait_for_timeout(5000)

    if "policy_denied" in page.url or "access blocked" in _visible_text(page).lower():
        _log("Radar blocked at the password step — this source IP is not accepted")
        return False
    if not solve_phone_gate(page, cfg, sms=sms):
        return False

    _log(f"post-password screen: {_visible_text(page)[:160]!r}")
    if mail is not None and not _on_email_code_screen(page):
        _log("not on an e-mail code screen; skipping the mailbox wait")
    elif mail is not None:
        code = mail.wait_for_code(timeout_s=cfg.river_code_timeout_s)
        if not code:
            _log("no e-mail code arrived")
            return False
        _log("code received, entering")
        enter_email_code(page, code)
        page.wait_for_timeout(5000)
        solve_phone_gate(page, cfg, sms=sms)

    return wait_for_console(page, cfg, timeout_s=cfg.river_console_timeout_s)


def _on_email_code_screen(page: Page) -> bool:
    txt = _visible_text(page).lower()
    return any(k in txt for k in ("verify your email", "enter the code",
                                  "check your email", "we sent"))


def sign_in(page: Page, email: str, password: str, cfg: Config, mail=None, sms=None) -> bool:
    """Sign up / sign in through the configured door, then wait for the console.

    `river_door=email` uses AuthKit's own form; `google` uses the console's
    Google door. Whichever is tried first, a Radar block on it does not end the
    attempt — the other door is tried before giving up, because Radar's block can
    be door-specific.
    """
    if cfg.river_door == "email":
        if sign_up_email(page, email, password, cfg, mail=mail, sms=sms):
            return True
        _log("e-mail door did not get through; trying the Google door")
        return sign_in_google(page, email, password, cfg, mail=mail, sms=sms)

    if sign_in_google(page, email, password, cfg, mail=mail, sms=sms):
        return True
    _log("Google door did not get through; trying the e-mail door")
    return sign_up_email(page, email, password, cfg, mail=mail, sms=sms)


def sign_in_google(page: Page, email: str, password: str, cfg: Config,
                   mail=None, sms=None) -> bool:
    """The console's own Google door."""
    open_signup(page, cfg)
    if is_on_console(page):
        return True

    if is_google(page):
        if not full_google_login(page, email, password, max_rounds=cfg.max_consent_rounds):
            _log("google side did not complete")
            return False
    elif not is_google(page):
        _log(f"not on Google and not on console: {page.url[:110]}")

    if wait_for_console(page, cfg):
        return True
    if phone_gate(page):
        return solve_phone_gate(page, cfg, sms=sms) and wait_for_console(page, cfg)
    return False


def _api(page: Page, catcher: TokenCatcher, cfg: Config, method: str, path: str,
         body: dict | None = None, timeout_s: int = 45) -> dict | None:
    """Call the console REST API. `page` supplies cookies, catcher the bearer."""
    deadline = time.time() + timeout_s
    while catcher.token is None and time.time() < deadline:
        page.wait_for_timeout(500)
    if catcher.token is None:
        _log("no bearer token available for API call")
        return None

    url = f"{cfg.river_console}{path}"
    res = page.context.request.fetch(
        url,
        method=method,
        headers={
            "Authorization": f"Bearer {catcher.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        data=json.dumps(body) if body else None,
    )
    try:
        return {"status": res.status, "body": res.json()}
    except Exception:
        return {"status": res.status, "body": res.text()[:400]}


def create_key(page: Page, catcher: TokenCatcher, name: str, cfg: Config) -> str | None:
    """Mint a key through the console API. Falls back to scraping the DOM."""
    for attempt in (1, 2):
        res = _api(page, catcher, cfg, "POST", "/api/keys",
                   {"name": name, "expires_in_days": cfg.river_key_expiry_days})
        if res and res["status"] in (200, 201):
            body = res["body"]
            if isinstance(body, dict) and body.get("key"):
                return body["key"]
            _log(f"unexpected key payload: {str(body)[:200]}")
        elif res:
            _log(f"create key attempt {attempt}: HTTP {res['status']} {str(res['body'])[:160]}")
        if attempt == 1:
            page.wait_for_timeout(2000)
    return mint_key_via_ui(page, name, cfg)


def mint_key_via_ui(page: Page, name: str, cfg: Config) -> str | None:
    """Create the key by driving the console's own UI, then read it back.

    Used when the REST call cannot be made; the key value is shown exactly once,
    so it is scraped immediately after creation.
    """
    _log("falling back to UI key creation")
    try:
        page.goto(f"{cfg.river_console}/keys", wait_until="domcontentloaded")
    except Exception:
        pass
    page.wait_for_timeout(4000)

    for label in ("Create API key", "Create key", "New key", "Create"):
        btn = page.query_selector(f"button:has-text('{label}')")
        if btn:
            btn.click()
            page.wait_for_timeout(1500)
            break

    field = page.query_selector("input[name=name], input[placeholder*=name i]")
    if field:
        field.fill(name)
        page.wait_for_timeout(500)

    for label in ("Create", "Create key", "Confirm", "Generate"):
        btn = page.query_selector(f"button:has-text('{label}')")
        if btn:
            btn.click()
            break
    page.wait_for_timeout(4000)

    found = KEY_PATTERN.search(page.content())
    if found:
        return found.group(0)
    _log(f"no key in DOM; text={_visible_text(page)[:200]!r}")
    return None


def read_balance(page: Page, catcher: TokenCatcher, cfg: Config) -> str | None:
    res = _api(page, catcher, cfg, "GET", "/api/billing/account")
    if res and res["status"] == 200 and isinstance(res["body"], dict):
        for k in ("balance_usd", "balance", "credit_usd"):
            if k in res["body"]:
                return str(res["body"][k])
    return None
