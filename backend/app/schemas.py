"""API request/response schemas.

The critical rule enforced here: `Exercise.answer` is never serialized into a GET
response. Answers only travel back to the client inside a grading result, after the
learner has committed to an attempt.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class LanguageOut(BaseModel):
    code: str
    name_en: str
    name_native: str


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider: str
    provider_id: str
    url: str
    title: str
    channel: str | None
    duration_s: float | None
    license_name: str | None
    upload_date: str | None


class ExercisePublic(BaseModel):
    """An exercise as the learner sees it — no answer field."""

    id: int
    kind: str
    order_idx: int
    prompt: str
    payload: dict[str, Any]
    cefr: str | None
    audio_start_s: float | None
    audio_end_s: float | None
    generator: str


class UnitSummary(BaseModel):
    id: int
    idx: int
    start_s: float
    end_s: float
    duration_s: float
    cefr: str | None
    wpm: float | None
    difficulty_score: float | None
    gist: str | None
    clip_url: str | None
    exercise_count: int


class UnitDetail(UnitSummary):
    exercises: list[ExercisePublic]
    difficulty_detail: dict[str, Any] = Field(default_factory=dict)


class LessonSummary(BaseModel):
    id: int
    title: str
    language: str
    skill: str
    topic: str | None
    cefr: str | None
    difficulty_score: float | None
    unit_count: int
    exercise_count: int
    duration_s: float | None
    source: SourceOut
    created_at: datetime


class LessonDetail(LessonSummary):
    units: list[UnitSummary]


class TranscriptOut(BaseModel):
    unit_id: int
    text: str
    words: list[dict[str, Any]]
    asr_backend: str
    asr_model: str


class AttemptIn(BaseModel):
    exercise_id: int
    response: dict[str, Any] = Field(default_factory=dict)
    learner_key: str = "anonymous"
    replays: int = 0


class AttemptOut(BaseModel):
    id: int
    exercise_id: int
    is_correct: bool
    score: float
    feedback: dict[str, Any]
    explanation: str | None
    # Revealed only now that the learner has answered.
    answer: dict[str, Any]
    audio_start_s: float | None
    audio_end_s: float | None


class IngestIn(BaseModel):
    url: HttpUrl
    language: str = "fr"
    topic: str | None = None
    asr_backend: str | None = None
    max_units: int | None = Field(default=None, ge=1, le=50)
    use_llm: bool = True
    require_cc: bool = False


class JobOut(BaseModel):
    job_id: str
    status: str  # queued | running | done | error
    url: str
    message: str | None = None
    lesson_id: int | None = None
    detail: dict[str, Any] | None = None


class ProgressOut(BaseModel):
    learner_key: str
    attempts: int
    correct: int
    accuracy: float
    mean_score: float
    by_kind: dict[str, dict[str, float]]
    units_touched: int


# ---------------------------------------------------------------- smart translation


class SenseOut(BaseModel):
    gloss_en: str
    when: str


class WordGlossOut(BaseModel):
    surface: str
    lemma: str | None = None
    pos: str | None = None
    gloss_en: str
    other_senses: list[SenseOut] = Field(default_factory=list)
    note: str | None = None
    zipf: float | None = None


class ExpressionOut(BaseModel):
    id: int | None = None
    canonical: str
    surface: str
    kind: str
    gloss_en: str
    literal_en: str | None = None
    note: str | None = None
    # Character spans of the expression's own words, so the UI can highlight a
    # discontinuous expression in place ("y a mis le feu" -> two spans).
    component_spans: list[list[int]] = Field(default_factory=list)
    char_start: int
    char_end: int
    confidence: float
    # precomputed | inferred | live
    source: str


class LookupIn(BaseModel):
    language: str = "fr"
    text: str = Field(min_length=1, max_length=20_000)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    # When set, precomputed expression spans for that unit are used (the instant path).
    unit_id: int | None = None


class LookupOut(BaseModel):
    language: str
    selection: str
    char_start: int
    char_end: int
    context: str
    # Original-video timeline; null when the selection can't be located in the audio.
    audio_start_s: float | None = None
    audio_end_s: float | None = None
    word: WordGlossOut
    expressions: list[ExpressionOut] = Field(default_factory=list)
    source: str
    unit_id: int | None = None
    lemmatizer: str
    inferred: bool
    error: str | None = None


class UnitExpressionsOut(BaseModel):
    unit_id: int
    expressions: list[dict[str, Any]] = Field(default_factory=list)


class VocabSaveIn(BaseModel):
    language: str = "fr"
    headword: str = Field(min_length=1, max_length=128)
    gloss_en: str | None = None
    example: str | None = None
    unit_id: int | None = None
    learner_key: str = "anonymous"


class VocabItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    language: str
    headword: str
    gloss_en: str | None
    example: str | None
    zipf: float | None
    reps: int
    due_at: datetime | None
