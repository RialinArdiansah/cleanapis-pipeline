"""Decisive Radar probe: is the block door-specific or environment-wide?

Runs the e-mail door (AuthKit's own signup form with a mail.tm inbox) to
separate three hypotheses:

  H1  Radar blocks this IP/environment outright  -> e-mail door blocked too
  H2  Radar blocks only the Google door          -> e-mail door proceeds
  H3  Radar challenges (phone/SMS) on e-mail     -> the phone-work path matters

Reports every screen it sees, so the outcome is evidence, not inference.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from camoufox.sync_api import Camoufox

from pipeline import river
from pipeline.config import Config
from pipeline.mailtm import MailTM


def snap(page) -> str:
    try:
        return page.inner_text("body")[:400].replace("\n", " | ")
    except Exception as exc:
        return f"<no text: {exc}>"


def main() -> int:
    cfg = Config(headless=False)
    mail = MailTM()
    addr = mail.create_account()
    print(f"[probe2] inbox={addr}", flush=True)

    with Camoufox(headless=cfg.headless, humanize=True, os=cfg.os_profile,
                  locale=[cfg.locale], geoip=cfg.geoip) as browser:
        page = browser.new_page()
        page.set_default_timeout(cfg.ui_timeout_ms)
        page.set_default_navigation_timeout(cfg.nav_timeout_ms)

        url = (f"{cfg.river_auth_host}/sign-up?client_id={cfg.river_client_id}"
               f"&redirect_uri={cfg.river_console}/auth/callback")
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        print(f"[probe2] landed: {page.url[:100]}", flush=True)
        print(f"[probe2] text: {snap(page)}", flush=True)

        # AuthKit's form may need a "Continue with email" hop first.
        clicked = page.evaluate(
            """() => {
                const b = [...document.querySelectorAll('button,a')].find(
                    el => /continue with email|sign up with email|use email/i.test(
                        (el.innerText || '').trim()));
                if (b) { b.click(); return b.innerText.trim(); }
                return null;
            }"""
        )
        if clicked:
            print(f"[probe2] clicked '{clicked}'", flush=True)
            page.wait_for_timeout(3500)
            print(f"[probe2] text: {snap(page)}", flush=True)

        # Fill whatever identity fields the form shows.
        filled = page.evaluate(
            f"""(() => {{
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                function set(sel, val) {{
                    const el = document.querySelector(sel);
                    if (!el) return false;
                    setter.call(el, val);
                    el.dispatchEvent(new Event('input', {{bubbles:true}}));
                    el.dispatchEvent(new Event('change', {{bubbles:true}}));
                    return true;
                }}
                const out = {{
                    email: set('input[name="email"], input[type="email"]', {addr!r}),
                }};
                const first = document.querySelector('input[name="first_name"]');
                if (first) {{
                    out.first = set('input[name="first_name"]', 'Rina');
                    out.last  = set('input[name="last_name"]', 'Puspita');
                }}
                return out;
            }})()"""
        )
        print(f"[probe2] filled: {filled}", flush=True)
        page.wait_for_timeout(1200)

        page.evaluate(
            """() => {
                const b = [...document.querySelectorAll('button')].find(
                    el => /continue|sign ?up|create account|lanjutkan/i.test(
                        (el.innerText || '').trim()));
                if (b) b.click();
            }"""
        )

        # Watch the next screens.
        seen: list[str] = []
        verdict = "unknown"
        for i in range(45):
            page.wait_for_timeout(1000)
            url_now = page.url
            txt = snap(page)
            low = txt.lower()
            key = f"{i}s {url_now[:70]} :: {txt[:160]}"
            if not seen or seen[-1][:120] != key[:120]:
                seen.append(key)
                print(f"[probe2] {key}", flush=True)

            if "policy_denied" in url_now or "access blocked" in low or "diblokir" in low:
                verdict = "BLOCKED (policy_denied)"
                break
            if any(k in low for k in river.PHONE_TEXT):
                verdict = "PHONE CHALLENGE"
                break
            if "password" in low and "input" in page.content().lower()[:200000]:
                has_pw = page.query_selector("input[type=password]")
                if has_pw:
                    verdict = "PASSWORD STEP (e-mail door open)"
                    break
            if any(k in low for k in ("verify your email", "enter the code sent to",
                                      "check your email", "verifikasi email")):
                verdict = "EMAIL CODE STEP (e-mail door open)"
                break

        print(f"\n[probe2] VERDICT: {verdict}", flush=True)
        print(f"[probe2] final url: {page.url}", flush=True)
        page.screenshot(path=str(Path(__file__).parent / "probe2_final.png"))

        if verdict.startswith("EMAIL CODE"):
            code = mail.wait_for_code(timeout_s=150)
            print(f"[probe2] email code: {code}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
