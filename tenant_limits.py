"""Business credential scope and durable, prepaid call reservations (seconds)."""
from contextvars import ContextVar
import json
import math
import re
import time

CURRENT = ContextVar("business", default=None)
RESERVATION = ContextVar("reservation", default=None)
KEYS = frozenset({"OPENROUTER_API_KEY", "GROQ_API_KEY", "RUMIK_API_KEY", "ASSEMBLYAI_API_KEY", "SARVAM_API_KEY", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER", "PLIVO_AUTH_ID", "PLIVO_AUTH_TOKEN", "PLIVO_PHONE_NUMBER", "TELNYX_API_KEY", "TELNYX_ACCOUNT_SID", "TELNYX_TEXML_APPLICATION_SID", "TELNYX_PHONE_NUMBER", "TELNYX_PUBLIC_KEY"})
PLANS = {"usd_100": 60_000, "inr_10000": 60_000, "usd_200": None, "inr_20000": None}


def init(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS business_service (
            slug TEXT PRIMARY KEY, config TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS business_periods (
            slug TEXT NOT NULL, period TEXT NOT NULL, ends_at INTEGER NOT NULL,
            allowance INTEGER, PRIMARY KEY(slug, period)
        );
        CREATE TABLE IF NOT EXISTS call_reservations (
            id TEXT PRIMARY KEY, slug TEXT NOT NULL, period TEXT NOT NULL,
            sid TEXT UNIQUE, seconds INTEGER NOT NULL, charged INTEGER,
            answered_at REAL, reason TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS reservation_period ON call_reservations(slug, period);
    """)


def configure(conn, slug, data):
    if not isinstance(data, dict) or not conn.execute("SELECT 1 FROM companies WHERE slug=?", (slug,)).fetchone():
        raise ValueError("Unknown business or invalid configuration")
    row = conn.execute("SELECT config FROM business_service WHERE slug=?", (slug,)).fetchone()
    config = json.loads(row[0]) if row else {}
    if row and any(data.get(key, config.get(key)) != config.get(key) for key in ("keys", "provider", "period", "plan", "ends_at")) and conn.execute("SELECT 1 FROM call_reservations WHERE slug=? AND charged IS NULL", (slug,)).fetchone():
        raise ValueError("Reconcile active calls before rotating credentials or renewing")
    if set(data) - {"keys", "provider", "plan", "period", "ends_at", "max_call_seconds", "silence_seconds", "max_turns", "paused", "call_mode", "business_knowledge", "greeting"}:
        raise ValueError("Unknown service setting")
    config.update(data)
    keys = config.get("keys", {})
    provider = config.get("provider")
    if not isinstance(provider, str) or provider not in {"twilio", "plivo", "telnyx", "auto"} or not isinstance(keys, dict) or set(keys) - KEYS:
        raise ValueError("Specify provider and supported credential names")
    required = {"GROQ_API_KEY", "RUMIK_API_KEY", "ASSEMBLYAI_API_KEY"}
    required |= ({"TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER"} if provider == "twilio" else {"PLIVO_AUTH_ID", "PLIVO_AUTH_TOKEN", "PLIVO_PHONE_NUMBER"} if provider == "plivo" else {"TELNYX_API_KEY", "TELNYX_ACCOUNT_SID", "TELNYX_TEXML_APPLICATION_SID", "TELNYX_PHONE_NUMBER", "TELNYX_PUBLIC_KEY"} if provider == "telnyx" else {"PLIVO_AUTH_ID", "PLIVO_AUTH_TOKEN", "PLIVO_PHONE_NUMBER", "TELNYX_API_KEY", "TELNYX_ACCOUNT_SID", "TELNYX_TEXML_APPLICATION_SID", "TELNYX_PHONE_NUMBER", "TELNYX_PUBLIC_KEY"})
    if any(not isinstance(v, str) or not v.strip() or len(v) > 4096 or any(ord(char) < 32 for char in v) for v in keys.values()) or not required <= keys.keys():
        raise ValueError("Business credentials are incomplete; shared credentials are never used")
    accounts = {"twilio": "TWILIO_ACCOUNT_SID", "plivo": "PLIVO_AUTH_ID", "telnyx": "TELNYX_ACCOUNT_SID"}
    phones = {"twilio": "TWILIO_PHONE_NUMBER", "plivo": "PLIVO_PHONE_NUMBER", "telnyx": "TELNYX_PHONE_NUMBER"}
    for other in conn.execute("SELECT config FROM business_service WHERE slug != ?", (slug,)):
        other_config = json.loads(other[0])
        for account in ({accounts[provider]} if provider in accounts else {accounts["plivo"], accounts["telnyx"]}):
            if other_config["keys"].get(account) == keys.get(account):
                raise ValueError("Use a separate telephony account or subaccount for each business")
    phone = keys[phones[provider]] if provider in phones else keys["PLIVO_PHONE_NUMBER"]
    if not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
        raise ValueError("Business phone must be E.164")
    if provider == "auto" and not re.fullmatch(r"\+[1-9]\d{7,14}", keys["TELNYX_PHONE_NUMBER"]):
        raise ValueError("Telnyx business phone must be E.164")
    plan, period, ends = config.get("plan"), config.get("period"), config.get("ends_at")
    if not isinstance(plan, str) or plan not in PLANS or not isinstance(period, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", period) or type(ends) is not int or ends <= time.time():
        raise ValueError("Specify a plan, unique paid period ID and future ends_at UTC timestamp")
    for name, default, lower, upper in (("max_call_seconds", 10800, 10, 10800), ("silence_seconds", 90, 30, 300), ("max_turns", 0, 0, 500)):
        value = config.setdefault(name, default)
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError(f"Invalid {name}")
    if type(config.setdefault("paused", False)) is not bool or config.setdefault("call_mode", "english") not in ("english", "hindi"):
        raise ValueError("Invalid pause or language setting")
    for name in ("greeting", "business_knowledge"):
        if not isinstance(config.setdefault(name, ""), str) or len(config[name]) > 4000:
            raise ValueError(f"Invalid {name}")
    existing = conn.execute("SELECT * FROM business_periods WHERE slug=? AND period=?", (slug, period)).fetchone()
    if existing and (existing["allowance"] != PLANS[plan] or existing["ends_at"] != ends):
        raise ValueError("A paid period is immutable; use a new period ID for renewal")
    conn.execute("INSERT OR IGNORE INTO business_periods VALUES (?,?,?,?)", (slug, period, ends, PLANS[plan]))
    conn.execute("UPDATE companies SET phone_number=? WHERE slug=?", (phone, slug))
    conn.execute("INSERT INTO business_service VALUES (?,?) ON CONFLICT(slug) DO UPDATE SET config=excluded.config", (slug, json.dumps(config)))


def scope(conn, slug):
    if not slug:
        return None
    row = conn.execute("SELECT b.config,c.name FROM business_service b JOIN companies c ON c.slug=b.slug WHERE b.slug=?", (slug,)).fetchone()
    if not row:
        raise ValueError("Business service is not configured")
    return dict(json.loads(row["config"]), slug=slug, business_name=row["name"])


def usage(conn, config):
    used, reserved = conn.execute("SELECT COALESCE(SUM(charged),0), COALESCE(SUM(CASE WHEN charged IS NULL THEN seconds ELSE 0 END),0) FROM call_reservations WHERE slug=? AND period=?", (config["slug"], config["period"])).fetchone()
    allowance = PLANS[config["plan"]]
    remaining = None if allowance is None else max(0, allowance - used - reserved)
    return {"plan": config["plan"], "period": config["period"], "ends_at": config["ends_at"], "used_seconds": used, "reserved_seconds": reserved, "remaining_seconds": remaining, "paused": config["paused"] or config["ends_at"] <= time.time() or remaining == 0}


def reserve(conn, config, identifier):
    # BEGIN IMMEDIATE also protects against a second service process.
    conn.execute("BEGIN IMMEDIATE")
    if config != scope(conn, config["slug"]):
        raise ValueError("Business configuration changed; retry with the current settings")
    existing = conn.execute("SELECT * FROM call_reservations WHERE id=?", (identifier,)).fetchone()
    if existing:
        if existing["slug"] != config["slug"] or existing["charged"] is not None:
            raise ValueError("Reservation is not active for this business")
        return dict(existing)
    state = usage(conn, config)
    if state["paused"]:
        raise ValueError("Business service paused: allowance exhausted, expired or manually paused")
    # ponytail: one active call per business; add configurable concurrency when needed.
    if conn.execute("SELECT 1 FROM call_reservations WHERE slug=? AND charged IS NULL", (config["slug"],)).fetchone():
        raise ValueError("Business has an active or unreconciled call")
    seconds = min(config["max_call_seconds"], state["remaining_seconds"] if state["remaining_seconds"] is not None else config["max_call_seconds"], int(config["ends_at"] - time.time()))
    if seconds < 1:
        raise ValueError("No call allowance remaining")
    conn.execute("INSERT INTO call_reservations(id,slug,period,seconds) VALUES (?,?,?,?)", (identifier, config["slug"], config["period"], seconds))
    return dict(conn.execute("SELECT * FROM call_reservations WHERE id=?", (identifier,)).fetchone())


def bind(conn, identifier, sid):
    row = conn.execute("SELECT * FROM call_reservations WHERE id=?", (identifier,)).fetchone()
    if not row or (row["sid"] and row["sid"] != sid):
        raise ValueError("Call does not match its reservation")
    if not re.fullmatch(r"[A-Za-z0-9:_-]{1,160}", sid):
        raise ValueError("Invalid provider call ID")
    call = conn.execute("SELECT company_slug FROM calls WHERE sid=?", (sid,)).fetchone()
    if call and call["company_slug"] != row["slug"]:
        raise ValueError("Call belongs to another business")
    conn.execute("UPDATE call_reservations SET sid=? WHERE id=?", (sid, identifier))


def settle(conn, sid, duration):
    # Only verified terminal provider callbacks or operator reconciliation supply duration.
    value = float(duration)
    if not math.isfinite(value) or not 0 <= value <= 86400:
        raise ValueError("Invalid provider duration")
    conn.execute("UPDATE call_reservations SET charged=MAX(COALESCE(charged,0),?) WHERE sid=?", (math.ceil(value), sid))


def repetition(messages):
    texts = [re.sub(r"\W+", " ", m.get("content", "").casefold()).strip() for m in messages if m.get("role") == "user" and not m.get("_private")]
    # ponytail: exact repeated substantive requests only; review logged endings before adding a classifier.
    if len(texts) < 3 or len(texts[-1].split()) < 4:
        return ""
    if len(texts) >= 4 and len(set(texts[-4:])) == 1:
        return "repeated_request"
    return "warn" if len(set(texts[-3:])) == 1 else ""
