# cleanapis-pipeline

Create CleanAPIs accounts with Google OAuth, harvest their `cc_…` API keys, attach
every key to [9Router](https://www.npmjs.com/package/9router) as one round-robin
pool, and verify the whole chain.

```
accounts.txt  (email|password)
    │
    ├─ Camoufox ──> cleanapis.com/?ref=…  ──> "Get Started" ──> "Sign up with Google"
    │                                          │
    │                                          └─ Google identifier → password → consent
    │
    ├─ dashboard/api-keys ──> Create API Key ──> cc_… (shown once)
    │
    ├─ result.txt  (email|password|apikey)          ← fsynced per account
    │
    └─ 9Router POST /api/providers ──> connection pool ──> GET /v1/models
                                                            POST /v1/chat/completions
```

## Why this shape

Three dead ends came first. They are documented because they cost hours and will
cost them again.

### `/register` is unwinnable — do not try

The password registration form carries a Cloudflare Turnstile widget
(sitekey `0x4AAAAAAFB4Bo8skMq7_4qB`) as `<input name="cf-turnstile-response">`,
**and the backend rejects every token, including genuine ones.**

| Attempt | Token obtained | Result |
|---|---|---|
| `requests` + CSRF `_token`, no Turnstile field | — | `419 Page Expired` |
| `requests` + garbage token | garbage | `Security verification failed` |
| Camoufox headless, `humanize=True` (Firefox 156) | 645 chars, genuine | `Security verification failed` |
| Camoufox headful | 645 chars, genuine | `Security verification failed` |
| Camoufox + `geoip=True` (locale/timezone matched to IP) | 645 chars, genuine | `Security verification failed` |
| Playwright Chromium, `channel="chrome"`, headless **and** headful | 0 chars | `Security verification failed` |
| **Real Chrome 155, own profile dir, attached over CDP** | **709 chars, genuine** | `Security verification failed` |
| apex vs `www.` hostname | both | fail both |
| real Chrome + per-key typing delays + mouse-clicked submit | 645 chars | fail |
| same email resubmitted after a "successful" attempt | — | no `already been taken` → **no account was ever created** |

The token *is* transmitted — captured POST body from a real browser:

```
_token=…&name=…&email=…&phone=…&password=…&password_confirmation=…
&cf-turnstile-response=1.3rMk…yFQdLzVQDCFD0T5FYsDDqSaUlR8Mxh-…   (645 chars)
```

Response: `302 → /register` with flash
`Security verification failed. Please complete the CAPTCHA.`
Garbage token and genuine token produce byte-identical output, and `/login` fails
the same way — so the Turnstile secret is misconfigured site-wide, not
register-specific. Nothing client-side can pass it.

Tell-tale that this is *not* ordinary bot detection: the widget auto-issues a
token in ~5 s without showing a challenge (Cloudflare is satisfied), and
`navigator.webdriver === false` with a real profile and real TLS. The rejection
happens downstream, in the application.

### Google's sign-in page has three traps

1. **Decoy password field.** The v3 signin form ships an invisible
   `<input type="password" name="hiddenPassword">` on the *identifier* page.
   Detecting the password step with `input[type=password]` therefore reports
   "password page" immediately and the flow stalls. Require `is_visible()` **and**
   exclude `hiddenPassword`. (`pipeline/google_oauth.py::real_password_visible`)
2. **Localised button text.** Google renders the surrounding UI in the account's
   language, so "Next" arrives as `berikutnya`, `siguiente`, `weiter`, `Далее`.
   `pipeline/google_oauth.py::click_primary` scores visible buttons by rendered
   background colour — the primary action is the only large saturated control —
   and filters a multilingual denylist of cancel/purchase words
   (`batal`, `nanti`, `kembali`, `$`, `top up`, `upgrade`, …).
3. **Account chooser.** With an existing Google session the flow starts at
   `accountchooser` and no form appears. Match the row by full email, else by
   local-part keywords, and never click a row showing a different `@` address.

Use a **fresh browser per account** — a reused context hands the flow to the
previous Google session.

### 9Router: use its REST API, not the dashboard

One key through the UI is a modal dance of ~30 s; the API does it in one POST.
Auth needs no password — 9Router derives a local trust token any process on the
machine can reproduce:

```
token  = sha256(machine_id + "9r-cli-auth" + cli_secret)[:16]
header = x-9r-cli-token: <token>
```

`machine_id` = `<data_dir>/machine-id`, `cli_secret` = `<data_dir>/auth/cli-secret`
(Windows data dir: `%APPDATA%\9router`). Same derivation as the shipped CLI,
`9router/src/cli/api/client.js`.

| Call | Purpose |
|---|---|
| `GET /api/providers` | list connections → `{connections:[…]}` |
| `POST /api/providers` | add one: `{provider, name, apiKey, baseUrl, defaultModel}` → `201` |
| `DELETE /api/providers/{id}` | remove |
| `POST /api/providers/{id}/test` | live validity |
| `GET /api/provider-nodes` | provider definitions |
| `GET /v1/models`, `POST /v1/chat/completions` | through the gateway |

Traps that cost time:

* `provider` in the POST body is the **provider node id**, not a display name.
  Get it from `GET /api/provider-nodes`, or read the `providerNodes` table in
  `<data_dir>/db/data.sqlite`.
* The raw `cli-secret` contents are **not** the token. Hash them.
* A connection test answering `{"valid": true}` is **healthy**. Watching for
  `"success": true` reports every working connection as broken.
* `GET /v1/models` prefixes custom-node models with the node's prefix
  (`cleanapi/gpt-5.5`), and the bare prefix is a usable alias.
* Responses may carry a trailing `data: [DONE]` even with `stream: false` —
  strip it before `json.loads`.
* A `200` with empty content and non-zero `reasoning_tokens` is **not** a
  failure; the model spent the budget thinking. Raise `max_tokens` for text, or
  judge by HTTP status.

## Install

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m camoufox fetch                          # download the browser (~one time)
```

`camoufox[geoip]` is required — without the extra, `geoip=True` raises
`NotInstalledGeoIPExtra` and the harvest dies mid-batch.

## Use

```bash
cp accounts.example.txt accounts.txt     # then fill in email|password

python cleanapis_pipeline.py status      # who is done, who is not — no side effects
python cleanapis_pipeline.py run         # harvest → attach → verify
```

Stage by stage:

```bash
python cleanapis_pipeline.py harvest              # keys only → result.txt
python cleanapis_pipeline.py connect              # result.txt → 9Router
python cleanapis_pipeline.py verify               # direct + through the router
python cleanapis_pipeline.py connect --replace    # rotate: delete then re-add
python cleanapis_pipeline.py run --only a@b.com,c@d.com
python cleanapis_pipeline.py harvest --show       # watch the browser work
```

Around 60–90 s per account. Results are written after every account, and already
harvested emails are skipped, so stopping and re-running is safe.

Exit codes: `1` harvest failures, `2` connect failure, `4` verification failure.

## Files

| File | Role |
|---|---|
| `accounts.txt` | input — `email\|password`, gitignored |
| `result.txt` | output — `email\|password\|apikey`, gitignored, fsynced per account |
| `pipeline/config.py` | all tunables in one dataclass |
| `pipeline/accounts.py` | account/result parsing, cras-safe upsert |
| `pipeline/google_oauth.py` | Google traps: decoy field, colour-clicked buttons, chooser |
| `pipeline/cleanapis.py` | referral landing → OAuth → key creation + banner scrape |
| `pipeline/harvest.py` | per-account orchestration, fresh browser each time |
| `pipeline/y9router.py` | trust-token derivation, REST attach, node lookup |
| `pipeline/verify.py` | direct + gateway verification, routing evidence |

## Requirements

* Python 3.10+
* Camoufox (anti-detect Firefox) + the `geoip` extra
* 9Router reachable — default `http://localhost:20128`, override with `ROUTER_URL`
* A Google-compatible account per row in `accounts.txt`

## Caveats

* `result.txt` stores passwords in plain text. Keep it out of git (it is in
  `.gitignore` by default), and prefer accounts you are willing to rotate.
* Google may still answer with a captcha or second factor for some accounts. That
  account fails and the batch continues; retry it later.
* The pipeline automates a sign-up flow. Use it only where you are entitled to
  the accounts it signs in with, and mind the provider's terms of service.
* Verified on Windows 11 with Chrome/Firefox-class browsers; the Linux/macOS
  paths follow the same APIs but are untested here.

## Credit

The OAuth-over-password-form approach and the colour-based Google button
detection follow the flow demonstrated by
[`bitsmith826/cleanapis-auto`](https://github.com/bitsmith826/cleanapis-auto).
This project is an independent reimplementation: packaged pipeline, the
`/register` failure analysis, the REST attach stage for 9Router, and the
verification stage are original to it.

## License

MIT — see [LICENSE](LICENSE).
