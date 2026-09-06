import os
import unittest
from unittest.mock import patch

from sales_policy import Campaign, Stage, infer_outcome, repeated_response, sales_state, stage_for, stage_instruction


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
        self.assertIn("₹10,000/month for up to 1,000 call minutes", discovery)
        self.assertIn("current workflow", discovery)
        self.assertIn("Never collect payment credentials", discovery)
        self.assertEqual(stage_for([{"role": "user", "content": "Yes, I am the owner."}]), Stage.DISCOVERY)


if __name__ == "__main__":
    unittest.main()
