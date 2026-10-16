#!/usr/bin/env python3
"""cleanapis-pipeline — one command for the whole chain.

    accounts.txt (email|password)
        -> Google OAuth sign-in            (Camoufox)
        -> CleanAPIs account + cc_ key     (/dashboard/api-keys)
        -> result.txt (email|password|apikey)
        -> 9Router connections             (REST API, x-9r-cli-token)
        -> verification                    (direct + through the router)

    python cleanapis_pipeline.py run              # harvest, attach, verify
    python cleanapis_pipeline.py harvest          # stage 1 only
    python cleanapis_pipeline.py connect          # stage 2 only (uses result.txt)
    python cleanapis_pipeline.py verify           # stage 3 only
    python cleanapis_pipeline.py status           # what is where, no side effects

Options worth knowing:
    --show          run the browser visibly
    --pause N       gap between accounts (seconds)
    --replace       delete an existing 9Router connection before re-adding
    --only a@b,c@d  restrict to specific accounts
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline.accounts import load_accounts, load_results
from pipeline.config import Config
from pipeline.harvest import run_connect, run_harvest
from pipeline.verify import run_verify


def cmd_status(cfg: Config, accounts_file: Path, results_file: Path) -> int:
    accounts = load_accounts(accounts_file)
    results = load_results(results_file)
    print(f"accounts file : {accounts_file}  ({len(accounts)} account(s))")
    print(f"results file  : {results_file}  ({len(results)} key(s))")
    print(f"router        : {cfg.router_url}")
    print(f"router data   : {cfg.router_data_dir()}")

    missing = [a.email for a in accounts if a.email not in results
               or not results[a.email].api_key]
    if missing:
        print(f"\nnot harvested yet ({len(missing)}):")
        for email in missing:
            print(f"  - {email}")
    else:
        print("\nall accounts harvested")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="CleanAPIs + 9Router pipeline")
    ap.add_argument("command", choices=["run", "harvest", "connect", "verify", "status"])
    ap.add_argument("--accounts", type=Path, default=Path("accounts.txt"))
    ap.add_argument("--results", type=Path, default=Path("result.txt"))
    ap.add_argument("--show", action="store_true", help="visible browser window")
    ap.add_argument("--pause", type=int, default=None, help="seconds between accounts")
    ap.add_argument("--replace", action="store_true", help="overwrite existing 9Router entries")
    ap.add_argument("--only", default=None, help="comma-separated emails")
    ap.add_argument("--no-verify", action="store_true")
    a = ap.parse_args()

    cfg = Config()
    if a.show:
        cfg.headless = False
    if a.pause is not None:
        cfg.pause_min = a.pause

    only = [e.strip() for e in a.only.split(",") if e.strip()] if a.only else None
    accounts_file = a.accounts if a.accounts.is_absolute() else Path.cwd() / a.accounts
    results_file = a.results if a.results.is_absolute() else Path.cwd() / a.results

    if a.command == "status":
        return cmd_status(cfg, accounts_file, results_file)

    rc = 0
    if a.command in ("run", "harvest"):
        _ok, failed = run_harvest(cfg, accounts_file, results_file, only=only)
        rc |= 1 if failed else 0

    if a.command in ("run", "connect"):
        try:
            run_connect(cfg, results_file, replace=a.replace)
        except Exception as exc:
            print(f"[connect] {type(exc).__name__}: {exc}")
            rc |= 2

    if a.command == "run" and not a.no_verify:
        if not run_verify(cfg, results_file):
            rc |= 4

    if a.command == "verify":
        if not run_verify(cfg, results_file):
            rc |= 4

    return rc


if __name__ == "__main__":
    sys.exit(main())
