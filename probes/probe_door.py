"""Live probe: does the Google door to console.river.ai ever demand a phone?

Run once against a real account and report exactly what the flow did, so the
Radar question is answered with evidence rather than assumption.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from camoufox.sync_api import Camoufox

from pipeline import river
from pipeline.config import Config


def main() -> int:
    email = sys.argv[1] if len(sys.argv) > 1 else "amingeri68@rurasa.my.id"
    cfg = Config(headless=False)

    # Credentials come from the accounts file, never from argv: a password on the
    # command line lands in the process list.
    from pipeline.accounts import load_accounts

    acct = None
    for fname in ("accounts.txt", "result.txt"):
        path = Path(__file__).resolve().parent / fname
        if not path.exists():
            continue
        for a in load_accounts(path):
            if a.email.lower() == email.lower():
                acct = a
                break
        if acct:
            break
    if acct is None:
        print(f"[probe] no credentials for {email} in accounts.txt", flush=True)
        return 5
    email, password = acct.email, acct.password

    print(f"[probe] account={email}", flush=True)
    t0 = time.time()

    with Camoufox(
        headless=cfg.headless,
        humanize=True,
        os=cfg.os_profile,
        locale=[cfg.locale],
        geoip=cfg.geoip,
    ) as browser:
        page = browser.new_page()
        page.set_default_timeout(cfg.ui_timeout_ms)
        page.set_default_navigation_timeout(cfg.nav_timeout_ms)

        catcher = river.TokenCatcher()
        catcher.attach(page)

        # Step 1: the door. Report which screen we land on.
        try:
            river.open_signup(page, cfg)
        except Exception as exc:
            print(f"[probe] open_signup FAILED: {type(exc).__name__}: {exc}", flush=True)
            print(f"[probe] url={page.url}", flush=True)
            page.screenshot(path=str(Path(__file__).parent / "probe_door.png"))
            return 1
        print(f"[probe] after door url={page.url[:120]}", flush=True)

        # Step 2: Google side.
        if river.is_google(page):
            print("[probe] on Google, driving login", flush=True)
            ok = river.full_google_login(page, email, password)
            print(f"[probe] google login returned {ok}; url={page.url[:120]}", flush=True)

        # Step 3: what did AuthKit / the console show us?
        for i in range(60):
            page.wait_for_timeout(1000)
            txt = river._visible_text(page).lower()
            phones = [k for k in river.PHONE_TEXT if k in txt]
            if phones:
                print(f"[probe] !!! PHONE GATE after {i}s: {phones}", flush=True)
                print(f"[probe] url={page.url}", flush=True)
                print(f"[probe] text={river._visible_text(page)[:800]!r}", flush=True)
                page.screenshot(path=str(Path(__file__).parent / "probe_phone.png"))
                return 3
            if river.is_on_console(page):
                print(f"[probe] CONSOLE LOADED after {i}s: {page.url}", flush=True)
                break
        else:
            print(f"[probe] never reached console; url={page.url}", flush=True)
            print(f"[probe] text={river._visible_text(page)[:900]!r}", flush=True)
            page.screenshot(path=str(Path(__file__).parent / "probe_stuck.png"))
            return 2

        # Step 4: token + key.
        for _ in range(40):
            if catcher.token:
                break
            page.wait_for_timeout(500)
        print(f"[probe] token captured: {bool(catcher.token)}", flush=True)

        bal = river.read_balance(page, catcher, cfg)
        print(f"[probe] balance={bal}", flush=True)

        key = river.create_key(page, catcher, f"river-probe-{int(time.time())}", cfg)
        print(f"[probe] key={key}", flush=True)
        if key:
            print(f"[probe] SUCCESS in {time.time() - t0:.0f}s", flush=True)
            return 0
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
