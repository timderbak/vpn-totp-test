import pytest
from app.csrf import generate_csrf_token, verify_csrf, CSRFInvalid


def test_round_trip():
    t = generate_csrf_token()
    verify_csrf(t, t)


def test_mismatch_rejected():
    with pytest.raises(CSRFInvalid):
        verify_csrf(generate_csrf_token(), generate_csrf_token())


def test_empty_rejected():
    with pytest.raises(CSRFInvalid):
        verify_csrf("", "")
