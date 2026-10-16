"""Does the console's own "Sign in" door (not sign-up) let a Google identity in?

probe4 showed the sign-IN policy does not hard-block, unlike sign-up. If AuthKit
provisions the user on a sign-in OAuth (common with JIT provisioning), there is a
free door and the phone challenge becomes the only open question.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from camoufox.sync_api import Camoufox

from pipeline import river
from pipeline.config import Config
from pipeline.accounts import load_accounts


def main() -> int:
    email = sys.argv[1] if len(sys.argv) > 1 else "amingeri68@rurasa.my.id"
    cfg = Config(headless=True)

    acct = None
    for fname in ("accounts.txt", "result.txt"):
        p = Path(__file__).resolve().parent / fname
        if p.exists():
            for a in load_accounts(p):
                if a.email.lower() == email.lower():
                    acct = a
                    break
        if acct:
            break
    if acct is None:
        print(f"[probe5] no credentials for {email}", flush=True)
        return 5

    print(f"[probe5] account={email}", flush=True)
    with Camoufox(headless=cfg.headless, humanize=True, os=cfg.os_profile,
                  locale=[cfg.locale], geoip=cfg.geoip) as browser:
        page = browser.new_page()
        page.set_default_timeout(cfg.ui_timeout_ms)
        page.set_default_navigation_timeout(cfg.nav_timeout_ms)

        catcher = river.TokenCatcher()
        catcher.attach(page)

        page.goto(cfg.river_console, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        clicked = page.evaluate(
            """() => {
                const a = [...document.querySelectorAll('a,button')].find(
                    el => /already have an account|sign in|log in|masuk/i.test(
                        (el.innerText || '').trim()));
                if (a) { a.click(); return a.innerText.trim(); }
                return null;
            }"""
        )
        print(f"[probe5] clicked: {clicked!r}", flush=True)
        page.wait_for_timeout(5000)
        print(f"[probe5] url: {page.url[:120]}", flush=True)

        # Then take the Google door on the sign-in surface.
        gclick = page.evaluate(
            """() => {
                const b = [...document.querySelectorAll('a,button')].find(
                    el => /google/i.test((el.innerText || '').trim())
                       || /provider=GoogleOAuth/i.test(el.getAttribute('href') || ''));
                if (b) { b.click(); return b.innerText.trim() || b.getAttribute('href'); }
                return null;
            }"""
        )
        print(f"[probe5] google door: {str(gclick)[:90]!r}", flush=True)

        for _ in range(30):
            page.wait_for_timeout(700)
            if river.is_google(page):
                break
        print(f"[probe5] at google: {river.is_google(page)} :: {page.url[:100]}", flush=True)

        if river.is_google(page):
            river.full_google_login(page, acct.email, acct.password)

        verdict = "unknown"
        for i in range(45):
            page.wait_for_timeout(1000)
            url = page.url
            txt = river._visible_text(page).lower()
            if "policy_denied" in url or "access blocked" in txt:
                verdict = f"BLOCKED (policy_denied) at {i}s"
                break
            if river.phone_gate(page):
                verdict = f"PHONE CHALLENGE at {i}s"
                break
            if river.is_on_console(page):
                verdict = f"CONSOLE REACHED at {i}s <-- SIGN-IN DOOR WORKS"
                break
            if any(k in txt for k in ("verify your email", "enter the code",
                                      "check your email")):
                verdict = f"EMAIL CODE STEP at {i}s"
                break
            if i == 44:
                verdict = f"STALLED at sign-in surface ({page.url[:60]})"

        print(f"\n[probe5] VERDICT: {verdict}", flush=True)
        print(f"[probe5] url: {page.url[:150]}", flush=True)
        print(f"[probe5] text: {river._visible_text(page)[:300]!r}", flush=True)
        print(f"[probe5] token: {bool(catcher.token)}", flush=True)
        page.screenshot(path="probe5.png")

        if verdict.startswith("CONSOLE"):
            for _ in range(60):
                if catcher.token:
                    break
                page.wait_for_timeout(500)
            key = river.create_key(page, catcher, f"river-signin-{int(time.time())}", cfg)
            print(f"[probe5] KEY: {key}", flush=True)
            if key:
                Path("probe5_key.txt").write_text(f"{acct.email}|{key}\n", encoding="utf-8")

    Path("probe5_result.txt").write_text(f"{email}\t{verdict}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
