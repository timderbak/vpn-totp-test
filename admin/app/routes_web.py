from pathlib import Path
from typing import Annotated
from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import pyotp

from app.config import get_settings
from app.deps import get_conn
from app.auth import (
    bootstrap_admin_if_needed, verify_password, set_admin_totp,
    verify_admin_totp, AuthFailed, TOTPRequired, AdminRow,
)
from app.audit import write_audit
from app.sessions import create_session, destroy_session
from app.totp import build_qr_png_base64, generate_enrollment, Enrollment
from app.ratelimit import check_rate_limit, RateLimited

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

PENDING_COOKIE = "__Host-admin_pending"        # admin_id awaiting TOTP step
SESSION_COOKIE = "__Host-admin_session"
ENROLL_SECRET_COOKIE = "__Host-admin_enroll"   # temp secret during admin TOTP enroll


def _ip(req: Request) -> str | None:
    return req.client.host if req.client else None


def _bootstrap(conn) -> None:
    s = get_settings()
    bootstrap_admin_if_needed(conn, username=s.bootstrap_username,
                              password_hash=s.bootstrap_password_hash)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, conn=Depends(get_conn)):
    _bootstrap(conn)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login_submit(
    request: Request, response: Response,
    username: Annotated[str, Form()], password: Annotated[str, Form()],
    conn=Depends(get_conn),
):
    _bootstrap(conn)
    ip = _ip(request)
    try:
        check_rate_limit(conn, action="login.fail", window_secs=900, max_count=5, ip=ip)
        check_rate_limit(conn, action="login.fail", window_secs=900, max_count=10,
                         target_user=username)
    except RateLimited as e:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            headers={"Retry-After": str(e.retry_after)})
    try:
        admin = verify_password(conn, username=username, password=password)
    except AuthFailed:
        write_audit(conn, actor_type="anonymous", actor_id=None,
                    action="login.fail", target_user=username, ip=ip,
                    user_agent=request.headers.get("user-agent"), result="fail")
        return templates.TemplateResponse(
            request, "login.html", {"error": "Invalid username or password"},
            status_code=401,
        )
    write_audit(conn, actor_type="admin", actor_id=admin.id,
                action="login.password.ok", target_user=None, ip=ip,
                user_agent=request.headers.get("user-agent"), result="ok")
    resp = RedirectResponse("/login/totp", status_code=303)
    resp.set_cookie(PENDING_COOKIE, str(admin.id),
                    httponly=True, secure=True, samesite="strict",
                    path="/", max_age=300)
    return resp


@router.get("/login/totp", response_class=HTMLResponse)
def totp_page(
    request: Request,
    pending: Annotated[str | None, Cookie(alias=PENDING_COOKIE)] = None,
    conn=Depends(get_conn),
):
    if not pending or not pending.isdigit():
        return RedirectResponse("/login", status_code=303)
    row = conn.execute("SELECT totp_secret FROM admins WHERE id=?", (int(pending),)).fetchone()
    if row is None:
        return RedirectResponse("/login", status_code=303)
    if row["totp_secret"] is None:
        return RedirectResponse("/login/enroll-totp", status_code=303)
    return templates.TemplateResponse(request, "login_totp.html", {"error": None})


@router.post("/login/totp")
def totp_submit(
    request: Request, code: Annotated[str, Form()],
    pending: Annotated[str | None, Cookie(alias=PENDING_COOKIE)] = None,
    conn=Depends(get_conn),
):
    if not pending or not pending.isdigit():
        return RedirectResponse("/login", status_code=303)
    admin_id = int(pending)
    ip = _ip(request)
    try:
        verify_admin_totp(conn, admin_id=admin_id, code=code)
    except (AuthFailed, TOTPRequired):
        write_audit(conn, actor_type="admin", actor_id=admin_id,
                    action="login.totp.fail", target_user=None, ip=ip,
                    user_agent=request.headers.get("user-agent"), result="fail")
        return templates.TemplateResponse(
            request, "login_totp.html", {"error": "Invalid code"}, status_code=401,
        )
    sid = create_session(conn, admin_id=admin_id, ip=ip,
                         user_agent=request.headers.get("user-agent"))
    write_audit(conn, actor_type="admin", actor_id=admin_id,
                action="login.ok", target_user=None, ip=ip,
                user_agent=request.headers.get("user-agent"), result="ok")
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(SESSION_COOKIE, sid, httponly=True, secure=True,
                    samesite="strict", path="/")
    resp.delete_cookie(PENDING_COOKIE, path="/")
    return resp


@router.get("/login/enroll-totp", response_class=HTMLResponse)
def enroll_totp_page(
    request: Request,
    pending: Annotated[str | None, Cookie(alias=PENDING_COOKIE)] = None,
):
    if not pending or not pending.isdigit():
        return RedirectResponse("/login", status_code=303)
    e = generate_enrollment(username=f"admin#{pending}", issuer="ocserv-admin")
    response = templates.TemplateResponse(
        request, "enroll_admin_totp.html",
        {"qr_b64": build_qr_png_base64(e), "secret": e.secret},
    )
    # store proposed secret in cookie (signed isn't needed since cookie itself is __Host-)
    response.set_cookie(ENROLL_SECRET_COOKIE, e.secret,
                        httponly=True, secure=True, samesite="strict",
                        path="/", max_age=600)
    return response


@router.post("/login/enroll-totp")
def enroll_totp_submit(
    request: Request, code: Annotated[str, Form()],
    pending: Annotated[str | None, Cookie(alias=PENDING_COOKIE)] = None,
    enroll_secret: Annotated[str | None, Cookie(alias=ENROLL_SECRET_COOKIE)] = None,
    conn=Depends(get_conn),
):
    if not pending or not pending.isdigit() or not enroll_secret:
        return RedirectResponse("/login", status_code=303)
    if not pyotp.TOTP(enroll_secret).verify(code, valid_window=1):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong code")
    set_admin_totp(conn, admin_id=int(pending), secret=enroll_secret)
    write_audit(conn, actor_type="admin", actor_id=int(pending),
                action="admin.totp.enrolled", target_user=None, ip=_ip(request),
                user_agent=request.headers.get("user-agent"), result="ok")
    resp = RedirectResponse("/login/totp", status_code=303)
    resp.delete_cookie(ENROLL_SECRET_COOKIE, path="/")
    return resp


@router.post("/logout")
def logout(
    request: Request,
    session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    conn=Depends(get_conn),
):
    if session:
        destroy_session(conn, session)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


from app.csrf import CSRFInvalid, generate_csrf_token, verify_csrf
from app.usernames import InvalidUsername
from app.users import enable_user, enroll_user, list_users, revoke_user, UserNotFound
from app.tokens import create_token, list_tokens, revoke_token
from app.deps import require_admin_web

CSRF_COOKIE = "__Host-csrf"


def _set_csrf(response: Response) -> str:
    tok = generate_csrf_token()
    response.set_cookie(CSRF_COOKIE, tok, httponly=False, secure=True,
                        samesite="strict", path="/")
    return tok


def _require_csrf(form_token: str | None, cookie_token: str | None) -> None:
    try:
        verify_csrf(form_token or "", cookie_token or "")
    except CSRFInvalid:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "csrf invalid")


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    admin: AdminRow = Depends(require_admin_web),
    conn=Depends(get_conn),
):
    settings = get_settings()
    users = list_users(settings.home_dir, settings.disabled_users_path, conn)
    csrf = generate_csrf_token()
    response = templates.TemplateResponse(
        request, "dashboard.html",
        {"admin": admin, "users": users, "csrf_token": csrf},
    )
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, secure=True,
                        samesite="strict", path="/")
    return response


@router.post("/users/{username}/enroll", response_class=HTMLResponse)
def web_enroll(
    request: Request, username: str,
    csrf_token: Annotated[str | None, Form()] = None,
    csrf_cookie: Annotated[str | None, Cookie(alias=CSRF_COOKIE)] = None,
    admin: AdminRow = Depends(require_admin_web),
    conn=Depends(get_conn),
):
    _require_csrf(csrf_token, csrf_cookie)
    settings = get_settings()
    try:
        result = enroll_user(settings.home_dir, conn, username=username,
                             actor_type="admin", actor_id=admin.id, issuer="ocserv-lab")
    except InvalidUsername:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad username")
    except UserNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such user")
    write_audit(conn, actor_type="admin", actor_id=admin.id,
                action="enroll.ok", target_user=username, ip=_ip(request),
                user_agent=request.headers.get("user-agent"), result="ok")
    return templates.TemplateResponse(
        request, "qr_once.html",
        {"username": username, "secret": result.enrollment.secret,
         "qr_b64": result.qr_png_base64,
         "scratch_codes": list(result.enrollment.scratch_codes)},
    )


@router.post("/users/{username}/revoke")
def web_revoke(
    request: Request, username: str,
    csrf_token: Annotated[str | None, Form()] = None,
    csrf_cookie: Annotated[str | None, Cookie(alias=CSRF_COOKIE)] = None,
    admin: AdminRow = Depends(require_admin_web),
    conn=Depends(get_conn),
):
    _require_csrf(csrf_token, csrf_cookie)
    settings = get_settings()
    try:
        revoke_user(settings.home_dir, settings.disabled_users_path, conn,
                    username=username, actor_type="admin", actor_id=admin.id)
    except (InvalidUsername, UserNotFound) as e:
        code = status.HTTP_400_BAD_REQUEST if isinstance(e, InvalidUsername) else status.HTTP_404_NOT_FOUND
        raise HTTPException(code, str(e))
    write_audit(conn, actor_type="admin", actor_id=admin.id,
                action="revoke.ok", target_user=username, ip=_ip(request),
                user_agent=request.headers.get("user-agent"), result="ok")
    return RedirectResponse("/", status_code=303)


@router.post("/users/{username}/enable")
def web_enable(
    request: Request, username: str,
    csrf_token: Annotated[str | None, Form()] = None,
    csrf_cookie: Annotated[str | None, Cookie(alias=CSRF_COOKIE)] = None,
    admin: AdminRow = Depends(require_admin_web),
    conn=Depends(get_conn),
):
    _require_csrf(csrf_token, csrf_cookie)
    settings = get_settings()
    try:
        enable_user(settings.home_dir, settings.disabled_users_path, conn,
                    username=username, actor_type="admin", actor_id=admin.id)
    except InvalidUsername:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad username")
    write_audit(conn, actor_type="admin", actor_id=admin.id,
                action="enable.ok", target_user=username, ip=_ip(request),
                user_agent=request.headers.get("user-agent"), result="ok")
    return RedirectResponse("/", status_code=303)


@router.get("/tokens", response_class=HTMLResponse)
def tokens_page(
    request: Request, admin: AdminRow = Depends(require_admin_web), conn=Depends(get_conn),
):
    rows = list_tokens(conn)
    csrf = generate_csrf_token()
    response = templates.TemplateResponse(
        request, "tokens.html", {"admin": admin, "tokens": rows, "csrf_token": csrf},
    )
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, secure=True,
                        samesite="strict", path="/")
    return response


@router.post("/tokens", response_class=HTMLResponse)
def tokens_create(
    request: Request,
    name: Annotated[str, Form()], scopes: Annotated[str, Form()],
    csrf_token: Annotated[str | None, Form()] = None,
    csrf_cookie: Annotated[str | None, Cookie(alias=CSRF_COOKIE)] = None,
    admin: AdminRow = Depends(require_admin_web), conn=Depends(get_conn),
):
    _require_csrf(csrf_token, csrf_cookie)
    scope_list = [s.strip() for s in scopes.split(",") if s.strip()]
    if not name.strip() or not scope_list:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "name + scopes required")
    created = create_token(conn, name=name.strip(), scopes=scope_list,
                           created_by_admin_id=admin.id)
    write_audit(conn, actor_type="admin", actor_id=admin.id,
                action="token.create", target_user=None, ip=_ip(request),
                user_agent=request.headers.get("user-agent"), result="ok",
                details={"name": name, "scopes": scope_list, "token_id": created.token_id})
    return templates.TemplateResponse(
        request, "token_once.html",
        {"plaintext": created.plaintext, "name": name},
    )


@router.post("/tokens/{token_id}/revoke")
def tokens_revoke(
    request: Request, token_id: int,
    csrf_token: Annotated[str | None, Form()] = None,
    csrf_cookie: Annotated[str | None, Cookie(alias=CSRF_COOKIE)] = None,
    admin: AdminRow = Depends(require_admin_web), conn=Depends(get_conn),
):
    _require_csrf(csrf_token, csrf_cookie)
    revoke_token(conn, token_id)
    write_audit(conn, actor_type="admin", actor_id=admin.id,
                action="token.revoke", target_user=None, ip=_ip(request),
                user_agent=request.headers.get("user-agent"), result="ok",
                details={"token_id": token_id})
    return RedirectResponse("/tokens", status_code=303)


@router.get("/audit", response_class=HTMLResponse)
def audit_page(
    request: Request, admin: AdminRow = Depends(require_admin_web),
    conn=Depends(get_conn), limit: int = 100, offset: int = 0,
):
    limit = min(max(1, limit), 500)
    rows = conn.execute(
        "SELECT id, ts, actor_type, actor_id, action, target_user, ip, result "
        "FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset),
    ).fetchall()
    return templates.TemplateResponse(
        request, "audit.html", {"admin": admin, "rows": rows, "offset": offset, "limit": limit},
    )
