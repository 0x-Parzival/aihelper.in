import unittest

from turn_manager import Action, TurnInput, TurnManager, TurnState


class TurnManagerTests(unittest.TestCase):
    def setUp(self):
        self.manager = TurnManager()

    def test_continuing_caller_keeps_floor(self):
        decision = self.manager.decide(TurnInput(partial_text="I need to change my booking", stability=.9, caller_speaking=True))
        self.assertEqual(decision.action, Action.LISTEN)

    def test_agent_stops_immediately_on_barge_in(self):
        decision = self.manager.decide(TurnInput(partial_text="wait", caller_speaking=True, agent_speaking=True))
        self.assertEqual(decision.action, Action.INTERRUPT)

    def test_final_turn_plans_without_candidate(self):
        decision = self.manager.decide(TurnInput(partial_text="Can you help me?", stability=.9, asr_final=True, pause_ms=260))
        self.assertEqual(decision.action, Action.PLAN)
        self.assertEqual(decision.state, TurnState.PLANNING)

    def test_stable_sentence_can_be_planned_but_not_spoken_while_caller_talks(self):
        decision = self.manager.decide(TurnInput(partial_text="I need to change my booking.", stability=.9, caller_speaking=True))
        self.assertEqual(decision.action, Action.PLAN)

    def test_final_turn_speaks_ready_candidate(self):
        decision = self.manager.decide(TurnInput(partial_text="Can you help me?", stability=.9, end_of_turn=True, pause_ms=250, response_candidate="Yes, I can help."))
        self.assertEqual(decision.action, Action.SPEAK)
        self.assertEqual(decision.response_candidate, "Yes, I can help.")

    def test_short_pause_does_not_take_turn(self):
        decision = self.manager.decide(TurnInput(partial_text="I need information", stability=.95, pause_ms=300))
        self.assertEqual(decision.action, Action.LISTEN)

    def test_final_self_repair_discards_stale_response(self):
        decision = self.manager.decide(TurnInput(partial_text="Tuesday, sorry, Thursday", stability=.95, asr_final=True, pause_ms=700, response_candidate="Booked."))
        self.assertEqual(decision.action, Action.REPAIR)
        self.assertEqual(decision.state, TurnState.PLANNING)

    def test_long_narrative_gets_one_bounded_backchannel(self):
        event = TurnInput(partial_text="I was trying to book an appointment because my order has not arrived yet", stability=.9, caller_speaking=True, narrative_ms=2000, now_ms=5000)
        self.assertEqual(self.manager.decide(event).action, Action.BACKCHANNEL)
        self.assertEqual(self.manager.decide(event).backchannel, None)
        self.assertEqual(self.manager.decide(event).action, Action.LISTEN)

    def test_backchannel_never_at_apparent_turn_end(self):
        event = TurnInput(partial_text="My order has not arrived yet.", stability=.9, caller_speaking=True, narrative_ms=3000, now_ms=5000)
        self.assertNotEqual(self.manager.decide(event).action, Action.BACKCHANNEL)

    def test_sensitive_context_blocks_backchannel(self):
        event = TurnInput(partial_text="I was trying to explain the medical issue that happened last week", stability=.9, caller_speaking=True, narrative_ms=3000, sensitive_context=True, now_ms=9000)
        self.assertEqual(self.manager.decide(event).action, Action.LISTEN)

    def test_high_impact_ambiguity_gets_specific_repair(self):
        event = TurnInput(partial_text="Tuesday or Thursday", stability=.9, asr_final=True, high_impact_field="appointment date", ambiguous_options=("Tuesday", "Thursday"))
        decision = self.manager.decide(event)
        self.assertEqual(decision.action, Action.REPAIR)
        self.assertEqual(decision.clarification, "Did you mean Tuesday or Thursday?")

    def test_style_has_bounded_safe_keys(self):
        self.manager.observe_caller_style("I need a booking for Thursday", speech_rate_wpm=185)
        style = self.manager.response_style()
        self.assertIn("response_length", style)
        self.assertIn("preferred_terms", style)
        self.assertEqual(style["pace"], "brisk")


if __name__ == "__main__":
    unittest.main()
