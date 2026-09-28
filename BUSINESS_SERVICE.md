# Business credentials, usage and call protection

Implemented in the checkout; configure and verify business accounts before deploying. Existing businesses now fail closed until configured. No customer credentials or subscriptions were created automatically.

Hermes/operator routes require `X-AI-Helper-Secret`. Customer dashboard credentials only control that customer's calls and contacts. Never give customers the operator secret.

## Provider routing — read this first

Operator calls default to Telnyx globally. Explicit tenant `provider` values are honored.
The optional `provider: "auto"` mode preserves dual-channel routing:

| Destination | Provider | Webhook | Stream |
| --- | --- | --- | --- |
| `+91...` (India) | Plivo | `/plivo/answer` | `/plivo/stream` |
| Any other E.164 number | Telnyx | `/telnyx/voice` | `/telnyx/stream` |

Outbound auto routing uses the `to` number. Inbound routing uses the provider webhook configured on that phone number. `call_provider()` is the single routing rule in `server.py`. Carrier permissions and destination availability still apply.

For a business using both channels, configure `provider: "auto"` and supply both Plivo and Telnyx credentials. The Telnyx number is used for international inbound calls; the Plivo number is used for Indian inbound calls. Keep both numbers in E.164 format.

## Configure

Use `POST /internal/ai-helper/businesses/{slug}/service` after creating the company. JSON fields:

| Field | Value |
| --- | --- |
| `provider` | `auto` for Plivo-India/Telnyx-international routing; legacy `twilio`, `plivo`, and `telnyx` are also accepted |
| `keys` | Object containing this business's provider credentials below |
| `plan` | `usd_100`, `inr_10000`, `usd_200`, or `inr_20000` |
| `period` | Unique paid subscription period ID, such as an invoice ID |
| `ends_at` | Actual paid period expiry, integer Unix timestamp in UTC |
| `max_call_seconds` | Default 10,800 (3 hours); allowed 10–10,800 |
| `silence_seconds` | Default 90; allowed 30–300 |
| `max_turns` | Default 0 (no turn cap); allowed 0–500 |
| `paused` | Boolean, default false |
| `call_mode` | `english` (default) or `hindi` |
| `business_knowledge`, `greeting` | Optional business-specific text |

Required keys for `provider: "auto"`: `GROQ_API_KEY`, `RUMIK_API_KEY`, and `ASSEMBLYAI_API_KEY` for English and Hindi, plus `PLIVO_AUTH_ID`, `PLIVO_AUTH_TOKEN`, `PLIVO_PHONE_NUMBER`, `TELNYX_API_KEY`, `TELNYX_ACCOUNT_SID`, `TELNYX_TEXML_APPLICATION_SID`, `TELNYX_PHONE_NUMBER`, and `TELNYX_PUBLIC_KEY`. Optional per-business `OPENROUTER_API_KEY` enables Jev; tenant calls never borrow the operator's Jev key.

Minimal service configuration shape:

```json
{
  "provider": "auto",
  "keys": {
    "GROQ_API_KEY": "...",
    "RUMIK_API_KEY": "...",
    "ASSEMBLYAI_API_KEY": "...",
    "PLIVO_AUTH_ID": "...",
    "PLIVO_AUTH_TOKEN": "...",
    "PLIVO_PHONE_NUMBER": "+919876543210",
    "TELNYX_API_KEY": "...",
    "TELNYX_ACCOUNT_SID": "...",
    "TELNYX_TEXML_APPLICATION_SID": "...",
    "TELNYX_PHONE_NUMBER": "+14155550123",
    "TELNYX_PUBLIC_KEY": "..."
  },
  "plan": "usd_100",
  "period": "invoice-2026-09",
  "ends_at": 1798761600
}
```

Each business must use its own telephony account/subaccount and number. Business calls never fall back to environment credentials. API keys are stored in the private SQLite database (0600 permissions), not returned by the API or dashboard; protect backups too. Static HTTP access to databases, source files, environment files and directory listings is denied.

Configuration updates merge top-level fields; supplying `keys` replaces the full key object. Credentials and billing period cannot change while a call remains active/unreconciled. `{"paused": true}` blocks new calls and stops live streams; provider duration caps remain in place for fallback calls. Clearing a manual pause does not refill an allowance. Renew only after payment with a new `period` and `ends_at`; previous periods and usage are retained. There is no automatic unpaid renewal.

## Call and usage controls

- `POST /internal/ai-helper/businesses/{slug}/call`: `{"to":"+12025550123","context":"Call instructions"}`. Uses business credentials and reserves usage before dialing. Company dashboard calls use the same path internally.
- `GET /internal/ai-helper/businesses/{slug}/usage`: used, reserved and available seconds, pause state and call cutoff reasons. No credentials are returned. The company dashboard also displays minutes.
- `POST /internal/ai-helper/businesses/{slug}/reconcile`: `{"call_sid":"provider-call-id"}`. Fetches a terminal call's actual duration using that business's provider credentials when a callback was missed.

The 1,000-minute plans each allocate 60,000 seconds per paid period. Usage is answered call duration rounded up to the next second, not recording length; carrier invoice rounding can differ. A SQLite write transaction reserves the lesser of the per-call cap, remaining allowance and time until expiry. One active/unreconciled call per business is allowed. Both inbound and outbound calls consume the allowance. Exhausted or expired businesses cannot start another call; other businesses are unaffected. Unlimited plans still enforce per-call protections.

Verified terminal callbacks settle the reservation idempotently. Failed dials with an uncertain outcome retain their allowance and block another attempt. Process restarts do not release reservations. If no provider call ID was bound, an operator must inspect provider records and resolve that uncertain request before service can continue; do not retry blindly or delete its reservation. Known call IDs can use the reconcile endpoint.

## Abuse controls and their limits

Live calls stop on the duration cap, an optional turn cap or sustained lack of recognized caller input. The third identical substantive request receives a warning; the fourth ends the call. Short repeated responses such as “yes” do not trigger this rule. The assistant redirects unrelated or deliberately prolonged requests to the business purpose. These signals limit cost; they do not establish a person's intent. Long monologues without recognized final transcripts can hit the silence limit, so tune that setting to the business's needs.

The fallback speech-gather path also limits duration and turns, warns on repetition, and ends after a second consecutive empty response. Provider-level duration limits survive a lost audio connection or application restart. A separate timer covers live streams even when no media packets arrive. Hangup requests address one specific provider call; they never terminate the whole account.

Provider behavior checked against [Twilio call controls](https://www.twilio.com/docs/voice/api/call-resource), [Plivo call controls](https://docs.plivo.com/docs/voice/api/calls), [Plivo scheduled hangup XML](https://www.plivo.com/docs/voice/xml/routing), and [Plivo signature validation](https://docs.plivo.com/docs/voice/concepts/signature-validation). Provider/network latency and carrier billing remain external; do not promise zero overshoot in billed cost.

## Verification and deployment

Run from the checkout:

```sh
/opt/aihelper/.venv/bin/python -m unittest test_tenant_limits test_hermes_api test_sales_policy test_turn_manager test_call_metrics
```

Checks use temporary databases and mocked providers: concurrent admission at the allowance boundary, repeat callbacks, renewals, expiry, unlimited-plan limits, async/thread credential isolation, wrong-business signatures, Plivo request/call ID mapping, stream timeout without media, repeat-request warnings and private-file blocking.

Deployment must include `tenant_limits.py` alongside `server.py`, the existing `sales_policy.py` and sales skill changes. Back up the live database before startup migrations. Supply real per-business keys and paid period details through the authenticated API, then perform a controlled live call on each configured provider to verify duration reporting, hangup and recording behavior. No live calls or production restart were performed during this change.
