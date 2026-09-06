import tempfile
import unittest
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
                self.assertIn("Do not explain features, price, or the full solution", server.agent_system())
                self.assertIn("one idea per sentence", server.agent_system())
                self.assertIn("longer pause after objections", server.agent_system())
                self.assertIn("AI Helper", server.outbound_greeting())
            finally:
                server.DB_PATH = original

    def test_mulberry_strips_voice_changing_events(self):
        original = server.RUMIK_MODEL
        server.RUMIK_MODEL = "mulberry"
        try:
            self.assertEqual(server.voice_text("Hello <laugh> friend."), "Hello  friend.")
            self.assertEqual(server.tts_text("Hello <not-a-tag> friend."), "Hello  friend.")
        finally:
            server.RUMIK_MODEL = original

    def test_rumik_sales_voice_profile_is_conversational(self):
        original = server.RUMIK_SPEAKER
        server.RUMIK_SPEAKER = ""
        try:
            options = server.rumik_voice_options()
            self.assertNotIn("description", options)
            self.assertEqual(options["speaker"], "speaker_1")
        finally:
            server.RUMIK_SPEAKER = original

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
                self.assertIn("नमस्ते", server.outbound_greeting())
            finally:
                server.DB_PATH = original


if __name__ == "__main__":
    unittest.main()
