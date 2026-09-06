# AI agent instructions

## Voice provider routing

This repository has two telephony channels:

- Indian destinations (`+91...`) use Plivo.
- Every other international destination uses Telnyx.

Use `server.call_provider(number, business)` as the single source of truth. Do not add provider selection in callers.

For a business using both channels, configure `provider: "auto"` and provide both credential sets. Read [BUSINESS_SERVICE.md](BUSINESS_SERVICE.md) before changing service configuration.

Provider endpoints:

- Plivo inbound answer: `/plivo/answer`; media stream: `/plivo/stream`.
- Telnyx inbound TeXML: `/telnyx/voice`; status callback: `/telnyx/status`; media stream: `/telnyx/stream`.

When changing telephony code, preserve E.164 validation, provider webhook signature verification, usage reservations, terminal-call reconciliation, and the provider-specific PCMU stream protocol.
