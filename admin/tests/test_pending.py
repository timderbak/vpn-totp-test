"""Tests for HMAC-signed __Host-admin_pending / __Host-admin_enroll cookies.

Closes the auth-bypass class of bugs where an attacker who passed the
password step with their own account changed the cookie to another
admin_id and hijacked the TOTP-enroll flow.
"""
import time
import pytest
from app import pending


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("ADMIN_BOOTSTRAP_USERNAME", "admin1")
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD_HASH", "$2b$12$x" * 4)
    monkeypatch.setenv("ADMIN_COOKIE_SECRET", "0" * 64)
    monkeypatch.setenv("ADMIN_LDAP_BIND_DN", "cn=x,dc=y")
    monkeypatch.setenv("ADMIN_LDAP_BIND_PASSWORD", "x")
    from app.config import get_settings
    get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None


def test_mint_verify_roundtrip():
    cookie = pending.mint(admin_id=42, stage=pending.STAGE_PASSWORD_OK, ttl_seconds=60)
    assert pending.verify(cookie, expected_stage=pending.STAGE_PASSWORD_OK) == 42


def test_verify_rejects_tampered_admin_id():
    """Attacker tries to change admin_id 1 → 2 in the payload."""
    cookie = pending.mint(admin_id=1, stage=pending.STAGE_PASSWORD_OK, ttl_seconds=60)
    # split, decode payload, mutate, re-encode, KEEP old signature
    import base64
    payload_b64, sig_b64 = cookie.split(".", 1)
    payload = base64.urlsafe_b64decode(payload_b64 + "==").decode()
    tampered_payload = payload.replace("1|", "2|", 1)
    tampered_payload_b64 = base64.urlsafe_b64encode(tampered_payload.encode()).rstrip(b"=").decode()
    forged = f"{tampered_payload_b64}.{sig_b64}"
    with pytest.raises(pending.PendingInvalid):
        pending.verify(forged, expected_stage=pending.STAGE_PASSWORD_OK)


def test_verify_rejects_wrong_stage():
    """Cookie minted for password step cannot be reused as enroll step."""
    cookie = pending.mint(admin_id=1, stage=pending.STAGE_PASSWORD_OK, ttl_seconds=60)
    with pytest.raises(pending.PendingInvalid):
        pending.verify(cookie, expected_stage=pending.STAGE_ENROLL_TOTP)


def test_verify_rejects_expired():
    cookie = pending.mint(admin_id=1, stage=pending.STAGE_PASSWORD_OK, ttl_seconds=-1)
    with pytest.raises(pending.PendingInvalid):
        pending.verify(cookie, expected_stage=pending.STAGE_PASSWORD_OK)


def test_verify_rejects_missing():
    with pytest.raises(pending.PendingInvalid):
        pending.verify(None, expected_stage=pending.STAGE_PASSWORD_OK)
    with pytest.raises(pending.PendingInvalid):
        pending.verify("", expected_stage=pending.STAGE_PASSWORD_OK)


def test_verify_rejects_malformed():
    with pytest.raises(pending.PendingInvalid):
        pending.verify("not-a-cookie", expected_stage=pending.STAGE_PASSWORD_OK)
    with pytest.raises(pending.PendingInvalid):
        pending.verify("garbage.garbage", expected_stage=pending.STAGE_PASSWORD_OK)
