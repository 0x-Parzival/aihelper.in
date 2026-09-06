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
    "India: ₹10,000/month for up to 1,000 call minutes or ₹20,000/month for unlimited minutes. "
    "Outside India: $100/month for up to 1,000 call minutes or $200/month for unlimited minutes. "
    "Confirm the business country before quoting; do not infer it from language. "
    "Quote only that market's two plans"
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


def sales_state(messages: list[dict]) -> dict[str, bool]:
    """Small, explainable state map used to guide the next sales move."""
    caller = " ".join(str(m.get("content", "")) for m in messages if m.get("role") == "user")
    text = caller.lower()
    return {
        "permission_granted": bool(re.search(r"\b(yes|sure|okay|go ahead|you can ask|fine)\b", text)),
        "decision_maker_confirmed": bool(re.search(r"\b(i am|i'm|yes,? i)\b.{0,20}\b(owner|manager|partner|decision)\b", text)),
        "problem_shared": bool(re.search(r"\b(missed|miss|problem|issue|difficult|struggle|slow|lost|lose|need|want|leads)\b", text)),
        "impact_shared": bool(re.search(r"\b(cost|impact|loss|revenue|hours|leads|calls per|per week|per month)\b", text)),
        "next_step_requested": bool(_MEETING.search(caller)),
    }


def infer_outcome(messages: list[dict]) -> str:
    caller = " ".join(str(m.get("content", "")) for m in messages if m.get("role") == "user")
    if _OPT_OUT.search(caller):
        return "opted_out"
    if re.search(r"\b(not interested|no thanks|don't want|do not want)\b", caller, re.I):
        return "not_interested"
    if re.search(r"\b(send|email|whatsapp)\b", caller, re.I):
        return "send_info"
    if re.search(r"\b(call back|callback|later)\b", caller, re.I):
        return "callback_requested"
    if _MEETING.search(caller):
        return "qualified_demo"
    if re.search(r"\b(no answer|voicemail)\b", caller, re.I):
        return "no_answer"
    return "not_qualified"


_OPT_OUT = re.compile(r"\b(?:stop calling|do not call|remove me|unsubscribe|not interested)\b", re.I)
_OBJECTION = re.compile(r"\b(?:expensive|cost|price|busy|not a good time|already have|send (?:me )?an email)\b", re.I)
_MEETING = re.compile(r"\b(?:meeting|demo|calendar|schedule|thursday|friday|tomorrow)\b", re.I)
_QUALIFICATION = re.compile(r"\b(?:budget|approve|timeline|this quarter|next month)\b", re.I)


def stage_for(messages: list[dict]) -> str:
    caller = " ".join(str(m.get("content", "")) for m in messages if m.get("role") == "user" and not str(m.get("content", "")).startswith("Private"))
    if _OPT_OUT.search(caller):
        return Stage.OPT_OUT
    if _MEETING.search(caller):
        return Stage.CLOSE
    if _OBJECTION.search(caller):
        return Stage.OBJECTION
    if _QUALIFICATION.search(caller) or len(caller.split()) > 60:
        return Stage.QUALIFICATION
    return Stage.DISCOVERY if caller.strip() else Stage.OPEN


def stage_instruction(messages: list[dict], campaign: Campaign) -> str:
    stage = stage_for(messages)
    state = sales_state(messages)
    caller_turns = sum(1 for message in messages if message.get("role") == "user" and not str(message.get("content", "")).startswith("Private"))
    opening_window = (
        "First-30-seconds rule: open with a fresh, caller-relevant observation or question, identify yourself as an AI assistant without leading with the brand, and earn the next few seconds with one concise relevance question. Introduce the brand when explaining the solution. Do not give a long pitch, pretend to know the prospect, use a misleading pattern interrupt, or manufacture urgency."
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
    rules = {
        Stage.OPEN: f"Open warmly, transparently, and briefly. Say who you are, the business you represent, and why you called in the first sentence. Ask permission for one short question, then confirm authority: 'Are you the owner or the person who decides how calls are handled?' If they are not, ask for the right contact without pressure. Use only verified business research; never pretend to know a pain that has not been stated. Do not pitch price yet.",
        Stage.DISCOVERY: f"Run conversational discovery, not an interrogation. Reflect the prospect's wording and ask one open question at a time. Establish current workflow, call volume, missed-call or follow-up impact, existing alternative, desired outcome, and what a useful solution must do. Pause after answers. With permission, explain that AI Helper can answer calls on the business's behalf, capture the caller's need, summarize the call, and follow the owner's instructions. Map one concrete workflow before discussing price. If fit is clear, present both plans: {campaign.monthly_price}. This is a fit-and-budget question, never a demand for payment or payment details. If timing is poor, offer to continue for one question or arrange a better time.",
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
