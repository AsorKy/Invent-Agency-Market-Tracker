"""Expose the Streamlit service health shell.

This module provides importable ``/health`` and ``/ready`` endpoints for the
future reporting container without importing Streamlit, which is not yet a
project dependency. It relates to ``Home.py`` and ``pages/README.md``; those
files define the later multipage entrypoint and page inventory.
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from libs.common.lifecycle import graceful_lifespan, instrument_app
from libs.common.settings import get_settings

settings = get_settings()
app = FastAPI(
    title="Invent Market Tracker Streamlit Shell",
    version=settings.app_version,
    lifespan=graceful_lifespan(),
)
instrument_app(app, "streamlit")


@app.get("/health")
async def health() -> dict[str, str]:
    """Report reporting-container liveness."""
    return {"status": "ok", "service": "streamlit", "version": settings.app_version}


@app.get("/ready")
async def ready() -> JSONResponse:
    """Report readiness for the dependency-free reporting scaffold."""
    return JSONResponse(
        status_code=200,
        content={"status": "ready", "dependencies": {"streamlit_runtime": "pending"}},
    )
