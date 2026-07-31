"""Smart-translation endpoints: selection lookup and unit expression spans."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..languages import get_language
from ..lexicon.normalize import normalize_vocab_v1
from ..lexicon.practice import grade_practice
from ..lexicon.resolver import expression_spans_for_unit, resolve_selection
from ..lexicon.sentence import LLMError, analyze_sentence
from ..models import ListeningUnit
from ..schemas import (
    LookupIn,
    LookupOut,
    PracticeCheckIn,
    PracticeCheckOut,
    PracticeOut,
    SentenceIn,
    SentenceOut,
    UnitExpressionsOut,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["lexicon"])


def _with_vocab_keys(result: dict[str, Any]) -> dict[str, Any]:
    """Return a decorated top-level payload copy with save-compatible vocabulary keys."""
    word = dict(result["word"])
    lemma = word.get("lemma")
    headword = lemma if isinstance(lemma, str) and lemma.strip() else result["selection"]
    word["normalized_headword"] = normalize_vocab_v1(headword)
    expressions = []
    for candidate in result["expressions"]:
        expression = dict(candidate)
        expression["normalized_headword"] = normalize_vocab_v1(expression["canonical"])
        expressions.append(expression)
    return {**result, "word": word, "expressions": expressions}


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

    response = LookupOut(**_with_vocab_keys(result))
    db.commit()  # persist any newly cached gloss
    return response


@router.post("/sentence", response_model=SentenceOut)
def analyse_sentence(payload: SentenceIn, db: Session = Depends(get_db)) -> SentenceOut:
    """Explain a sentence's grammar and generate practice items.

    Separate from /api/lookup because it needs a model call while lookup is mostly instant.
    The client fires this after a sentence-length selection and fills the popup in when it
    arrives, so naming the construction never waits on the explanation.
    """
    try:
        lang = get_language(payload.language)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None

    try:
        analysis, source = analyze_sentence(
            db,
            payload.text,
            lang,
            allow_llm=bool(settings.openai_api_key),
            refresh=payload.refresh,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except LLMError as exc:
        raise HTTPException(502, f"analysis failed: {exc}") from None

    db.commit()
    # Practices are stripped of their reference answers — those are revealed only by
    # /api/practice/check, after the learner has committed to an answer.
    return SentenceOut(
        **{k: v for k, v in analysis.items() if k != "practices"},
        practices=[
            PracticeOut(
                construction_key=p["construction_key"],
                schema_form=p.get("schema_form", ""),
                prompt_en=p["prompt_en"],
                hint_en=p.get("hint_en"),
                required_markers=p.get("required_markers", []),
            )
            for p in analysis.get("practices", [])
        ],
        source=source,
    )


@router.post("/practice/check", response_model=PracticeCheckOut)
def check_practice(payload: PracticeCheckIn, db: Session = Depends(get_db)) -> PracticeCheckOut:
    """Grade a free-form translation against the practice's target construction.

    The reference answer is looked up from the cached analysis rather than accepted from
    the client — otherwise a caller could submit its own reference and grade itself.
    """
    try:
        lang = get_language(payload.language)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None

    analysis, _ = analyze_sentence(
        db, payload.sentence, lang, allow_llm=bool(settings.openai_api_key)
    )
    practices = analysis.get("practices", [])
    if payload.practice_index >= len(practices):
        raise HTTPException(
            404, f"practice {payload.practice_index} not found for this sentence"
        )
    practice = practices[payload.practice_index]

    try:
        result = grade_practice(
            language=lang.code,
            construction_key=practice["construction_key"],
            prompt_en=practice["prompt_en"],
            reference_fr=practice["reference_fr"],
            alternatives=practice.get("alternatives", []),
            answer=payload.answer,
            lang=lang,
            allow_llm=bool(settings.openai_api_key),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None

    db.commit()
    return PracticeCheckOut(**result)


@router.get("/units/{unit_id}/expressions", response_model=UnitExpressionsOut)
def unit_expressions(unit_id: int, db: Session = Depends(get_db)) -> UnitExpressionsOut:
    """Expression spans for a unit, so the client can mark them before any click."""
    if not db.get(ListeningUnit, unit_id):
        raise HTTPException(404, f"unit {unit_id} not found")
    return UnitExpressionsOut(
        unit_id=unit_id, expressions=expression_spans_for_unit(db, unit_id)
    )
