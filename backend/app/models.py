"""ORM models.

Shape notes
-----------
Source        one downloaded media item (a YouTube video)
Transcript    one ASR pass over a Source (backend/model recorded for reproducibility)
Segment       an ASR segment; word-level timestamps live in `words_json`
Lesson        the pedagogical wrapper around a Source
ListeningUnit a 60-120s coherent chunk of a Lesson — what a learner actually drills
Exercise      one question attached to a ListeningUnit
Attempt       one learner submission against an Exercise
VocabItem     a word harvested for review, with SRS scheduling fields
UserProfile   application-side account state: tier and Stripe billing ids
UnitUnlock    one ListeningUnit a learner has spent a free-tier allowance slot on
StudySession  one sitting, so progress can report time and streaks rather than raw answers

Ownership is two columns wherever a row belongs to a learner: `user_id` for a signed-in Supabase
account, or `learner_key` with a NULL `user_id` for an anonymous device. `identity.owner_clause`
is the only place that chooses between them.

`skill` on Lesson/Exercise is what lets speaking / writing / reading / dictation
reuse this whole table set instead of forking it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, deferred, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# JSONB where the database supports it, JSON elsewhere, so the same models run against
# both Supabase Postgres and a local SQLite file.
JSON_TYPE = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON_TYPE, list[Any]: JSON_TYPE}


# --- vocabulary of string enums (kept as plain strings for SQLite friendliness) ---

SKILL_LISTENING = "listening"
SKILL_DICTATION = "dictation"
SKILLS = (SKILL_LISTENING, "speaking", "writing", "reading", SKILL_DICTATION)

EX_CLOZE = "cloze"
EX_MCQ = "mcq"
EX_TRUE_FALSE = "true_false"
EX_VOCAB_MATCH = "vocab_match"
EX_ORDERING = "ordering"
# Dictation reuses the Exercise/Attempt tables rather than growing its own: an item has a prompt,
# a withheld answer, an audio window and a CEFR level, which is exactly what an Exercise is, and
# reusing it means attempts, scoring history and progress work with no new plumbing.
EX_DICTATION_SENTENCE = "dictation_sentence"
EX_DICTATION_PASSAGE = "dictation_passage"
DICTATION_KINDS = (EX_DICTATION_SENTENCE, EX_DICTATION_PASSAGE)
EXERCISE_KINDS = (
    EX_CLOZE,
    EX_MCQ,
    EX_TRUE_FALSE,
    EX_VOCAB_MATCH,
    EX_ORDERING,
    *DICTATION_KINDS,
)

CEFR_LEVELS = ("A1", "A2", "B1", "B2", "C1", "C2")

# Expression categories. `idiom` is non-compositional ("coup de foudre"); `collocation`
# is compositional but conventionalized ("prendre une décision"); `fixed_phrase` covers
# discourse formulae ("en revanche"); `compound` covers lexicalized noun compounds
# ("feu d'artifice") — which matters because a learner selecting "feu" there needs the
# compound, not the verb idiom.
EXPR_IDIOM = "idiom"
EXPR_COLLOCATION = "collocation"
EXPR_PHRASAL = "phrasal_verb"
EXPR_FIXED = "fixed_phrase"
EXPR_COMPOUND = "compound"
EXPR_PROPER = "proper_noun"
EXPRESSION_KINDS = (
    EXPR_IDIOM,
    EXPR_COLLOCATION,
    EXPR_PHRASAL,
    EXPR_FIXED,
    EXPR_COMPOUND,
    EXPR_PROPER,
)


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("provider", "provider_id", name="uq_source_provider"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    language: Mapped[str] = mapped_column(String(8), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="youtube")
    provider_id: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    channel: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploader_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    topic: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Recorded so the library can be filtered to redistributable material.
    license_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    upload_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Object-store key (e.g. "sources/VIDEOID/original.m4a"), not a filesystem path —
    # identical under local and Supabase storage.
    audio_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Bytes, so storage growth can be reported without touching the store.
    audio_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    transcripts: Mapped[list["Transcript"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    lessons: Mapped[list["Lesson"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    language: Mapped[str] = mapped_column(String(8))
    asr_backend: Mapped[str] = mapped_column(String(32))
    asr_model: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    raw_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    source: Mapped[Source] = relationship(back_populates="transcripts")
    segments: Mapped[list["Segment"]] = relationship(
        back_populates="transcript",
        cascade="all, delete-orphan",
        order_by="Segment.idx",
    )


class Segment(Base):
    __tablename__ = "segments"
    __table_args__ = (Index("ix_segment_transcript_idx", "transcript_id", "idx"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    transcript_id: Mapped[int] = mapped_column(ForeignKey("transcripts.id", ondelete="CASCADE"))
    idx: Mapped[int] = mapped_column(Integer)
    start_s: Mapped[float] = mapped_column(Float)
    end_s: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)
    # [{"word": "bonjour", "start": 1.2, "end": 1.6, "probability": 0.98}, ...]
    words_json: Mapped[list[Any]] = deferred(mapped_column(JSON_TYPE, default=list))
    avg_logprob: Mapped[float | None] = mapped_column(Float, nullable=True)
    no_speech_prob: Mapped[float | None] = mapped_column(Float, nullable=True)

    transcript: Mapped[Transcript] = relationship(back_populates="segments")


class Lesson(Base):
    __tablename__ = "lessons"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    transcript_id: Mapped[int | None] = mapped_column(
        ForeignKey("transcripts.id", ondelete="SET NULL"), nullable=True
    )
    language: Mapped[str] = mapped_column(String(8), index=True)
    skill: Mapped[str] = mapped_column(String(16), default=SKILL_LISTENING, index=True)
    title: Mapped[str] = mapped_column(Text)
    topic: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cefr: Mapped[str | None] = mapped_column(String(4), nullable=True)
    difficulty_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    source: Mapped[Source] = relationship(back_populates="lessons")
    units: Mapped[list["ListeningUnit"]] = relationship(
        back_populates="lesson", cascade="all, delete-orphan", order_by="ListeningUnit.idx"
    )


class ListeningUnit(Base):
    __tablename__ = "listening_units"
    __table_args__ = (Index("ix_unit_lesson_idx", "lesson_id", "idx"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    lesson_id: Mapped[int] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"))
    idx: Mapped[int] = mapped_column(Integer)
    start_s: Mapped[float] = mapped_column(Float)
    end_s: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)
    words_json: Mapped[list[Any]] = deferred(mapped_column(JSON_TYPE, default=list))
    clip_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    wpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    cefr: Mapped[str | None] = mapped_column(String(4), nullable=True)
    difficulty_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    difficulty_detail: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    # Short gist so the UI can preview a unit without spoiling the answers.
    gist: Mapped[str | None] = mapped_column(Text, nullable=True)

    lesson: Mapped[Lesson] = relationship(back_populates="units")
    exercises: Mapped[list["Exercise"]] = relationship(
        back_populates="unit", cascade="all, delete-orphan", order_by="Exercise.order_idx"
    )

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)


class Exercise(Base):
    __tablename__ = "exercises"
    __table_args__ = (Index("ix_exercise_unit_order", "unit_id", "order_idx"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("listening_units.id", ondelete="CASCADE"))
    skill: Mapped[str] = mapped_column(String(16), default=SKILL_LISTENING, index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    order_idx: Mapped[int] = mapped_column(Integer, default=0)
    prompt: Mapped[str] = mapped_column(Text)
    # Everything the client needs to render, minus the answer.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    # Withheld from the client until an attempt is submitted.
    answer: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Replay window for "listen to this bit again".
    audio_start_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    audio_end_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    cefr: Mapped[str | None] = mapped_column(String(4), nullable=True)
    generator: Mapped[str] = mapped_column(String(32), default="llm")
    # auto (generated, unreviewed) | approved | rejected
    review_state: Mapped[str] = mapped_column(String(16), default="auto", index=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    unit: Mapped[ListeningUnit] = relationship(back_populates="exercises")
    attempts: Mapped[list["Attempt"]] = relationship(
        back_populates="exercise", cascade="all, delete-orphan"
    )


class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = (Index("ix_attempt_learner_created", "learner_key", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    exercise_id: Mapped[int] = mapped_column(ForeignKey("exercises.id", ondelete="CASCADE"))
    # Anonymous learner identity for now; swap for a real user FK when auth lands.
    learner_key: Mapped[str] = mapped_column(String(64), default="anonymous", index=True)
    # Set once Supabase Auth is wired up; learner_key remains the anonymous fallback.
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    response: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    feedback: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    replays: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    exercise: Mapped[Exercise] = relationship(back_populates="attempts")


class VocabItem(Base):
    __tablename__ = "vocab_items"
    __table_args__ = (
        Index(
            "uq_vocab_anon_word",
            "learner_key",
            "language",
            "normalized_headword",
            unique=True,
            postgresql_where=text("user_id IS NULL"),
            sqlite_where=text("user_id IS NULL"),
        ),
        Index(
            "uq_vocab_user_word",
            "user_id",
            "language",
            "normalized_headword",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
            sqlite_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "ix_vocab_anon_recent",
            "learner_key",
            "created_at",
            "id",
            postgresql_where=text("user_id IS NULL"),
            sqlite_where=text("user_id IS NULL"),
        ),
        Index(
            "ix_vocab_user_recent",
            "user_id",
            "created_at",
            "id",
            postgresql_where=text("user_id IS NOT NULL"),
            sqlite_where=text("user_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    learner_key: Mapped[str] = mapped_column(String(64), default="anonymous", index=True)
    # Set once Supabase Auth is wired up; learner_key remains the anonymous fallback.
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    language: Mapped[str] = mapped_column(String(8), index=True)
    headword: Mapped[str] = mapped_column(String(128))
    normalized_headword: Mapped[str] = mapped_column(String(128))
    gloss_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_gloss: Mapped[str] = mapped_column(Text, default="")
    example: Mapped[str | None] = mapped_column(Text, nullable=True)
    zipf: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit_id: Mapped[int | None] = mapped_column(
        ForeignKey("listening_units.id", ondelete="SET NULL"), nullable=True
    )
    # Minimal SM-2-ish scheduling.
    reps: Mapped[int] = mapped_column(Integer, default=0)
    lapses: Mapped[int] = mapped_column(Integer, default=0)
    ease: Mapped[float] = mapped_column(Float, default=2.5)
    interval_days: Mapped[float] = mapped_column(Float, default=0.0)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Expression(Base):
    """A multiword expression found in a specific passage.

    `component_spans` is the load-bearing field. French MWEs are frequently
    discontinuous — measured against the PARSEME-FR gold corpus, ~41% of French verbal
    MWEs have at least one intervening token ("y a mis le feu", "a mis le feu"). Storing
    only an envelope span would make a selection on any interior word resolve wrongly,
    and storing only the canonical surface would fail to match the inflected text at all.
    So we store each component's own span, and a selection touching ANY component
    resolves to the whole expression.
    """

    __tablename__ = "expressions"
    __table_args__ = (
        Index("ix_expr_unit_span", "unit_id", "char_start", "char_end"),
        Index("ix_expr_lemma_key", "language", "lemma_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Null for expressions harvested from arbitrary (non-unit) text.
    unit_id: Mapped[int | None] = mapped_column(
        ForeignKey("listening_units.id", ondelete="CASCADE"), nullable=True
    )
    language: Mapped[str] = mapped_column(String(8), index=True)

    surface: Mapped[str] = mapped_column(Text)  # as written: "a mis le feu"
    canonical: Mapped[str] = mapped_column(Text)  # dictionary form: "mettre le feu"
    # Sorted content lemmas, pipe-joined ("feu|mettre") — the cross-passage lookup key.
    lemma_key: Mapped[str] = mapped_column(String(255), default="")

    kind: Mapped[str] = mapped_column(String(24), default="idiom")
    gloss_en: Mapped[str] = mapped_column(Text)  # idiomatic meaning
    literal_en: Mapped[str | None] = mapped_column(Text, nullable=True)  # word-by-word
    note: Mapped[str | None] = mapped_column(Text, nullable=True)  # register / usage

    char_start: Mapped[int] = mapped_column(Integer)  # envelope, for ordering
    char_end: Mapped[int] = mapped_column(Integer)
    component_spans: Mapped[list[Any]] = mapped_column(JSON_TYPE, default=list)  # [[s,e],...]
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SentenceAnalysis(Base):
    """Cached grammar analysis of one selected sentence.

    Not precomputed at ingest, unlike expressions. Expressions need to exist before any
    click so known spans can be underlined, and word glosses repeat constantly so caching
    pays immediately. Sentence analysis is neither: the learner selects a specific sentence
    deliberately, most sentences are never selected, and precomputing every sentence of
    every unit would pay for analysis nobody reads. So it is generated on demand and cached
    by sentence, which makes a re-selection instant.
    """

    __tablename__ = "sentence_analyses"
    __table_args__ = (UniqueConstraint("language", "text_key", name="uq_sentence_lookup"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    language: Mapped[str] = mapped_column(String(8), index=True)
    text_key: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)

    translation_en: Mapped[str] = mapped_column(Text)
    register: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # [{key, schema_form, name_en, meaning_en, why_opaque, literal_trap,
    #   in_this_sentence, char_start, char_end, marker_spans, cefr, source}]
    structures: Mapped[list[Any]] = mapped_column(JSON_TYPE, default=list)
    # [{construction_key, schema_form, prompt_en, reference_fr, alternatives, hint_en}]
    practices: Mapped[list[Any]] = mapped_column(JSON_TYPE, default=list)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    hits: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GlossCache(Base):
    """Memoized single-word / free-phrase translations.

    Keyed by (language, surface, context) because sense depends on context: `feu` is
    fire, a traffic light, or gunfire depending on the sentence. `context_key` is a hash
    of the containing sentence — empty string for a deliberately context-free lookup.
    """

    __tablename__ = "gloss_cache"
    __table_args__ = (
        UniqueConstraint("language", "surface_key", "context_key", name="uq_gloss_lookup"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    language: Mapped[str] = mapped_column(String(8), index=True)
    surface_key: Mapped[str] = mapped_column(String(255))
    context_key: Mapped[str] = mapped_column(String(64), default="")

    surface: Mapped[str] = mapped_column(Text)
    lemma: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pos: Mapped[str | None] = mapped_column(String(32), nullable=True)
    gloss_en: Mapped[str] = mapped_column(Text)
    # Other senses this word can carry, so the learner sees the word isn't one-to-one.
    senses: Mapped[list[Any]] = mapped_column(JSON_TYPE, default=list)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    zipf: Mapped[float | None] = mapped_column(Float, nullable=True)
    hits: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --- accounts, sessions and entitlements -------------------------------------------------
#
# `user_id` here is a Supabase Auth user id (`sub`), stored as a plain string with no foreign
# key. Deliberately no FK: Supabase keeps its users in the `auth.users` table of the same
# Postgres database, but referencing it would tie these tables to a schema this application does
# not own and does not migrate, and would make the SQLite test database unbuildable — there is no
# `auth.users` there. The existing `attempts.user_id` and `vocab_items.user_id` already use this
# convention, so the new tables follow it rather than inventing a second one.


class UserProfile(Base):
    """Application-side facts about an account, alongside Supabase's own `auth.users` row.

    A separate table rather than Supabase user metadata. Tier drives access to paid content, so
    it has to be something only the server can write — user metadata is editable by the client
    holding that user's token, which would let a learner promote themselves to premium. It also
    keeps billing state next to the data the rest of the API already queries, instead of behind a
    call into the auth service on every request.

    Rows are created lazily on first authenticated request rather than by a signup hook, so an
    account made directly in the Supabase dashboard works exactly like one made in the app.
    """

    __tablename__ = "user_profiles"

    # The Supabase user id is the primary key: there is exactly one profile per account, and
    # making it the PK means the uniqueness is structural rather than a constraint to remember.
    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    # "free" | "premium". Anonymous visitors have no row at all and are treated as "anon".
    tier: Mapped[str] = mapped_column(String(16), default="free")
    stripe_customer_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # When the paid period ends. Kept so a cancelled-but-not-yet-expired subscription keeps
    # working to the end of the period the learner paid for, which is what Stripe bills for.
    premium_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class UnitUnlock(Base):
    """One listening unit a learner has spent an allowance slot on.

    Recording *which* units were opened, rather than counting how many, is what makes the free
    tier a choice instead of a prefix: the learner picks any two recordings in the library. It
    also makes the allowance stable — the second unit you opened stays open, where a "first N by
    id" rule would silently swap which units were free as the library grew.

    Premium learners accumulate no rows here; they are not metered, so writing an unlock per unit
    would be bookkeeping nobody reads.
    """

    __tablename__ = "unit_unlocks"
    __table_args__ = (
        # Mirrors the anon/user split used by vocab_items: one uniqueness rule per identity kind,
        # each scoped by a partial index so the unused column's NULLs cannot collide. A single
        # three-column unique index would not do it — in SQL, NULLs are never equal, so
        # (learner_key, NULL, unit) would admit unlimited duplicates.
        Index(
            "uq_unlock_anon_unit",
            "learner_key",
            "unit_id",
            unique=True,
            postgresql_where=text("user_id IS NULL"),
            sqlite_where=text("user_id IS NULL"),
        ),
        Index(
            "uq_unlock_user_unit",
            "user_id",
            "unit_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
            sqlite_where=text("user_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    learner_key: Mapped[str] = mapped_column(String(64), default="anonymous", index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    unit_id: Mapped[int] = mapped_column(
        ForeignKey("listening_units.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StudySession(Base):
    """One sitting: when a learner started working on a skill, and what they got through.

    Attempts already record every individual answer, so this is not where correctness lives. What
    attempts cannot answer is "how long did you study, and how often do you come back" — a row
    per answer has no notion of a sitting, and the gap between two answers is indistinguishable
    from the gap between two weeks. Sessions carry that, and they are what a progress view reads
    instead of aggregating every attempt ever made.
    """

    __tablename__ = "study_sessions"
    __table_args__ = (
        Index("ix_session_owner_started", "learner_key", "started_at"),
        Index("ix_session_user_started", "user_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    learner_key: Mapped[str] = mapped_column(String(64), default="anonymous", index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    language: Mapped[str] = mapped_column(String(8), default="fr", index=True)
    skill: Mapped[str] = mapped_column(String(16), default="listening", index=True)
    unit_id: Mapped[int | None] = mapped_column(
        ForeignKey("listening_units.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Advanced by a heartbeat from the client, so a session that ends by closing the tab still
    # records a plausible length instead of being open forever or lost entirely.
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    correct: Mapped[int] = mapped_column(Integer, default=0)
    seconds: Mapped[int] = mapped_column(Integer, default=0)
