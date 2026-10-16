"""A/B test: is Radar's block a fingerprint decision or an IP decision?

Runs the identical e-mail-door flow in a real Chrome/Chromium reached over CDP
instead of Camoufox (Firefox-based). Same machine, same IP, same mailbox
provider, same form-filling code — so the only variable is the browser.

  verdict BLOCKED in Chrome too   -> the block is environmental (IP / account),
                                     not a fingerprint tell
  verdict proceeds in Chrome      -> Camoufox's Firefox fingerprint is the tell
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright

from pipeline import river
from pipeline.config import Config
from pipeline.mailtm import MailTM

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9333


def wait_for_cdp(port: int, timeout_s: int = 45) -> str | None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as r:
                return json.load(r)["webSocketDebuggerUrl"]
        except Exception:
            time.sleep(1)
    return None


def main() -> int:
    variant = sys.argv[1] if len(sys.argv) > 1 else "chrome"
    profile = Path(__file__).resolve().parent / f"ab_profile_{variant}"
    profile.mkdir(exist_ok=True)

    cfg = Config(headless=True, river_door="email")
    mail = MailTM()
    addr = mail.create_account()
    password = "RiverAb!2026xyz"
    print(f"[ab:{variant}] inbox={addr}", flush=True)

    if variant == "chrome":
        exe = CHROME
    else:
        # Playwright's bundled Chromium, to separate "real Chrome" from "Chromium".
        exe = None

    if exe:
        args = [
            exe,
            "--headless=new",
            f"--remote-debugging-port={PORT}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
        ]
        print(f"[ab:{variant}] launching: {' '.join(args[:3])} ...", flush=True)
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ws = wait_for_cdp(PORT)
        if not ws:
            print(f"[ab:{variant}] Chrome did not expose CDP", flush=True)
            proc.kill()
            return 2
        print(f"[ab:{variant}] CDP up", flush=True)
    else:
        proc = None
        ws = None

    verdict = "unknown"
    with sync_playwright() as p:
        if ws:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
            ctx = browser.new_context()
        else:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context()
        page = ctx.new_page()
        page.set_default_timeout(cfg.ui_timeout_ms)
        page.set_default_navigation_timeout(cfg.nav_timeout_ms)

        try:
            print(f"[ab:{variant}] navigator check: "
                  f"{page.evaluate('navigator.userAgent')[:90]}", flush=True)

            river.open_email_signup(page, cfg)
            print(f"[ab:{variant}] form: {river._visible_text(page)[:110]!r}", flush=True)

            river.fill_signup_identity(page, "Rina", "Puspita", addr)
            page.evaluate(river.SUBMIT_JS)
            page.wait_for_timeout(4500)
            print(f"[ab:{variant}] after identity: {page.url[:80]}", flush=True)

            if river.phone_gate(page):
                verdict = "PHONE CHALLENGE (after identity)"
                print(f"[ab:{variant}] {verdict}", flush=True)
            else:
                river.fill_signup_password(page, password)
                for i in range(20):
                    page.wait_for_timeout(1000)
                    txt = river._visible_text(page).lower()
                    if "access blocked" in txt or "policy_denied" in page.url:
                        verdict = f"BLOCKED (policy_denied) after password, {i}s"
                        break
                    if river.phone_gate(page):
                        verdict = f"PHONE CHALLENGE after password, {i}s"
                        break
                    if page.query_selector("input[type=password]") is None and \
                       "password" not in page.url:
                        verdict = f"PROCEEDED past password, {i}s -> {page.url[:70]}"
                        break
                else:
                    verdict = "TIMEOUT (no clear verdict)"

            print(f"\n[ab:{variant}] VERDICT: {verdict}", flush=True)
            print(f"[ab:{variant}] final url: {page.url}", flush=True)
            print(f"[ab:{variant}] final text: {river._visible_text(page)[:300]!r}", flush=True)
            page.screenshot(path=f"ab_{variant}.png")
        except Exception as exc:
            print(f"[ab:{variant}] ERROR: {type(exc).__name__}: {exc}", flush=True)
            verdict = f"ERROR {type(exc).__name__}"
        finally:
            try:
                ctx.close()
            except Exception:
                pass

    if proc:
        proc.kill()

    out = Path(__file__).resolve().parent / "ab_result.json"
    prev = json.loads(out.read_text()) if out.exists() else {}
    prev[variant] = {"inbox": addr, "verdict": verdict}
    out.write_text(json.dumps(prev, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
