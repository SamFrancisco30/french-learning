"""FastAPI application entrypoint.

Run:  uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import init_db
from .routers import attempts, ingest, lessons, lexicon

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

app.include_router(lessons.router)
app.include_router(attempts.router)
app.include_router(ingest.router)
app.include_router(lexicon.router)


@app.on_event("startup")
def _startup() -> None:
    settings.ensure_dirs()
    init_db()
    logging.getLogger(__name__).info(
        "ready — asr=%s llm=%s db=%s",
        settings.asr_backend,
        settings.llm_model,
        settings.resolved_database_url(),
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
