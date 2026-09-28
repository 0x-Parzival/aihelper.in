"""Contact facts in the existing contacts table; no network or model calls.

Caller owns the connection transaction. Import as `contact_memory_store` in
server.py (which already defines a function named contact_memory).
"""
import json
import re
import time
from sales_policy import stage_for, Stage
from urllib.parse import urlsplit

FIELDS = {"email", "requirements", "pricing", "research"}


def validate_key(company_slug, number):
    if not isinstance(company_slug, str) or not re.fullmatch(r"(?:[a-z0-9]+(?:-[a-z0-9]+)*)?", company_slug):
        raise ValueError("Invalid company scope")
    if not isinstance(number, str) or not re.fullmatch(r"\+[1-9][0-9]{7,14}", number):
        raise ValueError("Use an E.164 number")


def decode(raw):
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Existing knowledge is not an object; repair it before saving")
    return value


def list_contacts(conn, company_slug):
    validate_key(company_slug, "+14155550123")
    rows = conn.execute("SELECT company_slug, number, name, knowledge, updated_at FROM contacts WHERE company_slug = ? ORDER BY updated_at DESC, number", (company_slug,))
    return [dict(zip(("company_slug", "number", "name", "knowledge", "updated_at"), row)) for row in rows]


def save_facts(conn, company_slug, number, facts, *, actor, name=None, expected=None):
    """Merge evidence-backed fields, preserving arbitrary legacy knowledge.

    Owner writes require `expected={name, knowledge}` from GET, or None for a
    new contact. Conflicts raise ValueError; HTTP adapters should return 409.
    Automated writes cannot replace owner facts or downgrade confirmed facts.
    `actor` must be supplied by trusted server code, never by the HTTP client.
    """
    validate_key(company_slug, number)
    if actor not in {"owner", "call", "research"}:
        raise ValueError("Invalid source")
    if not isinstance(facts, dict) or facts.keys() - FIELDS:
        raise ValueError("Unknown fact field")
    if name is not None and (not isinstance(name, str) or len(name) > 80):
        raise ValueError("Name must be at most 80 characters")
    row = conn.execute("SELECT name, knowledge FROM contacts WHERE company_slug=? AND number=?", (company_slug, number)).fetchone()
    old = {"name": row[0], "knowledge": row[1]} if row else None
    if (actor == "owner" or expected is not None) and expected != old:
        raise ValueError("Contact changed; reload before saving")
    knowledge = decode(row[1]) if row else {}
    memory = knowledge.setdefault("business_memory", {})
    if not isinstance(memory, dict):
        raise ValueError("Existing business_memory is not an object")
    now = int(time.time())
    for field, fact in facts.items():
        if not isinstance(fact, dict) or set(fact) - {"value", "confirmed", "evidence", "source_url", "call_sid"}:
            raise ValueError("Invalid fact")
        value, evidence = fact.get("value"), fact.get("evidence")
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 2000:
            raise ValueError("Facts need a value of 1–2000 characters")
        if not isinstance(evidence, str) or not 1 <= len(evidence.strip()) <= 2000:
            raise ValueError("Facts need supporting evidence")
        confirmed = fact.get("confirmed", False)
        if not isinstance(confirmed, bool):
            raise ValueError("Confirmation must be boolean")
        if field == "email" and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Invalid email")
        url = fact.get("source_url", "")
        if not isinstance(url, str) or len(url) > 2000:
            raise ValueError("Invalid source URL")
        if url:
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("Source URL must be public HTTP(S)")
        if (field == "research" or actor == "research") and not url:
            raise ValueError("Research needs a source URL")
        if actor == "research" and confirmed:
            raise ValueError("Research cannot confirm customer details")
        sid = fact.get("call_sid", "")
        if not isinstance(sid, str) or len(sid) > 128 or (actor == "call" and not sid):
            raise ValueError("Call facts need a call SID")
        prior = memory.get(field, {})
        if not isinstance(prior, dict):
            raise ValueError("Existing fact is not an object")
        if actor != "owner" and (prior.get("source") == "owner" or (prior.get("confirmed") and not confirmed)):
            continue
        memory[field] = dict(fact, value=value.strip(), evidence=evidence.strip(), confirmed=confirmed, source=actor, updated_at=now)
        if field == "research":
            sources = knowledge.setdefault("research_sources", [])
            if not isinstance(sources, list):
                raise ValueError("Existing research_sources is not an array")
            if not any(item.get("source_url") == url and item.get("value") == value.strip() for item in sources if isinstance(item, dict)):
                sources.append(memory[field].copy())
    encoded = json.dumps(knowledge, ensure_ascii=False, sort_keys=True)
    if len(encoded) > 32000:
        raise ValueError("Contact knowledge exceeds 32000 characters")
    saved_name = name.strip() if name is not None and actor == "owner" else (row[0] if row else "")
    if row:
        result = conn.execute("UPDATE contacts SET name=?, knowledge=?, updated_at=? WHERE company_slug=? AND number=? AND name=? AND knowledge=?", (saved_name, encoded, now, company_slug, number, *row))
    else:
        result = conn.execute("INSERT OR IGNORE INTO contacts(company_slug,number,name,knowledge,updated_at) VALUES(?,?,?,?,?)", (company_slug, number, saved_name, encoded, now))
    if result.rowcount != 1:
        raise ValueError("Contact changed; reload before saving")
    return {"company_slug": company_slug, "number": number, "name": saved_name, "knowledge": encoded, "updated_at": now}


def init_db(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS contact_statements (
        company_slug TEXT NOT NULL, number TEXT NOT NULL, call_sid TEXT NOT NULL,
        turn INTEGER NOT NULL, statement TEXT NOT NULL, created_at INTEGER NOT NULL,
        PRIMARY KEY(company_slug, number, call_sid, turn))""")


def remember_call(conn, company_slug, number, call_sid, messages):
    """Persist the FULL public transcript's caller turns, idempotently.

    Invoke after transcript saves and at completion, before prompt windowing.
    No summarizer can discard early decisions. Assistant speech is never evidence.
    Email is only confirmed for the literal phrase 'I confirm my email is ...'.
    Other decisions stay as attributed statements for review, never guessed facts.
    """
    validate_key(company_slug, number)
    if not isinstance(call_sid, str) or not 1 <= len(call_sid) <= 128 or not isinstance(messages, list):
        raise ValueError("Invalid call transcript")
    facts = {}
    statements = []
    for turn, message in enumerate(messages):
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise ValueError("Invalid transcript message")
        text = message["content"]
        if message.get("role") != "user" or message.get("_private"):
            continue
        statements.append((company_slug, number, call_sid, turn, text, int(time.time())))
        match = re.fullmatch(r"\s*(I confirm my email is|my email is)\s+([^\s@]+@[^\s@]+\.[^\s@]+?)[.!]?\s*", text, re.I)
        if match:
            confirmed = match[1].lower().startswith("i confirm")
            if confirmed or not facts.get("email", {}).get("confirmed"):
                facts["email"] = {"value": match[2], "confirmed": confirmed, "evidence": text, "call_sid": call_sid}
        elif turn and re.fullmatch(r"\s*(?:yes|correct|that's correct|that is correct|yes[, ]+that's correct)[.!]?\s*", text, re.I):
            previous = messages[turn - 1]
            addresses = re.findall(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>?.!,]+", previous.get("content", ""))
            email = facts.get("email")
            if (previous.get("role") == "assistant" and previous.get("delivery", "complete") == "complete"
                    and email and len(addresses) == 1 and addresses[0].casefold() == email["value"].casefold()
                    and re.search(r"correct|right|confirm", previous["content"], re.I)):
                email.update(confirmed=True, evidence=email["evidence"] + " / " + previous["content"] + " / " + text)
    result = save_facts(conn, company_slug, number, facts, actor="call")
    knowledge = decode(result["knowledge"])
    knowledge["last_call"] = {"sid": call_sid, "turns": len(messages)}
    if stage_for(messages) == Stage.OPT_OUT:
        knowledge["do_not_call"] = True
    result["knowledge"] = json.dumps(knowledge)
    conn.execute("UPDATE contacts SET knowledge=? WHERE company_slug=? AND number=?", (result["knowledge"], company_slug, number))
    conn.executemany("INSERT INTO contact_statements VALUES(?,?,?,?,?,?) ON CONFLICT(company_slug,number,call_sid,turn) DO UPDATE SET statement=excluded.statement", statements)
    return result


def caller_statements(conn, company_slug, number):
    """Return all durable caller evidence; HTTP layer must authenticate the scope."""
    validate_key(company_slug, number)
    rows = conn.execute("SELECT call_sid, turn, statement, created_at FROM contact_statements WHERE company_slug=? AND number=? ORDER BY created_at, call_sid, turn", (company_slug, number))
    return [dict(zip(("call_sid", "turn", "statement", "created_at"), row)) for row in rows]
