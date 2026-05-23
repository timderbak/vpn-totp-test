import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from passlib.context import CryptContext

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)
_BASE32_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


class TokenInvalid(ValueError):
    pass


@dataclass(frozen=True)
class CreatedToken:
    token_id: int
    plaintext: str


@dataclass(frozen=True)
class VerifiedToken:
    token_id: int
    scopes: list[str]


@dataclass(frozen=True)
class TokenListEntry:
    id: int
    name: str
    token_prefix: str
    scopes: list[str]
    created_at: str
    revoked_at: str | None
    last_used_at: str | None


def _generate_plaintext() -> str:
    body = "".join(secrets.choice(_BASE32_CHARS) for _ in range(32))
    return f"vpa_{body}"


def create_token(conn: sqlite3.Connection, *, name: str, scopes: list[str], created_by_admin_id: int) -> CreatedToken:
    plaintext = _generate_plaintext()
    hashed = _pwd.hash(plaintext)
    prefix = plaintext[:8]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO api_tokens(name, token_hash, token_prefix, scopes, created_by_admin_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (name, hashed, prefix, json.dumps(scopes), created_by_admin_id, now),
    )
    return CreatedToken(token_id=cur.lastrowid, plaintext=plaintext)


def verify_token(conn: sqlite3.Connection, plaintext: str) -> VerifiedToken:
    if not plaintext or not plaintext.startswith("vpa_") or len(plaintext) != 36:
        raise TokenInvalid()
    prefix = plaintext[:8]
    rows = conn.execute(
        "SELECT id, token_hash, scopes, revoked_at FROM api_tokens WHERE token_prefix=? AND revoked_at IS NULL",
        (prefix,),
    ).fetchall()
    for row in rows:
        if _pwd.verify(plaintext, row["token_hash"]):
            conn.execute("UPDATE api_tokens SET last_used_at=? WHERE id=?",
                         (datetime.now(timezone.utc).isoformat(timespec="seconds"), row["id"]))
            return VerifiedToken(token_id=row["id"], scopes=json.loads(row["scopes"]))
    raise TokenInvalid()


def revoke_token(conn: sqlite3.Connection, token_id: int) -> None:
    conn.execute("UPDATE api_tokens SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                 (datetime.now(timezone.utc).isoformat(timespec="seconds"), token_id))


def list_tokens(conn: sqlite3.Connection) -> list[TokenListEntry]:
    rows = conn.execute(
        "SELECT id, name, token_prefix, scopes, created_at, revoked_at, last_used_at "
        "FROM api_tokens ORDER BY id ASC"
    ).fetchall()
    return [TokenListEntry(
        id=r["id"], name=r["name"], token_prefix=r["token_prefix"],
        scopes=json.loads(r["scopes"]),
        created_at=r["created_at"], revoked_at=r["revoked_at"], last_used_at=r["last_used_at"],
    ) for r in rows]
