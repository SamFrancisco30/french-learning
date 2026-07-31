"""Ownership-scoped vocabulary endpoints."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..identity import LearnerIdentity, get_learner_identity
from ..languages import get_language
from ..schemas import (
    VocabEditIn,
    VocabItemOut,
    VocabListOut,
    VocabSavedKeysOut,
    VocabSaveIn,
)
from ..vocab.cursor import InvalidCursor
from ..vocab.service import InvalidVocabInput, VocabItemNotFound
from ..vocab.service import delete_vocab as delete_vocab_service
from ..vocab.service import edit_vocab as edit_vocab_service
from ..vocab.service import list_saved_keys as list_saved_keys_service
from ..vocab.service import list_vocab as list_vocab_service
from ..vocab.service import save_vocab as save_vocab_service

router = APIRouter(prefix="/api", tags=["vocab"])


def _validated_language(language: str) -> str:
    try:
        return get_language(language).code
    except ValueError:
        raise HTTPException(status_code=400, detail="unsupported language") from None


# Keep static collection routes ahead of any future /vocab/{item_id} route.
@router.get("/vocab/saved-keys", response_model=VocabSavedKeysOut)
def saved_keys(
    language: Annotated[str, Query(max_length=8)],
    identity: Annotated[LearnerIdentity, Depends(get_learner_identity)],
    db: Annotated[Session, Depends(get_db)],
) -> VocabSavedKeysOut:
    return list_saved_keys_service(
        db,
        identity=identity,
        language=_validated_language(language),
    )


@router.post("/vocab", response_model=VocabItemOut, status_code=status.HTTP_200_OK)
def save_vocab(
    payload: VocabSaveIn,
    identity: Annotated[LearnerIdentity, Depends(get_learner_identity)],
    db: Annotated[Session, Depends(get_db)],
) -> VocabItemOut:
    try:
        return save_vocab_service(db, identity=identity, payload=payload)
    except InvalidVocabInput:
        raise HTTPException(
            status_code=422, detail="invalid vocabulary input"
        ) from None


@router.get("/vocab", response_model=VocabListOut)
def list_vocab(
    identity: Annotated[LearnerIdentity, Depends(get_learner_identity)],
    db: Annotated[Session, Depends(get_db)],
    language: Annotated[str | None, Query(max_length=8)] = None,
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


@router.patch("/vocab/{item_id}", response_model=VocabItemOut)
def edit_vocab(
    item_id: int,
    payload: VocabEditIn,
    identity: Annotated[LearnerIdentity, Depends(get_learner_identity)],
    db: Annotated[Session, Depends(get_db)],
) -> VocabItemOut:
    try:
        return edit_vocab_service(
            db,
            identity=identity,
            item_id=item_id,
            payload=payload,
        )
    except VocabItemNotFound:
        raise HTTPException(
            status_code=404, detail="vocabulary item not found"
        ) from None


@router.delete("/vocab/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vocab(
    item_id: int,
    identity: Annotated[LearnerIdentity, Depends(get_learner_identity)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    try:
        delete_vocab_service(db, identity=identity, item_id=item_id)
    except VocabItemNotFound:
        raise HTTPException(
            status_code=404, detail="vocabulary item not found"
        ) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
