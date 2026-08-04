"""API request/response schemas.

The critical rule enforced here: `Exercise.answer` is never serialized into a GET
response. Answers only travel back to the client inside a grading result, after the
learner has committed to an attempt.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


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
    normalized_headword: str
    pos: str | None = None
    gloss_en: str
    other_senses: list[SenseOut] = Field(default_factory=list)
    note: str | None = None
    zipf: float | None = None


class ExpressionOut(BaseModel):
    id: int | None = None
    canonical: str
    surface: str
    normalized_headword: str
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
    # True when the selection is long enough to warrant sentence analysis.
    is_sentence: bool = False
    # Deterministic construction matches — free and instant, unlike POST /api/sentence.
    constructions: list[ConstructionHitOut] = Field(default_factory=list)
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
    model_config = ConfigDict(extra="forbid")

    language: str = "fr"
    headword: str = Field(max_length=128)
    gloss_en: str | None = Field(default=None, max_length=1000)
    example: str | None = Field(default=None, max_length=2000)
    unit_id: int | None = None


class VocabEditIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gloss_en: str | None = Field(default=None, max_length=1000)
    example: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_edit(self) -> VocabEditIn:
        if not ({"gloss_en", "example"} & self.model_fields_set):
            raise ValueError("at least one editable field is required")
        return self


class VocabSourceOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    lesson_id: int
    lesson_title: str
    unit_id: int
    unit_index: int


class VocabItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: int
    language: str
    headword: str
    normalized_headword: str
    gloss_en: str | None
    example: str | None
    zipf: float | None
    reps: int
    due_at: datetime | None
    created_at: datetime
    updated_at: datetime
    source: VocabSourceOut | None


class VocabListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[VocabItemOut]
    next_cursor: str | None
    total: int


class VocabSavedKeyOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: int
    normalized_headword: str


class VocabSavedKeysOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str
    items: list[VocabSavedKeyOut]


# ---------------------------------------------------------------- sentence grammar


class ConstructionHitOut(BaseModel):
    key: str
    schema_form: str
    name_en: str
    meaning_en: str
    why_opaque: str
    literal_trap: str | None = None
    cefr: str
    example_fr: str
    example_en: str
    register_note: str
    char_start: int
    char_end: int
    marker_spans: list[list[int]] = Field(default_factory=list)


class StructureOut(BaseModel):
    key: str
    schema_form: str
    name_en: str
    meaning_en: str
    why_opaque: str
    literal_trap: str | None = None
    in_this_sentence: str
    quote: str = ""
    cefr: str
    char_start: int = 0
    char_end: int = 0
    marker_spans: list[list[int]] = Field(default_factory=list)
    # pattern = found by the deterministic matcher; llm = proposed by the model, less certain
    source: str = "pattern"


class PracticeOut(BaseModel):
    construction_key: str
    schema_form: str = ""
    prompt_en: str
    hint_en: str | None = None
    required_markers: list[str] = Field(default_factory=list)
    # reference_fr and alternatives are withheld until the learner answers.


class SentenceIn(BaseModel):
    language: str = "fr"
    text: str = Field(min_length=1, max_length=600)
    refresh: bool = False


class SentenceOut(BaseModel):
    text: str
    translation_en: str
    register_note: str | None = None
    structures: list[StructureOut] = Field(default_factory=list)
    practices: list[PracticeOut] = Field(default_factory=list)
    notes: str | None = None
    source: str


class PracticeCheckIn(BaseModel):
    language: str = "fr"
    # The sentence the practice came from, so the reference can be looked up server-side
    # rather than trusted from the client.
    sentence: str = Field(min_length=1, max_length=600)
    practice_index: int = Field(ge=0, le=9)
    answer: str = Field(min_length=1, max_length=400)


class PracticeStructureOut(BaseModel):
    checked: bool
    used: bool
    missing_markers: list[str] = Field(default_factory=list)
    schema_form: str | None = None


class PracticeIssueOut(BaseModel):
    fragment: str
    problem: str
    fix: str


class PracticeCheckOut(BaseModel):
    correct: bool
    score: float
    headline: str
    structure: PracticeStructureOut
    meaning_ok: bool | None = None
    grammar_ok: bool | None = None
    issues: list[PracticeIssueOut] = Field(default_factory=list)
    corrected_fr: str | None = None
    note_en: str | None = None
    tolerance: str | None = None
    reference_fr: str
    better_than_reference: bool = False
    judged: bool


# ---------------------------------------------------------------- natural slow playback


class ClipVariantOut(BaseModel):
    unit_id: int
    speed: float
    url: str | None
    duration_s: float
    # True for the untouched original (speed 1.0); False for a reshaped variant.
    natural: bool
    word_factor: float | None = None
    inserted_silence_s: float | None = None
    # How many pauses the added time was spread across. With inserted_silence_s this gives the
    # mean gap, which is the number that actually tells a learner what 0.5x will sound like —
    # 316ms on one clip and 2.1s on a dense one, for the same requested speed. Optional so
    # variants cached before this field existed still deserialize.
    pauses: int | None = None
    # [[original_clip_s, stretched_clip_s], ...] at word starts. The client interpolates
    # through it so an exercise's replay window still lands on the right audio at 0.75x.
    time_map: list[list[float]] = Field(default_factory=list)


# ---------------------------------------------------------------- dictation


class DictationLevelOut(BaseModel):
    level: str
    mode: str
    attempts: int
    recent_mean: float | None = None
    # Shown to the learner: an adaptive level that cannot explain itself feels arbitrary.
    reason: str


class DictationItemOut(BaseModel):
    exercise_id: int
    mode: str
    prompt: str
    cefr: str | None
    difficulty_score: float | None = None
    word_count: int | None = None
    # One entry per word, giving that word's character count and nothing else. Drives the
    # underscore hints; carries no letters, so it cannot leak the answer.
    word_lengths: list[int] = Field(default_factory=list)
    sentence_count: int | None = None
    # ORIGINAL-VIDEO seconds, same timeline as the listening player.
    audio_start_s: float | None
    audio_end_s: float | None
    unit_id: int
    unit_start_s: float
    unit_end_s: float
    clip_url: str | None = None
    # The lesson, so the client can link back to this passage in the listening drill. The unit alone
    # is not enough: the listening route is /lesson/{id}/unit/{id}, and resolving one from the other
    # would be a second request for something already loaded here.
    lesson_id: int | None = None
    lesson_title: str | None = None
    topic: str | None = None


class DictationNextOut(BaseModel):
    item: DictationItemOut
    level: DictationLevelOut
    # What was actually served, which can differ from the target when a level is empty.
    served_level: str
    off_level: bool
    repeat: bool
    remaining_at_level: int


class DictationAudioOut(BaseModel):
    exercise_id: int
    url: str | None
    speed: float
    punctuation: bool
    # None when served from cache — the client reads the real duration off the audio element. See
    # the note in routers/dictation.py: the window length is not the file length.
    duration_s: float | None = None
    cached: bool


# --- accounts and entitlements ---


class PriceOut(BaseModel):
    """What premium costs. Read from Stripe rather than restated here, so the figure the paywall
    shows cannot drift from the amount actually charged."""

    amount_cents: int | None = None
    currency: str = ""
    interval: str | None = None


class AuthConfigOut(BaseModel):
    """What the browser needs to talk to Supabase Auth, served rather than baked into the bundle.

    `anon_key` is Supabase's *publishable* key and is meant to be public: unlike the service key it
    does not bypass row-level security. Serving it from here, instead of a VITE_ variable, keeps
    SUPABASE_URL configured in exactly one file and means rotating the key does not need a rebuild.
    """

    enabled: bool
    url: str | None = None
    anon_key: str | None = None
    billing_enabled: bool = False
    # Tier allowances, so the UI can say "2 free recordings" without hardcoding a number that
    # disagrees with the server's.
    anon_unit_limit: int
    member_unit_limit: int
    # What premium costs, read from Stripe so the figure cannot drift from what is charged. None
    # when billing is off or Stripe could not be reached — the UI then omits the price rather than
    # guessing.
    price: PriceOut | None = None


class EntitlementOut(BaseModel):
    tier: str  # "anon" | "free" | "premium"
    # None means unlimited; the client must not treat it as zero.
    unit_limit: int | None = None
    remaining: int | None = None
    unlocked_unit_ids: list[int] = Field(default_factory=list)
    premium_until: datetime | None = None


class MeOut(BaseModel):
    signed_in: bool
    user_id: str | None = None
    email: str | None = None
    entitlement: EntitlementOut


class ClaimIn(BaseModel):
    """The device key whose anonymous work should be moved onto the signed-in account."""

    learner_key: str = Field(min_length=1, max_length=64)


class ClaimOut(BaseModel):
    claimed: bool
    vocab_items: int = 0
    attempts: int = 0
    unlocks: int = 0
    sessions: int = 0
    entitlement: EntitlementOut


class UnlockOut(BaseModel):
    unit_id: int
    unlocked: bool
    entitlement: EntitlementOut


class CheckoutOut(BaseModel):
    url: str


class SessionHeartbeatIn(BaseModel):
    session_id: int | None = None
    language: str = "fr"
    skill: str = "listening"
    unit_id: int | None = None
    # Seconds of study to add since the last heartbeat. Bounded so a client that sleeps and wakes
    # cannot post an hour of "study" in one call.
    seconds: int = Field(default=0, ge=0, le=600)
    attempts: int = Field(default=0, ge=0, le=1000)
    correct: int = Field(default=0, ge=0, le=1000)


class StudySessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    language: str
    skill: str
    unit_id: int | None
    started_at: datetime
    last_seen_at: datetime
    attempts: int
    correct: int
    seconds: int
