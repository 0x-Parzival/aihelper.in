"""Offline end-to-end checks for voice, evidence and prioritized dialing."""
import asyncio
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock

import server
import jev
import lead_priority
import business_research
import contact_memory


class VoiceUpgradeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = patch.object(server, "DB_PATH", Path(tmp.name) / "calls.db")
        db.start(); self.addCleanup(db.stop)
        token = server.tenant_limits.CURRENT.set(None)
        self.addCleanup(server.tenant_limits.CURRENT.reset, token)
        self.network = patch("urllib.request.urlopen", side_effect=AssertionError("Unexpected network"))
        self.network.start(); self.addCleanup(self.network.stop)
        server.init_db()

    def request(self, method, path, payload=None, authorized=True):
        h = object.__new__(server.Handler)
        body = json.dumps(payload or {}).encode()
        h.path = path
        h.headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
        h.rfile, h.wfile = io.BytesIO(body), io.BytesIO()
        h.send_response = Mock(); h.send_header = Mock(); h.end_headers = Mock()
        h.require_dashboard = Mock(return_value=authorized)
        getattr(h, "do_" + method)()
        return json.loads(h.wfile.getvalue()) if h.wfile.getvalue() else None

    def lead(self, name="Acme", phone="+14155550123", band="high", callback=0):
        lead = server.ai_helper_create_lead(name, phone, metadata={"authorized_to_call": True, "callback_at": callback})
        meta = lead["metadata_json"]
        meta["priority"] = {"source": "jev", "band": band, "readiness_score": 90 if band == "high" else 60, "assessed_at": int(time.time()), "evidence_fingerprint": lead_priority.fingerprint(lead, {})}
        return server.ai_helper_update_lead(lead["id"], {"metadata_json": meta})

    def test_repeated_webhook_preserves_transcript_and_merges_context(self):
        server.start_call("c1", "outbound", "+14155550123")
        messages = [{"role": "user", "content": "We need evening appointment calls."}]
        server.save_transcript("c1", messages)
        server.start_call("c1", "outbound", "+14155550123", "Ask about their calendar", contact_name="Sam")
        call = server.load_call("c1")
        self.assertEqual(json.loads(call["transcript"]), messages)
        self.assertEqual(call["contact_name"], "Sam")
        self.assertIn("calendar", call["context"])

    def test_global_contact_memory_and_email_confirmation(self):
        server.start_call("c1", "outbound", "+14155550123")
        messages = [{"role": "user", "content": "My email is sam@example.com."}, {"role": "assistant", "content": "Is sam@example.com correct?"}, {"role": "user", "content": "Yes."}]
        server.save_transcript("c1", messages)
        contact = server.contact_for_number("", "+14155550123")
        self.assertTrue(json.loads(contact["knowledge"])["business_memory"]["email"]["confirmed"])
        with server.db() as conn:
            self.assertEqual(len(contact_memory.caller_statements(conn, "", "+14155550123")), 2)
        self.assertIn("sam@example.com", server.contact_memory(server.load_call("c1")))

    def test_future_call_recalls_requirements_without_a_summary(self):
        server.start_call("c1", "outbound", "+14155550123")
        server.save_transcript("c1", [{"role":"user", "content":"We need booking calls only after six, using AcmeCalendar."}])
        server.start_call("c2", "inbound", "+14155550123")
        self.assertIn("AcmeCalendar", server.contact_memory(server.load_call("c2")))

    def test_assistant_suggestion_does_not_confirm_email(self):
        with server.db() as conn:
            result = contact_memory.remember_call(conn, "", "+14155550123", "c1", [{"role": "assistant", "content": "Is invented@example.com correct?"}, {"role": "user", "content": "Yes."}])
        self.assertNotIn("email", json.loads(result["knowledge"])["business_memory"])

    def test_optout_blocks_every_outbound_provider(self):
        server.start_call("c1", "outbound", "+14155550123")
        server.save_transcript("c1", [{"role": "user", "content": "Do not call me again."}])
        with patch.object(server, "telnyx_outbound_call") as dial:
            with self.assertRaisesRegex(ValueError, "no further"):
                server.outbound_call("+14155550123", "telnyx")
            dial.assert_not_called()

    def test_long_call_keeps_early_requirements_and_clean_api_messages(self):
        messages = [{"role": "user", "content": "We use AcmeCalendar and only need evening bookings."}]
        messages += [{"role": "user", "content": f"More detail {n}"} for n in range(1000)]
        messages.append({"role": "assistant", "content": "Partial reply", "delivery": "interrupted"})
        window = server.conversation_window(messages)
        self.assertIn("AcmeCalendar", json.dumps(window))
        self.assertLess(len(json.dumps(window)), 4000)
        self.assertTrue(all(set(m) == {"role", "content"} for m in window))
        self.assertIn("do not assume", json.dumps(window))

    def test_muga_validates_events_and_incomplete_tags(self):
        with patch.object(server, "RUMIK_MODEL", "muga"):
            self.assertEqual(server.voice_text("[happy] Hello <chuckle> friend. <laugh>"), "[happy] Hello <chuckle> friend.")
            self.assertEqual(server.voice_text("[sad] Hello <laugh> friend."), "[sad] Hello friend.")
            self.assertEqual(server.voice_text("[neutral] Hello <lau"), "[neutral] Hello")

    def test_muga_receives_complete_utterance_and_jev_tone(self):
        server.start_call("c1", "outbound", "+14155550123")
        session = {"call_sid": "c1", "stream_sid": "s", "messages": [{"role": "user", "content": "That was funny."}], "provider": "telnyx"}
        sent = []
        async def speak(*args):
            sent.append(args[2]); args[6]()
        async def run():
            with patch.dict(server.os.environ, {"OPENROUTER_API_KEY": "test"}), patch.object(jev, "decide", return_value={**jev._result("jev", "happy", "chuckle", "answer_question"), "stage": "opening", "call_scope": "unclear"}), patch.object(server, "groq_stream", return_value=iter(["[neu", "tral] That", " helps. <chuckle>"])), patch.object(server, "send_twilio_audio", side_effect=speak):
                await server.stream_call_reply(AsyncMock(), session, session["messages"])
        asyncio.run(run())
        self.assertEqual(sent, ["[happy] That helps. <chuckle>"])
        self.assertEqual(session["messages"][-1]["delivery"], "complete")
        self.assertIn("response_latency_ms", session["messages"][-1])

    def test_jev_failure_keeps_conversation_running(self):
        server.start_call("c1", "outbound", "+14155550123")
        session = {"call_sid": "c1", "stream_sid": "s", "messages": [{"role": "user", "content": "Hello"}], "provider": "telnyx"}
        async def run():
            with patch.dict(server.os.environ, {"OPENROUTER_API_KEY": "test"}), patch.object(jev, "decide", return_value=jev._result()), patch.object(server, "groq_stream", return_value=iter(["Hello there."])), patch.object(server, "send_twilio_audio", new_callable=AsyncMock) as speak:
                await server.stream_call_reply(AsyncMock(), session, session["messages"])
                speak.assert_awaited_once()
        asyncio.run(run())

    def test_first_sentence_speaks_while_reply_is_still_generating(self):
        server.start_call("c1", "outbound", "+14155550123")
        session = {"call_sid": "c1", "stream_sid": "s", "messages": [{"role": "user", "content": "Hello"}], "provider": "telnyx"}
        async def run():
            proceed = asyncio.Event()
            spoken = asyncio.Event()
            tokens = iter(["[neutral] I can help.", " What calls do you handle?"])
            calls = 0
            async def next_token(_):
                nonlocal calls
                calls += 1
                if calls > 1:
                    await proceed.wait()
                return next(tokens, None)
            async def speak(*args):
                spoken.set()
                args[6]()
            with patch.dict(server.os.environ, {"OPENROUTER_API_KEY": ""}), patch.object(server, "groq_stream", return_value=tokens), patch.object(server, "next_stream_token", side_effect=next_token), patch.object(server, "send_twilio_audio", side_effect=speak) as output:
                task = asyncio.create_task(server.stream_call_reply(AsyncMock(), session, session["messages"]))
                await asyncio.wait_for(spoken.wait(), 1)
                self.assertFalse(task.done())
                self.assertIn("I can help", output.call_args.args[2])
                proceed.set()
                await asyncio.wait_for(task, 1)
        asyncio.run(run())

    def test_due_callback_precedes_higher_score_and_future_waits(self):
        high = self.lead()
        due = self.lead("Due", "+14155550124", "medium", time.time()-60)
        future = self.lead("Future", "+14155550125", "high", time.time()+3600)
        rows = server.prioritized_leads()
        self.assertEqual(rows[0]["id"], due["id"])
        self.assertTrue(next(r for r in rows if r["id"] == future["id"])["blocked_reason"])

    def test_claim_prevents_duplicate_or_uncertain_redial(self):
        lead = self.lead()
        with patch.object(server, "outbound_call", side_effect=TimeoutError("uncertain")) as dial:
            with self.assertRaises(TimeoutError): server.call_next_lead()
            with self.assertRaisesRegex(ValueError, "unreconciled"): server.call_next_lead()
            self.assertEqual(dial.call_count, 1)

    def test_next_call_uses_highest_eligible_business(self):
        first = self.lead()
        self.lead("Lower", "+14155550124", "medium")
        with patch.object(server, "outbound_call", return_value={"sid":"c1", "status":"queued"}) as dial:
            result = server.call_next_lead()
        self.assertEqual(result["lead_id"], first["id"])
        dial.assert_called_once_with(first["phone"])

    def test_readiness_never_overrides_new_evidence_optout_or_permission(self):
        lead = self.lead()
        self.assertEqual(lead_priority.eligibility(lead, {}), "")
        self.assertTrue(lead_priority.eligibility(lead, {"do_not_call": True}))
        self.assertTrue(lead_priority.eligibility(lead, {"new_fact": "no need"}))
        lead["metadata_json"]["authorized_to_call"] = False
        self.assertTrue(lead_priority.eligibility(lead, {}))

    def test_research_rejects_private_ip_before_connecting(self):
        with patch.object(business_research.socket, "getaddrinfo", return_value=[(2,1,6,"",("127.0.0.1",443))]), patch.object(business_research.socket, "create_connection") as connect:
            with self.assertRaises(ValueError): business_research.fetch_page("https://example.com")
            connect.assert_not_called()

    def test_jev_readiness_is_validated_and_not_a_probability(self):
        lead = self.lead()
        answer = {"readiness": {"type":"choice", "choice":"high", "confidence":0.9, "probabilities":{"high":0.97,"medium":0.01,"low":0.01,"unknown":0.01}}}
        with patch.object(jev, "evaluate", return_value=answer) as evaluate:
            result = lead_priority.assess(lead, {"requirements":"After-hours answering"}, "test")
            self.assertEqual((result["source"], result["readiness_score"]), ("jev", 90))
            self.assertNotIn("purchase_probability", result)
            self.assertEqual(len(evaluate.call_args.args[1]), 1)
            answer["readiness"]["confidence"] = 0.1
            self.assertEqual(lead_priority.assess(lead, {}, "test")["source"], "unscored")

    def test_recording_before_answer_is_not_lost(self):
        server.save_recording("c1", "https://example.com/rec.wav", "12")
        server.start_call("c1", "outbound", "+14155550123")
        call = server.load_call("c1")
        self.assertEqual(call["recording_url"], "https://example.com/rec.wav")
        self.assertEqual(call["direction"], "outbound")

    def test_duplicate_lead_cannot_bypass_number_cooldown(self):
        self.lead()
        server.start_call("recent", "outbound", "+14155550123")
        server.finish_call("recent", "completed")
        with patch.object(server, "outbound_call") as dial:
            with self.assertRaisesRegex(ValueError, "No eligible"):
                server.call_next_lead()
            dial.assert_not_called()

    def test_cancelled_generation_does_not_deadlock_or_speak(self):
        server.start_call("c1", "outbound", "+14155550123")
        session = {"call_sid":"c1", "stream_sid":"s", "messages":[], "provider":"telnyx"}
        async def run():
            blocked = asyncio.Event()
            async def delayed(_):
                await blocked.wait()
            with patch.object(server, "next_stream_token", side_effect=delayed), patch.object(server, "groq_stream", return_value=iter([])), patch.object(server, "send_twilio_audio", new_callable=AsyncMock) as speak:
                task = asyncio.create_task(server.stream_call_reply(AsyncMock(), session, []))
                await asyncio.sleep(0.01)
                task.cancel()
                await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 0.5)
                speak.assert_not_awaited()
        asyncio.run(run())

    def test_contacts_and_queue_require_dashboard_auth(self):
        for path in ("/api/contacts", "/api/leads", "/api/contacts/statements"):
            self.assertIsNone(self.request("GET", path, authorized=False))
        self.assertIsNone(self.request("POST", "/api/leads/call-next", authorized=False))

    def test_contact_api_preserves_legacy_and_requires_expected_revision(self):
        payload = {"number": "+14155550123", "name": "Sam", "facts": {"requirements": {"value": "Evening calls", "evidence": "Owner request"}}}
        saved = self.request("POST", "/api/contacts", payload)["contact"]
        self.assertEqual(self.request("GET", "/api/contacts")["contacts"][0]["number"], payload["number"])
        self.assertIn("error", self.request("POST", "/api/contacts", payload))
        payload["expected"] = {"name": saved["name"], "knowledge": saved["knowledge"]}
        self.assertIn("contact", self.request("POST", "/api/contacts", payload))


if __name__ == "__main__":
    unittest.main()
