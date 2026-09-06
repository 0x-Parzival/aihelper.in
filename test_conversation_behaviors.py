"""Offline behavioral contract for the real-time turn manager.

These tests deliberately have no network, ASR, TTS, or server dependency.
They skip until turn_manager.py implements the public API in VOICE_BEHAVIOR.md.
"""
import unittest

try:
    from turn_manager import Action, TurnInput, TurnManager
except ImportError:  # Enables incremental rollout without breaking existing CI.
    Action = TurnInput = TurnManager = None


@unittest.skipIf(TurnManager is None, "turn_manager.py has not been implemented")
class ConversationBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.manager = TurnManager()

    def decide(self, **changes):
        values = {
            "partial_text": "", "stability": 1.0, "pause_ms": 0,
            "caller_speaking": False, "self_repair": False, "asr_final": False,
            "end_of_turn": False, "response_candidate": None, "narrative_ms": 0,
            "agent_speaking": False, "sensitive_context": False,
            "high_impact_field": None, "ambiguous_options": (), "now_ms": 100_000,
        }
        values.update(changes)
        return self.manager.decide(TurnInput(**values))

    def test_short_thinking_pause_keeps_listening(self):
        result = self.decide(partial_text="I need to change my", pause_ms=180)
        self.assertEqual(result.action, Action.LISTEN)

    def test_vad_silence_corroborates_but_does_not_replace_completion(self):
        result = self.decide(partial_text="I need to change my", silence_ms=900)
        self.assertEqual(result.action, Action.LISTEN)
        result = self.decide(partial_text="I need to change my address.", stability=0.9, silence_ms=400)
        self.assertEqual(result.action, Action.PLAN)

    def test_self_correction_never_triggers_a_reply(self):
        result = self.decide(
            partial_text="Tuesday, sorry, Thursday afternoon", pause_ms=300,
            asr_final=True,
        )
        # A targeted repair is also acceptable after a final event; speaking a
        # stale candidate is not.
        self.assertIn(result.action, {Action.LISTEN, Action.REPAIR})
        self.assertNotEqual(result.action, Action.SPEAK)

    def test_caller_barge_in_stops_agent_immediately(self):
        result = self.decide(
            partial_text="Actually, no", caller_speaking=True, agent_speaking=True,
        )
        self.assertEqual(result.action, Action.INTERRUPT)

    def test_backchannel_requires_safe_sustained_narrative(self):
        result = self.decide(
            partial_text="I have been trying to resolve this since last week and the delivery still has not arrived",
            caller_speaking=True, narrative_ms=2_000, now_ms=120_000,
        )
        self.assertEqual(result.action, Action.BACKCHANNEL)
        self.assertIn(result.backchannel, {"mm-hm", "right", "I see"})

    def test_backchannel_is_not_random_or_allowed_for_sensitive_details(self):
        result = self.decide(
            partial_text="My card number is 4111 1111 1111 1111",
            caller_speaking=True, narrative_ms=2_000, sensitive_context=True,
            now_ms=120_000,
        )
        self.assertEqual(result.action, Action.LISTEN)

        first = self.decide(
            partial_text="I have been explaining the ongoing delivery issue in detail and need help today",
            caller_speaking=True, narrative_ms=2_000, now_ms=100_000,
        )
        self.assertEqual(first.action, Action.BACKCHANNEL)
        result = self.decide(
            partial_text="Here is some more background about the issue I am having today",
            caller_speaking=True, narrative_ms=2_000, now_ms=105_000,
        )
        self.assertEqual(result.action, Action.LISTEN)

    def test_clarification_names_the_ambiguous_high_impact_field(self):
        result = self.decide(
            partial_text="Please book it for Tuesday or Thursday", pause_ms=400,
            asr_final=True, stability=0.45, high_impact_field="appointment date",
            ambiguous_options=("Tuesday", "Thursday"),
        )
        self.assertEqual(result.action, Action.REPAIR)
        self.assertIn("tuesday", result.clarification.lower())
        self.assertIn("thursday", result.clarification.lower())
        self.assertNotIn("didn't catch", result.clarification.lower())

    def test_style_adaptation_is_useful_but_never_identity_mimicry(self):
        self.manager.observe_caller_style("Quick order status, please?")
        style = self.manager.response_style()
        self.assertEqual(style["response_length"], "concise")
        self.assertIn("order", style["preferred_terms"])
        forbidden = {"accent", "dialect", "identity", "ethnicity", "gender", "age", "disability"}
        self.assertTrue(forbidden.isdisjoint(style))


if __name__ == "__main__":
    unittest.main()
