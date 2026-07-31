"""Ownership-scoped vocabulary read endpoints."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..identity import LearnerIdentity, get_learner_identity
from ..languages import get_language
from ..schemas import VocabListOut, VocabSavedKeysOut
from ..vocab.cursor import InvalidCursor
from ..vocab.service import list_saved_keys as list_saved_keys_service
from ..vocab.service import list_vocab as list_vocab_service

router = APIRouter(prefix="/api", tags=["vocab"])


def _validated_language(language: str) -> str:
    try:
        return get_language(language).code
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


# Keep static collection routes ahead of any future /vocab/{item_id} route.
@router.get("/vocab/saved-keys", response_model=VocabSavedKeysOut)
def saved_keys(
    language: str,
    identity: Annotated[LearnerIdentity, Depends(get_learner_identity)],
    db: Annotated[Session, Depends(get_db)],
) -> VocabSavedKeysOut:
    return list_saved_keys_service(
        db,
        identity=identity,
        language=_validated_language(language),
    )


@router.get("/vocab", response_model=VocabListOut)
def list_vocab(
    identity: Annotated[LearnerIdentity, Depends(get_learner_identity)],
    db: Annotated[Session, Depends(get_db)],
    language: str | None = None,
    q: Annotated[str, Query(max_length=128)] = "",
    sort: Literal["recent", "alphabetical"] = "recent",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> VocabListOut:
    canonical_language = (
        _validated_language(language) if language is not None else None
    )
    try:
        return list_vocab_service(
            db,
            identity=identity,
            language=canonical_language,
            q=q,
            sort=sort,
            limit=limit,
            cursor=cursor,
        )
    except InvalidCursor:
        raise HTTPException(status_code=400, detail="invalid cursor") from None
