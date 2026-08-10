"""ORM models for drill mode.

Shape notes
-----------
DrillCollection  one bank the vendor published — a numbered mock test, or a
                 difficulty bucket ("READING 21分题库")
DrillQuestion    one item; `skill` decides which columns carry meaning
DrillOption      one of the four choices, for the multiple-choice skills
DrillAttempt     one learner submission against a DrillQuestion

`document` is the same field for every skill, because it plays the same role in
each: the thing the learner is given. For reading that is the passage, for
listening the transcript of what is played, for speaking and writing the task
prompt. Splitting it per skill would triple the column count and force every
query to know which one to read.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..models import JSON_TYPE, Base, utcnow

# Drill content is exam material, so the exam is part of every key. Only TCF is
# loaded today; PTE sits in the same scrape and would land beside it.
EXAM_TCF = "TCF"

# Skills reuse study mode's vocabulary so a unified progress view does not have
# to translate between two sets of names.
DRILL_SKILLS = ("reading", "listening", "speaking", "writing")

# `mcq` has options and one right answer. `production` has neither — the learner
# writes or speaks, and what ships with it is a model answer. `guide` is the
# vendor explaining how to answer a task; it is content to read, never to drill,
# and it is marked rather than dropped so a study section can still show it.
KIND_MCQ = "mcq"
KIND_PRODUCTION = "production"
KIND_GUIDE = "guide"
DRILL_KINDS = (KIND_MCQ, KIND_PRODUCTION, KIND_GUIDE)


class DrillCollection(Base):
    __tablename__ = "drill_collections"
    __table_args__ = (
        UniqueConstraint("exam", "name", name="uq_drill_collection_name"),
        Index("ix_drill_collection_skill", "exam", "skill"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    exam: Mapped[str] = mapped_column(String(16), default=EXAM_TCF, index=True)
    skill: Mapped[str] = mapped_column(String(16), index=True)
    # The vendor's own label, kept verbatim: "TCF/READING 21分题库". It is how a
    # question is traced back to where it came from.
    name: Mapped[str] = mapped_column(Text)
    # Set only where every item in the bank shares a level; the numbered mock
    # tests span A1 to C2 and leave this null.
    level: Mapped[str | None] = mapped_column(String(4), nullable=True)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    questions: Mapped[list["DrillQuestion"]] = relationship(
        back_populates="collection", cascade="all, delete-orphan"
    )


class DrillQuestion(Base):
    __tablename__ = "drill_questions"
    __table_args__ = (
        # The vendor's numeric id, which the import is keyed on so a re-run
        # updates rather than duplicates.
        UniqueConstraint("exam", "external_id", name="uq_drill_question_external"),
        # The sampling query: give me N unseen canonical items at this level.
        Index("ix_drill_sample", "skill", "level", "canonical"),
        Index("ix_drill_collection_seq", "collection_id", "seq"),
        CheckConstraint(
            "kind <> 'mcq' OR answer IS NOT NULL",
            name="ck_drill_mcq_has_answer",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    collection_id: Mapped[int] = mapped_column(
        ForeignKey("drill_collections.id", ondelete="CASCADE")
    )
    exam: Mapped[str] = mapped_column(String(16), default=EXAM_TCF, index=True)
    external_id: Mapped[int] = mapped_column(Integer)
    skill: Mapped[str] = mapped_column(String(16), index=True)
    kind: Mapped[str] = mapped_column(String(16), default=KIND_MCQ, index=True)

    seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    level: Mapped[str | None] = mapped_column(String(4), nullable=True, index=True)
    # TCF weights each item by the level it tests (3/9/15/21/26/33); `level` is
    # derived from it, and the raw weight is kept because scoring uses it.
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String(16), nullable=True)
    time_limit_s: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- content ---
    document: Mapped[str] = mapped_column(Text, default="")
    # The proofreading layer never overwrites what was transcribed; the fixed
    # text sits beside it and `corrections` lists every edit that produced it.
    document_corrected: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_zh: Mapped[str | None] = mapped_column(Text, nullable=True)
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Production skills only: what a good answer looks like, as the vendor wrote
    # it, plus the French-only view for drilling.
    model_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_answer_fr: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Multiple choice only. Null on production items, which the CHECK allows.
    answer: Mapped[str | None] = mapped_column(String(4), nullable=True)

    # --- media, as object-store keys like the rest of the app ---
    image_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- dedupe ---
    # The bank files the same item under several banks; 666 reading items and
    # 271 listening items are repeats. Duplicates are kept and pointed at the
    # copy that was retained, so a set can be reconstructed exactly while a
    # practice queue draws only from `canonical`.
    canonical: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    duplicate_of: Mapped[int | None] = mapped_column(
        ForeignKey("drill_questions.id", ondelete="SET NULL"), nullable=True
    )
    copies: Mapped[int] = mapped_column(Integer, default=1)

    # --- bookkeeping ---
    # Where each field came from: scraped, OCR'd, read by hand, inferred. An
    # answer that a model worked out is not the same fact as one the bank
    # shipped, and the difference has to survive into the database.
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    warnings: Mapped[list[Any]] = mapped_column(JSON_TYPE, default=list)
    corrections: Mapped[list[Any]] = mapped_column(JSON_TYPE, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    collection: Mapped[DrillCollection] = relationship(back_populates="questions")
    options: Mapped[list["DrillOption"]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
        order_by="DrillOption.label",
    )
    attempts: Mapped[list["DrillAttempt"]] = relationship(
        back_populates="question", cascade="all, delete-orphan"
    )


class DrillOption(Base):
    __tablename__ = "drill_options"
    __table_args__ = (
        UniqueConstraint("question_id", "label", name="uq_drill_option_label"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("drill_questions.id", ondelete="CASCADE")
    )
    label: Mapped[str] = mapped_column(String(4))
    text: Mapped[str] = mapped_column(Text, default="")
    text_corrected: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False)

    question: Mapped[DrillQuestion] = relationship(back_populates="options")


class DrillAttempt(Base):
    __tablename__ = "drill_attempts"
    __table_args__ = (
        Index("ix_drill_attempt_learner", "learner_key", "created_at"),
        Index("ix_drill_attempt_question", "question_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("drill_questions.id", ondelete="CASCADE")
    )
    # Same identity columns as study mode's `attempts`, so the two histories
    # line up in a UNION without a translation step.
    learner_key: Mapped[str] = mapped_column(String(64), default="anonymous", index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # The letter chosen, or null when the learner gave up / ran out of time.
    selected: Mapped[str | None] = mapped_column(String(4), nullable=True)
    # Production items have no key, so correctness is unknown rather than false.
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Free text or a recording key for production attempts.
    response: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    question: Mapped[DrillQuestion] = relationship(back_populates="attempts")
