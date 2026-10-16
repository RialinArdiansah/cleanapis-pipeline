"""Account/result file handling.

Two files, both plain text, both gitignored:

    accounts.txt   email|password
    result.txt     email|password|apikey

Every write to result.txt is flushed and fsynced so a killed batch never loses a
finished account, and re-running skips emails already present.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Account:
    email: str
    password: str
    api_key: str | None = None

    @property
    def has_key(self) -> bool:
        return bool(self.api_key)


def _iter_rows(path: Path):
    if not path.exists():
        return
    with path.open(encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "|" not in line:
                print(f"  ! {path.name}:{lineno} skipped (no '|' separator)")
                continue
            yield lineno, [p.strip() for p in line.split("|")]


def load_accounts(path: Path) -> list[Account]:
    """Read accounts.txt -> [Account]."""
    out: list[Account] = []
    for _lineno, parts in _iter_rows(path) or []:
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        out.append(Account(email=parts[0], password=parts[1]))
    return out


def load_results(path: Path) -> dict[str, Account]:
    """Read result.txt -> {email: Account(with api_key)}."""
    out: dict[str, Account] = {}
    for _lineno, parts in _iter_rows(path) or []:
        if len(parts) < 3 or not parts[0]:
            continue
        out[parts[0]] = Account(email=parts[0], password=parts[1], api_key=parts[2])
    return out


def save_result(path: Path, acct: Account) -> None:
    """Upsert one account's row, then fsync. Replaces any existing row for the email."""
    lines: list[str] = []
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            lines = [l.rstrip("\n") for l in fh if not l.startswith(acct.email + "|")]
    if not any(l.startswith("#") for l in lines):
        lines.insert(0, "# email|password|apikey")
    lines = [l for l in lines if l.strip()]
    lines.append(f"{acct.email}|{acct.password}|{acct.api_key or ''}")

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def pending(accounts: list[Account], results: dict[str, Account]) -> list[Account]:
    """Accounts with no harvested key yet."""
    return [a for a in accounts if a.email not in results or not results[a.email].has_key]
