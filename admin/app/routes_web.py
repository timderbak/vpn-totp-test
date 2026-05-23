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
