# Human-like voice behaviour contract

`turn_manager.py` owns conversational timing.  It must be deterministic and
fast: it selects an action, but does not call ASR, the LLM, or TTS itself.
The media loop carries out its decision and may cancel any queued work.

## Public API

```python
from turn_manager import Action, TurnDecision, TurnInput, TurnManager

decision = TurnManager().decide(TurnInput(...))
```

`Action` is an enum with: `LISTEN`, `PLAN`, `SPEAK`, `BACKCHANNEL`,
`INTERRUPT`, and `REPAIR`.

`TurnInput` contains these keyword fields:

```text
partial_text, stability, pause_ms, caller_speaking, self_repair, asr_final,
end_of_turn, response_candidate, narrative_ms, agent_speaking, now_ms,
sensitive_context, high_impact_field, ambiguous_options, speech_rate_wpm
```

`TurnDecision` contains `action`, a machine-readable `reason`, and optional
`response_candidate`, `backchannel`, or `clarification`.  `TurnManager.observe_caller_style(text)`
updates only per-call style state; `response_style()` returns a dictionary.

## Required behaviour

- **Floor management:** a caller who is speaking always owns the floor. If the
  agent is speaking, caller speech returns `STOP_SPEAKING` immediately. A
  silence shorter than the completion threshold returns `KEEP_LISTENING`.
- **Self repair:** phrases such as “sorry”, “I mean”, or a restart must extend
  the listening window. Do not answer a partial value that the caller is
  correcting.
- **Active listening:** issue a quiet acknowledgement only during a sustained
  intelligible narrative, with no final turn, no sensitive context, no recent
  acknowledgement, and no imminent completion. It must never be timer/random
  driven. Allow-list: `mm-hm`, `right`, `I see`; use at most one per 8 seconds
  and never use it while the caller speaks a name, number, address, payment,
  health, legal, safety, or consent detail.
- **Clarification:** when a low-confidence or conflicting high-impact detail
  blocks progress, return `REPAIR` with a targeted question naming the missing
  field (for example, “Did you mean Tuesday or Thursday?”). Never emit the
  generic “I didn’t catch that” unless no specific repair can be formed.
- **Accent lock:** choose one supported accent from the caller's E.164 calling
  region when the call begins, then keep it unchanged for the whole call. Never
  infer or imitate accent, dialect, or identity from speech. For an unknown or
  ambiguous region, use the configured Global fallback.
- **Voice presentation:** change to an adult masculine or feminine presentation
  only after an explicit caller request. Do not infer it from their voice, and
  do not generate child or teen voices. This changes no accent setting.
- **Bounded adaptation:** caller terms, directness, and pace may guide wording
  and response length. Never infer or imitate disability, age, gender,
  ethnicity, or emotional vulnerability. Do not store this style state beyond
  the call.

## Integration checklist

- Current Muga integration uses one leading tone and optional compatible inline event,
  chosen by Jev when configured. Muga does not use `speaker_1`. See
  [VOICE_RESEARCH.md](VOICE_RESEARCH.md) for current runtime behavior and limits;
  the framework-oriented checklist below is guidance, not a claim that every hook is connected.
- Before the greeting, select the baseline profile from the business name,
  configured campaign, and authorized call objective. Use
  `RUMIK_BUSINESS_VOICE_PROFILE` for a business-approved exact description;
  this override still receives the locked caller-region accent.
- Feed stable partial ASR results into `decide` continuously; final ASR is not
  the only turn signal. Feed per-turn transcript-derived speech rate into
  bounded response-style adaptation; the accent remains the call-start region
  selection and must not be altered by the transcript or model.
- On `BEGIN_RESPONSE`, start cancellable response/TTS streaming; first audio
  should be a short complete sentence.
- On `STOP_SPEAKING`, cancel every pending LLM/TTS/playback task before handling
  the caller’s new speech.
- On `BACKCHANNEL`, play only the supplied short acknowledgement and continue
  listening; never add it to the business transcript as caller intent.
- On `REPAIR`, preserve known context, ask the supplied specific question, and
  confirm names, numbers, dates, addresses, price, and consent.
- Emit latency and decision telemetry: input time, action, reason, first audio,
  playback, and barge-in cancellation. Record audio only under applicable
  consent policy.

## Acceptance scenarios

Run `python -m unittest test_conversation_behaviors.py` after implementing the
module, then make consented test calls for: long narratives, mid-thought
pauses, self-corrections, interruptions, ambiguous dates/numbers, and fast vs
detail-oriented callers.
