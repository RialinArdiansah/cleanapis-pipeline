"""Minimal mail.tm client: throwaway inbox + read the 6-digit verification code.

mail.tm is used only as a receiving mailbox for AuthKit's e-mail code. It is a
disposable domain, which is exactly the signal WorkOS Radar scores as risky, so
treat a phone challenge on an inbox from here as expected and not as a bug.
"""
from __future__ import annotations

import re
import secrets
import string
import time

import requests

BASE = "https://api.mail.tm"
CODE_RE = re.compile(r"\b(\d{6})\b")


class MailTM:
    def __init__(self, timeout: int = 30) -> None:
        self.s = requests.Session()
        self.s.headers["Accept"] = "application/json"
        self.timeout = timeout
        self.address: str | None = None
        self.password: str | None = None
        self.token: str | None = None

    # ---- plumbing -------------------------------------------------------
    def _json(self, method: str, path: str, **kw):
        r = self.s.request(method, f"{BASE}{path}", timeout=self.timeout, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"mail.tm {method} {path} -> {r.status_code} {r.text[:200]}")
        return r.json() if r.content else None

    def domains(self) -> list[str]:
        data = self._json("GET", "/domains")
        items = data.get("hydra:member", data) if isinstance(data, dict) else data
        return [d["domain"] for d in items if d.get("isActive", True)]

    # ---- public ---------------------------------------------------------
    def create_account(self, domain: str | None = None) -> str:
        """Create a mailbox and return its address.

        `password` must exist BEFORE the token call: generating it afterwards
        authenticates with a different string and mail.tm answers 401.
        """
        dom = domain or self.domains()[0]
        alphabet = string.ascii_lowercase + string.digits
        last_exc: Exception | None = None
        for _ in range(6):
            local = "rv" + "".join(secrets.choice(alphabet) for _ in range(11))
            self.address = f"{local}@{dom}"
            self.password = "Rv!" + "".join(
                secrets.choice(string.ascii_letters + string.digits) for _ in range(14)
            )
            try:
                self._json("POST", "/accounts",
                           json={"address": self.address, "password": self.password})
                break
            except RuntimeError as exc:
                last_exc = exc
                if "429" in str(exc):
                    time.sleep(5)
                    continue
                raise
        else:
            raise RuntimeError(f"mail.tm could not create a mailbox: {last_exc}")

        tok = self._json("POST", "/token",
                         json={"address": self.address, "password": self.password})
        if not tok or "token" not in tok:
            raise RuntimeError(f"mail.tm refused the token: {str(tok)[:150]}")
        self.token = tok["token"]
        self.s.headers["Authorization"] = f"Bearer {self.token}"
        return self.address

    def messages(self) -> list[dict]:
        data = self._json("GET", "/messages?page=1")
        return data.get("hydra:member", data) if isinstance(data, dict) else data

    def wait_for_code(self, timeout_s: int = 150, poll_s: int = 5,
                      sender_hint: str | None = None) -> str | None:
        """Poll for the first message carrying a 6-digit code."""
        deadline = time.time() + timeout_s
        seen: set[str] = set()
        while time.time() < deadline:
            try:
                msgs = self.messages()
            except RuntimeError:
                time.sleep(poll_s)
                continue
            for m in msgs:
                mid = m.get("id")
                if not mid or mid in seen:
                    continue
                seen.add(mid)
                if sender_hint and sender_hint.lower() not in str(m.get("from", "")).lower():
                    continue
                try:
                    full = self._json("GET", f"/messages/{mid}")
                except RuntimeError:
                    continue
                blob = " ".join(str(full.get(k) or "") for k in ("subject", "intro", "text"))
                html = full.get("html") or []
                blob += " " + " ".join(str(h) for h in html)
                hit = CODE_RE.search(blob)
                if hit:
                    return hit.group(1)
            time.sleep(poll_s)
        return None
