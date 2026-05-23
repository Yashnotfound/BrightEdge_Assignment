"""FastAPI app entrypoint + Lambda handler."""
from __future__ import annotations

from fastapi import FastAPI
from mangum import Mangum

from crawler.api.routes import router

app = FastAPI(title="BrightEdge Crawler", version="0.1.0", docs_url="/docs", redoc_url=None)
app.include_router(router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


# Lambda entry point
handler = Mangum(app, lifespan="off")
