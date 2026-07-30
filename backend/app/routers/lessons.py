"""Lesson / unit / exercise read endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..languages import supported_languages
from ..models import Exercise, Lesson, ListeningUnit, Segment, Source, Transcript
from ..storage import get_store
from ..schemas import (
    ExercisePublic,
    LanguageOut,
    LessonDetail,
    LessonSummary,
    SourceOut,
    TranscriptOut,
    UnitDetail,
    UnitSummary,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["lessons"])


def clip_url(unit: ListeningUnit) -> str | None:
    """Playable URL for a unit's clip, or None.

    Only called for a single unit at a time. Lesson listings deliberately leave this null:
    on the Supabase backend each URL is a signed-URL request, so signing every unit of every
    lesson in a library listing would be N network calls for links nobody clicks.
    """
    if not unit.clip_key:
        return None
    try:
        return get_store().url_for(unit.clip_key)
    except Exception:  # noqa: BLE001 - a signing failure must not break the whole response
        log.warning("could not produce a URL for %s", unit.clip_key)
        return None


def _unit_summary(u: ListeningUnit, exercise_count: int) -> UnitSummary:
    return UnitSummary(
        id=u.id,
        idx=u.idx,
        start_s=round(u.start_s, 3),
        end_s=round(u.end_s, 3),
        duration_s=round(u.duration_s, 3),
        cefr=u.cefr,
        wpm=u.wpm,
        difficulty_score=u.difficulty_score,
        gist=u.gist,
        # Left unsigned in listings — see clip_url's docstring.
        clip_url=None,
        exercise_count=exercise_count,
    )


@router.get("/languages", response_model=list[LanguageOut])
def list_languages() -> list[LanguageOut]:
    return [
        LanguageOut(code=l.code, name_en=l.name_en, name_native=l.name_native)
        for l in supported_languages()
    ]


@router.get("/lessons", response_model=list[LessonSummary])
def list_lessons(
    db: Session = Depends(get_db),
    language: str | None = None,
    skill: str | None = "listening",
    cefr: str | None = None,
    topic: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[LessonSummary]:
    stmt = select(Lesson).options(selectinload(Lesson.source), selectinload(Lesson.units))
    if language:
        stmt = stmt.where(Lesson.language == language)
    if skill:
        stmt = stmt.where(Lesson.skill == skill)
    if cefr:
        stmt = stmt.where(Lesson.cefr == cefr)
    if topic:
        stmt = stmt.where(Lesson.topic == topic)
    lessons = db.scalars(stmt.order_by(Lesson.created_at.desc()).limit(limit)).all()

    counts = _exercise_counts(db, [u.id for l in lessons for u in l.units])
    return [
        LessonSummary(
            id=l.id,
            title=l.title,
            language=l.language,
            skill=l.skill,
            topic=l.topic,
            cefr=l.cefr,
            difficulty_score=l.difficulty_score,
            unit_count=len(l.units),
            exercise_count=sum(counts.get(u.id, 0) for u in l.units),
            duration_s=l.source.duration_s if l.source else None,
            source=SourceOut.model_validate(l.source),
            created_at=l.created_at,
        )
        for l in lessons
    ]


@router.get("/lessons/{lesson_id}", response_model=LessonDetail)
def get_lesson(lesson_id: int, db: Session = Depends(get_db)) -> LessonDetail:
    lesson = db.scalar(
        select(Lesson)
        .where(Lesson.id == lesson_id)
        .options(selectinload(Lesson.source), selectinload(Lesson.units))
    )
    if not lesson:
        raise HTTPException(404, f"lesson {lesson_id} not found")

    counts = _exercise_counts(db, [u.id for u in lesson.units])
    return LessonDetail(
        id=lesson.id,
        title=lesson.title,
        language=lesson.language,
        skill=lesson.skill,
        topic=lesson.topic,
        cefr=lesson.cefr,
        difficulty_score=lesson.difficulty_score,
        unit_count=len(lesson.units),
        exercise_count=sum(counts.values()),
        duration_s=lesson.source.duration_s if lesson.source else None,
        source=SourceOut.model_validate(lesson.source),
        created_at=lesson.created_at,
        units=[_unit_summary(u, counts.get(u.id, 0)) for u in lesson.units],
    )


@router.get("/units/{unit_id}", response_model=UnitDetail)
def get_unit(unit_id: int, db: Session = Depends(get_db)) -> UnitDetail:
    unit = db.scalar(
        select(ListeningUnit)
        .where(ListeningUnit.id == unit_id)
        .options(selectinload(ListeningUnit.exercises))
    )
    if not unit:
        raise HTTPException(404, f"unit {unit_id} not found")

    summary = _unit_summary(unit, len(unit.exercises)).model_dump()
    summary["clip_url"] = clip_url(unit)  # signed here, where it is actually played
    return UnitDetail(
        **summary,
        difficulty_detail=unit.difficulty_detail or {},
        # NOTE: answers deliberately omitted — see schemas.ExercisePublic.
        exercises=[
            ExercisePublic(
                id=e.id,
                kind=e.kind,
                order_idx=e.order_idx,
                prompt=e.prompt,
                payload=e.payload or {},
                cefr=e.cefr,
                audio_start_s=e.audio_start_s,
                audio_end_s=e.audio_end_s,
                generator=e.generator,
            )
            for e in unit.exercises
        ],
    )


@router.get("/units/{unit_id}/transcript", response_model=TranscriptOut)
def get_unit_transcript(unit_id: int, db: Session = Depends(get_db)) -> TranscriptOut:
    """Full text for a unit. The UI gates this behind 'reveal transcript'."""
    unit = db.get(ListeningUnit, unit_id)
    if not unit:
        raise HTTPException(404, f"unit {unit_id} not found")
    tr = db.get(Transcript, unit.lesson.transcript_id) if unit.lesson.transcript_id else None
    return TranscriptOut(
        unit_id=unit.id,
        text=unit.text,
        words=unit.words_json or [],
        asr_backend=tr.asr_backend if tr else "unknown",
        asr_model=tr.asr_model if tr else "unknown",
    )


@router.get("/sources", response_model=list[SourceOut])
def list_sources(db: Session = Depends(get_db), language: str | None = None) -> list[SourceOut]:
    stmt = select(Source).order_by(Source.created_at.desc())
    if language:
        stmt = stmt.where(Source.language == language)
    return [SourceOut.model_validate(s) for s in db.scalars(stmt).all()]


def _exercise_counts(db: Session, unit_ids: list[int]) -> dict[int, int]:
    if not unit_ids:
        return {}
    rows = db.execute(
        select(Exercise.unit_id, func.count(Exercise.id))
        .where(Exercise.unit_id.in_(unit_ids))
        .group_by(Exercise.unit_id)
    ).all()
    return {uid: n for uid, n in rows}
