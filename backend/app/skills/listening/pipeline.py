"""End-to-end listening pipeline.

    YouTube URL
      -> yt-dlp audio download
      -> ASR with word timestamps
      -> segmentation into 60-120s listening units
      -> per-unit difficulty analysis (CEFR estimate)
      -> per-unit clip extraction
      -> exercises: deterministic cloze + LLM comprehension set
      -> persisted Lesson / ListeningUnit / Exercise rows

Every stage is resumable: an already-downloaded source is reused, and `reuse_transcript`
skips a second (paid) ASR pass while still rebuilding exercises.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...asr import get_transcriber, prompt_for
from ...asr.base import ASRResult, ASRSegment, Word
from ...config import settings
from ...languages import get_language
from ...lexicon.extractor import extract_expressions
from ...media import audio as audio_utils
from ...media.youtube import download_audio
from ...storage import clip_key as make_clip_key
from ...storage import get_store, source_key, transcript_key
from ...storage.cleanup import derived_paths, prune_after_upload
from ...models import (
    EX_CLOZE,
    SKILL_LISTENING,
    Exercise,
    Expression,
    Lesson,
    ListeningUnit,
    Segment,
    Source,
    Transcript,
)
from . import difficulty as difficulty_mod
from .cloze import build_cloze
from .generator import LLMError, generate_unit_exercises
from .segmenter import segment_into_units

log = logging.getLogger(__name__)


@dataclass
class PipelineReport:
    lesson_id: int
    source_id: int
    transcript_id: int
    title: str
    language: str
    units: int
    exercises: int
    expressions: int
    cefr: str
    difficulty: float
    asr_backend: str
    asr_model: str
    reused_transcript: bool
    llm_failures: int
    audio_key: str
    local_mb_freed: float

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


# ------------------------------------------------------------------ persistence helpers


def _upsert_source(db: Session, info, language: str, topic: str | None) -> Source:
    src = db.scalar(
        select(Source).where(
            Source.provider == info.provider, Source.provider_id == info.provider_id
        )
    )
    if src is None:
        src = Source(provider=info.provider, provider_id=info.provider_id)
        db.add(src)

    src.language = language
    src.url = info.url
    src.title = info.title
    src.channel = info.channel
    src.uploader_url = info.uploader_url
    src.duration_s = info.duration_s
    src.topic = topic or src.topic
    src.license_name = info.license_name
    src.upload_date = info.upload_date
    src.description = info.description
    # Upload the source to object storage. ASR needs a local file to read, so the
    # download stays on disk for this run and the store gets a copy under a stable key.
    store = get_store()
    key = source_key(info.provider_id, info.audio_path.suffix)
    store.put_file(key, info.audio_path)
    src.audio_key = key
    src.audio_bytes = info.audio_path.stat().st_size
    db.flush()
    return src


def _save_transcript(db: Session, src: Source, result: ASRResult) -> Transcript:
    raw = json.dumps(result.to_dict(), ensure_ascii=False, indent=2).encode("utf-8")
    raw_key = transcript_key(src.provider_id, result.backend)
    get_store().put_bytes(raw_key, raw, overwrite=True)

    tr = Transcript(
        source_id=src.id,
        language=src.language,
        asr_backend=result.backend,
        asr_model=result.model,
        text=result.text,
        word_count=result.word_count,
        duration_s=result.duration_s,
        raw_key=raw_key,
    )
    db.add(tr)
    db.flush()

    for seg in result.segments:
        db.add(
            Segment(
                transcript_id=tr.id,
                idx=seg.idx,
                start_s=seg.start,
                end_s=seg.end,
                text=seg.text,
                words_json=[w.to_dict() for w in seg.words],
                avg_logprob=seg.avg_logprob,
                no_speech_prob=seg.no_speech_prob,
            )
        )
    db.flush()
    return tr


def _load_segments(tr: Transcript) -> list[ASRSegment]:
    out: list[ASRSegment] = []
    for s in tr.segments:
        words = [
            Word(
                text=w.get("word", ""),
                start=float(w.get("start") or 0.0),
                end=float(w.get("end") or 0.0),
                probability=w.get("probability"),
            )
            for w in (s.words_json or [])
        ]
        out.append(
            ASRSegment(
                idx=s.idx,
                start=s.start_s,
                end=s.end_s,
                text=s.text,
                words=words,
                avg_logprob=s.avg_logprob,
                no_speech_prob=s.no_speech_prob,
            )
        )
    return out


# ------------------------------------------------------------------ main entry point


def build_listening_lesson(
    db: Session,
    url: str,
    *,
    language: str = "fr",
    topic: str | None = None,
    asr_backend: str | None = None,
    asr_model: str | None = None,
    max_units: int | None = None,
    max_duration_s: float | None = 3600.0,
    require_cc: bool = False,
    use_llm: bool = True,
    reuse_transcript: bool = True,
    make_clips: bool = True,
    extract_mwe: bool = True,
    cleanup_local: bool | None = None,
) -> PipelineReport:
    lang = get_language(language)
    settings.ensure_dirs()

    # 1. audio -------------------------------------------------------------
    log.info("downloading audio for %s", url)
    info = download_audio(
        url, settings.audio_dir, max_duration_s=max_duration_s, require_cc=require_cc
    )
    src = _upsert_source(db, info, lang.code, topic)
    log.info("source %s: %r (%s)", src.id, src.title, src.channel)

    # 2. transcript --------------------------------------------------------
    existing = db.scalar(
        select(Transcript)
        .where(Transcript.source_id == src.id)
        .order_by(Transcript.created_at.desc())
    )
    reused = False
    if reuse_transcript and existing is not None:
        log.info("reusing transcript %s (%s/%s)", existing.id, existing.asr_backend, existing.asr_model)
        tr = existing
        segments = _load_segments(tr)
        reused = True
    else:
        transcriber = get_transcriber(asr_backend, asr_model)
        log.info("transcribing with %s/%s", transcriber.name, transcriber.model)
        result = transcriber.transcribe(
            info.audio_path, language=lang.asr_code, prompt=prompt_for(lang.code)
        )
        if not result.segments:
            raise RuntimeError("ASR returned no segments — audio may be silent or non-speech")
        tr = _save_transcript(db, src, result)
        segments = result.segments
        log.info("transcript: %d words, %d segments", tr.word_count, len(segments))

    if not any(s.words for s in segments):
        raise RuntimeError(
            "transcript has no word-level timestamps; cloze generation needs them. "
            "Use ASR_BACKEND=openai with whisper-1, or the local faster-whisper backend."
        )

    # 3. units -------------------------------------------------------------
    units = segment_into_units(segments)
    if max_units:
        units = units[:max_units]
    if not units:
        raise RuntimeError("segmentation produced no usable units")

    # Replace any previous lesson for this source so re-runs stay idempotent.
    for old in list(src.lessons):
        if old.skill == SKILL_LISTENING:
            db.delete(old)
    db.flush()

    lesson = Lesson(
        source_id=src.id,
        transcript_id=tr.id,
        language=lang.code,
        skill=SKILL_LISTENING,
        title=src.title,
        topic=src.topic or topic,
        cefr=None,
    )
    db.add(lesson)
    db.flush()

    # One store for the whole run. `_upsert_source` has its own local handle for the source
    # upload; the clip loop below needs one in this scope too.
    store = get_store()
    clip_dir = settings.clip_dir / src.provider_id
    # (object key, local file) for every upload this run, so cleanup can confirm each
    # object is really in the store before removing its local copy.
    uploaded: list[tuple[str, Path]] = [(src.audio_key or "", info.audio_path)]
    reports: list[difficulty_mod.DifficultyReport] = []
    total_exercises = 0
    total_expressions = 0
    llm_failures = 0
    lesson_topic = lesson.topic

    for u in units:
        report = difficulty_mod.analyze(
            u.text, u.duration_s, lang, words=[w.to_dict() for w in u.words]
        )
        reports.append(report)

        unit_clip_key: str | None = None
        if make_clips:
            # Cut locally with ffmpeg, then hand the file to the store. The key format is
            # identical under local and Supabase backends, so nothing downstream changes.
            local_clip = clip_dir / f"unit_{u.idx:03d}.m4a"
            if not local_clip.exists():
                audio_utils.extract_clip(info.audio_path, u.start_s, u.end_s, local_clip)
            unit_clip_key = store.put_file(make_clip_key(src.provider_id, u.idx), local_clip)
            uploaded.append((unit_clip_key, local_clip))

        unit_row = ListeningUnit(
            lesson_id=lesson.id,
            idx=u.idx,
            start_s=u.start_s,
            end_s=u.end_s,
            text=u.text,
            words_json=[w.to_dict() for w in u.words],
            clip_key=unit_clip_key,
            wpm=report.wpm,
            cefr=report.cefr,
            difficulty_score=report.score,
            difficulty_detail=report.to_dict(),
        )
        db.add(unit_row)
        db.flush()

        exercises: list[dict[str, Any]] = []

        # Deterministic cloze first — it never depends on the LLM being available.
        cz = build_cloze(
            u.words,
            lang,
            unit_start_s=u.start_s,
            unit_end_s=u.end_s,
            display_text=u.text,  # punctuated ASR text; words are aligned onto it
            cefr=report.cefr,
        )
        if cz:
            cz["order_idx"] = 0
            exercises.append(cz)

        if use_llm:
            try:
                gen = generate_unit_exercises(
                    transcript=u.text,
                    words=u.words,
                    lang=lang,
                    cefr=report.cefr,
                    wpm=report.wpm,
                    unit_start_s=u.start_s,
                    unit_end_s=u.end_s,
                    source_title=src.title,
                )
                unit_row.gist = gen.get("gist_en")
                lesson_topic = lesson_topic or gen.get("topic")
                for ex in gen["exercises"]:
                    ex["order_idx"] = ex["order_idx"] + 1  # cloze holds slot 0
                    exercises.append(ex)
            except LLMError as exc:
                llm_failures += 1
                log.warning("LLM generation failed for unit %d: %s", u.idx, exc)

        # Multiword-expression annotations. Precomputed here so a selection lookup is a
        # pure span query at runtime — the popup must not wait on a model call.
        if use_llm and extract_mwe:
            try:
                found = extract_expressions(u.text, lang)
                for e in found:
                    db.add(Expression(unit_id=unit_row.id, language=lang.code, **e))
                total_expressions += len(found)
            except LLMError as exc:
                llm_failures += 1
                log.warning("expression extraction failed for unit %d: %s", u.idx, exc)

        for ex in exercises:
            db.add(
                Exercise(
                    unit_id=unit_row.id,
                    skill=SKILL_LISTENING,
                    kind=ex["kind"],
                    order_idx=ex.get("order_idx", 0),
                    prompt=ex["prompt"],
                    payload=ex.get("payload", {}),
                    answer=ex.get("answer", {}),
                    explanation=ex.get("explanation"),
                    audio_start_s=ex.get("audio_start_s"),
                    audio_end_s=ex.get("audio_end_s"),
                    cefr=report.cefr,
                    generator=ex.get("generator", "llm"),
                )
            )
            total_exercises += 1

        log.info(
            "unit %d/%d  %.0fs  %s (%.0f)  %d exercises",
            u.idx + 1,
            len(units),
            u.duration_s,
            report.cefr,
            report.score,
            len(exercises),
        )

    score, cefr = difficulty_mod.aggregate(reports)
    lesson.difficulty_score = score
    lesson.cefr = cefr
    lesson.topic = lesson_topic
    if not src.topic:
        src.topic = lesson_topic
    db.flush()

    # Free the scratch files. Each is deleted only if its object is confirmed present in
    # the store, so a failed upload leaves the local copy intact.
    do_cleanup = settings.cleanup_local_after_upload if cleanup_local is None else cleanup_local
    freed = 0.0
    if do_cleanup:
        files, dirs = derived_paths(settings.audio_dir, info.audio_path.stem)
        report_cleanup = prune_after_upload(store, uploaded=uploaded, derived=files, dirs=dirs)
        freed = report_cleanup.mb_freed
        log.info("local cleanup: %s", report_cleanup.summary())

    return PipelineReport(
        lesson_id=lesson.id,
        source_id=src.id,
        transcript_id=tr.id,
        title=src.title,
        language=lang.code,
        units=len(units),
        exercises=total_exercises,
        expressions=total_expressions,
        cefr=cefr,
        difficulty=score,
        asr_backend=tr.asr_backend,
        asr_model=tr.asr_model,
        reused_transcript=reused,
        llm_failures=llm_failures,
        audio_key=src.audio_key or "",
        local_mb_freed=round(freed, 2),
    )
