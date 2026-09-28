# AI agent instructions

## Voice provider routing

This repository defaults operator calls to Telnyx globally (`CALL_PROVIDER=telnyx`).
Explicit tenant providers are honored. For `provider: "auto"` only:

- Indian destinations (`+91...`) use Plivo.
- Every other international destination uses Telnyx.

Use `server.call_provider(number, business)` as the single source of truth. Do not add provider selection in callers.

For a business using both channels, configure `provider: "auto"` and provide both credential sets. Read [BUSINESS_SERVICE.md](BUSINESS_SERVICE.md) before changing service configuration.

Provider endpoints:

- Plivo inbound answer: `/plivo/answer`; media stream: `/plivo/stream`.
- Telnyx inbound TeXML: `/telnyx/voice`; status callback: `/telnyx/status`; media stream: `/telnyx/stream`.

When changing telephony code, preserve E.164 validation, provider webhook signature verification, usage reservations, terminal-call reconciliation, and the provider-specific PCMU stream protocol.

## Production diagnostics

Traceway runs locally at `http://127.0.0.1:8082`. Run `python3 deploy/traceway-status.py` for the website, login, and API monitors. Use the `traceway` MCP server or `traceway exceptions list --profile aihelper --since 24h`; the `aihelper` CLI profile selects the production project. Check `journalctl -u aihelper -u aihelper-auth` for voice details. Never run unrestricted `unittest discover` in `/opt/aihelper`: legacy test scripts place real calls. Use isolated, explicitly selected tests.
