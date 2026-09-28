# Deploying aihelper.in

Fresh-install example below: Caddy (TLS) -> Python -> SQLite. The live `aihelper.in` site runs Nginx; its current virtual host is [nginx-aihelper.conf](nginx-aihelper.conf). Keep `server_tokens off` in the main Nginx config. When the homepage's JSON-LD block changes, update its SHA-256 hash in the Content Security Policy before deploying the changed page.

## One-time server setup

```bash
# as root, on the VPS
apt update && apt install -y caddy python3-pip curl
curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
apt install -y nodejs

adduser --system --group --home /opt/aihelper www-data || true
mkdir -p /opt/aihelper

# copy the repo up (from your machine):
# rsync -av --exclude .git --exclude aihelper.db* ./ root@YOUR_VPS:/opt/aihelper/

cd /opt/aihelper
# Python 3.11 must be installed first; use the same virtualenv for both services.
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd auth && npm install --omit=dev && npm run migrate && cd ..
chown -R www-data:www-data /opt/aihelper
chmod 700 /opt/aihelper
chmod 600 .env   # secrets: GROQ_API_KEY, RUMIK_API_KEY, PLIVO_*, SMTP_PASS, PUBLIC_BASE_URL, DASHBOARD_PASS, ...

cp deploy/aihelper.service deploy/aihelper-auth.service /etc/systemd/system/
cp deploy/emotion-worker.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now emotion-worker aihelper aihelper-auth

cp deploy/Caddyfile /etc/caddy/Caddyfile
systemctl reload caddy
```

## DNS

Point `aihelper.in` A record at the VPS IP. Caddy gets TLS certificates automatically.

## Voice setup (one-time)

1. In the Plivo console, buy or port the Indian number with Voice and SMS enabled. Create a Voice Application, assign the number to it, and set:
   - Answer URL: `https://aihelper.in/plivo/answer` using `POST`
   - Hangup URL: `https://aihelper.in/plivo/hangup` using `POST`
   Plivo signs these callbacks with the account Auth Token; no shared webhook URL secret is needed.
2. Fill `.env` on the VPS:
   - `PUBLIC_BASE_URL=https://aihelper.in`
   - `PLIVO_AUTH_ID`, `PLIVO_AUTH_TOKEN`, and `PLIVO_PHONE_NUMBER` (see `deploy/plivo.env.example`)
   - `ASSEMBLYAI_API_KEY` (Universal Streaming for live call transcription)
   - `RUMIK_API_KEY` (speech generation)
   - Optional outbound sales-campaign language: `SALES_AGENT_NAME`, `SALES_PROCESS`, `SALES_MECHANISM`, and `SALES_OUTCOME`. These form the transparent value proposition: “We do PROCESS through MECHANISM to OUTCOME.” The agent identifies itself as AI and names the represented business before the pitch. `SALES_IDEAL_CUSTOMER_PROFILE` may contain only authorized, business-relevant fit criteria; do not include age, salary, health, ethnicity, or inferred interests. `SALES_VALUE_EVIDENCE` must contain only supportable claims.
   - `ACTIVE_LISTENING_ENABLED=1` to enable the guarded, occasional “mm-hm” listener acknowledgement; set it to `0` to disable it.
   - `EMOTION_WORKER_URL=http://127.0.0.1:8010` (optional local SenseVoice emotion worker)
   - `SENSEVOICE_DEVICE=cuda:0` (or `cpu`; GPU is recommended)
   - `DASHBOARD_PASS` (required: use a strong unique value; user is `admin`)
   - `BETTER_AUTH_SECRET` and `AUTH_BRIDGE_SECRET` (two different random values, each at least 32 characters)
   - `BETTER_AUTH_URL=https://aihelper.in`
   - `SPIRITUALAI_OWNER_EMAIL=YOUR_EMAIL` (the one verified account allowed to access `/spiritualai` and its private call recordings)
   - Gmail SMTP for verification and password-reset emails:
     - Enable 2-Step Verification for `verify.aihelper@gmail.com`, then create a Google **App Password** (Google Account → Security → App passwords).
     - Set `SMTP_USER=verify.aihelper@gmail.com` and `SMTP_PASS` to that 16-character App Password. Do not use the normal Google account password.
     - Gmail defaults are built in: `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=465`, and `SMTP_SECURE=true`. Optionally set `SMTP_FROM="AI Helper <verify.aihelper@gmail.com>"`.
4. Copy the included Caddyfile, validate it, and reload Caddy, then `systemctl restart aihelper`.
   ```bash
   caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
   systemctl reload caddy
   ```

## Verify

```bash
curl https://aihelper.in/api/health          # {"ok": true, ...}
curl -u admin:YOURPASS https://aihelper.in/api/calls | head -c 400
curl https://aihelper.in/auth/ok
```

Create a company from the owner dashboard and confirm the owner receives a verification email from `verify.aihelper@gmail.com`. Open its link, then sign in at the company URL. Confirm that the user record is marked verified in `auth/better-auth.sqlite`.

For the private calling workspace, sign in as `SPIRITUALAI_OWNER_EMAIL` and open `https://aihelper.in/spiritualai`. Each call announces that it may be recorded, then stores its summary and recording. Confirm recording and consent requirements for every jurisdiction where you call.

Then call the Indian Plivo number and an international Telnyx number from your phone and watch the dashboard. `+91` destinations use Plivo; all other destinations use Telnyx.

For Telnyx, assign the international number to the TeXML application and configure its voice webhook as `https://aihelper.in/telnyx/voice`. Use `deploy/telnyx.env.example` for the API key, account SID, application SID, number, and Ed25519 public key.

## Local emotion worker

Run this beside `aihelper` after `pip3 install -r requirements.txt`:

```bash
SENSEVOICE_DEVICE=cuda:0 EMOTION_WORKER_PORT=8010 python3 emotion_worker.py
```

The main call server sends it an in-memory 8-second PCM window every two seconds. The worker is optional: calls stay live if it is unavailable.

## Notes

- DB lives at /opt/aihelper/aihelper.db (SQLite, WAL). Back it up with `sqlite3 aihelper.db ".backup backup.db"` under load.
- Logs: `journalctl -u aihelper -f`
- The dashboard password prints once at startup if DASHBOARD_PASS is unset.
