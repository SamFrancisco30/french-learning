"""Smart-translation endpoints: selection lookup, unit expression spans, vocab saving."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..languages import get_language
from ..lexicon.resolver import expression_spans_for_unit, resolve_selection
from ..models import ListeningUnit, VocabItem
from ..schemas import (
    LookupIn,
    LookupOut,
    UnitExpressionsOut,
    VocabItemOut,
    VocabSaveIn,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["lexicon"])


@router.post("/lookup", response_model=LookupOut)
def lookup(payload: LookupIn, db: Session = Depends(get_db)) -> LookupOut:
    """Translate a selected word or phrase, with any expression it belongs to."""
    if payload.char_end <= payload.char_start:
        raise HTTPException(400, "char_end must be greater than char_start")
    try:
        get_language(payload.language)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None

    try:
        result = resolve_selection(
            db,
            language=payload.language,
            text=payload.text,
            char_start=payload.char_start,
            char_end=payload.char_end,
            unit_id=payload.unit_id,
            allow_llm=bool(settings.openai_api_key),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None

    db.commit()  # persist any newly cached gloss
    return LookupOut(**result)


@router.get("/units/{unit_id}/expressions", response_model=UnitExpressionsOut)
def unit_expressions(unit_id: int, db: Session = Depends(get_db)) -> UnitExpressionsOut:
    """Expression spans for a unit, so the client can mark them before any click."""
    if not db.get(ListeningUnit, unit_id):
        raise HTTPException(404, f"unit {unit_id} not found")
    return UnitExpressionsOut(
        unit_id=unit_id, expressions=expression_spans_for_unit(db, unit_id)
    )


@router.post("/vocab", response_model=VocabItemOut)
def save_vocab(payload: VocabSaveIn, db: Session = Depends(get_db)) -> VocabItemOut:
    """Add a word or expression to the learner's review queue.

    Idempotent per (learner, language, headword): saving twice updates the gloss rather
    than creating a duplicate or resetting review progress.
    """
    lang = get_language(payload.language)
    headword = payload.headword.strip()

    existing = db.scalar(
        select(VocabItem).where(
            VocabItem.learner_key == payload.learner_key,
            VocabItem.language == lang.code,
            VocabItem.headword == headword,
        )
    )
    if existing:
        if payload.gloss_en:
            existing.gloss_en = payload.gloss_en
        if payload.example:
            existing.example = payload.example
        db.commit()
        db.refresh(existing)
        return VocabItemOut.model_validate(existing)

    item = VocabItem(
        learner_key=payload.learner_key,
        language=lang.code,
        headword=headword,
        gloss_en=payload.gloss_en,
        example=payload.example,
        zipf=round(lang.zipf(headword), 2),
        unit_id=payload.unit_id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return VocabItemOut.model_validate(item)


@router.get("/vocab", response_model=list[VocabItemOut])
def list_vocab(
    db: Session = Depends(get_db),
    learner_key: str = Query(default="anonymous"),
    language: str | None = None,
) -> list[VocabItemOut]:
    stmt = select(VocabItem).where(VocabItem.learner_key == learner_key)
    if language:
        stmt = stmt.where(VocabItem.language == language)
    rows = db.scalars(stmt.order_by(VocabItem.created_at.desc())).all()
    return [VocabItemOut.model_validate(r) for r in rows]
