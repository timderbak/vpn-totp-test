from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from app.security_headers import SecurityHeadersMiddleware
from app.routes_api import router as api_router
from app.routes_web import router as web_router
from app.deps import NeedsLogin
from app.ldap_client import LdapUnavailable

app = FastAPI(title="ocserv admin", docs_url=None, redoc_url=None)
app.add_middleware(SecurityHeadersMiddleware)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.include_router(api_router)
app.include_router(web_router)


@app.exception_handler(NeedsLogin)
async def _needs_login(request: Request, exc: NeedsLogin):
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(LdapUnavailable)
async def _ldap_unavailable(request: Request, exc: LdapUnavailable):
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=503,
            content={"error": "ldap_unavailable", "detail": str(exc)},
        )
    raise exc


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
