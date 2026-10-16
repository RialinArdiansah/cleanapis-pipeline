#!/usr/bin/env python3
"""cleanapis-pipeline — one command for the whole chain, two providers.

Provider `cleanapis`:

    accounts.txt (email|password)
        -> Google OAuth sign-in              (Camoufox)
        -> CleanAPIs account + cc_ key       (/dashboard/api-keys)
        -> result.txt (email|password|apikey)
        -> 9Router connections               (REST API, x-9r-cli-token)
        -> verification                      (direct + through the router)

Provider `river` (console.river.ai, a different wall — see RIVER.md):

    fresh mail.tm inbox + generated password
        -> WorkOS AuthKit hosted sign-up     (Camoufox)
        -> mailed code                      (or an SMS challenge: pipeline/sms.py)
        -> console bearer token captured off the wire
        -> POST /api/keys                    -> rv_ key
        -> river_result.txt -> 9Router       -> verification

Usage:
    python cleanapis_pipeline.py run                        # CleanAPIs, full chain
    python cleanapis_pipeline.py --provider river run --count 3
    python cleanapis_pipeline.py --provider river harvest --count 1 --door email
    python cleanapis_pipeline.py --provider river verify

Options worth knowing:
    --provider {cleanapis,river}
    --show              run the browser visibly
    --pause N           gap between accounts (seconds)
    --count N           river only: how many fresh accounts to mint
    --door email|google river only: which signup door to try first
    --phone-provider K  river only: '' | 5sim | manual
    --phone N           river only: number for the manual provider
    --replace           delete an existing 9Router connection before re-adding
    --only a@b,c@d      restrict to specific accounts
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline.accounts import load_accounts, load_results
from pipeline.config import Config


def cmd_status(cfg: Config, accounts_file: Path, results_file: Path,
               provider: str) -> int:
    print(f"provider      : {provider}")
    if provider == "river":
        print(f"console       : {cfg.river_console}")
        print(f"auth host     : {cfg.river_auth_host}")
        print(f"signup door   : {cfg.river_door}")
        print(f"phone provider: {cfg.river_phone_provider or '(none)'}")
        print(f"results file  : {results_file}  "
              f"({len(load_results(results_file))} key(s))")
        print(f"router        : {cfg.router_url}")
        return 0

    accounts = load_accounts(accounts_file)
    results = load_results(results_file)
    print(f"accounts file : {accounts_file}  ({len(accounts)} account(s))")
    print(f"results file  : {results_file}  ({len(results)} key(s))")
    print(f"router        : {cfg.router_url}")
    print(f"router data   : {cfg.router_data_dir()}")

    missing = [a.email for a in accounts
               if a.email not in results or not results[a.email].api_key]
    if missing:
        print(f"\nnot harvested yet ({len(missing)}):")
        for email in missing:
            print(f"  - {email}")
    else:
        print("\nall accounts harvested")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="CleanAPIs / River AI -> 9Router pipeline")
    ap.add_argument("command", choices=["run", "harvest", "connect", "verify", "status"])
    ap.add_argument("--provider", choices=["cleanapis", "river"], default="cleanapis")
    ap.add_argument("--accounts", type=Path, default=None,
                    help="accounts.txt for cleanapis; for river, an optional "
                         "email|password list to reuse instead of fresh inboxes")
    ap.add_argument("--results", type=Path, default=None)
    ap.add_argument("--show", action="store_true", help="visible browser window")
    ap.add_argument("--pause", type=int, default=None, help="seconds between accounts")
    ap.add_argument("--replace", action="store_true",
                    help="overwrite existing 9Router entries")
    ap.add_argument("--only", default=None, help="comma-separated emails")
    ap.add_argument("--no-verify", action="store_true")
    # river-only
    ap.add_argument("--count", type=int, default=1,
                    help="river: how many fresh accounts to mint")
    ap.add_argument("--door", choices=["email", "google"], default=None,
                    help="river: which signup door to try first")
    ap.add_argument("--phone-provider", default=None,
                    help="river: '' | 5sim | manual")
    ap.add_argument("--phone", default=None, help="river: number for manual provider")
    a = ap.parse_args()

    cfg = Config()
    if a.show:
        cfg.headless = False
    if a.pause is not None:
        cfg.pause_min = a.pause
    if a.door:
        cfg.river_door = a.door
    if a.phone_provider is not None:
        cfg.river_phone_provider = a.phone_provider
    if a.phone:
        cfg.river_phone_number = a.phone
    # FIVESIM_TOKEN is read from the environment on purpose: a token on argv
    # lands in the process list.
    if cfg.river_phone_provider == "5sim" and not os.environ.get("FIVESIM_TOKEN"):
        print("[warn] river_phone_provider=5sim but FIVESIM_TOKEN is unset; the "
              "phone step will report the challenge instead of solving it")

    default_results = Path("river_result.txt") if a.provider == "river" else Path("result.txt")
    results_file = a.results or default_results
    if not results_file.is_absolute():
        results_file = Path.cwd() / results_file

    # Default accounts file differs per provider: reusing accounts.txt for River
    # would feed Google Workspace credentials into the River form, which is the
    # wrong account class entirely. River mints its own inboxes unless one is
    # named explicitly.
    if a.provider == "river":
        accounts_file = (a.accounts if a.accounts
                         else Path.cwd() / "river_accounts.txt")
        if not accounts_file.is_absolute():
            accounts_file = Path.cwd() / accounts_file
        use_accounts = a.accounts is not None and accounts_file.exists()
    else:
        accounts_file = a.accounts or Path("accounts.txt")
        if not accounts_file.is_absolute():
            accounts_file = Path.cwd() / accounts_file
        use_accounts = True

    if a.command == "status":
        return cmd_status(cfg, accounts_file, results_file, a.provider)

    if a.provider == "cleanapis":
        from pipeline.harvest import run_connect, run_harvest
        from pipeline.verify import run_verify as run_verify_clean

        run_harvest_clean = run_harvest
        run_connect_clean = run_connect
    else:
        from pipeline.river_harvest import run_connect_river, run_harvest_river
        from pipeline.river_verify import run_verify_river

    only = [e.strip() for e in a.only.split(",") if e.strip()] if a.only else None

    rc = 0
    if a.command in ("run", "harvest"):
        if a.provider == "cleanapis":
            _ok, failed = run_harvest_clean(cfg, accounts_file, results_file, only=only)
        else:
            _ok, failed = run_harvest_river(
                cfg, count=a.count,
                accounts_file=accounts_file if use_accounts else None,
                results_file=results_file,
            )
        rc |= 1 if failed else 0

    if a.command in ("run", "connect"):
        try:
            if a.provider == "cleanapis":
                run_connect_clean(cfg, results_file, replace=a.replace)
            else:
                run_connect_river(cfg, results_file)
        except Exception as exc:
            print(f"[connect] {type(exc).__name__}: {exc}")
            rc |= 2

    if a.command in ("run", "verify") and not (a.command == "run" and a.no_verify):
        try:
            if a.provider == "cleanapis":
                ok = run_verify_clean(cfg, results_file)
            else:
                ok = run_verify_river(cfg, results_file)
        except Exception as exc:
            print(f"[verify] {type(exc).__name__}: {exc}")
            ok = False
        if not ok:
            rc |= 4

    return rc


if __name__ == "__main__":
    sys.exit(main())
