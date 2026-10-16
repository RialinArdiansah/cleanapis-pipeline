"""Driving Google's OAuth sign-in with Camoufox.

This module exists because three things on accounts.google.com break naive
automation. All three were found the hard way:

1. DECOY PASSWORD FIELD. Google's v3 signin form ships an invisible
   `<input type="password" name="hiddenPassword">` on the *identifier* page. A
   detector that looks for `input[type=password]` therefore reports "we are on
   the password step" on the very first page and the flow stalls. Always require
   visibility AND exclude `hiddenPassword`.

2. LOCALISED BUTTON TEXT. Google serves the surrounding UI in the account's
   language, so "Next" arrives as "berikutnya" (id), "siguiente" (es),
   "weiter" (de), "Далее" (ru) ... Matching on text is a losing game. Instead,
   score every visible button by rendered background colour: Google's primary
   action is the only large, saturated, usually-blue control on the page. A
   denylist of cancellation/purchase words removes the false positives that a
   colour score alone would pick up.

3. ACCOUNT CHOOSER. When the profile already holds a Google session, the flow
   starts at `accountchooser` and the sign-in form never appears. Match the row
   by full email, else by local-part keywords, and never click a row that shows
   a different `@` address.
"""
from __future__ import annotations

import random
import time

from playwright.sync_api import Page

GOOGLE_HOST = "accounts.google.com"

EMAIL_SELECTORS = ["#identifierId", "input[type=email]"]
# NOTE: name=Passwd is the real field; filter hiddenPassword explicitly.
PASSWORD_SELECTOR_OK = "input[name=Passwd]"

# Words that mean "do not press this". Multilingual on purpose: the whole point
# is to survive a language switch mid-flow. Includes purchase/plan words because
# a stray priced button ($25, "Upgrade") has been observed on these pages.
CANCEL_WORDS = (
    "cancel", "cancelar", "abbrechen", "annuler", "annulla", "отмена", "отмена",
    "batal", "batalkan", "no thanks", "not now", "maybe later", "later",
    "tidak", "nanti", "nein", "zurück", "retour", "atrás", "kembali",
    "返回", "뒤로", "취소", "取消", "deny", "refuse", "reject", "decline",
    "$", "rp", "usd", "idr", "top up", "topup", "isi ulang", "beli", "buy",
    "purchase", "price", "harga", "upgrade", "langganan", "subscribe",
    "plan", "paket", "credit", "saldo", "bulan", "month", "year", "tahun",
)

# Collect visible buttons with the geometry and colour needed to rank them, plus
# a stable index so the caller can click exactly the one that was scored.
BUTTON_INFO_JS = """() => {
  const nodes = Array.from(document.querySelectorAll(
      'button, [role="button"], a[role="button"]'));
  const out = [];
  for (let i = 0; i < nodes.length; i++) {
    const b = nodes[i];
    const r = b.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const st = getComputedStyle(b);
    const m = (st.backgroundColor || '').match(/[0-9.]+/g);
    let red = 255, green = 255, blue = 255;
    if (m && m.length >= 3) { red = +m[0]; green = +m[1]; blue = +m[2]; }
    const mx = Math.max(red, green, blue), mn = Math.min(red, green, blue);
    out.push({
      i: i,
      text: (b.innerText || b.textContent || '').trim().slice(0, 60),
      coloured: (mx - mn) > 40 && mx > 100,
      blue: blue > red + 20 && blue > green + 20,
      area: Math.round(r.width * r.height),
    });
  }
  return out;
}"""


def is_google(page: Page) -> bool:
    return GOOGLE_HOST in (page.url or "")


def _log(msg: str) -> None:
    print(f"[google] {msg}", flush=True)


def real_password_visible(page: Page) -> bool:
    """True only for the genuine, visible password field.

    `hiddenPassword` is always present-but-invisible on the identifier page; a
    plain `input[type=password]` check matches it and breaks the step detection.
    """
    for sel in (PASSWORD_SELECTOR_OK, "input[type=password]"):
        try:
            el = page.query_selector(sel)
        except Exception:
            continue
        if not el:
            continue
        try:
            if not el.is_visible():
                continue
        except Exception:
            continue
        if el.get_attribute("name") == "hiddenPassword":
            continue
        return True
    return False


def click_primary(page: Page) -> str | None:
    """Click Google's primary button, chosen by COLOUR not text. Returns its label.

    Refuses to act outside accounts.google.com so a redirect that lands on the
    destination site cannot make this press an unrelated control.
    """
    if not is_google(page):
        return None
    try:
        info = page.evaluate(BUTTON_INFO_JS)
    except Exception:
        return None
    if not info:
        return None

    ranked = []
    for b in info:
        text = (b["text"] or "").strip().lower()
        if not text:
            continue
        if any(word in text for word in CANCEL_WORDS):
            continue
        # Skip bare brand/domain text like "cleanapis.com" — it is a label, not a button.
        if "." in text and " " not in text and len(text) < 30:
            continue
        score = 20 if b["blue"] else (8 if b["coloured"] else 0)
        score += min(3, b["area"] // 5000)
        if score <= 0:
            continue
        ranked.append((score, b["i"], text))

    if not ranked:
        return None
    ranked.sort(key=lambda t: (t[0], t[1]))
    _score, idx, text = ranked[-1]
    try:
        page.evaluate(
            """(i) => {
                const nodes = document.querySelectorAll(
                    'button, [role="button"], a[role="button"]');
                nodes[i].click();
            }""",
            idx,
        )
    except Exception:
        return None
    _log(f"clicked primary button: {text[:40]!r}")
    return text


def click_own_account(page: Page, email: str) -> bool:
    """On the account chooser, click the row for `email`."""
    local = email.split("@")[0].replace(".", " ").replace("_", " ")
    keywords = [k for k in local.split() if len(k) >= 4]

    try:
        label = page.evaluate(
            """([email, keywords]) => {
                const nodes = Array.from(document.querySelectorAll(
                    'a, button, [role="link"], [role="button"], div, li'));
                const visible = (el) => {
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                };
                for (const el of nodes) {
                    const t = (el.innerText || el.textContent || '').trim();
                    if (t.toLowerCase().includes(email.toLowerCase()) && visible(el)) {
                        return t.slice(0, 60);
                    }
                }
                if (keywords.length) {
                    for (const el of nodes) {
                        const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                        if (!t || !visible(el)) continue;
                        if (t.includes('@')) continue;   // a different account
                        if (keywords.every(k => t.includes(k))) return t.slice(0, 60);
                    }
                }
                return null;
            }""",
            [email, keywords],
        )
    except Exception:
        return False

    if not label:
        return False
    first_line = label.split("\n")[0]
    try:
        page.get_by_text(first_line, exact=False).first.click(timeout=10_000)
        _log(f"picked account row: {first_line!r}")
        return True
    except Exception:
        return False


def type_identifier(page: Page, email: str) -> bool:
    """Type the email into the identifier field. Never touches a password field."""
    field = None
    for sel in EMAIL_SELECTORS:
        # page.wait_for_selector, NOT ElementHandle.wait_for: an ElementHandle has
        # no .wait_for() (that lives on Locator), and calling it raises
        # AttributeError which a broad except would swallow into a silent failure.
        try:
            page.wait_for_selector(sel, state="visible", timeout=30_000)
        except Exception:
            continue
        try:
            el = page.query_selector(sel)
        except Exception:
            continue
        if el and el.is_visible():
            field = el
            break
    if field is None:
        _log("identifier field never appeared")
        return False

    try:
        field.click()
        page.wait_for_timeout(250)
        for ch in email:                       # real keystrokes: some variants ignore fill()
            page.keyboard.type(ch, delay=random.randint(20, 45))
        page.wait_for_timeout(250)
        got = field.input_value()
        if email.split("@")[0].lower() not in got.lower():
            field.fill(email)
            got = field.input_value()
        _log(f"identifier = {got!r}")
        return email.split("@")[0].lower() in got.lower()
    except Exception as exc:
        _log(f"could not type identifier: {type(exc).__name__}: {exc}")
        return False


def type_password(page: Page, password: str) -> bool:
    """Type into the real password field (name=Passwd).

    Uses Locator.fill() rather than click+keystrokes: the field is re-rendered as
    the page animates in, so a raw ElementHandle goes stale between
    query_selector() and click() and raises a bare `Error`. fill() waits for the
    element itself. Keystrokes remain as a fallback for variants that ignore it.
    """
    loc = page.locator("input[name=Passwd]")
    try:
        loc.wait_for(state="visible", timeout=30_000)
    except Exception as exc:
        _log(f"password field never became visible: {type(exc).__name__}")
        return False

    try:
        loc.fill(password)
        if loc.input_value() == password:
            return True
    except Exception as exc:
        _log(f"fill() on the password field failed: {type(exc).__name__}: {exc}")

    try:
        loc.click()
        page.wait_for_timeout(random.randint(150, 350))
        for ch in password:
            page.keyboard.type(ch, delay=random.randint(25, 60))
        page.wait_for_timeout(random.randint(150, 350))
        return loc.input_value() == password
    except Exception as exc:
        _log(f"keystroke fallback failed: {type(exc).__name__}: {exc}")
        return False


def finish_oauth(page: Page, email: str, password: str, max_rounds: int = 12,
                 identifier_accepted: bool = True) -> bool:
    """Drive Google's side of the flow until the browser leaves accounts.google.com.

    The caller owns the destination check (see cleanapis.is_signed_in); this only
    has to get us out of Google.

    `identifier_accepted=False` means we never got an email typed in, so there is
    nothing to advance past — bail out instead of hammering buttons.
    """
    t0 = time.time()
    for round_no in range(1, max_rounds + 1):
        if not is_google(page):
            _log(f"left Google after {round_no - 1} round(s), {time.time() - t0:.0f}s")
            return True

        url = page.url or ""
        if "accountchooser" in url and click_own_account(page, email):
            page.wait_for_timeout(3000)
            continue

        if real_password_visible(page):
            _log("password step")
            if type_password(page, password):
                click_primary(page)
                page.wait_for_timeout(4000)
                continue
            _log("could not fill the password field — stopping rather than clicking blindly")
            return False

        # Only press a button when the identifier has actually been accepted, or
        # when Google is showing a password/consent screen. Otherwise a page that
        # never advanced gets the primary button pressed round after round while
        # the flow goes nowhere.
        if not identifier_accepted:
            _log("identifier was not accepted — not clicking further")
            return False

        if click_primary(page):
            page.wait_for_timeout(3500)
            continue

        _log(f"round {round_no}: nothing to press, waiting")
        page.wait_for_timeout(2500)

    return not is_google(page)


def full_google_login(page: Page, email: str, password: str, max_rounds: int = 12) -> bool:
    """Identifier -> password -> consent. Assumes the page is already at Google."""
    try:
        page.wait_for_selector("#identifierId, input[type=email]", timeout=60_000)
    except Exception:
        pass

    typed = type_identifier(page, email)
    if typed:
        click_primary(page)
        page.wait_for_timeout(4500)

    body = ""
    try:
        body = page.inner_text("body")
    except Exception:
        pass
    if "Couldn't find" in body or "Couldn’t find" in body:
        _log(f"Google reports no such account: {email}")
        return False

    return finish_oauth(page, email, password, max_rounds=max_rounds,
                        identifier_accepted=typed)
