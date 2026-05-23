import re
import base64
import pyotp
from app.totp import generate_enrollment, format_google_authenticator_file, build_qr_png_base64


def test_generate_enrollment_returns_secret_and_codes():
    e = generate_enrollment(username="alice", issuer="ocserv-lab")
    assert re.fullmatch(r"[A-Z2-7]{32}", e.secret)
    assert len(e.scratch_codes) == 5
    for code in e.scratch_codes:
        assert re.fullmatch(r"[0-9]{8}", code)
    # secret is a valid base32 TOTP
    assert len(pyotp.TOTP(e.secret).now()) == 6


def test_format_file_round_trips():
    e = generate_enrollment(username="alice", issuer="ocserv-lab")
    content = format_google_authenticator_file(e)
    lines = content.splitlines()
    assert lines[0] == e.secret
    assert '" RATE_LIMIT 3 30' in content
    assert '" DISALLOW_REUSE' in content
    assert '" TOTP_AUTH' in content
    assert '" WINDOW_SIZE 3' in content
    # scratch codes at end
    for code in e.scratch_codes:
        assert code in content


def test_qr_png_is_valid_base64_png():
    e = generate_enrollment(username="alice", issuer="ocserv-lab")
    png_b64 = build_qr_png_base64(e)
    raw = base64.b64decode(png_b64)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
