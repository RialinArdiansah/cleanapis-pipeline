"""Verification stage: prove the keys actually work, at two levels.

Level 1 -- direct: hit CleanAPIs with each key. Proves the key is live and the
account has quota.

Level 2 -- through the router: POST a completion to 9Router and confirm it is
routed to one of the attached connections. Proves the whole chain, which is the
only claim worth making.

A 200 with an empty message and non-zero `reasoning_tokens` is NOT a failure --
reasoning models can spend the whole max_tokens budget thinking. Judge by HTTP
status, and raise max_tokens when you want visible text back.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from .accounts import load_results
from .config import Config


def verify_direct(cfg: Config, results_file: Path) -> list[dict]:
    """GET /v1/models then POST /v1/chat/completions per key."""
    rows = [a for a in load_results(results_file).values() if a.api_key]
    out: list[dict] = []
    for i, acct in enumerate(rows, 1):
        rec = {"email": acct.email, "key": acct.api_key,
               "models_status": None, "models": 0, "chat_status": None}
        try:
            r = requests.get(f"{cfg.base_url}/v1/models",
                             headers={"Authorization": f"Bearer {acct.api_key}"},
                             timeout=45)
            rec["models_status"] = r.status_code
            if r.ok:
                rec["models"] = len(r.json().get("data", []))
                c = requests.post(
                    f"{cfg.base_url}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {acct.api_key}",
                             "Content-Type": "application/json"},
                    json={"model": "claude-opus-4.8", "max_tokens": 300,
                          "messages": [{"role": "user",
                                        "content": "Reply with exactly: PING"}]},
                    timeout=120,
                )
                rec["chat_status"] = c.status_code
                if c.ok:
                    rec["usage"] = c.json().get("usage", {})
        except Exception as exc:
            rec["error"] = f"{type(exc).__name__}: {exc}"
        verdict = "OK" if rec["models_status"] == 200 and rec["chat_status"] == 200 else "FAIL"
        print(f"  {i:3}. {verdict:4} {acct.email:34} models={rec['models_status']}"
              f"/{rec['models']:3} chat={rec['chat_status']}", flush=True)
        out.append(rec)
    good = sum(1 for r in out if r["models_status"] == 200 and r["chat_status"] == 200)
    print(f"\n[verify] direct: {good}/{len(out)} keys live")
    return out


def verify_through_router(cfg: Config, model_prefix: str = "cleanapi/") -> bool:
    """Ask 9Router for a completion under the CleanAPIs node's prefix."""
    from .y9router import cli_token, list_connections

    s = requests.Session()
    s.headers.update({"x-9r-cli-token": cli_token(cfg)})

    import sqlite3
    db = Path(cfg.router_data_dir()) / "db" / "data.sqlite"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    row = con.execute("select key from apiKeys where isActive=1 limit 1").fetchone()
    if not row:
        print("[verify] no active gateway key in 9Router")
        return False
    gateway_key = row[0]

    r = requests.get(f"{cfg.router_url}/v1/models",
                     headers={"Authorization": f"Bearer {gateway_key}"}, timeout=60)
    if not r.ok:
        print(f"[verify] gateway /v1/models -> {r.status_code}")
        return False
    ids = [m["id"] for m in r.json().get("data", [])]
    mine = [i for i in ids if i.lower().startswith(model_prefix.lower())]
    print(f"[verify] gateway: {len(ids)} models, {len(mine)} under {model_prefix}*")
    if not mine:
        return False

    for model in mine:
        resp = requests.post(
            f"{cfg.router_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {gateway_key}",
                     "Content-Type": "application/json"},
            json={"model": model, "max_tokens": 700, "stream": False,
                  "messages": [{"role": "user",
                                "content": "Reply with exactly this and nothing else: ROUTER_OK"}]},
            timeout=180,
        )
        text = resp.text.replace("data: [DONE]", "").strip()
        try:
            body = json.loads(text)
        except Exception:
            print(f"  {model}: unparsable ({resp.status_code}) {text[:120]}")
            continue
        if "error" in body:
            print(f"  {model}: error {json.dumps(body['error'])[:120]}")
            continue
        msg = (body.get("choices") or [{}])[0].get("message") or {}
        content = (msg.get("content") or "").strip()
        print(f"  {model}: HTTP {resp.status_code} routed={body.get('model')} "
              f"content={content[:40]!r} usage={body.get('usage', {}).get('total_tokens')}")
        if content:
            return True

    print("[verify] gateway answered but no model returned text "
          "(reasoning models may need a larger max_tokens)")
    return False


def routing_evidence(cfg: Config, limit: int = 8) -> None:
    """Show which connections actually served recent traffic."""
    from .y9router import cli_token, list_connections

    try:
        by_id = {c["id"]: c.get("name") for c in list_connections(cfg)}
    except Exception:
        by_id = {}

    import sqlite3
    db = Path(cfg.router_data_dir()) / "db" / "data.sqlite"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    print("\n[verify] recent routed requests:")
    for row in con.execute("select timestamp, model, connectionId, status from "
                           "usageHistory order by id desc limit ?", (limit,)):
        name = by_id.get(row["connectionId"], "(unknown)")
        print(f"  {row['timestamp']}  {row['model'][:26]:26}  "
              f"{str(row['connectionId'])[:8]}  -> {name}  [{row['status']}]")


def run_verify(cfg: Config, results_file: Path, through_router: bool = True) -> bool:
    direct = verify_direct(cfg, results_file)
    ok = bool(direct) and all(
        r["models_status"] == 200 and r["chat_status"] == 200 for r in direct
    )
    if through_router:
        ok = verify_through_router(cfg) and ok
        routing_evidence(cfg)
    return ok
