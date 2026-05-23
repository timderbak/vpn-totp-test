from fastapi import FastAPI
from app.security_headers import SecurityHeadersMiddleware
from app.routes_api import router as api_router

app = FastAPI(title="ocserv admin", docs_url=None, redoc_url=None)
app.add_middleware(SecurityHeadersMiddleware)
app.include_router(api_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
