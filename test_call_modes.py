import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import server


class CallModeTests(unittest.TestCase):
    def test_hindi_mode_uses_sarvam_and_hindi_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            original = server.DB_PATH
            server.DB_PATH = Path(directory) / "test.db"
            try:
                server.init_db()
                server.set_settings({"call_mode": "hindi"})
                self.assertEqual(server.call_mode()["stt_provider"], "sarvam")
                self.assertIn("Hindi", server.agent_system())
                self.assertIn("adult female", server.agent_system())
                self.assertIn("feminine first-person grammar", server.agent_system())
                self.assertIn("₹10,000/month for up to 1,000 call minutes", server.agent_system())
                self.assertIn("do not mention AI Helper", server.agent_system())
                self.assertIn("one idea per sentence", server.agent_system())
                self.assertIn("longer pause after objections", server.agent_system())
                self.assertNotIn("AI Helper", server.outbound_greeting())
            finally:
                server.DB_PATH = original

    def test_mulberry_keeps_one_safe_voice_event(self):
        original = server.RUMIK_MODEL
        server.RUMIK_MODEL = "mulberry"
        try:
            self.assertEqual(server.voice_text("Hello <laugh> friend. <scream> Wow."), "Hello <laugh> friend. Wow.")
            self.assertEqual(server.tts_text("Hello <not-a-tag> friend."), "Hello friend.")
        finally:
            server.RUMIK_MODEL = original

    def test_tts_uses_spoken_money_phone_and_plain_text(self):
        self.assertEqual(server.tts_text("- Pay ₹10,000 & $25 to +917457852306 (10%)."), "Pay 10,000 rupees and 25 dollars to plus 9 1 7 4 5 7 8 5 2 3 0 6 (10 percent).")

    def test_groq_stream_retries_once_before_any_delta(self):
        import io
        import json as jsonlib

        line = b'data: {"choices": [{"delta": {"content": "Hi"}}]}\n'
        done = b"data: [DONE]\n"

        class FakeResp:
            def __init__(self, lines):
                self.lines = lines

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def __iter__(self):
                return iter(self.lines)

        boom = urllib.error.HTTPError("https://x", 502, "bad gateway", {}, io.BytesIO())
        with patch.object(server, "agent_system", return_value="sys"), patch.object(server, "setting", return_value="k"), patch("urllib.request.urlopen", side_effect=[boom, FakeResp([line, done])]) as opened:
            self.assertEqual(list(server.groq_stream([{"role": "user", "content": "hi"}])), ["Hi"])
            self.assertEqual(opened.call_count, 2)

    def test_turn_intent_learns_the_business_before_contact_details(self):
        base = [{"role": "assistant", "content": server.outbound_greeting()}]
        casual = server.turn_intent(base + [{"role": "user", "content": "Yes, I am free now."}])
        self.assertIn("what kind of business", casual)
        first = server.turn_intent(base + [{"role": "user", "content": "Too many calls every day"}])
        self.assertIn("answer calls on their behalf", first)
        pitched = base + [{"role": "user", "content": "Too many calls"}, {"role": "assistant", "content": "Ava can answer calls on your behalf and send a summary."}]
        second = server.turn_intent(pitched)
        self.assertIn("200-dollar-per-month", second)
        mailed = pitched + [{"role": "user", "content": "it is paul at example dot com, my email is paul@example.com"}]
        third = server.turn_intent(mailed)
        self.assertIn("200-dollar-per-month", third)

    def test_neutral_is_the_default_muga_tone(self):
        self.assertEqual(server.RUMIK_TONE, "neutral")
        self.assertIn("[excited] when asking a question", server.AVA_VOICE_RULES)

    def test_muga_voice_text_starts_every_utterance_with_one_tone(self):
        original = server.RUMIK_MODEL
        server.RUMIK_MODEL = "muga"
        try:
            self.assertEqual(server.voice_text("Hello friend."), "[neutral] Hello friend.")
            self.assertEqual(server.voice_text("[sad] <sigh> Sorry to hear that."), "[sad] <sigh> Sorry to hear that.")
            self.assertEqual(server.voice_text("Hello <laugh> friend. <scream> Wow."), "[neutral] Hello <laugh> friend. Wow.")
            self.assertEqual(server.voice_text("Hello <curious> friend."), "[neutral] Hello friend.")
            self.assertEqual(server.voice_text(""), "")
        finally:
            server.RUMIK_MODEL = original

    def test_rumik_sales_voice_profile_is_conversational(self):
        options = server.rumik_voice_options()
        if server.RUMIK_VOICE_FRAME.get("model") == "mulberry":
            self.assertEqual(options["description"], server.RUMIK_VOICE_FRAME["description"])
            self.assertEqual(options["speaker"], server.RUMIK_VOICE_FRAME["speaker"])
        else:
            self.assertEqual(options["model"], server.RUMIK_VOICE_FRAME["model"])
            self.assertEqual(options["temperature"], server.RUMIK_VOICE_FRAME["temperature"])

    def test_dropped_rumik_session_falls_back_to_synthesis(self):
        class BrokenSession:
            async def send(self, text):
                raise OSError("session dropped")

        class Socket:
            def __init__(self):
                self.events = []

            async def send(self, event):
                self.events.append(event)

        socket = Socket()
        with patch("server.audio", return_value=(b"wav", "audio/wav")), patch("server.wav_to_mulaw", return_value=b"audio"):
            import asyncio
            asyncio.run(server.send_twilio_audio(socket, "stream", "Hello.", BrokenSession()))
        self.assertTrue(socket.events)

    def test_hindi_greeting_has_speech_text(self):
        original = server.DB_PATH
        with tempfile.TemporaryDirectory() as directory:
            server.DB_PATH = Path(directory) / "test.db"
            try:
                server.init_db()
                server.set_settings({"call_mode": "hindi"})
                self.assertNotIn("AI Helper", server.outbound_greeting())
            finally:
                server.DB_PATH = original

    def test_assemblyai_uses_native_phone_audio(self):
        config = server.assemblyai_stt_config()
        self.assertEqual((config["encoding"], config["sample_rate"]), ("pcm_mulaw", 8000))
        self.assertEqual(config["speech_model"], "universal-3-5-pro")


if __name__ == "__main__":
    unittest.main()
