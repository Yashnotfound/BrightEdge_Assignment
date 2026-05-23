"""FastAPI app entrypoint + Lambda handler."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from mangum import Mangum

from crawler.api.routes import router

app = FastAPI(title="BrightEdge Crawler", version="0.1.0", docs_url="/docs", redoc_url=None)
app.include_router(router)

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@app.get("/", response_class=HTMLResponse, tags=["meta"])
def index() -> str:
    path = _WEB_DIR / "index.html"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "<h1>BrightEdge Crawler</h1><p>See <a href='/docs'>/docs</a>.</p>"


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


handler = Mangum(app, lifespan="off")
