import os
import unittest
from unittest.mock import patch

from sales_policy import PRICING, Campaign, Stage, infer_outcome, repeated_response, sales_state, stage_for, stage_instruction


class SalesPolicyTests(unittest.TestCase):
    def test_opening_discloses_ai_and_business(self):
        campaign = Campaign("Ava", "Acme", "improve follow-up", "an AI voice agent", "miss fewer leads")
        opening = campaign.opening("Priya")
        self.assertIn("AI assistant", opening)
        self.assertIn("Acme", opening)
        self.assertIn("Priya", opening)
        self.assertTrue(opening.endswith("?"))
        self.assertNotIn("old friend", opening.lower())

    def test_value_proposition_follows_process_mechanism_outcome(self):
        campaign = Campaign("Ava", "Acme", "improve follow-up", "an AI voice agent", "miss fewer leads")
        self.assertEqual(campaign.value_proposition(), "We improve follow-up through an AI voice agent to miss fewer leads.")

    def test_opt_out_overrides_every_sales_stage(self):
        messages = [{"role": "user", "content": "Not interested. Please remove me and do not call again."}]
        self.assertEqual(stage_for(messages), Stage.OPT_OUT)
        self.assertIn("end the conversation", stage_instruction(messages, Campaign("Ava", "Acme", "x", "y", "z")))

    def test_objection_and_close_have_bounded_rules(self):
        objection = [{"role": "user", "content": "This sounds expensive."}]
        closing = [{"role": "user", "content": "Can we schedule a demo Thursday?"}]
        self.assertEqual(stage_for(objection), Stage.OBJECTION)
        self.assertIn("acknowledge", stage_instruction(objection, Campaign("Ava", "Acme", "x", "y", "z")))
        self.assertEqual(stage_for(closing), Stage.CLOSE)
        self.assertIn("Confirm exact date", stage_instruction(closing, Campaign("Ava", "Acme", "x", "y", "z")))

    def test_first_30_seconds_are_transparent_and_question_led(self):
        instruction = stage_instruction([], Campaign("Ava", "Acme", "improve response", "an AI voice agent", "fewer missed leads"))
        self.assertIn("First-30-seconds rule", instruction)
        self.assertIn("transparent introduction", instruction)
        self.assertIn("misleading pattern interrupt", instruction)

    def test_icp_and_value_are_bounded_to_authorized_business_data(self):
        campaign = Campaign("Ava", "Acme", "improve response", "an AI voice agent", "fewer missed leads", ideal_customer_profile="US dental practices with 5–50 staff", value_evidence="customers report fewer missed calls")
        instruction = stage_instruction([], campaign)
        self.assertIn("authorized, current business-contact data", instruction)
        self.assertIn("Never infer, mention, or use age, salary", instruction)
        self.assertIn("US dental practices", instruction)
        self.assertIn("customers report fewer missed calls", instruction)

    def test_discovery_and_objection_use_progressive_questions(self):
        campaign = Campaign("Ava", "Acme", "x", "y", "z")
        discovery = stage_instruction([{"role": "user", "content": "We are handling calls manually."}], campaign)
        objection = stage_instruction([{"role": "user", "content": "This sounds expensive."}], campaign)
        self.assertIn("conversational discovery", discovery)
        self.assertIn("find the real issue", objection)

    def test_sales_state_and_outcomes_are_structured(self):
        messages = [
            {"role": "user", "content": "Yes, I am the owner. We miss calls and lose leads."},
            {"role": "assistant", "content": "Would a short demo help?"},
            {"role": "user", "content": "Yes, schedule a demo Thursday."},
        ]
        state = sales_state(messages)
        self.assertTrue(state["decision_maker_confirmed"])
        self.assertTrue(state["problem_shared"])
        self.assertEqual(infer_outcome(messages), "qualified_demo")

    def test_opt_out_outcome_wins(self):
        self.assertEqual(infer_outcome([{"role": "user", "content": "Please remove me and do not call again."}]), "opted_out")

    def test_repeated_response_detects_exact_and_near_duplicates(self):
        self.assertTrue(repeated_response("How are you handling calls today?", "How are you handling calls today?"))
        self.assertTrue(repeated_response("How are you handling calls today?", "How are you handling calls today now?"))
        self.assertFalse(repeated_response("How are you handling calls today?", "What happens to missed calls?"))

    def test_opening_sales_sequence_confirms_authority_before_price(self):
        campaign = Campaign("Ava", "Acme", "improve response", "an AI voice agent", "fewer missed leads")
        opening = stage_instruction([], campaign)
        discovery = stage_instruction([{"role": "user", "content": "Yes, I own the business."}], campaign)
        self.assertIn("owner or the person who decides", opening)
        self.assertIn("current workflow", discovery)
        self.assertIn("what a useful solution must do", discovery.lower())
        self.assertIn("$200 USD", discovery)
        self.assertIn("$500 USD", discovery)
        self.assertIn("current workflow", discovery)
        self.assertIn("Never collect payment credentials", discovery)
        self.assertEqual(stage_for([{"role": "user", "content": "Yes, I am the owner."}]), Stage.DISCOVERY)

    def test_current_turn_replaces_stale_objections_and_meetings(self):
        for old in ("That is expensive.", "I am busy.", "Please schedule a demo."):
            with self.subTest(old=old):
                messages = [{"role": "user", "content": old},
                            {"role": "user", "content": "We handle enquiries manually."}]
                self.assertEqual(stage_for(messages), Stage.DISCOVERY)
                self.assertFalse(sales_state(messages)["next_step_requested"])
                self.assertEqual(infer_outcome(messages), "not_qualified")

    def test_dates_and_workflow_mentions_are_not_demo_requests(self):
        for text in ("Thursday", "Friday", "Tomorrow", "We miss calls on Saturdays.",
                     "Our calendar is full.", "What does the demo cover?",
                     "Call back tomorrow at three."):
            with self.subTest(text=text):
                messages = [{"role": "user", "content": text}]
                self.assertNotEqual(stage_for(messages), Stage.CLOSE)
                self.assertFalse(sales_state(messages)["next_step_requested"])
                self.assertNotEqual(infer_outcome(messages), "qualified_demo")

    def test_negated_interest_does_not_close_or_permanently_opt_out(self):
        for text in ("Not interested in a demo.", "I don't want to schedule a demo.",
                     "I’m not ready to book a meeting.", "No demo thanks.",
                     "I do not need a meeting."):
            with self.subTest(text=text):
                messages = [{"role": "user", "content": "Please schedule a demo."},
                            {"role": "user", "content": text}]
                self.assertEqual(stage_for(messages), Stage.OBJECTION)
                self.assertFalse(sales_state(messages)["next_step_requested"])
                self.assertEqual(infer_outcome(messages), "not_interested")
                messages.append({"role": "user", "content": "Actually, can we book a demo?"})
                self.assertEqual(stage_for(messages), Stage.CLOSE)

    def test_private_records_never_supply_caller_state_or_intent(self):
        campaign = Campaign("Ava", "Acme", "x", "y", "z")
        for prefix in ("Private caller record", "  private owner instruction", "Private acoustic context"):
            with self.subTest(prefix=prefix):
                private = [{"role": "user", "content": prefix + ": Yes, I am the owner. We lose leads per week. Schedule a demo. Stop calling me.", "_private": True}]
                self.assertFalse(any(sales_state(private).values()))
                self.assertEqual(stage_for(private), Stage.OPEN)
                self.assertEqual(infer_outcome(private), "not_qualified")
                self.assertEqual(stage_instruction(private, campaign), stage_instruction([], campaign))
                public = [{"role": "user", "content": "Please book a demo."}]
                self.assertEqual(sales_state(public + private), sales_state(public))
                self.assertEqual(stage_for(public + private), Stage.CLOSE)
                self.assertEqual(infer_outcome(public + private), "qualified_demo")

    def test_clear_opt_out_persists_across_later_turns(self):
        for text in ("Stop calling.", "Do not call.", "Don't call", "Don’t call me again.", "Do not contact us.",
                     "Please remove me from your list.", "Take us off the list.",
                     "Unsubscribe.", "No more sales calls."):
            with self.subTest(text=text):
                messages = [{"role": "user", "content": text},
                            {"role": "assistant", "content": "Would you like a demo?"},
                            {"role": "user", "content": "Schedule a demo tomorrow."}]
                self.assertEqual(stage_for(messages), Stage.OPT_OUT)
                self.assertEqual(infer_outcome(messages), "opted_out")
                self.assertFalse(sales_state(messages)["next_step_requested"])

    def test_negated_workflow_is_not_a_contact_opt_out(self):
        messages = [{"role": "user", "content": "Don't call it a demo; it is a technical review."}]
        self.assertNotEqual(stage_for(messages), Stage.OPT_OUT)
        self.assertNotEqual(infer_outcome(messages), "opted_out")

    def test_guidance_covers_adaptive_business_discovery_and_confirmations(self):
        campaign = Campaign("Ava", "Acme", "x", "y", "z", monthly_price="unapproved price")
        instruction = stage_instruction([{"role": "user", "content": "We miss calls."}], campaign)
        for requirement in ("inbound", "outbound", "meetings", "timed callbacks", "integrations",
                            "call volume", "languages", "human escalation", "success measure",
                            "owner's needs", "read back the full address", "explicit confirmation",
                            "humor only when welcome", "Never pressure", "skip questions already answered"):
            self.assertIn(requirement, instruction)
        self.assertEqual(instruction.count(PRICING), 1)
        self.assertNotIn("unapproved price", instruction)
        with patch.dict(os.environ, {"SALES_MONTHLY_PRICE": "unapproved price"}):
            self.assertEqual(Campaign.from_env("Acme").monthly_price, PRICING)


if __name__ == "__main__":
    unittest.main()
