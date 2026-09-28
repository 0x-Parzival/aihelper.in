# AI Helper security review — 25 September 2026

## Scope

Reviewed the live `aihelper.in` Nginx site, homepage and dashboard routes, Better Auth login and recovery, public browser voice APIs, callback calling, provider recording playback, call prompts, database and file permissions, and production Node dependencies. This is a source and live configuration review with safe HTTP checks; it does not claim to be a penetration test of the phone carriers, AI providers, or hosting account.

## Findings and changes

| Severity | Finding | Action |
| --- | --- | --- |
| High | Public callback could trigger paid calls using only a self-asserted consent checkbox; its IP limit reset on restart. | Added atomic SQLite limits: two requests per IP per day, one per number per day, twelve globally per day. Cross-site browser requests are rejected. Calls still require the checkbox. |
| High | Public speech tokens and AI replies had only restartable in-memory IP limits. | Added persistent per-IP minute/day limits and global daily limits for STT, intro and replies. Browser origin/referrer checks apply. |
| High | Caller text beginning with `Private` could be mistaken for trusted internal guidance and removed from public transcript analysis. | Internal guidance now uses a server-only `_private` marker; browser-supplied message fields are reduced to `role` and `content`. |
| High | Auth dependencies had one high and one moderate advisory in `npm audit`. | Updated Nodemailer to 9.1.1 and transitive `qs` to a patched release; production audit reports zero advisories. |
| Medium | Login redirect accepted unusual local paths, and reset tokens were interpolated into inline JavaScript without HTML-safe escaping. | Rejected backslashes and control characters in redirect paths; escaped `<` when embedding values in scripts. |
| Medium | Auth rate limiting depended on production mode, which was unset in the service. | Explicitly enabled Better Auth rate limiting and set `NODE_ENV=production`; capped password-reset and verification-email requests. |
| Medium | Recording downloads had no size cap and files were world-readable on disk. | Allowed only observed provider HTTPS hosts, capped downloads at 64 MiB, forced MP3 response type, and set recordings to directory mode 0700 and file mode 0600. Recording remains enabled. |
| Low | Web responses exposed Nginx version and Express implementation. | Disabled Nginx version tokens and Express's `X-Powered-By` header. |
| Medium | Content Security Policy allowed all inline JavaScript. | Moved homepage, dashboard, and auth code into same-origin script files. The live policy now allows only same-origin scripts and the fixed structured-data hash, and blocks inline event handlers. |
| Low | A malformed production environment line caused service-manager warnings. | Repaired the line and retained a mode-0600 backup. |

[Better Auth's 1.7 upgrade guide](https://better-auth.com/docs/guides/1-7-upgrade-guide) explains that newer releases no longer write the older required `account.issuer` column. After backing up the SQLite database, the obsolete index and column were removed. Authentication restarted without the schema warning.

## Verification

- 147 offline Python tests passed, including persistent limits, private-message separation, recording host rejection, and demo-context selection.
- Node syntax checks for the external browser scripts, Nginx configuration test, and production `npm audit --omit=dev` passed; the latter reported zero vulnerabilities. The live CSP no longer includes `script-src 'unsafe-inline'`.
- Live homepage and login returned HTTP 200. Cross-site STT request returned HTTP 429 without issuing a token. `/.env`, `/aihelper.db`, and `/server.py` returned HTTP 403. Production services are active.
- The homepage has no pricing block. After a minute of conversation, the browser suggests a call and submits bounded recent and introductory chat context with a callback request. The call prompt treats it as unverified until the answerer confirms they requested the call.

## Remaining risks

- A checkbox and rate limits cannot prove that the person entering a number owns it. Phone verification or a managed bot challenge is needed before public callbacks can be considered resistant to determined abuse. The daily global cap bounds spending but can also block legitimate demos when exhausted.
- The Nginx Content Security Policy still permits inline styles because the current pages use them. Moving those styles into files would permit a stricter style policy.
- The owner dashboard uses a password previously shared in chat. Rotate it to a long unique secret through the dashboard before treating it as private again; this review did not override the owner's chosen password.
- Provider accounts, DNS, host patch state, and external voice systems were not independently penetrated or certified in this review.
