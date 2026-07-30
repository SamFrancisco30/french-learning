"""Dictation: browse the curated items, and get the next one at the learner's level.

The adaptive part is deliberately derived from Attempt rows rather than stored in a new table.
Two reasons. A learner's level is a *summary* of their attempts, so persisting it creates a second
source of truth that can disagree with the history — and a migration against the live Postgres has
a real cost that a GROUP BY does not. If this ever needs to be a stored, hand-tuned value it can
become one, and the derivation here is the seed for it.

The ladder is intentionally simple enough to explain to a learner in one sentence: hold above 90%
and you move up, drop below 60% and you move down, and it takes a few attempts either way so one
bad clip does not demote you.
"""

from __future__ import annotations

import logging
import random

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..models import (
    CEFR_LEVELS,
    DICTATION_KINDS,
    EX_DICTATION_PASSAGE,
    EX_DICTATION_SENTENCE,
    Attempt,
    Exercise,
    Lesson,
    ListeningUnit,
)
from ..schemas import DictationItemOut, DictationLevelOut, DictationNextOut
from ..storage import get_store

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dictation", tags=["dictation"])

MODES = {"sentence": EX_DICTATION_SENTENCE, "paragraph": EX_DICTATION_PASSAGE}

# How the ladder moves. Both windows are short enough to feel responsive and long enough that a
# single mistyped clip cannot move you.
WINDOW = 5           # attempts considered
PROMOTE_AT = 0.90    # mean score at or above this, over a full window, moves up
DEMOTE_AT = 0.60     # below this moves down
MIN_FOR_MOVE = 3     # never move on fewer than this many attempts

# Where a learner with no history starts. A2 rather than A1: starting too easy reads as the app
# wasting your time, and the ladder drops faster than it climbs.
START_LEVEL = "A2"


def _level_index(level: str) -> int:
    try:
        return CEFR_LEVELS.index(level)
    except ValueError:
        return CEFR_LEVELS.index(START_LEVEL)


def learner_level(db: Session, learner_key: str, mode: str) -> DictationLevelOut:
    """Derive the learner's current level for this mode from their dictation history."""
    kind = MODES[mode]
    rows = db.execute(
        select(Attempt.score, Exercise.cefr)
        .join(Exercise, Attempt.exercise_id == Exercise.id)
        .where(Attempt.learner_key == learner_key, Exercise.kind == kind)
        .order_by(Attempt.created_at.desc())
        .limit(WINDOW)
    ).all()

    if not rows:
        return DictationLevelOut(
            level=START_LEVEL, mode=mode, attempts=0, recent_mean=None,
            reason="no attempts yet — starting at A2",
        )

    scores = [float(s or 0.0) for s, _ in rows]
    mean = sum(scores) / len(scores)
    # The level you were last served at is the anchor; the window then nudges it.
    anchor = next((c for _, c in rows if c), START_LEVEL)
    idx = _level_index(anchor)

    reason = f"holding at {anchor}"
    if len(scores) >= MIN_FOR_MOVE and mean >= PROMOTE_AT and idx < len(CEFR_LEVELS) - 1:
        idx += 1
        reason = f"{mean:.0%} over your last {len(scores)} — moved up"
    elif len(scores) >= MIN_FOR_MOVE and mean < DEMOTE_AT and idx > 0:
        idx -= 1
        reason = f"{mean:.0%} over your last {len(scores)} — moved down"

    return DictationLevelOut(
        level=CEFR_LEVELS[idx], mode=mode, attempts=len(scores),
        recent_mean=round(mean, 3), reason=reason,
    )


def _item_out(ex: Exercise, *, with_audio: bool) -> DictationItemOut:
    payload = ex.payload or {}
    url = None
    if with_audio and ex.unit.clip_key:
        try:
            url = get_store().url_for(ex.unit.clip_key)
        except Exception:  # noqa: BLE001 - a signing failure must not break the response
            log.warning("could not sign %s", ex.unit.clip_key)
    return DictationItemOut(
        exercise_id=ex.id,
        mode="sentence" if ex.kind == EX_DICTATION_SENTENCE else "paragraph",
        prompt=ex.prompt,
        cefr=ex.cefr,
        difficulty_score=payload.get("difficulty_score"),
        word_count=payload.get("word_count"),
        sentence_count=payload.get("sentence_count"),
        # Audio window on the ORIGINAL-VIDEO timeline, matching the listening player.
        audio_start_s=ex.audio_start_s,
        audio_end_s=ex.audio_end_s,
        unit_id=ex.unit_id,
        unit_start_s=ex.unit.start_s,
        unit_end_s=ex.unit.end_s,
        clip_url=url,
        lesson_title=ex.unit.lesson.title,
        topic=ex.unit.lesson.topic,
    )


def _candidates(db: Session, kind: str, language: str):
    return (
        select(Exercise)
        .join(ListeningUnit, Exercise.unit_id == ListeningUnit.id)
        .join(Lesson, ListeningUnit.lesson_id == Lesson.id)
        .where(Exercise.kind == kind, Lesson.language == language)
        .options(selectinload(Exercise.unit).selectinload(ListeningUnit.lesson))
    )


@router.get("/levels", response_model=list[DictationLevelOut])
def get_levels(
    learner_key: str = Query("anonymous"),
    db: Session = Depends(get_db),
) -> list[DictationLevelOut]:
    """The learner's derived level in each mode, with the reason it is what it is."""
    return [learner_level(db, learner_key, m) for m in MODES]


@router.get("/inventory")
def inventory(language: str = "fr", db: Session = Depends(get_db)) -> dict:
    """How many items exist per mode and level. Drives the level picker and shows the gaps."""
    rows = db.execute(
        select(Exercise.kind, Exercise.cefr, func.count(Exercise.id))
        .join(ListeningUnit, Exercise.unit_id == ListeningUnit.id)
        .join(Lesson, ListeningUnit.lesson_id == Lesson.id)
        .where(Exercise.kind.in_(DICTATION_KINDS), Lesson.language == language)
        .group_by(Exercise.kind, Exercise.cefr)
    ).all()

    out: dict[str, dict[str, int]] = {m: {} for m in MODES}
    by_kind = {v: k for k, v in MODES.items()}
    for kind, cefr, n in rows:
        mode = by_kind.get(kind)
        if mode:
            out[mode][cefr or "unrated"] = n
    return {
        "language": language,
        "by_mode": out,
        "totals": {m: sum(v.values()) for m, v in out.items()},
        "levels": list(CEFR_LEVELS),
    }


@router.get("/next", response_model=DictationNextOut)
def next_item(
    mode: str = Query("sentence"),
    learner_key: str = Query("anonymous"),
    language: str = "fr",
    level: str | None = Query(None, description="Override the derived level."),
    db: Session = Depends(get_db),
) -> DictationNextOut:
    """One item at the learner's level, preferring something they have not done.

    Falls outward to neighbouring levels when the requested one is empty rather than returning
    nothing: a learner at C2 with no C2 sentences should still get their hardest available item,
    and be told that is what happened.
    """
    if mode not in MODES:
        raise HTTPException(400, f"mode must be one of {', '.join(MODES)}")
    kind = MODES[mode]

    derived = learner_level(db, learner_key, mode)
    target = level or derived.level
    if target not in CEFR_LEVELS:
        raise HTTPException(400, f"level must be one of {', '.join(CEFR_LEVELS)}")

    done = set(
        db.scalars(
            select(Attempt.exercise_id)
            .join(Exercise, Attempt.exercise_id == Exercise.id)
            .where(Attempt.learner_key == learner_key, Exercise.kind == kind)
        ).all()
    )

    # Search the target level first, then outwards by CEFR distance.
    order = sorted(CEFR_LEVELS, key=lambda c: abs(_level_index(c) - _level_index(target)))
    for candidate_level in order:
        pool = db.scalars(_candidates(db, kind, language).where(Exercise.cefr == candidate_level)).all()
        if not pool:
            continue
        fresh = [e for e in pool if e.id not in done]
        # Everything at this level has been attempted: repeat rather than refuse, since repetition
        # is how dictation is practised anyway.
        chosen = random.choice(fresh or pool)
        return DictationNextOut(
            item=_item_out(chosen, with_audio=True),
            level=derived,
            served_level=candidate_level,
            off_level=candidate_level != target,
            repeat=not fresh,
            remaining_at_level=len(fresh),
        )

    raise HTTPException(
        404,
        f"no {mode} dictation items exist for language {language!r}. "
        "Run: python scripts/curate_dictation.py sync",
    )


@router.get("/items", response_model=list[DictationItemOut])
def list_items(
    mode: str = Query("sentence"),
    language: str = "fr",
    level: str | None = None,
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
) -> list[DictationItemOut]:
    """Browse items. Audio URLs are omitted — signing 50 of them would be 50 network calls."""
    if mode not in MODES:
        raise HTTPException(400, f"mode must be one of {', '.join(MODES)}")
    stmt = _candidates(db, MODES[mode], language)
    if level:
        stmt = stmt.where(Exercise.cefr == level)
    rows = db.scalars(stmt.order_by(Exercise.unit_id, Exercise.order_idx).limit(limit)).all()
    return [_item_out(e, with_audio=False) for e in rows]
