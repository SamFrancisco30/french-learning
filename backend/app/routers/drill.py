"""Drill mode: browse the imported exam banks, draw the next item, submit an answer.

Two things shape this router.

The answer must not reach the client before the attempt. An exam item gives it
away three ways — the `answer` letter, the option flagged `is_correct`, and an
explanation that routinely states it outright ("正确答案：A") — so the question
schema has no field any of them could travel in, and the explanation is returned
only in the attempt response. Withholding by omission from the schema rather than
by stripping at each endpoint means a new endpoint cannot forget.

Sampling prefers items the learner has not seen. It is a LEFT JOIN against their
own attempts rather than a stored queue: a queue is a second source of truth that
drifts from the history the moment anything else writes an attempt, and the join
costs one index scan. Items already answered are not excluded outright — once the
bank is exhausted at a level, repeating is better than returning nothing.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..drill.models import (
    DRILL_KINDS,
    DRILL_SKILLS,
    KIND_GUIDE,
    KIND_MCQ,
    DrillAttempt,
    DrillCollection,
    DrillOption,
    DrillQuestion,
)
from ..drill.schemas import (
    DrillAttemptIn,
    DrillCollectionOut,
    DrillOptionOut,
    DrillProgressOut,
    DrillQuestionOut,
    DrillResultOut,
)
from ..identity import LearnerIdentity, optional_learner_identity
from ..models import CEFR_LEVELS
from ..storage import get_drill_store

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/drill", tags=["drill"])

# optional_learner_identity yields None, not a 401, for a caller with no usable
# identity — a browser that blocks localStorage cannot produce a device key, and
# the drill bank is readable without one. Such a caller has no attempt history,
# and anything they submit is recorded under the anonymous key.
Identity = Annotated[LearnerIdentity | None, Depends(optional_learner_identity)]
Db = Annotated[Session, Depends(get_db)]

ANON_KEY = "anonymous"


def _signed(key: str | None) -> str | None:
    """A playable/renderable URL for a drill object, or None if it has no key."""
    if not key:
        return None
    try:
        return get_drill_store().url_for(key)
    except Exception as exc:  # noqa: BLE001 - a missing object must not 500 the item
        log.warning("could not sign drill key %r: %s", key, exc)
        return None


def _document(question: DrillQuestion) -> str:
    """What the learner reads.

    The corrected text when the proofreading layer produced one: the verbatim
    transcription is the record of what was on the image, not the best reading of
    it. Both are in the row; this endpoint serves the corrected one.
    """
    return question.document_corrected or question.document or ""


def _to_question_out(question: DrillQuestion) -> DrillQuestionOut:
    return DrillQuestionOut(
        id=question.id,
        skill=question.skill,
        kind=question.kind,
        collection=question.collection.name if question.collection else "",
        level=question.level,
        seq=question.seq,
        title=question.title,
        time_limit_s=question.time_limit_s,
        document=_document(question),
        question=question.question,
        options=[
            DrillOptionOut(label=o.label, text=o.text_corrected or o.text)
            for o in sorted(question.options, key=lambda o: o.label)
        ],
        image_url=_signed(question.image_key),
        audio_url=_signed(question.audio_key),
        # A listening item's document is the transcript of what is played, so
        # showing it up front answers the question for the learner.
        document_is_spoiler=question.skill == "listening",
    )


def _visible(stmt: Select, *, skill: str | None, level: str | None,
             collection_id: int | None, include_duplicates: bool,
             include_guides: bool) -> Select:
    """The filters every listing and sampling query shares."""
    if skill:
        stmt = stmt.where(DrillQuestion.skill == skill)
    if level:
        stmt = stmt.where(DrillQuestion.level == level)
    if collection_id:
        stmt = stmt.where(DrillQuestion.collection_id == collection_id)
    if not include_duplicates:
        stmt = stmt.where(DrillQuestion.canonical.is_(True))
    if not include_guides:
        # Guides are the vendor explaining how to answer a task — content to
        # read, never to drill.
        stmt = stmt.where(DrillQuestion.kind != KIND_GUIDE)
    return stmt


@router.get("/collections", response_model=list[DrillCollectionOut])
def list_collections(
    db: Db,
    skill: str | None = Query(None),
) -> list[DrillCollectionOut]:
    if skill and skill not in DRILL_SKILLS:
        raise HTTPException(400, f"skill must be one of {', '.join(DRILL_SKILLS)}")

    distinct = (
        select(DrillQuestion.collection_id, func.count().label("n"))
        .where(DrillQuestion.canonical.is_(True), DrillQuestion.kind != KIND_GUIDE)
        .group_by(DrillQuestion.collection_id)
        .subquery()
    )
    stmt = (
        select(DrillCollection, func.coalesce(distinct.c.n, 0))
        .outerjoin(distinct, distinct.c.collection_id == DrillCollection.id)
        .order_by(DrillCollection.skill, DrillCollection.name)
    )
    if skill:
        stmt = stmt.where(DrillCollection.skill == skill)

    return [
        DrillCollectionOut(
            id=c.id, skill=c.skill, name=c.name, level=c.level,
            item_count=c.item_count, distinct_count=n,
        )
        for c, n in db.execute(stmt).all()
    ]


@router.get("/next", response_model=DrillQuestionOut)
def next_question(
    db: Db,
    identity: Identity,
    skill: str = Query(..., description="reading | listening | speaking | writing"),
    level: str | None = Query(None, description="A1..C2"),
    collection_id: int | None = Query(None),
) -> DrillQuestionOut:
    if skill not in DRILL_SKILLS:
        raise HTTPException(400, f"skill must be one of {', '.join(DRILL_SKILLS)}")
    if level and level not in CEFR_LEVELS:
        raise HTTPException(400, f"level must be one of {', '.join(CEFR_LEVELS)}")

    stmt = _visible(
        select(DrillQuestion).options(
            selectinload(DrillQuestion.options),
            selectinload(DrillQuestion.collection),
        ),
        skill=skill, level=level, collection_id=collection_id,
        include_duplicates=False, include_guides=False,
    )
    if identity is not None:
        # This learner's attempts, so unseen items sort first without excluding
        # seen ones — the bank at a level is finite, and repeating beats 404.
        seen = (
            select(DrillAttempt.question_id)
            .where(_owned(identity))
            .distinct()
            .subquery()
        )
        stmt = stmt.outerjoin(seen, seen.c.question_id == DrillQuestion.id).order_by(
            seen.c.question_id.is_not(None), func.random()
        )
    else:
        stmt = stmt.order_by(func.random())

    question = db.scalars(stmt.limit(1)).first()
    if question is None:
        raise HTTPException(404, "no drill items match that filter")
    return _to_question_out(question)


@router.get("/questions/{question_id}", response_model=DrillQuestionOut)
def get_question(question_id: int, db: Db) -> DrillQuestionOut:
    question = db.scalars(
        select(DrillQuestion)
        .where(DrillQuestion.id == question_id)
        .options(selectinload(DrillQuestion.options),
                 selectinload(DrillQuestion.collection))
    ).first()
    if question is None:
        raise HTTPException(404, "no such drill question")
    return _to_question_out(question)


@router.post("/attempts", response_model=DrillResultOut)
def submit_attempt(
    body: DrillAttemptIn,
    db: Db,
    identity: Identity,
) -> DrillResultOut:
    question = db.scalars(
        select(DrillQuestion)
        .where(DrillQuestion.id == body.question_id)
        .options(selectinload(DrillQuestion.options))
    ).first()
    if question is None:
        raise HTTPException(404, "no such drill question")

    selected = (body.selected or "").strip().upper() or None
    if question.kind == KIND_MCQ:
        labels = {o.label.upper() for o in question.options}
        if selected is not None and selected not in labels:
            raise HTTPException(
                400, f"selected must be one of {', '.join(sorted(labels))} or null"
            )
        # A skipped item is answered incorrectly, not left unjudged: the learner
        # saw it and did not get it, which is what a progress figure should say.
        correct = selected is not None and selected == (question.answer or "").upper()
    else:
        # Production tasks have no key. Correctness is unknown, not false — a
        # written answer is graded elsewhere, or by the learner against the model.
        correct = None

    attempt = DrillAttempt(
        question_id=question.id,
        learner_key=identity.learner_key if identity else ANON_KEY,
        user_id=identity.user_id if identity else None,
        selected=selected,
        is_correct=correct,
        elapsed_ms=body.elapsed_ms,
        response=body.response or {},
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)

    provenance = question.provenance or {}
    answer_source = provenance.get("answer")
    return DrillResultOut(
        attempt_id=attempt.id,
        question_id=question.id,
        correct=correct,
        answer=question.answer,
        selected=selected,
        explanation=question.explanation,
        document_zh=question.document_zh,
        model_answer=question.model_answer_fr or question.model_answer,
        # Surfaced so a learner can weigh a key that a model worked out
        # differently from one the bank shipped.
        answer_source=answer_source if answer_source != "json" else None,
    )


@router.get("/progress", response_model=list[DrillProgressOut])
def progress(db: Db, identity: Identity) -> list[DrillProgressOut]:
    if identity is None:
        return []
    rows = db.execute(
        select(
            DrillQuestion.skill,
            DrillQuestion.level,
            func.count().label("attempted"),
            func.count(DrillAttempt.id).filter(DrillAttempt.is_correct.is_(True)),
            # Production attempts are recorded with is_correct NULL, because a
            # written or spoken answer has no key to check against. Counting them
            # in the denominator would report a learner who wrote a good essay as
            # 0% — so they are counted separately and left out of the ratio.
            func.count(DrillAttempt.id).filter(DrillAttempt.is_correct.is_not(None)),
        )
        .join(DrillQuestion, DrillQuestion.id == DrillAttempt.question_id)
        .where(_owned(identity))
        .group_by(DrillQuestion.skill, DrillQuestion.level)
        .order_by(DrillQuestion.skill, DrillQuestion.level)
    ).all()
    return [
        DrillProgressOut(
            skill=skill, level=level, attempted=attempted, correct=correct,
            graded=graded,
            accuracy=round(correct / graded, 4) if graded else None,
        )
        for skill, level, attempted, correct, graded in rows
    ]


def _owned(identity: LearnerIdentity):
    """Scope attempts to this caller.

    Mirrors identity.owner_clause, which is typed against the study-mode models;
    the rule is the same and the reason for the `user_id IS NULL` half is too — an
    anonymous query must not match rows an account has since claimed from the
    same device key.
    """
    if identity.user_id is not None:
        return DrillAttempt.user_id == identity.user_id
    return (DrillAttempt.user_id.is_(None)) & (
        DrillAttempt.learner_key == identity.learner_key
    )
