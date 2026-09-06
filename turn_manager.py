"""Conservative, provider-independent policy for conversational turn taking.

The turn manager decides *when* an agent may act; it deliberately does not
decide what the agent should say.  This makes it safe to use ahead of an LLM
or TTS pipeline and simple to exercise with recorded ASR events.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Optional


class Action(str, Enum):
    LISTEN = "listen"
    PLAN = "plan"
    SPEAK = "speak"
    BACKCHANNEL = "backchannel"
    INTERRUPT = "interrupt"
    REPAIR = "repair"


class TurnState(str, Enum):
    LISTENING = "listening"
    POSSIBLE_COMPLETION = "possible_completion"
    PLANNING = "planning"
    SPEAKING = "speaking"


@dataclass(frozen=True)
class TurnInput:
    """A snapshot of ASR and media state for one policy decision."""

    partial_text: str = ""
    stability: float = 0.0  # normalized ASR stability/confidence, 0..1
    pause_ms: int = 0
    caller_speaking: bool = False
    self_repair: bool = False
    asr_final: bool = False
    end_of_turn: bool = False
    response_candidate: Optional[str] = None
    narrative_ms: int = 0
    # Never insert listener feedback while discussing sensitive matters.
    sensitive_context: bool = False
    # e.g. "date", "address", "amount"; low confidence requires repair.
    high_impact_field: Optional[str] = None
    # Explicit alternatives extracted by ASR/business logic, if any.
    ambiguous_options: tuple[str, ...] = ()
    # Set by the media layer while any agent audio is queued or playing.
    agent_speaking: bool = False
    # Transcript words divided by observed caller speech time. This is only a
    # per-call presentation cue, never an identity or emotion signal.
    speech_rate_wpm: float = 0.0
    # Media-layer voice-activity silence. It corroborates ASR's end-of-turn
    # signal but never replaces a final transcript on its own.
    silence_ms: int = 0
    now_ms: int = 0


@dataclass(frozen=True)
class TurnDecision:
    action: Action
    state: TurnState
    reason: str
    # A response candidate is only usable when SPEAK is returned.
    response_candidate: Optional[str] = None
    # Only populated for a vetted active-listening sound/phrase.
    backchannel: Optional[str] = None
    # Only populated when the manager can ask a precise, low-friction repair.
    clarification: Optional[str] = None


_SELF_REPAIR = re.compile(
    r"\b(?:sorry|rather|i mean|no,? wait|actually|correction)\b", re.I
)
_INTERRUPTION_CUE = re.compile(r"\b(?:wait|stop|no|hold on|actually|excuse me|sorry)\b", re.I)
_COMPLETE_ENDING = re.compile(r"(?:[.?!…]|\b(?:thanks|thank you|bye|goodbye)\b)\s*$", re.I)


class TurnManager:
    """Stateful policy with intentionally cautious thresholds.

    A response is planned early but is never spoken merely because a brief
    pause occurred.  That distinction prevents cutting callers off while
    retaining the latency benefit of incremental ASR.
    """

    def __init__(
        self,
        min_turn_pause_ms: int = 250,
        normal_turn_pause_ms: int = 350,
        uncertain_turn_pause_ms: int = 550,
        backchannel_after_ms: int = 1_800,
        backchannel_cooldown_ms: int = 8_000,
    ) -> None:
        if min_turn_pause_ms < 0 or normal_turn_pause_ms < min_turn_pause_ms:
            raise ValueError("turn pause thresholds must be ordered")
        self.min_turn_pause_ms = min_turn_pause_ms
        self.normal_turn_pause_ms = normal_turn_pause_ms
        self.uncertain_turn_pause_ms = uncertain_turn_pause_ms
        self.backchannel_after_ms = backchannel_after_ms
        self.backchannel_cooldown_ms = backchannel_cooldown_ms
        self.state = TurnState.LISTENING
        self._last_backchannel_ms: Optional[int] = None
        self._caller_word_count = 0
        self._caller_turn_count = 0
        self._preferred_terms: list[str] = []
        self._speech_rate_samples: list[float] = []

    def observe_caller_style(self, text: str, speech_rate_wpm: float = 0.0) -> None:
        """Collect a deliberately small, non-sensitive pacing signal per call."""
        words = text.split()
        if words:
            self._caller_word_count += len(words)
            self._caller_turn_count += 1
            for word in re.findall(r"[a-zA-Z][a-zA-Z'-]{2,}", text.lower()):
                if word not in _STYLE_STOP_WORDS and word not in self._preferred_terms:
                    self._preferred_terms.append(word)
                    if len(self._preferred_terms) >= 5:
                        break
        if 50 <= speech_rate_wpm <= 260:
            self._speech_rate_samples.append(speech_rate_wpm)
            del self._speech_rate_samples[:-8]

    def response_style(self) -> dict[str, object]:
        """Return a bounded presentation hint for the content generator.

        This intentionally avoids accent, identity, emotion, or demographic
        inference.  It only distinguishes concise callers from callers who
        consistently provide more detail.
        """
        average = self._caller_word_count / max(self._caller_turn_count, 1)
        average_wpm = sum(self._speech_rate_samples) / max(len(self._speech_rate_samples), 1)
        pace = "unhurried" if average_wpm and average_wpm < 110 else "brisk" if average_wpm > 170 else "standard"
        return {
            "response_length": "concise" if average and average < 9 else "standard",
            "preferred_terms": tuple(self._preferred_terms),
            "pace": pace,
        }

    def adapt_timing(self, speech_rate_wpm: float = 0.0, high_impact: bool = False) -> None:
        """Tune only the bounded pause budget to this caller's speaking pace."""
        if speech_rate_wpm > 170:
            normal = 450
        elif speech_rate_wpm and speech_rate_wpm < 110:
            normal = 850
        else:
            normal = 650
        self.normal_turn_pause_ms = normal
        self.uncertain_turn_pause_ms = min(1_100, normal + (250 if high_impact else 150))

    @staticmethod
    def _has_repair_cue(event: TurnInput) -> bool:
        return event.self_repair or bool(_SELF_REPAIR.search(event.partial_text))

    @staticmethod
    def _looks_complete(text: str) -> bool:
        return bool(text.strip() and _COMPLETE_ENDING.search(text))

    def decide(self, event: TurnInput) -> TurnDecision:
        """Return one safe next action for the supplied real-time snapshot."""
        text = event.partial_text.strip()
        repair = self._has_repair_cue(event)

        # Voice activity always wins: callers can interrupt at any moment.
        if event.caller_speaking:
            if (event.agent_speaking or self.state == TurnState.SPEAKING) and self._actionable_interruption(text):
                self.state = TurnState.LISTENING
                return TurnDecision(Action.INTERRUPT, self.state, "caller barged in")
            self.state = TurnState.LISTENING
            if repair:
                return TurnDecision(Action.LISTEN, self.state, "caller is repairing their utterance")
            # Planning is safe before a handoff only when the current partial
            # is already a complete, stable sentence. The media layer must
            # still discard this candidate if the final ASR differs.
            if self._looks_complete(text) and event.stability >= 0.80:
                self.state = TurnState.PLANNING
                return TurnDecision(Action.PLAN, self.state, "stable partial may be turn-complete")
            if self._may_backchannel(event, text):
                self._last_backchannel_ms = event.now_ms
                return TurnDecision(Action.BACKCHANNEL, self.state, "long continuing narrative", backchannel="mm-hm")
            return TurnDecision(Action.LISTEN, self.state, "caller retains the floor")

        if repair and (event.asr_final or event.end_of_turn):
            # Tell the orchestration layer to discard an answer planned from
            # the pre-correction transcript and plan against the repair.
            self.state = TurnState.PLANNING
            return TurnDecision(Action.REPAIR, self.state, "caller completed a self-repair")
        if repair:
            self.state = TurnState.LISTENING
            return TurnDecision(Action.LISTEN, self.state, "wait through possible self-repair")

        # A final ASR event or provider end-of-turn is the strongest handoff.
        # Media VAD only corroborates that signal; it cannot by itself steal
        # the floor from a caller who may simply be thinking.
        definitive_handoff = event.asr_final or event.end_of_turn
        likely_completion = self._looks_complete(text) and event.stability >= 0.65
        candidate = (event.response_candidate or "").strip() or None
        observed_pause = max(event.pause_ms, event.silence_ms)

        if not text:
            self.state = TurnState.LISTENING
            return TurnDecision(Action.LISTEN, self.state, "no stable caller content")

        if event.high_impact_field and (event.stability < 0.80 or event.ambiguous_options):
            self.state = TurnState.PLANNING
            clarification = self._clarification(event)
            return TurnDecision(Action.REPAIR, self.state, "confirm high-impact detail", clarification=clarification)

        if (definitive_handoff or likely_completion) and event.stability >= 0.50:
            if candidate and observed_pause >= self.min_turn_pause_ms:
                self.state = TurnState.SPEAKING
                return TurnDecision(Action.SPEAK, self.state, "caller yielded floor", candidate)
            self.state = TurnState.PLANNING
            return TurnDecision(Action.PLAN, self.state, "likely turn completion")

        # Do not infer a turn from a short silence.  Longer silence requires
        # stable, linguistically complete content before speaking.
        required_pause = self.normal_turn_pause_ms if event.stability >= 0.80 else self.uncertain_turn_pause_ms
        if observed_pause >= required_pause and likely_completion:
            self.state = TurnState.PLANNING
            return TurnDecision(Action.PLAN, self.state, "conservative silence handoff")

        self.state = TurnState.POSSIBLE_COMPLETION if observed_pause >= self.min_turn_pause_ms else TurnState.LISTENING
        return TurnDecision(Action.LISTEN, self.state, "await stronger handoff evidence")

    @staticmethod
    def _actionable_interruption(text: str) -> bool:
        words = text.split()
        return len(words) >= 2 or bool(_INTERRUPTION_CUE.search(text))

    def _may_backchannel(self, event: TurnInput, text: str) -> bool:
        if event.sensitive_context or event.narrative_ms < self.backchannel_after_ms or len(text.split()) < 8:
            return False
        if self._looks_complete(text) or event.stability < 0.60:
            return False
        if self._last_backchannel_ms is None:
            return True
        return event.now_ms - self._last_backchannel_ms >= self.backchannel_cooldown_ms

    @staticmethod
    def _clarification(event: TurnInput) -> str:
        if len(event.ambiguous_options) == 2:
            return f"Did you mean {event.ambiguous_options[0]} or {event.ambiguous_options[1]}?"
        if event.ambiguous_options:
            return f"Which {event.high_impact_field} did you mean: {', '.join(event.ambiguous_options)}?"
        return f"Could you please confirm the {event.high_impact_field}?"


# Kept as an alias for callers that used the initial development name.
Decision = TurnDecision


_STYLE_STOP_WORDS = frozenset({
    "about", "after", "because", "could", "have", "need", "that", "the",
    "this", "with", "would", "your", "please", "thanks",
})
