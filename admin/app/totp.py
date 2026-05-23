import base64
import io
import secrets
from dataclasses import dataclass
import pyotp
import qrcode

_BASE32_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


@dataclass(frozen=True)
class Enrollment:
    username: str
    issuer: str
    secret: str               # base32, 32 chars
    scratch_codes: tuple[str, ...]


def _random_base32(length: int = 32) -> str:
    return "".join(secrets.choice(_BASE32_CHARS) for _ in range(length))


def _scratch_code() -> str:
    return f"{secrets.randbelow(10**8):08d}"


def generate_enrollment(username: str, issuer: str) -> Enrollment:
    return Enrollment(
        username=username,
        issuer=issuer,
        secret=_random_base32(32),
        scratch_codes=tuple(_scratch_code() for _ in range(5)),
    )


def format_google_authenticator_file(e: Enrollment) -> str:
    # Mirrors the file format google-authenticator writes — same one PAM reads.
    flags = [
        '" RATE_LIMIT 3 30',
        '" DISALLOW_REUSE',
        '" TOTP_AUTH',
        '" WINDOW_SIZE 3',
    ]
    parts = [e.secret, *flags, *e.scratch_codes, ""]
    return "\n".join(parts)


def build_otpauth_uri(e: Enrollment) -> str:
    return pyotp.TOTP(e.secret).provisioning_uri(
        name=e.username, issuer_name=e.issuer
    )


def build_qr_png_base64(e: Enrollment) -> str:
    img = qrcode.make(build_otpauth_uri(e))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")
