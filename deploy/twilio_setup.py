#!/usr/bin/env python3
"""One-shot Twilio setup: verify credentials, point your number at this server, show balance.
Reads the same .env as server.py. Run after filling TWILIO_* and PUBLIC_BASE_URL:
    python3 deploy/twilio_setup.py
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
env_file = ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip().removeprefix("export "), value.strip().strip("\"'"))

SID = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
NUMBER = os.environ.get("TWILIO_PHONE_NUMBER", "").strip()
BASE = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")

missing = [name for name, value in [
    ("TWILIO_ACCOUNT_SID", SID), ("TWILIO_AUTH_TOKEN", TOKEN),
    ("TWILIO_PHONE_NUMBER", NUMBER), ("PUBLIC_BASE_URL", BASE)] if not value]
if missing:
    sys.exit(f"First fill in .env: {', '.join(missing)}")

AUTH = "Basic " + base64.b64encode(f"{SID}:{TOKEN}".encode()).decode()


def api(path, data=None):
    request = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{SID}/{path}",
        data=urllib.parse.urlencode(data).encode() if data else None,
        headers={"Authorization": AUTH})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode()[:200]
        raise SystemExit(f"Twilio API {error.code} on {path}: {detail}")


account = api(f"{SID}.json")
print(f"Account: {account['friendly_name']} ({account['status']})")
balance = api("Balance.json")
print(f"Balance: {balance['balance']} {balance['currency']}")

numbers = api("IncomingPhoneNumbers.json?PageSize=50")["incoming_phone_numbers"]
mine = next((n for n in numbers if n["phone_number"] == NUMBER), None)
if not mine:
    owned = ", ".join(n["phone_number"] for n in numbers) or "(none)"
    raise SystemExit(f"{NUMBER} is not on this account. Owned numbers: {owned}")

voice_url = f"{BASE}/twilio/voice"
current = mine.get("voice_url", "")
api(f"IncomingPhoneNumbers/{mine['sid']}.json", {"VoiceUrl": voice_url, "VoiceMethod": "POST"})
print(f"{NUMBER}: VoiceUrl {'already' if current == voice_url else 'updated'} -> {voice_url}")

print("\nReady. Trial accounts can only call numbers verified under Phone Numbers > Verified Caller IDs.")
