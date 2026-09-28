"""Transparent, bounded cold-call policy for the live voice agent.

The policy supplies instructions and state hints; it never dials a number,
collects payment data, or overrides local calling/consent rules.
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import os
import re

PRICING = (
    "Inbound call handling: $200 USD subscription. Inbound plus outbound calling: $500 USD subscription. "
    "Billing interval, included usage, telephony charges, and country availability must be confirmed; do not infer or promise them."
)

@dataclass(frozen=True)
class Campaign:
    agent_name: str
    business_name: str
    process: str
    mechanism: str
    outcome: str
    calendar_link: str = ""
    ideal_customer_profile: str = ""
    value_evidence: str = ""
    monthly_price: str = PRICING

    @classmethod
    def from_env(cls, business_name: str) -> "Campaign":
        return cls(
            agent_name=os.environ.get("SALES_AGENT_NAME", "Ava").strip() or "Ava",
            business_name=business_name,
            process=os.environ.get("SALES_PROCESS", "help businesses respond to customer calls").strip(),
            mechanism=os.environ.get("SALES_MECHANISM", "an AI voice agent").strip(),
            outcome=os.environ.get("SALES_OUTCOME", "so fewer qualified opportunities are missed").strip(),
            calendar_link=os.environ.get("SALES_CALENDAR_LINK", "").strip(),
            ideal_customer_profile=os.environ.get("SALES_IDEAL_CUSTOMER_PROFILE", "").strip(),
            value_evidence=os.environ.get("SALES_VALUE_EVIDENCE", "").strip(),
            monthly_price=PRICING,
        )

    def value_proposition(self) -> str:
        return f"We {self.process} through {self.mechanism} to {self.outcome}."

    def opening(self, first_name: str = "") -> str:
        name = f" {first_name.strip()}," if first_name.strip() else ""
        return f"Hi{name} this is {self.agent_name}, an AI assistant calling for {self.business_name}. We help with {self.outcome}. Is that something you look after?"


class Stage:
    OPEN = "open"
    DISCOVERY = "discovery"
    QUALIFICATION = "qualification"
    OBJECTION = "objection"
    CLOSE = "close"
    OPT_OUT = "opt_out"


def repeated_response(previous: str, candidate: str, threshold: float = 0.92) -> bool:
    """Detect exact or near-identical agent replies within one call."""
    normalize = lambda value: re.sub(r"\W+", " ", value.casefold()).strip()
    old, new = normalize(previous), normalize(candidate)
    return bool(old and new and SequenceMatcher(None, old, new).ratio() >= threshold)


def _caller_turns(messages: list[dict]) -> list[str]:
    return [str(m.get("content", "")).strip().replace("’", "'") for m in messages
            if m.get("role") == "user" and str(m.get("content", "")).strip()
            and not m.get("_private")]


def _meeting_requested(text: str) -> bool:
    # ponytail: conservative text hints; use explicit confirmed intent if richer state is needed.
    return bool(_MEETING.search(text) and not _DECLINE.search(text))


def sales_state(messages: list[dict]) -> dict[str, bool]:
    """Small, explainable state map used to guide the next sales move."""
    turns = _caller_turns(messages)
    caller = " ".join(turns)
    text = caller.lower()
    return {
        "permission_granted": bool(re.search(r"\b(yes|sure|okay|go ahead|you can ask|fine)\b", text)),
        "decision_maker_confirmed": bool(re.search(r"\b(i am|i'm|yes,? i)\b.{0,20}\b(owner|manager|partner|decision)\b", text)),
        "problem_shared": bool(re.search(r"\b(missed|miss|problem|issue|difficult|struggle|slow|lost|lose|need|want|leads)\b", text)),
        "impact_shared": bool(re.search(r"\b(cost|impact|loss|revenue|hours|leads|calls per|per week|per month)\b", text)),
        "next_step_requested": bool(turns and _meeting_requested(turns[-1]) and not any(_OPT_OUT.search(turn) for turn in turns)),
    }


def infer_outcome(messages: list[dict]) -> str:
    turns = _caller_turns(messages)
    if any(_OPT_OUT.search(turn) for turn in turns):
        return "opted_out"
    caller = turns[-1] if turns else ""
    if _DECLINE.search(caller):
        return "not_interested"
    if re.search(r"\b(send|email|whatsapp)\b", caller, re.I):
        return "send_info"
    if re.search(r"\b(call back|callback|later)\b", caller, re.I):
        return "callback_requested"
    if _meeting_requested(caller):
        return "qualified_demo"
    if re.search(r"\b(no answer|voicemail)\b", caller, re.I):
        return "no_answer"
    return "not_qualified"


_OPT_OUT = re.compile(r"\b(?:stop (?:calling|contacting|phoning)(?: me| us)?|(?:do not|don't|never) (?:call|contact|phone)(?: me| us| again| anymore| any more|(?=\s*(?:[.!?]|$)))|no more (?:sales )?calls|remove (?:me|us)(?: from (?:your|the) list)?|take (?:me|us) off (?:your|the) list|unsubscribe)\b", re.I)
_DECLINE = re.compile(r"\b(?:not interested|no thanks|not now|no (?:demo|meeting)|(?:don't|do not|cannot|can't|won't|wouldn't|not ready to|not looking to)\b|not\b.{0,25}\b(?:want|need|book|schedule|demo|meeting))", re.I)
_OBJECTION = re.compile(r"\b(?:expensive|cost|price|busy|not a good time|already have|send (?:me )?an email)\b", re.I)
_MEETING = re.compile(r"\b(?:(?:schedule|book|arrange|set up|want|like|interested in|can we|let's)\b[^.!?\n]{0,60}\b(?:meeting|demo|call|review|pilot)|(?:demo|meeting)\s+(?:please|sounds good|works for me))\b", re.I)
_QUALIFICATION = re.compile(r"\b(?:budget|approve|timeline|this quarter|next month)\b", re.I)


def stage_for(messages: list[dict]) -> str:
    turns = _caller_turns(messages)
    if any(_OPT_OUT.search(turn) for turn in turns):
        return Stage.OPT_OUT
    caller = turns[-1] if turns else ""
    if _DECLINE.search(caller):
        return Stage.OBJECTION
    if _meeting_requested(caller):
        return Stage.CLOSE
    if _OBJECTION.search(caller):
        return Stage.OBJECTION
    if _QUALIFICATION.search(caller) or len(caller.split()) > 60:
        return Stage.QUALIFICATION
    return Stage.DISCOVERY if caller.strip() else Stage.OPEN


def stage_instruction(messages: list[dict], campaign: Campaign) -> str:
    stage = stage_for(messages)
    state = sales_state(messages)
    caller_turns = len(_caller_turns(messages))
    opening_window = (
        "First-30-seconds rule: give a brief transparent introduction identifying yourself as an AI assistant, the business, and the sales purpose, then earn the next few seconds with one concise relevance question. Do not give a long pitch, pretend to know the prospect, use a misleading pattern interrupt, or manufacture urgency."
        if caller_turns < 2 else
        "Continue by earning one small next step at a time: briefly reflect the answer, then ask one useful question or offer one concrete next action."
    )
    data_foundation = "Pre-call foundation: use only authorized, current business-contact data. Confirm the person, company, and contact details before relying on them. Never infer, mention, or use age, salary, ethnicity, health, or personal interests for eligibility, persuasion, or personalization."
    if campaign.ideal_customer_profile:
        data_foundation += f" The configured business ICP is: {campaign.ideal_customer_profile}. Use it only to assess business fit; if fit is unclear, ask rather than assume."
    value_delta = "Value framing: explain a concrete, verifiable business outcome and the mechanism that creates it. Tie the offer to the prospect's own words. Do not exaggerate savings, manufacture scarcity, or frame the prospect as irrational for keeping their money."
    if campaign.value_evidence:
        value_delta += f" Approved evidence: {campaign.value_evidence}."
    state_rules = f"Sales state: {json_state(state)} Only advance when the caller has supplied the missing information. Ask permission before pitching."
    common = "Be transparent that you are an AI assistant and the business you represent. Do not impersonate a friend or employee, use false urgency, or conceal the sales purpose. Ask one respectful question at a time. Never collect payment credentials. " + opening_window + " " + data_foundation + " " + value_delta + " " + state_rules
    common += (
        " Keep replies natural and concise, usually one or two short sentences. Answer their question first; adapt to their pace and skip questions already answered."
        " Use light, situational humor only when welcome; never joke about their concerns or force a joke. Never pressure a sale or guarantee sales, savings, or results."
        " Confirm the owner's needs in their own words before recommending a workflow. Discover only relevant gaps: inbound calls, outbound follow-up, meetings,"
        " timed callbacks, integrations with their existing tools, call volume, languages, human escalation, and their success measure."
        " Verify required integrations and capabilities before promising them. For timed callbacks, confirm permission, exact date, time, time zone, and number."
        " When collecting an email, read back the full address, clarify ambiguous spelling, and ask for explicit confirmation before treating it as confirmed."
        " Accept a refusal without another pitch; a declined demo is not permission for follow-up. A clear no-contact request overrides all sales guidance."
        f" Approved pricing, the only source for quotes: {PRICING}. Never invent fees, discounts, free setup, or alternative prices."
    )
    rules = {
        Stage.OPEN: f"Open warmly, transparently, and briefly. Say who you are, the business you represent, and why you called in the first sentence. Ask permission for one short question, then confirm authority: 'Are you the owner or the person who decides how calls are handled?' If they are not, ask for the right contact without pressure. Use only verified business research; never pretend to know a pain that has not been stated. Do not pitch price yet.",
        Stage.DISCOVERY: "Run conversational discovery, not an interrogation. Reflect the prospect's wording and ask one open question at a time. Establish current workflow, call volume, missed-call or follow-up impact, existing alternative, desired outcome, and what a useful solution must do. Pause after answers. With permission, explain that the assistant can answer calls on the business's behalf, capture the caller's need, summarize the call, and follow the owner's instructions. Map one concrete workflow before recommending a plan. If they ask about price, answer directly using approved pricing; otherwise offer both market plans when fit is clear. This is a fit-and-budget question, never a demand for payment or payment details. If timing is poor, offer a callback at their preferred time without pushing another question.",
        Stage.QUALIFICATION: "Qualify fit respectfully: problem, frequency, business impact, current alternative, decision authority, other stakeholders, requirements, timing, and budget range. Ask what would stop the project and how they would judge success. Confirm the summary: 'Did I understand that correctly?' Disqualify honestly when there is no fit. Never use pressure, fake urgency, or a long list of questions.",
        Stage.OBJECTION: "Use acknowledge → clarify → respond → confirm. Say 'That makes sense,' restate the concern, and ask one open question to find the real issue: price, priority, timing, trust, authority, or fit. Respond only to that issue using the prospect's stated needs and approved evidence. Then ask whether the response addresses the concern. Do not argue, pile on features, promise discounts, or claim unverified results.",
        Stage.CLOSE: "If there is clear fit and interest, summarize the problem, desired outcome, agreed workflow, selected plan, decision maker, and next step. Ask for a specific action: demo, technical review, pilot, or setup call. Confirm exact date, time zone, contact channel, attendees, and action. Never claim a callback, calendar invite, pilot, or activation exists unless it is actually configured. If they decline, accept it and ask permission for a future follow-up only once.",
        Stage.OPT_OUT: "Immediately acknowledge the opt-out, confirm no further sales calls, and end the conversation. Do not continue discovery or attempt persuasion.",
    }
    return common + "\nCurrent sales stage: " + stage + ". " + rules[stage]


def json_state(state: dict[str, bool]) -> str:
    return ", ".join(f"{key}={value}" for key, value in state.items())


def delivery_instruction(messages: list[dict]) -> str:
    """Text-level pacing cues for a TTS provider that lacks live prosody controls."""
    stage = stage_for(messages)
    return {
        Stage.OPEN: "Delivery: friendly and inviting; medium pace, short phrases, a natural vocal smile, no hype.",
        Stage.DISCOVERY: "Delivery: calm and reassuring; slower pace, leave room after questions.",
        Stage.QUALIFICATION: "Delivery: calm and clear; factual, respectful, never interrogatory.",
        Stage.OBJECTION: "Delivery: empathetic, persuasive, and steady; begin measured, acknowledge first, then make the next question clear without raising pressure.",
        Stage.CLOSE: "Delivery: confident, positive, and unhurried; clearly separate the two offered times.",
        Stage.OPT_OUT: "Delivery: brief, respectful, and final.",
    }[stage]
