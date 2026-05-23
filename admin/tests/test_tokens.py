import pytest
from app.db import init_db, connect
from app.tokens import (
    create_token, verify_token, revoke_token, list_tokens, TokenInvalid,
)


@pytest.fixture
def conn(tmp_path):
    db = str(tmp_path / "a.db")
    init_db(db)
    c = connect(db)
    c.execute("INSERT INTO admins(username, password_hash, created_at) VALUES (?, ?, datetime('now'))",
              ("admin1", "x"))
    return c


def test_create_returns_plaintext_with_vpa_prefix(conn):
    created = create_token(conn, name="ci-bot", scopes=["enroll", "read"], created_by_admin_id=1)
    assert created.plaintext.startswith("vpa_")
    assert len(created.plaintext) == 36
    assert created.token_id > 0


def test_verify_accepts_correct_plaintext(conn):
    created = create_token(conn, name="ci-bot", scopes=["enroll"], created_by_admin_id=1)
    verified = verify_token(conn, created.plaintext)
    assert verified.token_id == created.token_id
    assert verified.scopes == ["enroll"]


def test_verify_rejects_unknown_token(conn):
    with pytest.raises(TokenInvalid):
        verify_token(conn, "vpa_invalidinvalidinvalidinvalidaaa")


def test_verify_rejects_revoked(conn):
    created = create_token(conn, name="ci-bot", scopes=["read"], created_by_admin_id=1)
    revoke_token(conn, created.token_id)
    with pytest.raises(TokenInvalid):
        verify_token(conn, created.plaintext)


def test_list_tokens_returns_safe_fields(conn):
    create_token(conn, name="alpha", scopes=["read"], created_by_admin_id=1)
    create_token(conn, name="beta", scopes=["enroll", "revoke"], created_by_admin_id=1)
    rows = list_tokens(conn)
    names = [r.name for r in rows]
    assert names == ["alpha", "beta"]
    for r in rows:
        assert r.token_prefix.startswith("vpa_")
        assert not hasattr(r, "token_hash")  # no hash exposed
        assert r.scopes  # parsed list
