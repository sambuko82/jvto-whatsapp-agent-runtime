import hashlib
import hmac
import json

from jvto_agent_runtime.meta_webhook import (
    normalize_payload,
    privacy_safe_ref,
    verify_signature,
    verify_subscription,
)

SECRET = "test-app-secret"
SALT = "test-salt"


def _sign(payload: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


# --- subscription verification ---------------------------------------------


def test_verify_subscription_valid():
    assert verify_subscription("subscribe", "tok", "12345", verify_token="tok") == "12345"


def test_verify_subscription_wrong_token():
    assert verify_subscription("subscribe", "wrong", "12345", verify_token="tok") is None


def test_verify_subscription_fails_closed_without_token():
    assert verify_subscription("subscribe", "", "12345", verify_token="") is None


# --- signature verification ------------------------------------------------


def test_verify_signature_accepts_valid_hmac():
    body = b'{"hello":"world"}'
    assert verify_signature(body, _sign(body), app_secret=SECRET) is True


def test_verify_signature_rejects_tampered_body():
    body = b'{"hello":"world"}'
    sig = _sign(body)
    assert verify_signature(b'{"hello":"evil"}', sig, app_secret=SECRET) is False


def test_verify_signature_fails_closed_without_secret():
    body = b"{}"
    assert verify_signature(body, _sign(body), app_secret="") is False


def test_verify_signature_rejects_malformed_header():
    body = b"{}"
    assert verify_signature(body, "md5=deadbeef", app_secret=SECRET) is False
    assert verify_signature(body, None, app_secret=SECRET) is False


def test_verify_signature_rejects_non_ascii_header_without_raising():
    # A non-ASCII signature must fail closed (False), not raise.
    body = b"{}"
    assert verify_signature(body, "sha256=café", app_secret=SECRET) is False


# --- privacy-safe reference ------------------------------------------------


def test_privacy_safe_ref_is_stable_and_opaque():
    ref1 = privacy_safe_ref("628123456789", salt=SALT)
    ref2 = privacy_safe_ref("628123456789", salt=SALT)
    assert ref1 == ref2
    assert ref1.startswith("ctx_")
    assert "628123456789" not in ref1  # raw PII must not appear


def test_privacy_safe_ref_varies_with_salt():
    assert privacy_safe_ref("628123456789", salt="a") != privacy_safe_ref("628123456789", salt="b")


# --- payload normalization -------------------------------------------------


def test_normalize_payload_extracts_text_and_hides_pii():
    payload = json.loads(
        json.dumps(
            {
                "entry": [
                    {
                        "changes": [
                            {
                                "value": {
                                    "messages": [
                                        {
                                            "from": "628123456789",
                                            "id": "wamid.ABC",
                                            "type": "text",
                                            "timestamp": "1700000000",
                                            "text": {"body": "Halo, mau tanya paket Bromo"},
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ]
            }
        )
    )
    messages = normalize_payload(payload, salt=SALT)
    assert len(messages) == 1
    msg = messages[0]
    assert msg["text"] == "Halo, mau tanya paket Bromo"
    assert msg["message_id"] == "wamid.ABC"
    assert msg["type"] == "text"
    assert msg["context_ref"].startswith("ctx_")
    # No raw phone number anywhere in the normalized record.
    assert "628123456789" not in json.dumps(msg)


def test_normalize_payload_handles_empty_and_nontext():
    assert normalize_payload({}, salt=SALT) == []
    payload = {"entry": [{"changes": [{"value": {"messages": [{"from": "1", "id": "x", "type": "image"}]}}]}]}
    messages = normalize_payload(payload, salt=SALT)
    assert len(messages) == 1
    assert messages[0]["type"] == "image"
    assert messages[0]["text"] == ""
