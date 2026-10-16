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

    # --- 9router ---
    router_url: str = "http://localhost:20128"
    router_node_hint: str = "cleanapis"
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
