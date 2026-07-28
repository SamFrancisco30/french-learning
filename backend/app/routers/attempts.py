"""Attempt submission, grading and progress."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..languages import get_language
from ..models import Attempt, Exercise
from ..schemas import AttemptIn, AttemptOut, ProgressOut
from ..skills.listening.grading import grade

router = APIRouter(prefix="/api", tags=["attempts"])


@router.post("/attempts", response_model=AttemptOut)
def submit_attempt(payload: AttemptIn, db: Session = Depends(get_db)) -> AttemptOut:
    ex = db.get(Exercise, payload.exercise_id)
    if not ex:
        raise HTTPException(404, f"exercise {payload.exercise_id} not found")

    lang = get_language(ex.unit.lesson.language)
    result = grade(ex.kind, payload.response, ex.answer or {}, lang)

    attempt = Attempt(
        exercise_id=ex.id,
        learner_key=payload.learner_key or "anonymous",
        response=payload.response,
        is_correct=result.is_correct,
        score=result.score,
        feedback=result.feedback,
        replays=payload.replays,
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)

    return AttemptOut(
        id=attempt.id,
        exercise_id=ex.id,
        is_correct=result.is_correct,
        score=result.score,
        feedback=result.feedback,
        explanation=ex.explanation,
        answer=ex.answer or {},
        audio_start_s=ex.audio_start_s,
        audio_end_s=ex.audio_end_s,
    )


@router.get("/progress", response_model=ProgressOut)
def get_progress(
    db: Session = Depends(get_db),
    learner_key: str = Query(default="anonymous"),
) -> ProgressOut:
    rows = db.execute(
        select(
            Exercise.kind,
            func.count(Attempt.id),
            func.sum(func.cast(Attempt.is_correct, __import__("sqlalchemy").Integer)),
            func.avg(Attempt.score),
        )
        .join(Exercise, Exercise.id == Attempt.exercise_id)
        .where(Attempt.learner_key == learner_key)
        .group_by(Exercise.kind)
    ).all()

    by_kind: dict[str, dict[str, float]] = {}
    total = correct = 0
    weighted = 0.0
    for kind, n, n_correct, avg_score in rows:
        n = int(n or 0)
        n_correct = int(n_correct or 0)
        avg_score = float(avg_score or 0.0)
        by_kind[kind] = {
            "attempts": n,
            "correct": n_correct,
            "accuracy": round(n_correct / n, 4) if n else 0.0,
            "mean_score": round(avg_score, 4),
        }
        total += n
        correct += n_correct
        weighted += avg_score * n

    units_touched = (
        db.scalar(
            select(func.count(func.distinct(Exercise.unit_id)))
            .join(Attempt, Attempt.exercise_id == Exercise.id)
            .where(Attempt.learner_key == learner_key)
        )
        or 0
    )

    return ProgressOut(
        learner_key=learner_key,
        attempts=total,
        correct=correct,
        accuracy=round(correct / total, 4) if total else 0.0,
        mean_score=round(weighted / total, 4) if total else 0.0,
        by_kind=by_kind,
        units_touched=int(units_touched),
    )
