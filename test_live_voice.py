"""Small offline checks for the live Twilio audio bridge."""
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

import server


class LiveVoiceTests(unittest.TestCase):
    def setUp(self):
        server.RATE_LIMITS.clear()

    def test_rate_limit_resets_after_its_window(self):
        self.assertTrue(server.allow_request("test", 2, 60, now=100))
        self.assertTrue(server.allow_request("test", 2, 60, now=101))
        self.assertFalse(server.allow_request("test", 2, 60, now=102))
        self.assertTrue(server.allow_request("test", 2, 60, now=160))

    def test_company_password_is_hashed_and_verified(self):
        stored = server.password_hash("a secure company password")
        self.assertNotIn("a secure company password", stored)
        self.assertTrue(server.password_matches("a secure company password", stored))
        self.assertFalse(server.password_matches("wrong password", stored))

    def test_company_slug_is_url_safe(self):
        self.assertEqual(server.company_slug("Acme & Sons Ltd."), "acme-sons-ltd")

    def test_company_calls_are_isolated_and_keep_follow_up(self):
        original_path = server.DB_PATH
        with tempfile.TemporaryDirectory() as directory:
            server.DB_PATH = Path(directory) / "test.db"
            try:
                server.init_db()
                company = server.create_company("Acme", "a secure company password")
                server.save_contact(company["slug"], "+14155550123", "Priya", '{"appointment":"Tuesday"}')
                server.start_call("CA123", "outbound", "+14155550123", "Confirm the appointment", company["slug"], "Priya")
                server.set_next_response("CA123", company["slug"], "Call back tomorrow morning")
                calls = server.company_calls(company["slug"])
                self.assertEqual(calls[0]["contact_name"], "Priya")
                self.assertEqual(calls[0]["next_response"], "Call back tomorrow morning")
                self.assertIn("Priya", server.contact_memory(calls[0]))
            finally:
                server.DB_PATH = original_path

    def test_twilio_audio_conversion(self):
        pcm = b"\0\0" * 1600
        source = io.BytesIO()
        with wave.open(source, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(pcm)
        mulaw = server.wav_to_mulaw(source.getvalue())
        restored, _ = server.mulaw_to_pcm16(mulaw, None)
        self.assertGreater(len(mulaw), 0)
        self.assertGreater(len(restored), 0)

    def test_phrase_buffer_waits_for_natural_chunk(self):
        self.assertEqual(server.take_speakable_phrase("one two three")[0], "")
        phrase, rest = server.take_speakable_phrase("one two three four five six seven eight, ")
        self.assertEqual(phrase, "one two three four five six seven eight,")
        self.assertEqual(rest, "")

    def test_emotion_requires_two_matching_windows(self):
        state = server.EmotionState()
        state.observe({"emotion": "angry", "energy": "high"}, now=10)
        self.assertIsNone(state.context())
        state.observe({"emotion": "angry", "energy": "high"}, now=12)
        self.assertEqual(state.context()["emotion"], "frustrated")
        self.assertEqual(state.context()["trend"], "increasing")

    def test_assembly_key_alias_is_accepted(self):
        previous = os.environ.pop("ASSEMBLYAI_API_KEY", None)
        alias = os.environ.get("ASSEMBLY_API_KEY")
        os.environ["ASSEMBLY_API_KEY"] = "test-key"
        try:
            self.assertEqual(server.setting("ASSEMBLYAI_API_KEY"), "test-key")
        finally:
            if alias:
                os.environ["ASSEMBLY_API_KEY"] = alias
            else:
                os.environ.pop("ASSEMBLY_API_KEY", None)
            if previous:
                os.environ["ASSEMBLYAI_API_KEY"] = previous

    @patch("server.setting", return_value="test-key")
    @patch("server.Rumik")
    def test_audio_uses_rumik(self, rumik, _setting):
        class Result:
            content_type = "audio/wav"

            def __bytes__(self):
                return b"wav"

        result = Result()
        rumik.return_value.__enter__.return_value.speech.create.return_value = result
        self.assertEqual(server.audio("Hello"), (b"wav", "audio/wav"))
        rumik.return_value.__enter__.return_value.speech.create.assert_called_once_with(
            text="Hello", model=server.RUMIK_MODEL, description=server.RUMIK_DESCRIPTION, speaker=server.RUMIK_SPEAKER
        )


if __name__ == "__main__":
    unittest.main()
