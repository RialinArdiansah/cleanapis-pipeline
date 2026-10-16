"""River harvest stage: create River accounts and mint one `rv_` key each.

River's signup has no API, so each account is created by driving WorkOS AuthKit's
hosted UI in a fresh browser. Three Radar outcomes are handled distinctly:

  allow      -> the flow continues on its own
  challenge  -> a phone number and its SMS code are supplied (pipeline/sms.py)
  block      -> `policy_denied`; the other signup door is tried, then the
                account is reported rather than silently retried

Inboxes are real mail.tm mailboxes per account so the mailed code has somewhere
to land, and because a reused mailbox re-logs the same user instead of creating
a new one.
"""
from __future__ import annotations

import random
import time
from pathlib import Path

from camoufox.sync_api import Camoufox

from . import river, y9router
from .accounts import Account, load_accounts, load_results, pending, save_result
from .config import Config
from .mailtm import MailTM
from .sms import from_config as sms_from_config


def _new_account(cfg: Config) -> Account:
    """Mint a fresh mailbox + password for one River account."""
    mail = MailTM()
    address = mail.create_account()
    password = "Rv!" + "".join(
        random.choice("abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789")
        for _ in range(15)
    )
    return Account(email=address, password=password)


def harvest_one(acct: Account, cfg: Config) -> str | None:
    """Run one River account end to end. Returns the rv_ key or None."""
    mail = MailTM()
    mail.address = acct.email
    mail.password = acct.password
    # The mailbox was created moments ago by the caller; re-authenticate on it so
    # this function also works for an account handed in from accounts.txt.
    try:
        tok = mail._json("POST", "/token",
                         json={"address": acct.email, "password": acct.password})
        if not tok or "token" not in tok:
            raise RuntimeError(f"mail.tm rejected the mailbox: {str(tok)[:120]}")
        mail.token = tok["token"]
        mail.s.headers["Authorization"] = f"Bearer {mail.token}"
    except Exception as exc:
        print(f"[river] cannot open inbox {acct.email}: {type(exc).__name__}: {exc}",
              flush=True)

    sms = None
    try:
        sms = sms_from_config(cfg)
    except Exception as exc:
        print(f"[river] phone provider unavailable: {exc}", flush=True)

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

        if not river.sign_in(page, acct.email, acct.password, cfg, mail=mail, sms=sms):
            print(f"[river] {acct.email}: sign-up did not reach the console", flush=True)
            return None

        for _ in range(60):
            if catcher.token:
                break
            page.wait_for_timeout(500)
        if not catcher.token:
            print(f"[river] {acct.email}: no console token captured", flush=True)

        balance = river.read_balance(page, catcher, cfg)
        if balance:
            print(f"[river] {acct.email}: balance {balance}", flush=True)

        return river.create_key(page, catcher, acct.email, cfg)


def run_harvest_river(cfg: Config, count: int = 1, accounts_file: Path | None = None,
                      results_file: Path | None = None) -> tuple[int, int]:
    """Create `count` fresh River accounts, or process an accounts file.

    Returns (ok, failed). Every success is written immediately, so an interrupted
    batch resumes with the keys already earned.
    """
    results_file = results_file or Path("river_result.txt")
    ok = failed = 0
    todo: list[Account] = []

    if accounts_file and Path(accounts_file).exists():
        todo = list(load_accounts(accounts_file))
        done = load_results(results_file)
        if cfg.river_skip_existing:
            todo = pending(todo, done)
        print(f"[river] {len(todo)} account(s) from {accounts_file}", flush=True)
    else:
        print(f"[river] minting {count} fresh account(s)", flush=True)
        for _ in range(count):
            todo.append(_new_account(cfg))

    for i, acct in enumerate(todo, 1):
        print(f"\n[river] === {i}/{len(todo)}: {acct.email} ===", flush=True)
        t0 = time.time()
        try:
            key = harvest_one(acct, cfg)
        except Exception as exc:
            print(f"[river] {acct.email}: {type(exc).__name__}: {exc}", flush=True)
            key = None

        if key:
            acct.api_key = key
            save_result(results_file, acct)
            print(f"[river] OK {acct.email}: {key[:14]}... ({len(key)} chars) "
                  f"in {time.time() - t0:.0f}s -> {results_file.name}", flush=True)
            ok += 1
        else:
            print(f"[river] FAIL {acct.email} ({time.time() - t0:.0f}s)", flush=True)
            failed += 1

        if i < len(todo):
            wait = cfg.pause_min + random.randint(0, cfg.pause_jitter)
            time.sleep(wait)

    print(f"\n[river] done: ok={ok} failed={failed}", flush=True)
    return ok, failed


def run_connect_river(cfg: Config, results_file: Path) -> tuple[int, int]:
    """Attach every rv_ key to the River provider node in 9Router."""
    return y9router.attach_all(cfg, results_file, node_hint=cfg.router_river_node_hint,
                               base_url=cfg.river_api_base, key_prefix=cfg.river_key_prefix)
