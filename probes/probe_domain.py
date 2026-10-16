"""Tell the two remaining Radar tells apart: e-mail domain vs source IP.

All previous attempts used a mail.tm disposable domain (`maxxspace.com`), which
is exactly the class Radar is built to score. This runs the identical flow with
a real custom-domain address on the same machine and IP, so the only variable
that changes is the domain.

  proceeds to the mailed-code screen -> the disposable domain was the tell
  blocked again                      -> the tell is environmental (IP/device)
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
    addr = sys.argv[1] if len(sys.argv) > 1 else "probe@example.com"
    cfg = Config(headless=True, river_door="email")
    print(f"[probe3] address={addr}", flush=True)

    with Camoufox(headless=cfg.headless, humanize=True, os=cfg.os_profile,
                  locale=[cfg.locale], geoip=cfg.geoip) as browser:
        page = browser.new_page()
        page.set_default_timeout(cfg.ui_timeout_ms)
        page.set_default_navigation_timeout(cfg.nav_timeout_ms)

        river.open_email_signup(page, cfg)
        print(f"[probe3] form: {river._visible_text(page)[:100]!r}", flush=True)

        river.fill_signup_identity(page, "Rina", "Puspita", addr)
        page.evaluate(river.SUBMIT_JS)
        page.wait_for_timeout(4500)
        print(f"[probe3] after identity: {page.url[:80]}", flush=True)

        if river.phone_gate(page):
            print("[probe3] VERDICT: PHONE CHALLENGE after identity", flush=True)
            return 3

        river.fill_signup_password(page, "RiverProbe!2026x")
        verdict = "TIMEOUT"
        for i in range(25):
            page.wait_for_timeout(1000)
            txt = river._visible_text(page).lower()
            if "access blocked" in txt or "policy_denied" in page.url:
                verdict = f"BLOCKED (policy_denied) at {i}s"
                break
            if river.phone_gate(page):
                verdict = f"PHONE CHALLENGE at {i}s"
                break
            if any(k in txt for k in ("verify your email", "enter the code",
                                      "check your email", "we sent")):
                verdict = f"PROCEEDED to e-mail code screen at {i}s"
                break
            if "password" not in page.url and not page.query_selector("input[type=password]"):
                verdict = f"PROCEEDED past password at {i}s -> {page.url[:70]}"
                break

        print(f"\n[probe3] VERDICT: {verdict}", flush=True)
        print(f"[probe3] url: {page.url[:110]}", flush=True)
        print(f"[probe3] text: {river._visible_text(page)[:250]!r}", flush=True)
        page.screenshot(path="probe3.png")

    Path("probe3_result.txt").write_text(f"{addr}\t{verdict}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
