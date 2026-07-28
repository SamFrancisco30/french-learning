"""Ingest endpoint.

Ingest is minutes-long (download + ASR + N LLM calls), so the request returns a job id
immediately and the client polls. The job registry is in-process: fine for single-worker
local use, and the obvious upgrade path is a real queue (Celery/RQ/arq) plus a jobs table.
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException

from ..db import session_scope
from ..languages import get_language
from ..schemas import IngestIn, JobOut
from ..skills.listening.pipeline import build_listening_lesson

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["ingest"])

_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()
# One ingest at a time: ffmpeg + ASR are already CPU-saturating.
_ingest_gate = threading.Semaphore(1)


def _set(job_id: str, **fields: Any) -> None:
    with _lock:
        _jobs.setdefault(job_id, {}).update(fields)


def _run_ingest(job_id: str, payload: IngestIn) -> None:
    with _ingest_gate:
        _set(job_id, status="running", message="downloading audio")
        try:
            with session_scope() as db:
                report = build_listening_lesson(
                    db,
                    str(payload.url),
                    language=payload.language,
                    topic=payload.topic,
                    asr_backend=payload.asr_backend,
                    max_units=payload.max_units,
                    use_llm=payload.use_llm,
                    require_cc=payload.require_cc,
                )
            _set(
                job_id,
                status="done",
                message=f"{report.units} units, {report.exercises} exercises",
                lesson_id=report.lesson_id,
                detail=report.to_dict(),
            )
            log.info("ingest %s done: lesson %s", job_id, report.lesson_id)
        except Exception as exc:  # noqa: BLE001 - surface any failure to the poller
            log.exception("ingest %s failed", job_id)
            _set(job_id, status="error", message=f"{type(exc).__name__}: {exc}")


@router.post("/ingest", response_model=JobOut, status_code=202)
def start_ingest(payload: IngestIn, background: BackgroundTasks) -> JobOut:
    try:
        get_language(payload.language)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None

    job_id = uuid.uuid4().hex[:12]
    _set(job_id, status="queued", url=str(payload.url), message="queued", lesson_id=None)
    background.add_task(_run_ingest, job_id, payload)
    return JobOut(job_id=job_id, status="queued", url=str(payload.url), message="queued")


@router.get("/ingest/{job_id}", response_model=JobOut)
def get_job(job_id: str) -> JobOut:
    with _lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, f"job {job_id} not found")
    return JobOut(
        job_id=job_id,
        status=job.get("status", "unknown"),
        url=job.get("url", ""),
        message=job.get("message"),
        lesson_id=job.get("lesson_id"),
        detail=job.get("detail"),
    )


@router.get("/ingest", response_model=list[JobOut])
def list_jobs() -> list[JobOut]:
    with _lock:
        items = list(_jobs.items())
    return [
        JobOut(
            job_id=jid,
            status=j.get("status", "unknown"),
            url=j.get("url", ""),
            message=j.get("message"),
            lesson_id=j.get("lesson_id"),
        )
        for jid, j in items
    ]
