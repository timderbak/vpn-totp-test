from fastapi import FastAPI

app = FastAPI(title="ocserv admin", docs_url=None, redoc_url=None)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
