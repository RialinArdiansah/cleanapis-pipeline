"""Pipeline configuration.

Everything tunable lives here so the CLI stays thin and the harvest module has no
module-level magic numbers.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Config:
    # --- CleanAPIs ---
    base_url: str = "https://cleanapis.com"
    # Referral landing page. The site's OAuth entry works from this path; going
    # straight to /auth/google/redirect is less reliable.
    ref_code: str = "CC9ELETL"
    keys_path: str = "/dashboard/api-keys"
    key_prefix: str = "cc_"
    # Keys are `cc_` + 48 chars. Accept a little slack for format drift.
    key_pattern: str = r"cc_[A-Za-z0-9]{20,}"

    # --- browser ---
    headless: bool = True
    locale: str = "id-ID"
    geoip: bool = True
    os_profile: tuple[str, ...] = ("windows",)
    nav_timeout_ms: int = 90_000
    ui_timeout_ms: int = 30_000

    # --- pacing ---
    # A full account takes ~60-90s; these are only the gaps between accounts so a
    # batch does not look like a metronome.
    pause_min: int = 5
    pause_jitter: int = 3

    # --- google sign-in ---
    max_consent_rounds: int = 12

    # --- River AI (console.river.ai) ---
    river_console: str = "https://console.river.ai"
    river_auth_host: str = "https://login.console.river.ai"
    # AuthKit application id; public (it ships in the page) but pinned so a
    # rotation is a one-line change.
    river_client_id: str = "client_01M0TG5W3GBNMFS87K40PYHM98"
    river_keys_path: str = "/keys"
    river_key_expiry_days: int = 30
    river_code_timeout_s: int = 180
    river_console_timeout_s: int = 120
    river_sms_timeout_s: int = 240
    # Signup door: "email" avoids the Google door's Radar block; "google" skips
    # the mailed code but is blocked outright from some IPs (see pipeline/river.py).
    river_door: str = "email"
    river_first_name: str = "Rina"
    river_last_name: str = "Puspita"

    # --- phone numbers (WorkOS Radar SMS challenge) ---
    # '' disables the phone step and reports the challenge instead of solving it.
    # '5sim'  = rented virtual number via the 5sim API (needs FIVESIM_TOKEN).
    # 'manual' = a number you own; the arriving code is written to river_sms_file.
    river_phone_provider: str = ""
    river_phone_number: str = ""
    river_sms_file: str = "sms_code.txt"
    river_5sim_country: str = "england"
    river_5sim_operator: str = "any"

    # --- river api (inference is OpenAI-compatible) ---
    river_api_base: str = "https://api.river.ai/v1"
    river_key_prefix: str = "rv_"

    # --- fresh inboxes per run ---
    river_skip_existing: bool = False

    # --- 9router ---
    router_url: str = "http://localhost:20128"
    router_node_hint: str = "cleanapis"
    router_river_node_hint: str = "river"
    router_provider_default_model: str = "default"

    # --- derived / runtime ---
    data_dir: str = ""

    def router_data_dir(self) -> str:
        """9router's data directory (holds machine-id and auth/cli-secret)."""
        if self.data_dir:
            return self.data_dir
        if env := os.environ.get("ROUTER_DATA_DIR"):
            return env
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~/AppData/Roaming")
        return os.path.join(appdata, "9router")

    def login_url(self) -> str:
        return f"{self.base_url}/?ref={self.ref_code}"
