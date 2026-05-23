import hmac
import secrets


class CSRFInvalid(ValueError):
    pass


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def verify_csrf(form_token: str, cookie_token: str) -> None:
    if not form_token or not cookie_token:
        raise CSRFInvalid()
    if not hmac.compare_digest(form_token, cookie_token):
        raise CSRFInvalid()
