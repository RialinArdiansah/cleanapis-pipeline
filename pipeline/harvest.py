"""Harvest stage: create a CleanAPIs account and mint one API key, one account at
a time, each in a fresh browser.

A fresh Camoufox instance per account is deliberate: a reused context carries the
previous Google session, and the account chooser then hands the flow to the wrong
identity.
"""
from __future__ import annotations

import random
import time
from pathlib import Path

from camoufox.sync_api import Camoufox

from . import y9router
from .accounts import Account, load_accounts, load_results, pending, save_result
from .cleanapis import create_key, sign_in
from .config import Config


def harvest_one(acct: Account, cfg: Config) -> str | None:
    """Run one account end to end. Returns the cc_ key or None."""
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

        if not sign_in(page, acct.email, acct.password, cfg):
            print(f"[harvest] {acct.email}: sign-in failed", flush=True)
            return None
        return create_key(page, acct.email, cfg)


def run_harvest(cfg: Config, accounts_file: Path, results_file: Path,
                only: list[str] | None = None) -> tuple[int, int]:
    """Harvest keys for every pending account. Returns (ok, failed).

    Results are written per account as they succeed, so an interrupted run is
    resumed simply by invoking it again.
    """
    accounts = load_accounts(accounts_file)
    if not accounts:
        print(f"[harvest] no accounts in {accounts_file} — expected 'email|password' lines")
        return 0, 0

    if only:
        wanted = {e.lower() for e in only}
        accounts = [a for a in accounts if a.email.lower() in wanted]
        if not accounts:
            print(f"[harvest] none of the requested emails are in {accounts_file.name}")
            return 0, 0

    results = load_results(results_file)
    todo = pending(accounts, results)
    print(f"[harvest] {len(accounts)} account(s), {len(results)} already in "
          f"{results_file.name}, {len(todo)} to do", flush=True)

    ok = failed = 0
    for i, acct in enumerate(todo, 1):
        print(f"\n[harvest] === {i}/{len(todo)}: {acct.email} ===", flush=True)
        try:
            key = harvest_one(acct, cfg)
        except Exception as exc:            # one broken account must not end the batch
            print(f"[harvest] {acct.email}: {type(exc).__name__}: {exc}", flush=True)
            key = None

        if key:
            acct.api_key = key
            save_result(results_file, acct)
            print(f"[harvest] OK {acct.email}: {key[:14]}... ({len(key)} chars) -> "
                  f"{results_file.name}", flush=True)
            ok += 1
        else:
            failed += 1

        if i < len(todo):
            wait = cfg.pause_min + random.randint(0, cfg.pause_jitter)
            time.sleep(wait)

    print(f"\n[harvest] done: ok={ok} failed={failed}", flush=True)
    return ok, failed


def run_connect(cfg: Config, results_file: Path, replace: bool = False) -> tuple[int, int]:
    """Attach every harvested key to 9Router. Delegates to the 9router module."""
    return y9router.attach_all(cfg, results_file, replace=replace)
