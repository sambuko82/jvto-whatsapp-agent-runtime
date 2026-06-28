# Meta webhook edge (Phase 4)

A thin, security-first boundary for Meta (WhatsApp) inbound events. It deliberately does
**not** classify intent/entities, decide route truth, or send replies — classification is
upstream of this repo and replies use the Meta Send API (needs credentials, out of scope).

## Module

- `src/jvto_agent_runtime/meta_webhook.py` — `verify_subscription`, `verify_signature`,
  `privacy_safe_ref`, `normalize_payload`.

## Endpoints

- `GET /webhooks/meta` — Meta's subscription handshake. Echoes `hub.challenge` only when
  `hub.verify_token` matches `JVTO_META_VERIFY_TOKEN`; otherwise 403 (fails closed).
- `POST /webhooks/meta` — verifies the `X-Hub-Signature-256` HMAC over the raw body using
  `JVTO_META_APP_SECRET` (constant-time compare; fails closed if unconfigured/malformed →
  403), then normalizes the payload and returns the inbound messages. It does not classify
  or reply.

## Privacy

Raw sender identifiers (phone numbers) are reduced to an opaque, stable `context_ref`
(`privacy_safe_ref`, salted by `JVTO_META_CONTEXT_SALT`). No raw PII is retained or emitted
— consistent with the ownership boundary (this repo does not own raw customer PII).

## Configuration (environment only — never committed)

| Variable | Purpose |
|---|---|
| `JVTO_META_VERIFY_TOKEN` | GET subscription verification token |
| `JVTO_META_APP_SECRET`   | App secret for HMAC signature verification |
| `JVTO_META_CONTEXT_SALT` | Salt for the privacy-safe context reference |

## Downstream flow (out of scope for this scaffold)

`normalize_payload` output → external intent/entity classifier → `POST /v1/decisions` →
allowed live tools (`/v1/live-tools`) → reply via the Meta Send API → audit metadata.

## Note on testing

The pure functions are unit-tested (`tests/test_meta_webhook.py`). The HTTP layer is not
covered here because `fastapi.testclient` requires `httpx`, which is not a project
dependency; the endpoints are thin wrappers over the tested functions.
