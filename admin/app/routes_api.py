from fastapi import APIRouter, Depends, HTTPException, Request, status
from app.config import get_settings
from app.deps import get_conn, require_scope, require_token
from app.tokens import VerifiedToken
from app.audit import write_audit
from app.usernames import InvalidUsername, is_valid_username
from app.users import (
    EnrollResult, enable_user, enroll_user, list_users, revoke_user, UserNotFound,
)
from app.ratelimit import check_rate_limit, RateLimited

router = APIRouter(prefix="/api/v1")
ISSUER = "ocserv-lab"


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("/users")
def api_list_users(
    request: Request,
    conn=Depends(get_conn),
    token: VerifiedToken = Depends(require_scope("read")),
):
    settings = get_settings()
    items = list_users(settings.home_dir, settings.disabled_users_path, conn)
    write_audit(conn, actor_type="api", actor_id=token.token_id,
                action="users.list", target_user=None, ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"), result="ok")
    return [
        {"username": u.username, "has_totp": u.has_totp,
         "disabled": u.disabled, "last_issued_at": u.last_issued_at}
        for u in items
    ]


def _user_or_404(home_dir: str, denylist_path: str, conn, username: str) -> dict:
    if not is_valid_username(username):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid username")
    items = {u.username: u for u in list_users(home_dir, denylist_path, conn)}
    if username not in items:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    u = items[username]
    return {"username": u.username, "has_totp": u.has_totp,
            "disabled": u.disabled, "last_issued_at": u.last_issued_at}


@router.get("/users/{username}")
def api_get_user(
    username: str, request: Request,
    conn=Depends(get_conn),
    token: VerifiedToken = Depends(require_scope("read")),
):
    settings = get_settings()
    return _user_or_404(settings.home_dir, settings.disabled_users_path, conn, username)


@router.post("/users/{username}/enroll")
def api_enroll(
    username: str, request: Request,
    conn=Depends(get_conn),
    token: VerifiedToken = Depends(require_scope("enroll")),
):
    settings = get_settings()
    ip = _client_ip(request)
    try:
        check_rate_limit(conn, action="enroll.fail", window_secs=60, max_count=1,
                         target_user=username)
    except RateLimited as e:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            headers={"Retry-After": str(e.retry_after)})
    try:
        result: EnrollResult = enroll_user(
            settings.home_dir, conn,
            username=username, actor_type="api", actor_id=token.token_id,
            issuer=ISSUER,
        )
    except InvalidUsername:
        write_audit(conn, actor_type="api", actor_id=token.token_id,
                    action="enroll.fail", target_user=username, ip=ip,
                    user_agent=request.headers.get("user-agent"), result="fail",
                    details={"reason": "invalid_username"})
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid username")
    except UserNotFound:
        write_audit(conn, actor_type="api", actor_id=token.token_id,
                    action="enroll.fail", target_user=username, ip=ip,
                    user_agent=request.headers.get("user-agent"), result="fail",
                    details={"reason": "user_not_found"})
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    write_audit(conn, actor_type="api", actor_id=token.token_id,
                action="enroll.ok", target_user=username, ip=ip,
                user_agent=request.headers.get("user-agent"), result="ok")
    return {
        "secret": result.enrollment.secret,
        "scratch_codes": list(result.enrollment.scratch_codes),
        "qr_png_base64": result.qr_png_base64,
    }


@router.post("/users/{username}/revoke")
def api_revoke(
    username: str, request: Request,
    conn=Depends(get_conn),
    token: VerifiedToken = Depends(require_scope("revoke")),
):
    settings = get_settings()
    try:
        revoke_user(settings.home_dir, settings.disabled_users_path, conn,
                    username=username, actor_type="api", actor_id=token.token_id)
    except (InvalidUsername, UserNotFound) as e:
        code = status.HTTP_400_BAD_REQUEST if isinstance(e, InvalidUsername) else status.HTTP_404_NOT_FOUND
        raise HTTPException(code, str(e))
    write_audit(conn, actor_type="api", actor_id=token.token_id,
                action="revoke.ok", target_user=username, ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"), result="ok")
    return {"ok": True}


@router.post("/users/{username}/enable")
def api_enable(
    username: str, request: Request,
    conn=Depends(get_conn),
    token: VerifiedToken = Depends(require_scope("revoke")),
):
    settings = get_settings()
    try:
        enable_user(settings.home_dir, settings.disabled_users_path, conn,
                    username=username, actor_type="api", actor_id=token.token_id)
    except InvalidUsername:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid username")
    write_audit(conn, actor_type="api", actor_id=token.token_id,
                action="enable.ok", target_user=username, ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"), result="ok")
    return {"ok": True}


@router.get("/audit")
def api_audit(
    request: Request, conn=Depends(get_conn),
    token: VerifiedToken = Depends(require_scope("read")),
    limit: int = 100, offset: int = 0,
):
    limit = min(max(1, limit), 500)
    rows = conn.execute(
        "SELECT id, ts, actor_type, actor_id, action, target_user, ip, result "
        "FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]
