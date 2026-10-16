"""Last free avenue: does OAuth sign-IN (no signup hint) escape the signup policy?

Radar's sign-up and sign-in policies are configured separately. Every attempt so
far carried signup intent (`source=signup` on the console link, or
`screen_hint=sign-up` on AuthKit), so the sign-**up** policy judged them. This
hits plain `/user_management/authorize?provider=GoogleOAuth` with no signup
marker, so the sign-**in** policy is what applies.

  reaches the console -> there is a free door that bypasses the signup policy
  policy_denied again  -> the block is at the environment level, and only a
                          different source IP will move it
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

AUTHORIZE = (
    "https://auth.console.river.ai/user_management/authorize"
    "?provider=GoogleOAuth"
    "&client_id=client_01M0TG5W3GBNMFS87K40PYHM98"
    "&redirect_uri=https%3A%2F%2Flogin.console.river.ai%2Fapi%2Fcallback"
    "&response_type=code"
)


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
        print(f"[probe4] no credentials for {email}", flush=True)
        return 5

    print(f"[probe4] account={email}", flush=True)
    with Camoufox(headless=cfg.headless, humanize=True, os=cfg.os_profile,
                  locale=[cfg.locale], geoip=cfg.geoip) as browser:
        page = browser.new_page()
        page.set_default_timeout(cfg.ui_timeout_ms)
        page.set_default_navigation_timeout(cfg.nav_timeout_ms)

        page.goto(AUTHORIZE, wait_until="domcontentloaded")
        page.wait_for_timeout(6000)
        print(f"[probe4] landed: {page.url[:130]}", flush=True)
        print(f"[probe4] text: {river._visible_text(page)[:250]!r}", flush=True)

        if river.is_google(page):
            print("[probe4] on Google (no gate before it)", flush=True)
            ok = river.full_google_login(page, acct.email, acct.password)
            print(f"[probe4] google returned {ok}", flush=True)
        else:
            print("[probe4] did not reach Google", flush=True)

        verdict = "unknown"
        for i in range(40):
            page.wait_for_timeout(1000)
            url = page.url
            txt = river._visible_text(page).lower()
            if "policy_denied" in url or "access blocked" in txt or "diblokir" in txt:
                verdict = f"BLOCKED (policy_denied) at {i}s"
                break
            if river.phone_gate(page):
                verdict = f"PHONE CHALLENGE at {i}s"
                break
            if river.is_on_console(page):
                verdict = f"CONSOLE REACHED at {i}s <-- FREE DOOR"
                break
            if "error=" in url:
                verdict = f"ERROR: {url[:120]}"

        print(f"\n[probe4] VERDICT: {verdict}", flush=True)
        print(f"[probe4] url: {page.url[:150]}", flush=True)
        print(f"[probe4] text: {river._visible_text(page)[:300]!r}", flush=True)
        page.screenshot(path="probe4.png")

    Path("probe4_result.txt").write_text(f"{email}\t{verdict}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
