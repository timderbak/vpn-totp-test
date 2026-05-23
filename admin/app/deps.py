from typing import Annotated
from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from app.config import Settings, get_settings
from app.db import connect, init_db
from app.sessions import lookup_session, touch_session, SessionInvalid
from app.tokens import verify_token, TokenInvalid, VerifiedToken
from app.auth import AdminRow

# A single connection per process is fine for SQLite in WAL mode at lab scale.
_conn = None


def get_conn():
    global _conn
    if _conn is None:
        settings = get_settings()
        init_db(settings.db_path)
        _conn = connect(settings.db_path)
    return _conn


def require_admin(
    request: Request,
    conn = Depends(get_conn),
    session_cookie: Annotated[str | None, Cookie(alias="__Host-admin_session")] = None,
) -> AdminRow:
    if not session_cookie:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "no session")
    try:
        s = lookup_session(conn, session_cookie)
    except SessionInvalid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session invalid")
    touch_session(conn, s.id)
    row = conn.execute("SELECT id, username, totp_secret FROM admins WHERE id=?",
                       (s.admin_id,)).fetchone()
    if not row:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "admin missing")
    return AdminRow(id=row["id"], username=row["username"], totp_enrolled=row["totp_secret"] is not None)


def require_token(
    conn = Depends(get_conn),
    authorization: Annotated[str | None, Header()] = None,
) -> VerifiedToken:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer")
    plaintext = authorization[len("Bearer "):]
    try:
        return verify_token(conn, plaintext)
    except TokenInvalid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")


def require_scope(scope: str):
    def _check(token: VerifiedToken = Depends(require_token)) -> VerifiedToken:
        if scope not in token.scopes:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing scope: {scope}")
        return token
    return _check
