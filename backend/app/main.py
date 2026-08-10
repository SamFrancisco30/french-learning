"""FastAPI application entrypoint.

Run:  uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
import re
from pathlib import PurePosixPath

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.engine import make_url

from .config import settings
from .db import init_db
from .errors import register_database_error_handler
from .routers import (
    account, attempts, billing, dictation, drill, ingest, lessons, lexicon, vocab,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

app = FastAPI(
    title="Polyglot — listening skill API",
    version="0.1.0",
    description=(
        "Turns authentic media into listening-comprehension lessons: "
        "YouTube -> ASR (word timestamps) -> segmentation -> auto-generated exercises."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_database_error_handler(app)
app.include_router(lessons.router)
app.include_router(attempts.router)
app.include_router(ingest.router)
app.include_router(lexicon.router)
app.include_router(dictation.router)
app.include_router(vocab.router)
app.include_router(account.router)
app.include_router(billing.router)
app.include_router(drill.router)


def _safe_log_token(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    cleaned = re.sub(r"[^A-Za-z0-9._:+-]+", "_", value).strip("_")
    return cleaned or fallback


def safe_database_label(database_url: str) -> str:
    """Return a startup-log label with no credentials, query, port, or local path."""
    try:
        url = make_url(database_url)
    except (TypeError, ValueError):
        return "database"

    driver = _safe_log_token(url.drivername, "database")
    database = url.database or ":memory:"
    if url.get_backend_name() == "sqlite":
        filename = PurePosixPath(database.replace("\\", "/")).name
        return f"{driver} {_safe_log_token(filename, 'database')}"

    hostname = _safe_log_token(url.host, "local")
    database_name = PurePosixPath(database.replace("\\", "/")).name
    return f"{driver} {hostname}/{_safe_log_token(database_name, 'database')}"


@app.on_event("startup")
def _startup() -> None:
    settings.ensure_dirs()
    init_db()
    logging.getLogger(__name__).info(
        "ready — asr=%s llm=%s db=%s",
        settings.asr_backend,
        settings.llm_model,
        safe_database_label(settings.resolved_database_url()),
    )


@app.get("/api/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "asr_backend": settings.asr_backend,
        "llm_model": settings.llm_model,
        "openai_key_present": bool(settings.openai_api_key),
    }


# Audio clips + downloaded media. Range requests (needed for <audio> seeking) are
# handled by StaticFiles.
settings.ensure_dirs()
app.mount("/media", StaticFiles(directory=str(settings.data_dir)), name="media")
