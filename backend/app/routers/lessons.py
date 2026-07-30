"""Lesson / unit / exercise read endpoints."""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..languages import supported_languages
from ..models import Exercise, Lesson, ListeningUnit, Segment, Source, Transcript
from ..media.timestretch import TimeStretchError, natural_slow
from ..storage import get_store, variant_clip_key, variant_map_key
from ..schemas import (
    ClipVariantOut,
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


@router.get("/units/{unit_id}/clip", response_model=ClipVariantOut)
def get_unit_clip(
    unit_id: int,
    speed: float = Query(default=1.0, ge=0.4, le=1.0),
    db: Session = Depends(get_db),
) -> ClipVariantOut:
    """Playable clip at `speed`, reshaped to sound like deliberate speech.

    Below 1.0 this is not `playbackRate`. Uniform slowdown stretches the inside of every
    phoneme, which is what makes it sound underwater; a speaker slowing down instead keeps
    articulation near normal and inserts pauses. So the variant stretches the words only
    slightly and puts the rest of the time into the gaps.

    Generated on first request and cached in the object store, because it costs a decode,
    a WSOLA pass and an encode — a few seconds — and the same unit is replayed constantly.
    """
    unit = db.get(ListeningUnit, unit_id)
    if not unit:
        raise HTTPException(404, f"unit {unit_id} not found")
    if not unit.clip_key:
        raise HTTPException(404, f"unit {unit_id} has no audio clip")

    store = get_store()

    if speed >= 0.999:
        return ClipVariantOut(
            unit_id=unit.id,
            speed=1.0,
            url=clip_url(unit),
            duration_s=round(unit.duration_s, 3),
            natural=True,
            time_map=[],
        )

    provider_id = unit.lesson.source.provider_id
    key = variant_clip_key(provider_id, unit.idx, speed)
    map_key = variant_map_key(provider_id, unit.idx, speed)

    # Serve the cached variant when both the audio and its time map are present. Without
    # the map the client couldn't translate replay windows, so a half-written pair is
    # treated as absent and regenerated.
    if store.exists(key) and store.exists(map_key):
        try:
            meta = json.loads(store.get_bytes(map_key).decode("utf-8"))
            return ClipVariantOut(unit_id=unit.id, speed=speed, url=store.url_for(key), **meta)
        except Exception:  # noqa: BLE001 - corrupt cache, fall through and rebuild
            log.warning("unreadable variant map %s; regenerating", map_key)

    words = unit.words_json or []
    if len(words) < 8:
        raise HTTPException(
            409,
            "this unit has too few word timings to reshape; natural slow playback needs "
            "word-level timestamps from ASR",
        )

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "original.m4a"
        src.write_bytes(store.get_bytes(unit.clip_key))
        out = Path(tmp) / f"slow_{speed:g}.m4a"
        try:
            res = natural_slow(
                src, words, speed=speed, clip_start_s=unit.start_s, dst=out
            )
        except (TimeStretchError, ValueError) as exc:
            raise HTTPException(500, f"could not reshape this clip: {exc}") from None

        store.put_file(key, out, overwrite=True)
        meta = {
            "duration_s": res.new_duration_s,
            "natural": False,
            "word_factor": res.word_factor,
            "inserted_silence_s": res.inserted_silence_s,
            "pauses": res.boundaries,
            "time_map": res.time_map,
        }
        store.put_bytes(map_key, json.dumps(meta).encode("utf-8"), overwrite=True)

    return ClipVariantOut(unit_id=unit.id, speed=speed, url=store.url_for(key), **meta)


def _located_words(unit: ListeningUnit) -> list[dict]:
    """Unit words carrying both their timing and their span in the displayed text.

    The stored word list has timestamps but no character spans, because ASR emits bare
    tokens while the display text is punctuated. Recovering the spans here — with the same
    aligner the cloze builder uses — is what lets the client highlight the word being
    spoken: it needs one array that answers "which characters is this moment?".

    Doing it server-side matters. The client cannot re-tokenize its way to the same answer:
    the model may emit "l'a" as one token or as "l" + "a", so any naive split would drift
    and highlight the wrong word — silently, and worse the further into the passage you get.

    Words the aligner could not place are dropped rather than sent with null spans: they
    have nothing to highlight, and holding the previous word through them reads better than
    a gap.
    """
    raw = [w for w in (unit.words_json or []) if (w.get("word") or "").strip()]
    if not raw or not unit.text:
        return []

    from ..asr.base import Word
    from ..skills.listening.align import align_words_to_text

    spans = align_words_to_text(
        [
            Word(
                text=w.get("word", ""),
                start=float(w.get("start") or 0.0),
                end=float(w.get("end") or 0.0),
                probability=w.get("probability"),
            )
            for w in raw
        ],
        unit.text,
    )
    return [
        {
            "word": w.get("word", ""),
            "start": round(float(w.get("start") or 0.0), 3),
            "end": round(float(w.get("end") or 0.0), 3),
            "char_start": span[0],
            "char_end": span[1],
        }
        for w, span in zip(raw, spans)
        if span is not None
    ]


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
        words=_located_words(unit),
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
