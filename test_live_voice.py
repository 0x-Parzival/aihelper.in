"""Small offline checks for the live Twilio audio bridge."""
import asyncio
import io
import json
import os
import urllib.error
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

    def test_outbound_greeting_is_spoken_owner_instruction_stays_private(self):
        # The owner instruction must never be read aloud as the greeting; the
        # agent receives it as private context and speaks the short hello.
        company = server.create_company("Acme", "a secure company password")
        server.start_call("CA-out", "outbound", "+14155550123", "Treat them as the owner and pitch", company["slug"], "Priya")
        call = server.load_call("CA-out")
        self.assertEqual(call["context"], "Treat them as the owner and pitch")
        transcript = [m for m in json.loads(call["transcript"]) if m.get("role") == "assistant"]
        self.assertEqual(len(transcript), 1)
        self.assertNotIn("owner", transcript[0]["content"])
        self.assertNotIn("AI Helper", transcript[0]["content"])

    def test_company_calls_are_isolated_and_keep_follow_up(self):
        company = server.create_company("Acme", "a secure company password")
        server.start_call("CA123", "outbound", "+14155550123", "Confirm the appointment", company["slug"], "Priya")
        server.set_next_response("CA123", company["slug"], "Call back tomorrow morning")
        calls = server.company_calls(company["slug"])
        self.assertEqual(calls[0]["contact_name"], "Priya")
        self.assertEqual(calls[0]["next_response"], "Call back tomorrow morning")
        self.assertIn("Priya", server.contact_memory(calls[0]))

    def test_same_number_reuses_saved_call_summary(self):
        company = server.create_company("Acme", "a secure company password")
        server.start_call("CA123", "outbound", "+14155550123", "", company["slug"], "Priya")
        server.save_transcript("CA123", [{"role": "user", "content": "I need a callback tomorrow."}])
        server.finish_call("CA123", "completed", "Caller needs a callback tomorrow.")
        server.start_call("CA456", "inbound", "+14155550123", company_slug=company["slug"])
        memory = server.contact_memory(server.load_call("CA456"))
        self.assertIn("Priya", memory)
        self.assertIn("callback tomorrow", memory)

    def test_call_context_reads_contact_history_once_per_turn(self):
        call = {"transcript": "[]", "context": "", "company_slug": "", "number": "+14155550123"}
        with patch("server.contact_memory", return_value="{}") as memory:
            server.call_messages(call)
        memory.assert_called_once_with(call)

    def test_silence_nudge_fires_only_on_real_dead_air(self):
        base = {"speaking": False, "last_activity": 100.0, "last_spoken_at": 100.0}
        self.assertFalse(server.silence_nudge_due(base, 112.0))
        self.assertTrue(server.silence_nudge_due(base, 120.0))
        self.assertFalse(server.silence_nudge_due({**base, "speaking": True}, 200.0))
        self.assertFalse(server.silence_nudge_due({"speaking": False, "last_activity": 100.0, "last_spoken_at": 50.0}, 112.0))

    def test_call_messages_hold_private_intent_never_spoken(self):
        call = {"transcript": json.dumps([{"role": "user", "content": "Too many calls"}]), "context": "owner demo", "company_slug": "", "number": "+14155550123"}
        with patch("server.contact_memory", return_value=""):
            messages = server.call_messages(call)
        intent = [m for m in messages if "Private intent" in str(m.get("content", ""))]
        self.assertEqual(len(intent), 1)
        self.assertIn("stage=", intent[0]["content"])
        self.assertIn("Too many calls", intent[0]["content"])
        spoken = server.public_call_messages({"transcript": json.dumps(messages)})
        self.assertFalse(any("Private intent" in str(m.get("content", "")) for m in spoken))

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

    def test_plivo_can_play_the_locked_voice_greeting_before_streaming(self):
        xml = server.plivo_stream_twiml("test-greeting", "https://example.com/fixed-voice.wav").decode()
        self.assertLess(xml.index("<Play>"), xml.index("<Stream"))
        self.assertIn("fixed-voice.wav", xml)

    def test_telnyx_call_ids_and_resources_use_current_texml_shape(self):
        call_sid = "v3:call_sid-123"
        server.GREET_CACHE.clear()
        with patch.object(server, "telnyx_request", return_value={"call_sid": call_sid, "status": "queued"}) as request:
            with patch.object(server, "setting", side_effect=lambda key: {"TELNYX_ACCOUNT_SID": "acct", "TELNYX_API_KEY": "key", "TELNYX_TEXML_APPLICATION_SID": "app", "TELNYX_PHONE_NUMBER": "+14155550123", "PUBLIC_BASE_URL": "https://example.com", "RUMIK_API_KEY": "rumik", "ASSEMBLYAI_API_KEY": "assembly"}.get(key, "")), patch.object(server, "audio", return_value=(b"wav", "audio/wav")) as audio:
                result = server.telnyx_outbound_call("+442071838750")
        audio.assert_called_once()
        self.assertEqual(server.GREET_CACHE[call_sid][0], b"wav")
        self.assertEqual(result, {"sid": call_sid, "status": "queued", "provider": "telnyx"})
        self.assertEqual(request.call_args.args[0], "Calls")
        self.assertEqual(request.call_args.args[1]["ApplicationSid"], "app")
        self.assertIn("RecordingStatusCallback", request.call_args.args[1])
        # Answer is a bare stream URL: speech comes from the live session with
        # no Play fetch in the critical path.
        self.assertTrue(request.call_args.args[1]["Url"].endswith("/telnyx/voice"))

    def test_outbound_opener_speaks_greeting_then_listens(self):
        # One sentence at a time: the opener speaks only the seeded greeting
        # and waits for the caller instead of reciting a block.
        server.start_call("CA-intro", "outbound", "+442071838750", "owner demo")
        transcript = json.loads(server.load_call("CA-intro")["transcript"])
        self.assertEqual([m["content"] for m in transcript], [server.outbound_greeting()])

        class Socket:
            def __init__(self):
                self.events = []

            async def send(self, event):
                self.events.append(json.loads(event))

        async def speak():
            socket = Socket()
            session = {"call_sid": "CA-intro", "stream_sid": "stream", "messages": list(transcript), "rumik": None, "provider": "telnyx", "voice_options": None, "last_agent_response": ""}
            with patch("server.audio", return_value=(b"wav", "audio/wav")), patch("server.wav_to_mulaw", return_value=b"audio"):
                await server.speak_opener(socket, session)
            return socket, session

        socket, session = asyncio.run(speak())
        self.assertEqual(len(socket.events), 1)
        self.assertEqual(session["last_agent_response"], server.outbound_greeting())

    def test_opener_prefers_dial_time_audio_over_fresh_synthesis(self):
        # Greeting bytes made during ring time play with no TTS wait at all.
        server.GREET_CACHE.clear()
        server.start_call("CA-pre", "outbound", "+442071838750", "owner demo")
        server._store_greet("CA-pre", server.silence_wav(0.2))

        class Socket:
            def __init__(self):
                self.events = []

            async def send(self, event):
                self.events.append(json.loads(event))

        async def run():
            socket = Socket()
            session = {"call_sid": "CA-pre", "stream_sid": "stream", "messages": json.loads(server.load_call("CA-pre")["transcript"]), "rumik": None, "provider": "telnyx", "voice_options": None, "last_agent_response": "", "emotion": server.EmotionState(), "speaking": False}
            with patch("server.audio", side_effect=AssertionError("no fresh TTS")), patch("server.stream_call_reply", return_value=None) as reply, patch.object(server, "OPENER_NUDGE_SECONDS", 0):
                await server.opener_with_nudge(socket, session)
            return socket, session, reply

        socket, session, reply = asyncio.run(run())
        self.assertGreater(len(socket.events), 0)
        self.assertEqual(session["last_agent_response"], server.outbound_greeting())
        reply.assert_called_once()
        server.GREET_CACHE.clear()

    def test_media_frame_event_matches_provider_protocol(self):
        telnyx = server.media_frame_event("telnyx", "s", "QUJD")
        self.assertEqual(telnyx, {"event": "media", "media": {"payload": "QUJD"}})
        plivo = server.media_frame_event("plivo", "s", "QUJD")
        self.assertEqual(plivo["event"], "playAudio")
        twilio = server.media_frame_event("twilio", "s", "QUJD")
        self.assertEqual(twilio["streamSid"], "s")

    def test_telnyx_texml_starts_one_voice_stream_without_separate_greeting_audio(self):
        xml = server.telnyx_stream_texml("v3:call", "Hi Priya, is that you?").decode()
        self.assertIn("<Connect>", xml)
        self.assertNotIn("<Play>", xml)

    def test_telnyx_greeting_plays_before_connect_never_inside_it(self):
        # <Play> nested in <Connect> is invalid TeXML and Telnyx drops the
        # flow, leaving a silent call.
        with patch("server.audio", return_value=(b"wav", "audio/wav")):
            xml = server.telnyx_stream_texml("v3:call", "uuid", "Hello.").decode()
        self.assertIn("<Play", xml)
        self.assertLess(xml.index("<Play"), xml.index("<Connect>"))
        self.assertLess(xml.index("</Play>"), xml.index("<Connect>"))

    def test_stream_token_allows_telnyx_reconnects_until_expiry(self):
        server.LIVE_STREAM_TOKENS["v3:reconnect"] = ("token", 9_999_999_999)
        self.assertTrue(server.consume_live_stream_token("v3:reconnect", "token"))
        self.assertTrue(server.consume_live_stream_token("v3:reconnect", "token"))

    def test_prepared_plivo_duration_is_exact(self):
        self.assertEqual(server.plivo_playback_seconds(b"\0" * 20_000), 2.5)

    def test_telnyx_audio_uses_its_native_media_frame(self):
        class Socket:
            def __init__(self):
                self.events = []

            async def send(self, event):
                self.events.append(json.loads(event))

        socket = Socket()
        with patch("server.audio", return_value=(b"wav", "audio/wav")), patch("server.wav_to_mulaw", return_value=b"audio"):
            asyncio.run(server.send_twilio_audio(socket, "stream", "Hello.", provider="telnyx"))
        self.assertEqual(socket.events, [{"event": "media", "media": {"payload": "YXVkaW8="}}])

    def test_live_rumik_session_streams_without_waiting_for_wav(self):
        class Chunk:
            data = b"pcm"

        class Done:
            pass

        class Session:
            async def send(self, text):
                self.text = text

            async def events(self):
                yield Chunk()
                yield Done()

        class Socket:
            def __init__(self):
                self.events = []

            async def send(self, event):
                self.events.append(json.loads(event))

        socket, session = Socket(), Session()
        with patch("server.AudioChunk", Chunk), patch("server.UtteranceDone", Done), patch("server.pcm24_to_mulaw", return_value=(b"audio", None)), patch("server.audio") as audio:
            asyncio.run(server.send_twilio_audio(socket, "stream", "Hello.", session, "telnyx"))
        audio.assert_not_called()
        self.assertEqual(socket.events, [{"event": "media", "media": {"payload": "YXVkaW8="}}])

    def test_streaming_utterance_uses_session_pinned_voice(self):
        # The rumikai session pins model/description/speaker at connect time and
        # send() takes bare text only — every utterance therefore shares the
        # frozen voice without per-utterance overrides.
        class Chunk:
            data = b"pcm"

        class Done:
            pass

        class Session:
            def __init__(self):
                self.texts = []

            async def send(self, text):
                self.texts.append(text)

            async def events(self):
                yield Chunk()
                yield Done()

        class Socket:
            def __init__(self):
                self.events = []

            async def send(self, event):
                self.events.append(json.loads(event))

        socket, session = Socket(), Session()
        with patch("server.AudioChunk", Chunk), patch("server.UtteranceDone", Done), patch("server.pcm24_to_mulaw", return_value=(b"audio", None)), patch("server.audio") as audio:
            asyncio.run(server.send_twilio_audio(socket, "stream", "Hello.", session, "telnyx"))
        audio.assert_not_called()
        self.assertEqual(session.texts, [server.voice_text("Hello.")])
        options = server.rumik_session_options()
        expected = {key: server.rumik_voice_options()[key] for key in ("model", "description", "speaker") if key in server.rumik_voice_options()}
        self.assertEqual(options, expected)

    @patch.dict(os.environ, {"PLIVO_AUTH_ID": "", "PLIVO_AUTH_TOKEN": "", "PLIVO_PHONE_NUMBER": ""})
    @patch("server.telnyx_outbound_call")
    def test_international_outbound_calls_use_telnyx(self, telnyx):
        server.outbound_call("+14155550123")
        telnyx.assert_called_once_with("+14155550123")

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
        self.assertEqual(neutral, server.RUMIK_DESCRIPTION)
        self.assertEqual(neutral, frustrated)
        self.assertIn("high-pitched female voice", neutral)

    def test_outbound_greeting_confirms_the_contact_before_the_pitch(self):
        self.assertNotIn("AI Helper", server.outbound_greeting({"contact_name": "Priya"}))
        self.assertNotIn("AI Helper", server.outbound_greeting())

    def test_only_explicit_adult_voice_requests_change_persona(self):
        self.assertEqual(server.requested_voice_persona("Please change your voice to a woman."), "female")
        self.assertEqual(server.requested_voice_persona("Can you speak like a man?"), "male")
        self.assertIsNone(server.requested_voice_persona("I am a man."))
        self.assertEqual(server.voice_description("indian", persona="female"), server.RUMIK_DESCRIPTION)

    def test_business_context_selects_a_pre_call_baseline(self):
        profile = server.business_voice_profile({"company_slug": "", "context": "A dental clinic appointment assistant"})
        self.assertIn("healthcare assistant", profile)
        profile = server.business_voice_profile({"company_slug": "", "context": "SaaS platform sales call"})
        self.assertIn("explainer video voice", profile)

    def test_call_purpose_is_available_for_every_call(self):
        self.assertEqual(server.call_purpose({"direction": "outbound", "context": "Confirm a booked demo"}), "Confirm a booked demo")
        self.assertIn("inbound business call", server.call_purpose({"direction": "inbound", "context": ""}))

    def test_only_one_safe_voice_tag_survives(self):
        self.assertEqual(server.sanitize_voice_tags("<laugh> That's good. <chuckle>"), "<laugh> That's good.")
        self.assertEqual(server.sanitize_voice_tags("<scream> Please call 911."), "Please call 911.")

    def test_ava_personality_reaches_enterprise_tenants(self):
        token = server.tenant_limits.CURRENT.set({"business_name": "Acme", "call_mode": "English", "business_knowledge": "Appointments"})
        try:
            prompt = server.agent_system()
        finally:
            server.tenant_limits.CURRENT.reset(token)
        self.assertIn("Your name is Ava", prompt)
        self.assertIn("Laugh with the caller", prompt)
        self.assertIn("briefly and specifically appreciate", prompt)
        self.assertIn("answer their actual question directly", prompt)
        self.assertIn("playful observational humor", prompt)

    def test_every_mulberry_utterance_uses_a_fixed_preset_speaker(self):
        first = server.rumik_voice_options()
        second = server.rumik_voice_options(accent="british", persona="male", description="something else")
        self.assertEqual(first, second)
        if server.RUMIK_VOICE_FRAME.get("model") == "mulberry":
            self.assertEqual(first["speaker"], server.RUMIK_VOICE_FRAME["speaker"])
            self.assertEqual(first["description"], server.RUMIK_VOICE_FRAME["description"])
            for key in ("temperature", "top_p", "max_new_tokens"):
                self.assertEqual(first[key], server.RUMIK_VOICE_FRAME[key])
            self.assertNotIn("f0_up_key", first)
        else:
            # Muga is tone-steered: base selector plus sampling pins only.
            self.assertEqual(set(first), {"model", "temperature", "top_p", "max_new_tokens"})

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
        call = rumik.return_value.__enter__.return_value.speech.create.call_args
        self.assertEqual(call.kwargs["text"], server.voice_text("Hello"))
        for key, value in server.rumik_voice_options().items():
            self.assertEqual(call.kwargs[key], value)

    @patch("server.setting", return_value="test-key")
    @patch("server.Rumik")
    def test_audio_ignores_per_utterance_voice_overrides(self, rumik, _setting):
        class Result:
            content_type = "audio/wav"

            def __bytes__(self):
                return b"wav"

        rumik.return_value.__enter__.return_value.speech.create.return_value = Result()
        server.audio("Hello", {"speaker": "male_voice", "description": "male voice"})
        call = rumik.return_value.__enter__.return_value.speech.create.call_args
        for key, value in server.rumik_voice_options().items():
            self.assertEqual(call.kwargs[key], value)

    def test_live_voice_setup_does_not_open_a_tts_session(self):
        session = {"voice_persona": "female", "voice_persona_applied": None, "accent": "indian", "business_voice_profile": "", "rumik": None}
        asyncio.run(server.set_live_voice(session))
        self.assertIsNone(session["rumik"])
        self.assertEqual(session["voice_description"], server.RUMIK_DESCRIPTION)

    def test_repeated_llm_outage_escalates_and_saves_spoken_recovery(self):
        # A Groq outage used to replay one identical line forever without a
        # trace in the transcript; it must escalate and be recorded instead.
        class Socket:
            def __init__(self):
                self.events = []

            async def send(self, event):
                self.events.append(json.loads(event))

        async def run_once(session):
            socket = Socket()
            with patch("server.groq_stream", side_effect=RuntimeError("groq down")), patch("server.audio", return_value=(b"wav", "audio/wav")), patch("server.wav_to_mulaw", return_value=b"audio"):
                await server.stream_call_reply(socket, session, [{"role": "user", "content": "Hello?"}])
            return socket

        session = {"call_sid": "test", "stream_sid": "stream", "messages": [], "rumik": None, "provider": "telnyx", "voice_options": None, "last_agent_response": ""}
        asyncio.run(run_once(session))
        asyncio.run(run_once(session))
        self.assertEqual(session["groq_failures"], 2)
        self.assertIn("understand your situation", session["messages"][-1]["content"])
        asyncio.run(run_once(session))
        self.assertEqual(session["groq_failures"], 3)
        self.assertIn("call you back shortly", session["messages"][-1]["content"])


if __name__ == "__main__":
    unittest.main()
