"""9Router attachment.

Why talk to the REST API instead of driving the browser UI: adding one key
through the dashboard is a modal dance of ~30 s, and the API does it in one POST.
The API needs no password -- 9Router derives a local trust token that any process
on the machine can reproduce:

    token  = sha256(machine_id + "9r-cli-auth" + cli_secret)[:16]
    header = x-9r-cli-token: <token>

`machine_id` is <data_dir>/machine-id and `cli_secret` is <data_dir>/auth/cli-secret.
Same derivation as the shipped CLI (9router/src/cli/api/client.js).

Gotchas that cost real time:
  * `provider` in the POST body is the provider NODE id, not a display name.
  * The raw cli-secret contents are not the token; they must be hashed.
  * A connection test that answers {"valid": true} is HEALTHY. Watching for
    "success": true reports every working connection as broken.
  * /v1/models prefixes custom-node models with the node's prefix
    (`cleanapi/gpt-5.5`), and responses may carry a trailing `data: [DONE]`
    even when stream=false -- strip it before json.loads.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import requests

from .accounts import Account, load_results
from .config import Config


def cli_token(cfg: Config) -> str:
    """Derive the local trust token that 9Router's REST API accepts."""
    data_dir = Path(cfg.router_data_dir())
    machine_id = (data_dir / "machine-id").read_text(encoding="utf-8").strip()
    cli_secret = (data_dir / "auth" / "cli-secret").read_text(encoding="utf-8").strip()
    digest = hashlib.sha256(
        (machine_id + "9r-cli-auth" + cli_secret).encode()
    ).hexdigest()
    return digest[:16]


def session(cfg: Config) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Content-Type": "application/json",
        "x-9r-cli-token": cli_token(cfg),
    })
    return s


def find_node_id(cfg: Config, s: requests.Session | None = None) -> str | None:
    """Provider node id whose definition points at the target base URL.

    Prefers the API; falls back to reading the SQLite file, which works even when
    the node list endpoint changes shape.
    """
    hint = cfg.router_node_hint.lower()
    s = s or session(cfg)
    try:
        r = s.get(f"{cfg.router_url}/api/provider-nodes", timeout=30)
        if r.status_code == 200:
            payload = r.json() or {}
            for node in payload.get("nodes", []) or payload.get("data", {}).get("nodes", []):
                if hint in json.dumps(node.get("data") or {}).lower():
                    return node.get("id")
    except Exception:
        pass

    db = Path(cfg.router_data_dir()) / "db" / "data.sqlite"
    if db.exists():
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        for node_id, data in con.execute(
                "select id, data from providerNodes where type='openai-compatible'"):
            if hint in (data or "").lower():
                return node_id
    return None


def list_connections(cfg: Config) -> list[dict]:
    s = session(cfg)
    r = s.get(f"{cfg.router_url}/api/providers", timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"9Router returned {r.status_code} for /api/providers")
    body = r.json()
    return body.get("connections") or body.get("data", {}).get("connections") or []


def test_connection(cfg: Config, conn_id: str) -> tuple[bool, dict]:
    """Live validity check. `{\"valid\": true}` means healthy."""
    s = session(cfg)
    r = s.post(f"{cfg.router_url}/api/providers/{conn_id}/test", timeout=120)
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text[:200]}
    healthy = r.status_code == 200 and body.get("valid") is True
    return healthy, body


def attach_all(cfg: Config, results_file: Path, replace: bool = False) -> tuple[int, int]:
    """Attach every key in result.txt. Returns (added, skipped); raises on failure.

    Idempotent: a name already present is skipped unless `replace`, in which case
    the old connection is deleted first so a rotated key takes over the slot
    instead of creating a duplicate.
    """
    rows = load_results(results_file)
    if not rows:
        print(f"[connect] no keys in {results_file}")
        return 0, 0

    s = session(cfg)
    try:
        existing = {c.get("name"): c for c in list_connections(cfg)}
    except RuntimeError as exc:
        print(f"[connect] cannot reach 9Router at {cfg.router_url}: {exc}")
        raise

    node_id = find_node_id(cfg, s)
    if not node_id:
        raise RuntimeError(
            "no matching provider node in 9Router — create the OpenAI-compatible "
            f"provider (base URL containing '{cfg.router_node_hint}') once in the UI"
        )

    print(f"[connect] node={node_id}  {len(rows)} key(s)  "
          f"{len(existing)} existing connection(s)", flush=True)

    added = skipped = 0
    for i, acct in enumerate(rows.values(), 1):
        if not acct.api_key:
            continue
        if acct.email in existing and not replace:
            skipped += 1
            continue
        if acct.email in existing and replace:
            cid = existing[acct.email].get("id")
            d = s.delete(f"{cfg.router_url}/api/providers/{cid}", timeout=60)
            if d.status_code not in (200, 204):
                print(f"  [{i}/{len(rows)}] {acct.email}: delete failed {d.status_code}",
                      flush=True)
                continue

        payload = {
            "provider": node_id,
            "name": acct.email,
            "apiKey": acct.api_key,
            "baseUrl": cfg.base_url + "/v1",
            "defaultModel": cfg.router_provider_default_model,
        }
        p = s.post(f"{cfg.router_url}/api/providers", timeout=90, data=json.dumps(payload))
        if p.status_code in (200, 201):
            added += 1
            print(f"  [{i}/{len(rows)}] {acct.email}: attached", flush=True)
        else:
            print(f"  [{i}/{len(rows)}] {acct.email}: FAILED {p.status_code} "
                  f"{p.text[:140]}", flush=True)

    print(f"[connect] added={added} skipped={skipped}", flush=True)
    return added, skipped
