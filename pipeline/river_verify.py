"""River verification: prove each `rv_` key works, directly and via 9Router.

Level 1 — direct: `GET /v1/models` then a tiny completion against
`https://api.river.ai/v1`. Proves the key is live and the account has credit.

Level 2 — through the router: ask 9Router for a model under the River node's
prefix and confirm text comes back. Proves the whole chain.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import requests

from .accounts import load_results
from .config import Config


def verify_direct(cfg: Config, results_file: Path,
                  max_keys: int | None = None) -> list[dict]:
    rows = [a for a in load_results(results_file).values() if a.api_key]
    if max_keys:
        rows = rows[:max_keys]
    out: list[dict] = []
    for i, acct in enumerate(rows, 1):
        rec = {"email": acct.email, "key": acct.api_key,
               "models_status": None, "models": 0, "chat_status": None}
        try:
            r = requests.get(f"{cfg.river_api_base}/models",
                             headers={"Authorization": f"Bearer {acct.api_key}"},
                             timeout=45)
            rec["models_status"] = r.status_code
            if r.ok:
                data = r.json().get("data", [])
                rec["models"] = len(data)
                rec["model_ids"] = [m.get("id") for m in data][:6]
                model = rec["model_ids"][0] if rec["model_ids"] else None
                if model:
                    c = requests.post(
                        f"{cfg.river_api_base}/chat/completions",
                        headers={"Authorization": f"Bearer {acct.api_key}",
                                 "Content-Type": "application/json"},
                        json={"model": model, "max_tokens": 300,
                              "messages": [{"role": "user",
                                            "content": "Reply with exactly: PING"}]},
                        timeout=150,
                    )
                    rec["chat_status"] = c.status_code
                    if c.ok:
                        rec["usage"] = c.json().get("usage", {})
        except Exception as exc:
            rec["error"] = f"{type(exc).__name__}: {exc}"
        ok = rec["models_status"] == 200 and rec["chat_status"] == 200
        print(f"  {i:3}. {'OK' if ok else 'FAIL':4} {acct.email:34} "
              f"models={rec['models_status']}/{rec['models']:3} "
              f"chat={rec['chat_status']}", flush=True)
        out.append(rec)
    good = sum(1 for r in out if r["models_status"] == 200 and r["chat_status"] == 200)
    print(f"\n[verify:river] direct: {good}/{len(out)} keys live")
    return out


def verify_through_router(cfg: Config, model_prefix: str = "river/") -> bool:
    """Ask 9Router for a completion under the River node's prefix."""
    db = Path(cfg.router_data_dir()) / "db" / "data.sqlite"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    row = con.execute("select key from apiKeys where isActive=1 limit 1").fetchone()
    if not row:
        print("[verify:river] no active gateway key in 9Router")
        return False
    gateway_key = row[0]

    r = requests.get(f"{cfg.router_url}/v1/models",
                     headers={"Authorization": f"Bearer {gateway_key}"}, timeout=60)
    if not r.ok:
        print(f"[verify:river] gateway /v1/models -> {r.status_code}")
        return False
    ids = [m["id"] for m in r.json().get("data", [])]
    mine = [i for i in ids if i.lower().startswith(model_prefix.lower())]
    print(f"[verify:river] gateway: {len(ids)} models, {len(mine)} under {model_prefix}*")
    if not mine:
        return False

    for model in mine:
        resp = requests.post(
            f"{cfg.router_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {gateway_key}",
                     "Content-Type": "application/json"},
            json={"model": model, "max_tokens": 700, "stream": False,
                  "messages": [{"role": "user",
                                "content": "Reply with exactly this and nothing else: RIVER_OK"}]},
            timeout=200,
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

    print("[verify:river] gateway answered but no model returned text")
    return False


def routing_evidence(cfg: Config, limit: int = 8) -> None:
    """Show which connections actually served recent traffic."""
    try:
        from .y9router import list_connections
        by_id = {c["id"]: c.get("name") for c in list_connections(cfg)}
    except Exception:
        by_id = {}

    db = Path(cfg.router_data_dir()) / "db" / "data.sqlite"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    print("\n[verify:river] recent routed requests:")
    for row in con.execute("select timestamp, model, connectionId, status from "
                           "usageHistory order by id desc limit ?", (limit,)):
        name = by_id.get(row["connectionId"], "(unknown)")
        print(f"  {row['timestamp']}  {row['model'][:30]:30}  "
              f"{str(row['connectionId'])[:8]}  -> {name}  [{row['status']}]")


def run_verify_river(cfg: Config, results_file: Path, through_router: bool = True,
                     max_keys: int | None = None) -> bool:
    direct = verify_direct(cfg, results_file, max_keys=max_keys)
    ok = bool(direct) and all(
        r["models_status"] == 200 and r["chat_status"] == 200 for r in direct
    )
    if through_router:
        ok = verify_through_router(cfg) and ok
        routing_evidence(cfg)
    return ok
