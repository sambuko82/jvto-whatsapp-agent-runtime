"""Phase 4 — Meta (WhatsApp) webhook edge.

A thin, security-first boundary. It does NOT classify intent/entities, decide route
truth, or send replies — those are out of scope (classification is upstream of this
repo; sending uses the Meta Send API, which needs credentials). This module:

1. answers Meta's GET subscription verification;
2. verifies the `X-Hub-Signature-256` HMAC on inbound POSTs (fail-closed);
3. normalizes the payload into inbound messages, reducing raw sender identifiers to an
   opaque, stable `context_ref` so no raw customer PII is retained.

Configuration comes from the environment (never committed):
- `JVTO_META_VERIFY_TOKEN` — GET verification token.
- `JVTO_META_APP_SECRET`   — app secret for HMAC signature verification.
- `JVTO_META_CONTEXT_SALT` — salt for the privacy-safe context reference.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

VERIFY_TOKEN_ENV = "JVTO_META_VERIFY_TOKEN"
APP_SECRET_ENV = "JVTO_META_APP_SECRET"
CONTEXT_SALT_ENV = "JVTO_META_CONTEXT_SALT"


def verify_subscription(
    mode: str | None,
    token: str | None,
    challenge: str | None,
    *,
    verify_token: str | None = None,
) -> str | None:
    """Handle Meta's GET verification handshake.

    Returns the challenge string when the request is valid, else None (caller -> 403).
    Fails closed if no verify token is configured.
    """
    expected = verify_token if verify_token is not None else os.environ.get(VERIFY_TOKEN_ENV)
    if mode == "subscribe" and expected and token == expected:
        return challenge
    return None


def verify_signature(payload: bytes, signature_header: str | None, *, app_secret: str | None = None) -> bool:
    """Verify the X-Hub-Signature-256 HMAC over the raw request body.

    Fails closed when the secret is unconfigured or the header is missing/malformed.
    Uses a constant-time comparison.
    """
    secret = app_secret if app_secret is not None else os.environ.get(APP_SECRET_ENV)
    if not secret:
        return False
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    provided = signature_header.split("=", 1)[1]
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    # Compare as bytes: hmac.compare_digest on str raises TypeError for non-ASCII input,
    # which would turn a malformed header into a 500 instead of a clean rejection.
    return hmac.compare_digest(provided.encode("utf-8", "ignore"), expected.encode("ascii"))


def privacy_safe_ref(raw_id: str, *, salt: str | None = None) -> str:
    """Opaque, stable reference for a sender. Never store or emit the raw identifier."""
    salt = salt if salt is not None else os.environ.get(CONTEXT_SALT_ENV, "")
    digest = hashlib.sha256(f"{salt}|{raw_id}".encode("utf-8")).hexdigest()
    return f"ctx_{digest[:32]}"


def normalize_payload(payload: dict[str, Any], *, salt: str | None = None) -> list[dict[str, Any]]:
    """Extract normalized inbound messages from a Meta WhatsApp webhook payload.

    Each item: {context_ref, message_id, type, text, timestamp}. The raw sender id is
    reduced to an opaque context_ref; no raw PII (phone numbers, names) is retained.
    Tolerant of missing/partial structure.
    """
    messages: list[dict[str, Any]] = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            for message in value.get("messages", []) or []:
                text = ""
                if message.get("type") == "text":
                    text = (message.get("text") or {}).get("body", "")
                messages.append(
                    {
                        "context_ref": privacy_safe_ref(str(message.get("from", "")), salt=salt),
                        "message_id": message.get("id", ""),
                        "type": message.get("type", "unknown"),
                        "text": text,
                        "timestamp": message.get("timestamp", ""),
                    }
                )
    return messages
