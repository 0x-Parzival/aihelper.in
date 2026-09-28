# AI Helper

<p align="center">
  <img src="assets/aihelper-logo.png" alt="AI Helper" width="128">
</p>

<h3 align="center">A voice agent that answers, remembers, and follows through.</h3>

<p align="center">
  A practical AI calling platform with real-time speech, a live 3D web avatar,
  persistent caller context, and a workspace for managing conversations.
</p>

<p align="center">
  <a href="https://aihelper.in">Website</a> ·
  <a href="https://aihelper.in/#voice-demo">Try the voice demo</a>
</p>

---

## What makes it different

AI Helper brings a low-latency voice pipeline and useful business context together. It can answer inbound calls, place owner-directed outbound calls, and keep transcripts, summaries, and follow-up context available to the team.

- **Natural, real-time conversations.** AssemblyAI streams phone audio into text; Groq generates the next response; Rumik Muga turns it into expressive speech. The services run as a streaming call loop instead of waiting for a complete recording before responding.
- **Jev-guided call handling.** When configured, Jev provides structured advice about the next conversational move, tone, and sales stage. The application validates its output and has a local fallback, so Jev advises the flow rather than executing call actions.
- **Calls in both directions.** The agent handles inbound calls and can make outbound calls initiated by an authorized operator. Plivo and Telnyx provide telephony, with provider selection centralized in the server.
- **Memory between calls.** Contact records retain caller statements and evidence-backed details, scoped to a business and phone number. Confirmed owner-entered facts are protected from being silently replaced by automated data; opt-outs are retained.
- **A 3D presence on the website.** The homepage embeds a separately hosted speaking avatar. Browser audio and conversation state drive its speech animation, giving visitors a live face for the interactive voice demo.
- **A real operating workspace.** The dashboard supports business and call management, transcripts, summaries, and contact context. The auth service provides account access for company workspaces.

## How a voice turn works

```text
Caller audio
    │
    ▼
Plivo or Telnyx ── PCMU media stream ──► AssemblyAI streaming transcription
                                                │
                                                ▼
                                    Conversation and memory context
                                      ├─ Groq generates a reply
                                      └─ Jev may advise the next move
                                                │
                                                ▼
                                         Rumik Muga speech
                                                │
                                                ▼
                                      Audio back to the caller
```

The call server coordinates media, transcripts, conversation state, provider callbacks, and call completion. It keeps call limits and usage reservations in the provider path, verifies provider webhooks, and stores call and contact data in SQLite. AssemblyAI uses phone-native 8 kHz μ-law audio for the telephony stream, avoiding an unnecessary audio conversion in the normal call path.

Jev is an optional OpenRouter-backed decision aid. If unavailable or if its response fails validation, the conversation can use the local fallback. The voice generation and transcription paths are configured independently through environment settings.

## Website experience

The homepage is a responsive, dependency-light HTML/CSS/JavaScript site. Visitors can speak with the browser demo or request a demo call. The speaking 3D avatar is embedded from `spiritual-face.keshavbruh.workers.dev`; the homepage sends it animation and cursor updates while the demo plays audio. The avatar is a hosted web experience rather than a model bundled into this repository.

The customer dashboard is served from `dashboard/`. Static pages include privacy, terms, and AI disclosure information. The Python server also serves authenticated APIs and provider webhooks; the Node.js auth service handles account authentication and email verification.

## Stack

| Area | Technology |
| --- | --- |
| Website and dashboard | HTML, CSS, browser JavaScript |
| Calling and API server | Python 3.11, standard library HTTP server, `websockets` |
| Streaming transcription | AssemblyAI Universal Streaming |
| Response generation | Groq; optional Jev advice through OpenRouter |
| Speech generation | Rumik Muga |
| Phone networks | Plivo and Telnyx (Twilio integration is also present) |
| Data | SQLite |
| Authentication | Node.js 22+, Express, Better Auth, SQLite |
| Deployment | systemd, Nginx or Caddy, TLS |

## Repository map

```text
.
├── index.html                 Homepage and live browser demo
├── assets/                    Browser scripts, brand assets, demo audio
├── dashboard/                 Business dashboard
├── server.py                  Voice, API, and telephony server
├── jev.py                     Validated optional Jev integration
├── contact_memory.py          Evidence-backed contact memory
├── sales_policy.py            Shared call and opt-out policy
├── auth/                      Node.js account authentication service
├── deploy/                    Service units and reverse proxy examples
└── test_*.py                  Focused Python tests
```

## Run locally

The services require credentials for the providers you enable. Never commit `.env`, database files, or provider keys.

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt

cd auth
npm ci
npm run migrate
cd ..

.venv/bin/python server.py
```

Configure provider keys and service settings in the process environment or a private `.env` file. The deployment guide explains the production services, authentication setup, and telephony webhooks: [deploy/DEPLOY.md](deploy/DEPLOY.md). Business-specific provider settings and routing are documented in [BUSINESS_SERVICE.md](BUSINESS_SERVICE.md).

Typical voice configuration includes `ASSEMBLYAI_API_KEY`, `GROQ_API_KEY`, `RUMIK_API_KEY`, `PUBLIC_BASE_URL`, and the credentials for the chosen phone provider. Configure `OPENROUTER_API_KEY` to enable Jev where supported. The company and auth services need their own secrets; see the deployment guide and `deploy/*.env.example` files for the exact options. Keep real values private.

## Data and privacy

Call transcripts and contact memory are stored for the business that owns the interaction. Caller statements remain attributable to their source; memory is not treated as verified fact unless the confirmation rules establish it. Provider services process audio or text as required for the enabled features. Review [privacy.html](privacy.html), [ai-disclosure.html](ai-disclosure.html), and [SECURITY_AUDIT.md](SECURITY_AUDIT.md) before operating a deployment.

## License

No open-source license is currently included. All rights reserved unless the repository owner states otherwise.
