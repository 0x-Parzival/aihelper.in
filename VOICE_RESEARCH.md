# Voice stack decisions — 20 September 2026

## Verified provider guidance

**Telnyx:** Keep bidirectional RTP and explicitly request PCMU for both directions of the TeXML stream. The carrier stream must match the decoder; treating default PCMA or another negotiated codec as PCMU corrupts speech. A narrowband telephone connection cannot gain lost frequencies by upsampling. Pace outbound audio and clear it when interrupted. For global deployment, measure the media path and application-to-model round trips in target markets; use the connection's latency-based AnchorSite where applicable. No single VPS location is best for every country. Sources: [TeXML Stream](https://developers.telnyx.com/docs/voice/programmable-voice/texml-verbs/stream/index), [AnchorSite](https://support.telnyx.com/en/articles/5271423-guide-to-sip-anchorsite-settings).

**AssemblyAI:** Current model-selection documentation recommends `universal-3-5-pro` for streaming voice agents, with entity accuracy, conversational context and native code switching. English and Hindi are supported. Use native 8 kHz μ-law for this telephone pipeline, known-name keyterms, and adjustable endpointing. The implementation uses 160 ms minimum / 900 ms maximum silence as initial tuning values, not proven universal optima. Confirm email, dates, money and names through conversation; no model delivers guaranteed 100% transcription. Sources: [model selection](https://www.assemblyai.com/docs/streaming/select-the-speech-model), [turn detection](https://www.assemblyai.com/docs/universal-streaming/turn-detection), [streaming integration guidance](https://www.assemblyai.com/docs/coding-agent-prompts).

**Groq:** `qwen/qwen3.8-27b` supports non-thinking operation using `reasoning_effort="none"`; the implementation keeps temperature 0.7 / top_p 0.8 and short outputs. This is a preview model and needs account-level availability checks. The environment can retain a different supported Qwen model. Token throughput is not measured end-to-end response latency. Source: [Groq Qwen 3.8](https://console.groq.com/docs/model/qwen/qwen3.8-27b).

**Rumik Muga:** Buffer the complete tagged utterance before TTS; incomplete streamed tags may be spoken literally. Use one leading tone per paragraph, supported lowercase inline events (`laugh`, `chuckle`, `sigh`), and compatible emotion/event pairs. The documented sweet spot is 2–30 seconds; split longer speech at sentence boundaries. Muga supports Hindi/English, not all languages offered by AssemblyAI. Its expressive design trades latency against token-by-token synthesis; retaining Muga means respecting that trade-off. Sampling 0.7 is the documented consistency recommendation. Source: [Muga prompting](https://docs.rumik.ai/prompting-muga.md).

**Jev via OpenRouter:** One `/api/alpha/decisions` request can classify next action, tone and event together. `~typesafe/jev-latest` is the current latest alias. Confidence describes the output distribution; it is not proven decision accuracy or a customer's buying probability. Runtime advice has a 700 ms application deadline and conservative fallback. Lead scoring runs outside the conversation loop. Sources: [OpenRouter Jev](https://openrouter.ai/typesafe), [Decisions example](https://openrouter.ai/labs/jev/compile).

**Leading framework practice:** Naturalness depends on turn detection, interruptions, distinguishing backchannels, and latency measurement—not just a human-sounding TTS voice. LiveKit documents adaptive interruptions and false-interruption recovery. This implementation uses substantive partial transcripts for interruption, native audio pacing and honest incomplete-delivery annotations. It does not claim to reproduce LiveKit's acoustic interruption model. Source: [LiveKit turn tuning](https://docs.livekit.io/agents/logic/turns/tuning/).

## Implemented workflow

1. Save a scoped contact and research its public business page from the dashboard. Source URLs and excerpts remain explicitly unverified. Private-network destinations, DNS rebinding and unsafe redirects are rejected.
2. Start a call with persistent contact knowledge and earlier conversation context. Ask about actual needs; answer direct questions first; confirm email through readback. Never claim an email, booking or payment completed without a successful action.
3. The dashboard shows paginated calls, recordings, transcript, summary and contact evidence. Interrupted assistant text is labeled because exact audible word alignment is unavailable.
4. Add businesses to the outreach queue and explicitly enable each lead. Score with Jev, then use **Call next eligible business**. High/medium/low map to **90/60/20 readiness points**, a categorical rubric—not conversion odds. Unknown or uncertain leads need research. Due callbacks rank before general outreach. Future callbacks, opt-outs, stale/evidence-changed scores, existing calls and attempts within 24 hours block dialing. An uncertain dial remains claimed for reconciliation.

## Configuration and validation

Use Python 3.11 from the existing virtualenv; system Python 3.8 is incompatible with current runtime constructs. No new package dependency is required by the added modules.

```dotenv
CALL_PROVIDER=telnyx
ASSEMBLYAI_STREAMING_MODEL=universal-3-5-pro
GROQ_MODEL=qwen/qwen3.8-27b
RUMIK_MODEL=muga
RUMIK_TONE=neutral
STT_MIN_TURN_SILENCE=160
STT_MAX_TURN_SILENCE=900
MAX_CALL_SECONDS=1800
MAX_CALL_TURNS=200
# OPENROUTER_API_KEY: provision privately; never commit a real key.
```

Operator defaults allow 30 minutes / 200 caller turns; existing tenant caps remain unchanged until explicitly configured. Stored contact evidence is durable. The live prompt retains earlier verbatim dialogue up to 64k characters; beyond that, the middle is omitted with a visible private marker while the database retains the full transcript. STT disconnection currently ends gracefully rather than silently pretending it can still hear. Region-specific live listening, recovery and concurrency tests remain necessary.

Run `/opt/aihelper/.venv/bin/python -m unittest discover -p 'test_*.py'` from this checkout. Before rollout, back up the live SQLite database and reconcile active calls. Deploy Python modules and dashboard together; preserve production credentials and data. The live service currently runs from `/opt/aihelper`, a separate directory. These changes do not automatically restart it or start outreach.

Measure ASR-final-to-first-audio latency at p50/p95, interruption delay, transcription errors for names/email/amounts, repeat-question rate, hangup/recovery behavior, opt-outs and voluntary qualified next steps on authorized test calls. Stored `response_latency_ms` measures reply processing to first audio sent, not remote playback. Establish purchase-rate calibration from actual outcomes before displaying any percentage as a probability. No sales conversion, popularity, zero-error call or world's-best claim is established by offline tests.
