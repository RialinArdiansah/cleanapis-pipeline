"""Phone-number providers for OTP-gated sign-ups.

WorkOS Radar's *challenge* outcome (as opposed to *allow* and *block*) demands a
phone number that can receive a real SMS. Two ways to satisfy that exist, and
they are not equivalent:

* **Rented virtual numbers** (5sim and friends) — an API that hands you a number
  and exposes the inbound SMS. Cheap and automatable, but the numbers are VoIP
  ranges that have usually been used before, so risk engines are trained to
  distrust them. Expect a meaningful failure rate; retry on a fresh number.
* **A real SIM you control** — a spare handset or an eSIM. Highest success rate
  and the only option if Radar keeps refusing virtual ranges.

`Manual` is not a placeholder: pasting a number you own and reading the code off
your own phone is the reliable path when the site's risk engine rejects virtual
ranges, and it is worth reaching for early rather than after burning credit.

Keys are read from the environment, never from argv (argv lands in the process
list). Both providers expose the same three calls:

    num = provider.get_number()      -> PhoneNumber
    code = provider.wait_for_code(num, timeout_s)
    provider.release(num)
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

CODE_RE = re.compile(r"\b(\d{4,8})\b")


@dataclass
class PhoneNumber:
    number: str                      # E.164 where possible, e.g. +628123456789
    provider: str = ""
    activation_id: str = ""
    raw: dict = field(default_factory=dict)

    def local(self, country_code: str = "62") -> str:
        """Digits after the country code, which is what most forms want."""
        digits = re.sub(r"\D", "", self.number)
        cc = re.sub(r"\D", "", country_code)
        return digits[len(cc):] if digits.startswith(cc) else digits


class SMSProvider:
    """Common interface. Subclasses implement the three calls."""

    name = "base"

    def get_number(self, service: str = "any") -> PhoneNumber:
        raise NotImplementedError

    def wait_for_code(self, num: PhoneNumber, timeout_s: int = 180) -> str | None:
        raise NotImplementedError

    def release(self, num: PhoneNumber) -> None:
        return None


class FiveSim(SMSProvider):
    """5sim.net — rented numbers with an SMS inbox behind an API.

    Purchase assigns one number to one activation for a bounded window; the
    inbound code is polled off `check`. Always release the activation: an
    unreleased number stays billed and blocks the next purchase.

    Env: FIVESIM_TOKEN (required), FIVESIM_COUNTRY (default 'england'),
         FIVESIM_OPERATOR (default 'any').
    """

    name = "5sim"
    BASE = "https://5sim.net/v1"

    def __init__(self, token: str | None = None, country: str = "", operator: str = "") -> None:
        self.token = token or os.environ.get("FIVESIM_TOKEN", "")
        self.country = country or os.environ.get("FIVESIM_COUNTRY", "england")
        self.operator = operator or os.environ.get("FIVESIM_OPERATOR", "any")
        if not self.token:
            raise RuntimeError("FIVESIM_TOKEN is not set")

    def _get(self, path: str, timeout: int = 30):
        r = requests.get(
            f"{self.BASE}{path}",
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
            timeout=timeout,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"5sim {path} -> {r.status_code} {r.text[:200]}")
        return r.json()

    def prices(self, service: str = "any") -> dict:
        """Guest catalogue for the configured country — useful before spending."""
        r = requests.get(f"{self.BASE}/guest/products/{self.country}/{self.operator}",
                         headers={"Accept": "application/json"}, timeout=30)
        r.raise_for_status()
        return r.json()

    def get_number(self, service: str = "any") -> PhoneNumber:
        data = self._get(f"/user/buy/activation/{self.country}/{self.operator}/{service}")
        if "phone" not in data:
            raise RuntimeError(f"5sim returned no number: {str(data)[:200]}")
        return PhoneNumber(number="+" + str(data["phone"]).lstrip("+"),
                           provider=self.name, activation_id=str(data.get("id", "")),
                           raw=data)

    def wait_for_code(self, num: PhoneNumber, timeout_s: int = 180,
                      poll_s: int = 5) -> str | None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            data = self._get(f"/user/check/{num.activation_id}")
            status = str(data.get("status", "")).upper()
            for sms in data.get("sms") or []:
                hit = CODE_RE.search(str(sms.get("text", "")))
                if hit:
                    return hit.group(1)
            if status in ("CANCELED", "BANNED", "TIMEOUT"):
                return None
            time.sleep(poll_s)
        return None

    def release(self, num: PhoneNumber, finish: bool = True) -> None:
        try:
            self._get(f"/user/{'finish' if finish else 'cancel'}/{num.activation_id}")
        except Exception:
            pass


class Manual(SMSProvider):
    """A number you own, with the code pasted in.

    `code_source` is a file the operator writes the arriving code into; polling
    it keeps the flow scripted instead of blocking on stdin, which does not work
    in a non-interactive run.
    """

    name = "manual"

    def __init__(self, number: str, code_source: str | Path = "sms_code.txt",
                 country_code: str = "62") -> None:
        self._number = number
        self.code_source = Path(code_source)
        self.country_code = country_code

    def get_number(self, service: str = "any") -> PhoneNumber:
        return PhoneNumber(number=self._number, provider=self.name,
                           raw={"country_code": self.country_code})

    def wait_for_code(self, num: PhoneNumber, timeout_s: int = 300,
                      poll_s: int = 3) -> str | None:
        print(f"[sms] waiting for the code on {num.number} — "
              f"write it into {self.code_source}", flush=True)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.code_source.exists():
                hit = CODE_RE.search(self.code_source.read_text(encoding="utf-8"))
                if hit:
                    self.code_source.unlink(missing_ok=True)
                    return hit.group(1)
            time.sleep(poll_s)
        return None


def from_config(cfg) -> SMSProvider | None:
    """Build the provider named by config, or None when no number source is set.

    `river_phone_provider`: '' (none) | '5sim' | 'manual'
    `river_phone_number` + `river_sms_file`: used by 'manual'.
    """
    kind = (getattr(cfg, "river_phone_provider", "") or "").strip().lower()
    if kind == "5sim":
        return FiveSim(country=getattr(cfg, "river_5sim_country", "england"),
                       operator=getattr(cfg, "river_5sim_operator", "any"))
    if kind == "manual":
        number = getattr(cfg, "river_phone_number", "") or os.environ.get("RIVER_PHONE_NUMBER", "")
        if not number:
            raise RuntimeError("river_phone_provider=manual needs river_phone_number")
        return Manual(number, getattr(cfg, "river_sms_file", "sms_code.txt"))
    return None
