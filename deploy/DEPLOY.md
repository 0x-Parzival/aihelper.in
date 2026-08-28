# Deploying aihelper.in

Target: any $5-10/mo VPS (Ubuntu 22.04+). Stack: Caddy (TLS) -> python3 server.py -> SQLite.

## One-time server setup

```bash
# as root, on the VPS
apt update && apt install -y caddy python3-pip

adduser --system --group --home /opt/aihelper www-data || true
mkdir -p /opt/aihelper

# copy the repo up (from your machine):
# rsync -av --exclude .git --exclude aihelper.db* ./ root@YOUR_VPS:/opt/aihelper/

cd /opt/aihelper
pip3 install -r requirements.txt
chown -R www-data:www-data /opt/aihelper
chmod 700 /opt/aihelper
chmod 600 .env   # secrets: GROQ_API_KEY, RUMIK_API_KEY, TWILIO_*, PUBLIC_BASE_URL, DASHBOARD_PASS, ...

cp deploy/aihelper.service /etc/systemd/system/
cp deploy/emotion-worker.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now emotion-worker aihelper

cp deploy/Caddyfile /etc/caddy/Caddyfile
systemctl reload caddy
```

## DNS

Point `aihelper.in` A record at the VPS IP. Caddy gets TLS certificates automatically.

## Twilio console (one-time)

1. Phone Numbers -> your number:
   - "A call comes in" -> Webhook: `https://aihelper.in/twilio/voice` (HTTP POST)
2. Status Callback is already sent by the outbound API (`StatusCallback` param), no global setting needed.
3. Fill `.env` on the VPS:
   - `PUBLIC_BASE_URL=https://aihelper.in`
   - `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`
   - `OWNER_PHONE_NUMBER` (where summaries SMS to; also editable in dashboard)
   - `ASSEMBLYAI_API_KEY` (Universal Streaming for live call transcription)
   - `RUMIK_API_KEY` (speech generation)
   - `EMOTION_WORKER_URL=http://127.0.0.1:8010` (optional local SenseVoice emotion worker)
   - `SENSEVOICE_DEVICE=cuda:0` (or `cpu`; GPU is recommended)
   - `DASHBOARD_PASS` (required: use a strong unique value; user is `admin`)
4. Wire the webhook automatically (verifies creds, checks balance, points your number at this server):
   ```bash
   python3 deploy/twilio_setup.py
   ```
5. Copy the included Caddyfile, validate it, and reload Caddy, then `systemctl restart aihelper`. The `/twilio/stream` WebSocket route must reach port 8001.
   ```bash
   caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
   systemctl reload caddy
   ```

## Verify

```bash
curl https://aihelper.in/api/health          # {"ok": true, ...}
curl -u admin:YOURPASS https://aihelper.in/api/calls | head -c 400
```

Then call your Twilio number from your phone and watch the dashboard. The first greeting and every reply should play on the same live Media Stream, rather than waiting for a Twilio `<Gather>` turn.

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
