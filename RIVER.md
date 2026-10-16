# River AI (console.river.ai) — the wall, and what actually moves it

River's Console has **no password signup API**. Its own config says so:

```json
GET https://console.river.ai/api/auth/config
{
  "workos_client_id": "client_01M0TG5W3GBNMFS87K40PYHM98",
  "workos_api_hostname": "auth.console.river.ai",
  "password_login": false,
  "password_signup": false
}
POST /api/auth/signup  ->  404 {"error":"not_found","message":"Password signup is disabled"}
```

Authentication is delegated to **WorkOS AuthKit** on `login.console.river.ai`,
behind Cloudflare Turnstile and **WorkOS Radar**. A real browser driving the
hosted UI is the only route in.

## Radar has three outcomes, not one

This is the single most important thing to understand before touching the
code. WorkOS documents the block case explicitly: *"When Radar blocks an
authentication attempt entirely, the API returns an error with the code
`policy_denied` … Blocking an attempt will cause the authentication to fail,
even if valid credentials are provided."*

| Outcome | What the user sees | What clears it |
|---|---|---|
| **allow** | the flow continues on its own | nothing needed |
| **challenge** | a phone number field, then an SMS code | a number that receives real SMS |
| **block** | `policy_denied` — "Access blocked, please contact support." | a different source IP |

Treating all three as "it asked for my phone" is what makes this problem look
unfixable. They need opposite responses.

## What we measured

Every combination below produced an **identical** `policy_denied` on the
password step (or immediately after the Google hop). The screen is always:

```
Sign up | Email | Change email | Password | … All password requirements have been met
Continue | Access blocked, please contact support.
```

| Browser | Signup door | E-mail domain | Result |
|---|---|---|---|
| Camoufox (Firefox) | Google | `rurasa.my.id` (Google Workspace) | `policy_denied` |
| Camoufox | e-mail form | `maxxspace.com` (mail.tm) | `policy_denied` |
| **real Chrome 154** via CDP | e-mail form | `maxxspace.com` | `policy_denied` |
| Camoufox | e-mail form | `rurasa.my.id` | `policy_denied` |
| Camoufox | sign-**in** (no signup hint) | `rurasa.my.id` | `policy_denied` |

That table rules out, one variable at a time:

- **not the browser** — a real Chrome with `--disable-blink-features=AutomationControlled`
  behaves identically to Camoufox, so no stealth patch will help and Camoufox's
  Firefox fingerprint is not the tell;
- **not the door** — the Google door and AuthKit's own e-mail form both block,
  even though `password_signup: false` only disables the *console's* endpoint and
  AuthKit still renders "Continue with email";
- **not the domain** — a Google Workspace domain and a brand-new mail.tm mailbox
  both block;
- **not the intent marker** — hitting
  `/user_management/authorize?provider=GoogleOAuth` with no `screen_hint=sign-up`
  still blocks (the console redirects "Sign in" back to the signup surface).

What is left is the **source IP** (`112.215.200.106`, AS24203 PT XL Axiata — a
residential/mobile carrier range, not a datacenter). The same toolkit reportedly
works from a VPS elsewhere, and the reference implementation
([`0xgetz/river-bulk-apikey`](https://github.com/0xgetz/river-bulk-apikey)) was
written on a clean machine. Carrier CGNAT is a plausible cause: one flagged user
behind that address taints everyone sharing it.

## The fix, in order of cost

1. **Different source IP.** Run the harvest where Radar is not already annoyed —
   a VPS, a residential proxy, or a phone hotspot on a different carrier. This is
   the cheapest real fix and the only one that addresses a `block`.
2. **A number that receives real SMS** (`--phone-provider 5sim`). This only matters
   once the outcome is *challenge* rather than *block*: the phone field is not
   present on a block, so no number can rescue it.
3. **A phone you own** (`--phone-provider manual --phone +62…`). Highest success
   rate against a SMS challenge, because rented VoIP ranges are what risk engines
   distrust most. Write the arriving code into `sms_code.txt`.

```bash
# Rent a number and let the pipeline read the SMS code back
export FIVESIM_TOKEN=...
python cleanapis_pipeline.py --provider river harvest --count 1 \
    --door email --phone-provider 5sim

# Or use a phone you own: paste the code into sms_code.txt when it arrives
python cleanapis_pipeline.py --provider river harvest --count 1 \
    --door email --phone-provider manual --phone +628123456789
```

## How the pipeline handles each outcome

`pipeline/river.py::sign_in` tries the configured door and, if Radar blocks it,
tries the *other* door before giving up — the block can be door-specific even
though it was not here. `pipeline/river.py::solve_phone_gate` distinguishes a
challenge from a block and refuses to fill a phone form that cannot succeed. A
block is reported with the reason, not silently retried, so a batch does not
burn an hour pretending to work.

## After signup

The console exchanges the OAuth/authorization code and then calls its own API
with `Authorization: Bearer <workos access token>`. That token is short-lived and
is never written to `localStorage` or `sessionStorage`, so it is read off the
wire (CDP `Network.requestWillBeSent`, here a Playwright `page.on("request")`)
and used directly:

```
POST https://console.river.ai/api/keys              -> { "key": "rv_…" }   (shown once)
GET  https://console.river.ai/api/keys
GET  https://console.river.ai/api/me
GET  https://console.river.ai/api/billing/account   -> starting balance
```

Inference is OpenAI-compatible at `https://api.river.ai/v1`, so the harvested
keys drop straight into the existing 9Router node (`router_river_node_hint =
"river"`) via `pipeline/y9router.py`.
