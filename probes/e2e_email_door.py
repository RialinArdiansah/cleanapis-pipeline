"""End-to-end test of the e-mail door: signup -> mailed code -> console -> rv_ key.

This is the decisive test. It prints the screen at each step so a phone
challenge, a Radar block, or a silent stall is visible in the log rather than
being guessed at.
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


def main() -> int:
    cfg = Config(headless=False, river_door="email")
    mail = MailTM()
    addr = mail.create_account()
    password = "RiverProbe!2026x"
    print(f"[e2e] inbox={addr}", flush=True)
    print(f"[e2e] door={cfg.river_door}", flush=True)
    t0 = time.time()

    with Camoufox(headless=cfg.headless, humanize=True, os=cfg.os_profile,
                  locale=[cfg.locale], geoip=cfg.geoip) as browser:
        page = browser.new_page()
        page.set_default_timeout(cfg.ui_timeout_ms)
        page.set_default_navigation_timeout(cfg.nav_timeout_ms)

        catcher = river.TokenCatcher()
        catcher.attach(page)

        step = "signup"
        try:
            # Drive each stage explicitly so a failure names its stage.
            river.open_email_signup(page, cfg)
            print(f"[e2e] signup form: {river._visible_text(page)[:150]!r}", flush=True)

            filled = river.fill_signup_identity(page, "Rina", "Puspita", addr)
            print(f"[e2e] identity: {filled}", flush=True)
            page.evaluate(river.SUBMIT_JS)
            page.wait_for_timeout(4500)
            print(f"[e2e] after identity: {page.url[:90]} :: "
                  f"{river._visible_text(page)[:130]!r}", flush=True)

            if river.phone_gate(page):
                print("[e2e] PHONE CHALLENGE right after identity", flush=True)
                return 3

            step = "password"
            if not river.fill_signup_password(page, password):
                print("[e2e] password step failed", flush=True)
                return 4
            page.wait_for_timeout(6000)
            print(f"[e2e] after password: {page.url[:90]} :: "
                  f"{river._visible_text(page)[:160]!r}", flush=True)

            if river.phone_gate(page):
                print("[e2e] PHONE CHALLENGE at/after password step", flush=True)
                page.screenshot(path="e2e_phone.png")
                return 3

            step = "email-code"
            code = mail.wait_for_code(timeout_s=cfg.river_code_timeout_s)
            print(f"[e2e] mailed code: {code}", flush=True)
            if not code:
                print(f"[e2e] no code; screen now: "
                      f"{river._visible_text(page)[:250]!r}", flush=True)
                page.screenshot(path="e2e_nocode.png")
                return 5

            river.enter_email_code(page, code)
            page.wait_for_timeout(6000)
            print(f"[e2e] after code: {page.url[:100]}", flush=True)

            step = "console"
            ok = river.wait_for_console(page, cfg, timeout_s=cfg.river_console_timeout_s)
            print(f"[e2e] console reached: {ok} :: {page.url[:100]}", flush=True)
            if not ok:
                print(f"[e2e] text: {river._visible_text(page)[:300]!r}", flush=True)
                page.screenshot(path="e2e_noconsole.png")
                return 6

            step = "token"
            for _ in range(60):
                if catcher.token:
                    break
                page.wait_for_timeout(500)
            print(f"[e2e] token: {bool(catcher.token)}", flush=True)

            bal = river.read_balance(page, catcher, cfg)
            print(f"[e2e] balance: {bal}", flush=True)

            step = "key"
            key = river.create_key(page, catcher, f"river-e2e-{int(time.time())}", cfg)
            print(f"[e2e] KEY: {key}", flush=True)
            if key:
                print(f"[e2e] SUCCESS in {time.time() - t0:.0f}s", flush=True)
                Path("e2e_key.txt").write_text(f"{addr}|{password}|{key}\n", encoding="utf-8")
                return 0
            print(f"[e2e] key mint failed at stage={step}", flush=True)
            page.screenshot(path="e2e_nokey.png")
            return 7
        except Exception as exc:
            print(f"[e2e] FAILED at stage={step}: {type(exc).__name__}: {exc}", flush=True)
            try:
                page.screenshot(path=f"e2e_fail_{step}.png")
            except Exception:
                pass
            return 8


if __name__ == "__main__":
    raise SystemExit(main())
