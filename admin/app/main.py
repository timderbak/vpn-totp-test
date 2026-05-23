from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from app.security_headers import SecurityHeadersMiddleware
from app.routes_api import router as api_router
from app.routes_web import router as web_router
from app.deps import NeedsLogin

app = FastAPI(title="ocserv admin", docs_url=None, redoc_url=None)
app.add_middleware(SecurityHeadersMiddleware)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.include_router(api_router)
app.include_router(web_router)


@app.exception_handler(NeedsLogin)
async def _needs_login(request: Request, exc: NeedsLogin):
    return RedirectResponse("/login", status_code=303)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
