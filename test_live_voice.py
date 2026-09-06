"""Small offline checks for the live Twilio audio bridge."""
import asyncio
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
        self.original_db_path = server.DB_PATH
        self.temp_directory = tempfile.TemporaryDirectory()
        server.DB_PATH = Path(self.temp_directory.name) / "test.db"
        server.init_db()

    def tearDown(self):
        server.DB_PATH = self.original_db_path
        self.temp_directory.cleanup()

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
        company = server.create_company("Acme", "a secure company password")
        server.save_contact(company["slug"], "+14155550123", "Priya", '{"appointment":"Tuesday"}')
        server.start_call("CA123", "outbound", "+14155550123", "Confirm the appointment", company["slug"], "Priya")
        server.set_next_response("CA123", company["slug"], "Call back tomorrow morning")
        calls = server.company_calls(company["slug"])
        self.assertEqual(calls[0]["contact_name"], "Priya")
        self.assertEqual(calls[0]["next_response"], "Call back tomorrow morning")
        self.assertIn("Priya", server.contact_memory(calls[0]))

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

    def test_plivo_stream_uses_native_mulaw_for_reliable_bidirectional_audio(self):
        xml = server.plivo_stream_twiml("test-mulaw").decode()
        self.assertIn('contentType="audio/x-mulaw;rate=8000"', xml)

    def test_prepared_plivo_duration_is_exact(self):
        self.assertEqual(server.plivo_playback_seconds(b"\0" * 20_000), 2.5)

    @patch.dict(os.environ, {"PLIVO_AUTH_ID": "", "PLIVO_AUTH_TOKEN": "", "PLIVO_PHONE_NUMBER": ""})
    @patch("server.twilio_outbound_call")
    def test_outbound_calls_use_twilio_when_plivo_is_not_configured(self, twilio):
        server.outbound_call("+14155550123")
        twilio.assert_called_once_with("+14155550123")

    def test_phrase_buffer_waits_for_natural_chunk(self):
        self.assertEqual(server.take_speakable_phrase("one two three")[0], "")
        phrase, rest = server.take_speakable_phrase("one two three four five six seven eight, ")
        self.assertEqual(phrase, "")
        self.assertEqual(rest, "one two three four five six seven eight, ")

    def test_speech_chunks_release_sentences_before_generation_finishes(self):
        chunks, remainder = server.speech_chunks("Yeah, that helps. If someone calls while your staff is busy")
        self.assertEqual(chunks, ["Yeah, that helps."])
        self.assertEqual(remainder, "If someone calls while your staff is busy")
        chunks, remainder = server.speech_chunks(remainder, final=True)
        self.assertEqual(chunks, ["If someone calls while your staff is busy"])
        self.assertEqual(remainder, "")

    def test_first_phrase_is_a_short_complete_sentence(self):
        phrase, rest = server.take_speakable_phrase("Yes, I can help. What are you trying to do?", first=True)
        self.assertEqual(phrase, "Yes, I can help.")
        self.assertEqual(rest, "What are you trying to do?")
        self.assertEqual(rest, "What are you trying to do?")

    def test_emotion_requires_two_matching_windows(self):
        state = server.EmotionState()
        state.observe({"emotion": "angry", "energy": "high"}, now=10)
        self.assertIsNone(state.context())
        state.observe({"emotion": "angry", "energy": "high"}, now=12)
        self.assertEqual(state.context()["emotion"], "frustrated")
        self.assertEqual(state.context()["trend"], "increasing")

    def test_caller_accent_is_locked_from_call_region(self):
        self.assertEqual(server.caller_accent("+919876543210"), "indian")
        self.assertEqual(server.caller_accent("+442071838750"), "british")
        self.assertEqual(server.caller_accent("+4930123456"), server.RUMIK_DEFAULT_ACCENT)

    def test_delivery_changes_never_replace_the_locked_voice(self):
        accent = server.caller_accent("+919876543210")
        neutral = server.voice_description(accent)
        frustrated = server.voice_description(accent, {"emotion": "frustrated"})
        self.assertIn("Eva", neutral)
        self.assertEqual(neutral, frustrated)

    def test_only_explicit_adult_voice_requests_change_persona(self):
        self.assertEqual(server.requested_voice_persona("Please change your voice to a woman."), "female")
        self.assertEqual(server.requested_voice_persona("Can you speak like a man?"), "male")
        self.assertIsNone(server.requested_voice_persona("I am a man."))
        self.assertIn("adult female studio voice", server.voice_description("indian", persona="female"))

    def test_business_context_selects_a_pre_call_baseline(self):
        profile = server.business_voice_profile({"company_slug": "", "context": "A dental clinic appointment assistant"})
        self.assertIn("healthcare assistant", profile)
        profile = server.business_voice_profile({"company_slug": "", "context": "SaaS platform sales call"})
        self.assertIn("explainer video voice", profile)

    def test_call_purpose_is_available_for_every_call(self):
        self.assertEqual(server.call_purpose({"direction": "outbound", "context": "Confirm a booked demo"}), "Confirm a booked demo")
        self.assertIn("inbound business call", server.call_purpose({"direction": "inbound", "context": ""}))

    def test_only_one_safe_voice_tag_survives(self):
        self.assertEqual(server.sanitize_voice_tags("<curious> Could you confirm that? <laugh>"), "<curious> Could you confirm that?")
        self.assertEqual(server.sanitize_voice_tags("<scream> Please call 911."), "Please call 911.")

    def test_every_mulberry_utterance_uses_a_fixed_preset_speaker(self):
        original = server.RUMIK_SPEAKER
        try:
            server.RUMIK_SPEAKER = ""
            first = server.rumik_voice_options()
            server.RUMIK_SPEAKER = "invalid"
            second = server.rumik_voice_options()
            self.assertEqual(first["speaker"], "speaker_1")
            self.assertEqual(second["speaker"], "speaker_1")
        finally:
            server.RUMIK_SPEAKER = original

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
            text="Hello", model=server.RUMIK_MODEL, speaker="speaker_1"
        )

    def test_live_voice_setup_does_not_open_a_tts_session(self):
        session = {"voice_persona": "female", "voice_persona_applied": None, "accent": "indian", "business_voice_profile": "", "rumik": None}
        asyncio.run(server.set_live_voice(session))
        self.assertIsNone(session["rumik"])
        self.assertIn("adult female", session["voice_description"])


if __name__ == "__main__":
    unittest.main()
