"""Signed cookies for pending-login state.

Plain `admin_id` in __Host-admin_pending was tamperable: an attacker who
passed the password step with their own account could change the cookie
to point at another admin and (if that admin had no TOTP yet) hijack
the enrollment flow.

We HMAC-sign the cookie payload with ADMIN_COOKIE_SECRET. Payload also
carries an expiry and a stage marker so a password-step cookie cannot
be reused as an enroll-step cookie.

Cookie value: `{base64(payload)}.{base64(hmac)}`
Payload:      `{admin_id}|{expiry_unix}|{stage}`
"""
import base64
import hashlib
import hmac
import time

from app.config import get_settings

# Stages — separate so an attacker cannot replay one across the other.
STAGE_PASSWORD_OK = "pw"
STAGE_ENROLL_TOTP = "enr"


class PendingInvalid(Exception):
    """Raised when the cookie is missing/tampered/expired/wrong-stage."""


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sign(payload: str) -> str:
    s = get_settings()
    key = s.cookie_secret.encode("utf-8")
    sig = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).digest()
    return f"{_b64e(payload.encode('utf-8'))}.{_b64e(sig)}"


def mint(admin_id: int, stage: str, ttl_seconds: int) -> str:
    """Create a signed cookie value for the given admin_id at the given stage."""
    expiry = int(time.time()) + ttl_seconds
    payload = f"{admin_id}|{expiry}|{stage}"
    return _sign(payload)


def verify(cookie_value: str | None, expected_stage: str) -> int:
    """Validate cookie. Return admin_id on success, raise PendingInvalid otherwise."""
    if not cookie_value or "." not in cookie_value:
        raise PendingInvalid("missing")
    s = get_settings()
    key = s.cookie_secret.encode("utf-8")
    try:
        payload_b64, sig_b64 = cookie_value.split(".", 1)
        payload = _b64d(payload_b64)
        sig = _b64d(sig_b64)
    except Exception:
        raise PendingInvalid("malformed")

    expected_sig = hmac.new(key, payload, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected_sig):
        raise PendingInvalid("bad-signature")

    try:
        admin_id_s, expiry_s, stage = payload.decode("utf-8").split("|", 2)
        admin_id = int(admin_id_s)
        expiry = int(expiry_s)
    except (ValueError, UnicodeDecodeError):
        raise PendingInvalid("malformed-payload")

    if stage != expected_stage:
        raise PendingInvalid(f"wrong-stage")
    if time.time() > expiry:
        raise PendingInvalid("expired")
    return admin_id
