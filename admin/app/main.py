from fastapi import FastAPI
from app.security_headers import SecurityHeadersMiddleware

app = FastAPI(title="ocserv admin", docs_url=None, redoc_url=None)
app.add_middleware(SecurityHeadersMiddleware)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
