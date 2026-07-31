"""Ownership-scoped vocabulary operations."""

from __future__ import annotations

import sqlite3
from typing import Literal

from sqlalchemy import Select, and_, case, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from ..identity import LearnerIdentity
from ..languages import get_language
from ..lexicon.normalize import normalize_vocab_v1
from ..models import Lesson, ListeningUnit, VocabItem, utcnow
from ..schemas import (
    VocabEditIn,
    VocabItemOut,
    VocabListOut,
    VocabSavedKeyOut,
    VocabSavedKeysOut,
    VocabSaveIn,
    VocabSourceOut,
)
from .cursor import (
    decode_cursor,
    encode_alphabetical_cursor,
    encode_recent_cursor,
)

VocabSort = Literal["recent", "alphabetical"]


class InvalidVocabInput(ValueError):
    """The mutation payload is inconsistent with server-owned vocabulary data."""


class VocabItemNotFound(LookupError):
    """No vocabulary item exists for both the id and learner identity."""


def owner_clause(identity: LearnerIdentity) -> ColumnElement[bool]:
    """Return the database predicate for exactly one learner identity."""
    if identity.user_id is not None:
        return VocabItem.user_id == identity.user_id
    return and_(
        VocabItem.user_id.is_(None),
        VocabItem.learner_key == identity.learner_key,
    )


def escape_like(value: str) -> str:
    """Escape a literal value for LIKE using backslash as the escape character."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _filter_clauses(
    *,
    identity: LearnerIdentity,
    language: str | None,
    normalized_q: str,
) -> list[ColumnElement[bool]]:
    clauses = [owner_clause(identity)]
    if language is not None:
        clauses.append(VocabItem.language == language)
    if normalized_q:
        pattern = f"%{escape_like(normalized_q)}%"
        clauses.append(
            or_(
                VocabItem.normalized_headword.like(pattern, escape="\\"),
                VocabItem.normalized_gloss.like(pattern, escape="\\"),
            )
        )
    return clauses


def build_vocab_select(
    *,
    identity: LearnerIdentity,
    language: str | None,
    normalized_q: str,
) -> Select[tuple[VocabItem, int | None, int | None, int | None, str | None]]:
    """Build the portable filtered item/source statement."""
    return (
        select(
            VocabItem,
            ListeningUnit.id,
            ListeningUnit.idx,
            Lesson.id,
            Lesson.title,
        )
        .outerjoin(ListeningUnit, VocabItem.unit_id == ListeningUnit.id)
        .outerjoin(Lesson, ListeningUnit.lesson_id == Lesson.id)
        .where(
            *_filter_clauses(
                identity=identity,
                language=language,
                normalized_q=normalized_q,
            )
        )
    )


def to_vocab_out(
    row: tuple[VocabItem, int | None, int | None, int | None, str | None],
) -> VocabItemOut:
    item, unit_id, unit_index, lesson_id, lesson_title = row
    source = None
    if (
        unit_id is not None
        and unit_index is not None
        and lesson_id is not None
        and lesson_title is not None
    ):
        source = VocabSourceOut(
            lesson_id=lesson_id,
            lesson_title=lesson_title,
            unit_id=unit_id,
            unit_index=unit_index,
        )
    return VocabItemOut(
        id=item.id,
        language=item.language,
        headword=item.headword,
        normalized_headword=item.normalized_headword,
        gloss_en=item.gloss_en,
        example=item.example,
        zipf=item.zipf,
        reps=item.reps,
        due_at=item.due_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
        source=source,
    )


def _owned_item_select(
    identity: LearnerIdentity,
    *clauses: ColumnElement[bool],
) -> Select[tuple[VocabItem, int | None, int | None, int | None, str | None]]:
    return build_vocab_select(
        identity=identity,
        language=None,
        normalized_q="",
    ).where(*clauses)


def _select_owned_item(
    db: Session,
    identity: LearnerIdentity,
    *clauses: ColumnElement[bool],
) -> tuple[VocabItem, int | None, int | None, int | None, str | None] | None:
    return db.execute(
        _owned_item_select(identity, *clauses).execution_options(
            populate_existing=True
        )
    ).one_or_none()


def _validate_save(payload: VocabSaveIn) -> tuple[str, str, str, object]:
    try:
        language_profile = get_language(payload.language)
    except ValueError:
        raise InvalidVocabInput from None
    display_headword = payload.headword.strip()
    normalized_headword = normalize_vocab_v1(payload.headword)
    if not 1 <= len(normalized_headword) <= 128:
        raise InvalidVocabInput
    return (
        language_profile.code,
        display_headword,
        normalized_headword,
        language_profile,
    )


def _validate_source(db: Session, unit_id: int | None, language: str) -> None:
    if unit_id is None:
        return
    unit_language = db.scalar(
        select(Lesson.language)
        .join(ListeningUnit, ListeningUnit.lesson_id == Lesson.id)
        .where(ListeningUnit.id == unit_id)
    )
    if unit_language != language:
        raise InvalidVocabInput


def _present(value: str | None) -> bool:
    return value is not None and bool(value.strip())


def _fill_existing(
    db: Session,
    *,
    identity: LearnerIdentity,
    row: tuple[VocabItem, int | None, int | None, int | None, str | None],
    payload: VocabSaveIn,
) -> VocabItemOut:
    item = row[0]
    fill_conditions: list[ColumnElement[bool]] = []
    values: dict[str, object] = {}
    if not _present(item.gloss_en) and _present(payload.gloss_en):
        gloss_condition = (
            VocabItem.gloss_en.is_(None)
            if item.gloss_en is None
            else VocabItem.gloss_en == item.gloss_en
        )
        fill_conditions.append(gloss_condition)
        values["gloss_en"] = case(
            (gloss_condition, payload.gloss_en),
            else_=VocabItem.gloss_en,
        )
        values["normalized_gloss"] = case(
            (gloss_condition, normalize_vocab_v1(payload.gloss_en or "")),
            else_=VocabItem.normalized_gloss,
        )
    if not _present(item.example) and _present(payload.example):
        example_condition = (
            VocabItem.example.is_(None)
            if item.example is None
            else VocabItem.example == item.example
        )
        fill_conditions.append(example_condition)
        values["example"] = case(
            (example_condition, payload.example),
            else_=VocabItem.example,
        )
    if item.unit_id is None and payload.unit_id is not None:
        source_condition = VocabItem.unit_id.is_(None)
        fill_conditions.append(source_condition)
        values["unit_id"] = case(
            (source_condition, payload.unit_id),
            else_=VocabItem.unit_id,
        )
    if not fill_conditions:
        return to_vocab_out(row)

    values["updated_at"] = utcnow()
    result = db.execute(
        update(VocabItem)
        .where(
            VocabItem.id == item.id,
            owner_clause(identity),
            or_(*fill_conditions),
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount:
        db.commit()
    else:
        db.rollback()
    refreshed = _select_owned_item(db, identity, VocabItem.id == item.id)
    if refreshed is None:  # pragma: no cover - a concurrent delete is exceptional
        raise VocabItemNotFound
    return to_vocab_out(refreshed)


def _is_owner_unique_race(
    error: IntegrityError,
    identity: LearnerIdentity,
) -> bool:
    original = error.orig
    expected_constraint = (
        "uq_vocab_user_word"
        if identity.user_id is not None
        else "uq_vocab_anon_word"
    )
    sqlstate = getattr(original, "sqlstate", None) or getattr(
        original, "pgcode", None
    )
    diagnostic = getattr(original, "diag", None)
    if sqlstate == "23505":
        return (
            diagnostic is not None
            and getattr(diagnostic, "constraint_name", None) == expected_constraint
        )
    if not isinstance(original, sqlite3.IntegrityError):
        return False
    columns = (
        "vocab_items.user_id, vocab_items.language, "
        "vocab_items.normalized_headword"
        if identity.user_id is not None
        else "vocab_items.learner_key, vocab_items.language, "
        "vocab_items.normalized_headword"
    )
    return str(original) == f"UNIQUE constraint failed: {columns}"


def save_vocab(
    db: Session,
    *,
    identity: LearnerIdentity,
    payload: VocabSaveIn,
) -> VocabItemOut:
    language, display_headword, normalized_headword, language_profile = _validate_save(
        payload
    )
    _validate_source(db, payload.unit_id, language)
    key_clauses = (
        VocabItem.language == language,
        VocabItem.normalized_headword == normalized_headword,
    )
    existing = _select_owned_item(db, identity, *key_clauses)
    if existing is not None:
        return _fill_existing(
            db,
            identity=identity,
            row=existing,
            payload=payload,
        )

    item = VocabItem(
        learner_key=identity.learner_key,
        user_id=identity.user_id,
        language=language,
        headword=display_headword,
        normalized_headword=normalized_headword,
        gloss_en=payload.gloss_en,
        normalized_gloss=normalize_vocab_v1(payload.gloss_en or ""),
        example=payload.example,
        zipf=round(language_profile.zipf(display_headword), 2),
        unit_id=payload.unit_id,
    )
    db.add(item)
    try:
        db.commit()
    except IntegrityError as error:
        if not _is_owner_unique_race(error, identity):
            raise
        db.rollback()
        winner = _select_owned_item(db, identity, *key_clauses)
        if winner is None:
            raise
        return _fill_existing(
            db,
            identity=identity,
            row=winner,
            payload=payload,
        )
    db.refresh(item)
    created = _select_owned_item(db, identity, VocabItem.id == item.id)
    if created is None:  # pragma: no cover - commit made the row visible
        raise VocabItemNotFound
    return to_vocab_out(created)


def edit_vocab(
    db: Session,
    *,
    identity: LearnerIdentity,
    item_id: int,
    payload: VocabEditIn,
) -> VocabItemOut:
    values: dict[str, object] = {"updated_at": utcnow()}
    if "gloss_en" in payload.model_fields_set:
        values["gloss_en"] = payload.gloss_en
        values["normalized_gloss"] = normalize_vocab_v1(payload.gloss_en or "")
    if "example" in payload.model_fields_set:
        values["example"] = payload.example
    result = db.execute(
        update(VocabItem)
        .where(VocabItem.id == item_id, owner_clause(identity))
        .values(**values)
    )
    if result.rowcount == 0:
        db.rollback()
        raise VocabItemNotFound
    db.commit()
    row = _select_owned_item(db, identity, VocabItem.id == item_id)
    if row is None:  # pragma: no cover - a concurrent delete after update
        raise VocabItemNotFound
    return to_vocab_out(row)


def delete_vocab(
    db: Session,
    *,
    identity: LearnerIdentity,
    item_id: int,
) -> None:
    result = db.execute(
        delete(VocabItem).where(VocabItem.id == item_id, owner_clause(identity))
    )
    if result.rowcount == 0:
        db.rollback()
        raise VocabItemNotFound
    db.commit()


def list_vocab(
    db: Session,
    *,
    identity: LearnerIdentity,
    language: str | None,
    q: str,
    sort: VocabSort,
    limit: int,
    cursor: str | None,
) -> VocabListOut:
    normalized_q = normalize_vocab_v1(q)
    position = None
    if cursor is not None:
        position = decode_cursor(
            cursor,
            sort=sort,
            language=language,
            q=normalized_q,
        )

    clauses = _filter_clauses(
        identity=identity,
        language=language,
        normalized_q=normalized_q,
    )
    total = db.scalar(select(func.count(VocabItem.id)).where(*clauses)) or 0
    statement = build_vocab_select(
        identity=identity,
        language=language,
        normalized_q=normalized_q,
    )

    if sort == "recent":
        statement = statement.order_by(VocabItem.created_at.desc(), VocabItem.id.desc())
    else:
        statement = statement.order_by(
            VocabItem.normalized_headword.asc(), VocabItem.id.asc()
        )

    if position is not None:
        if sort == "recent":
            statement = statement.where(
                or_(
                    VocabItem.created_at < position.last_created_at,
                    and_(
                        VocabItem.created_at == position.last_created_at,
                        VocabItem.id < position.last_id,
                    ),
                )
            )
        else:
            statement = statement.where(
                or_(
                    VocabItem.normalized_headword > position.last_headword,
                    and_(
                        VocabItem.normalized_headword == position.last_headword,
                        VocabItem.id > position.last_id,
                    ),
                )
            )

    rows = list(db.execute(statement.limit(limit + 1)).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more and rows:
        last_item = rows[-1][0]
        if sort == "recent":
            next_cursor = encode_recent_cursor(
                language=language,
                q=normalized_q,
                last_created_at=last_item.created_at,
                last_id=last_item.id,
            )
        else:
            next_cursor = encode_alphabetical_cursor(
                language=language,
                q=normalized_q,
                last_headword=last_item.normalized_headword,
                last_id=last_item.id,
            )
    return VocabListOut(
        items=[to_vocab_out(row) for row in rows],
        next_cursor=next_cursor,
        total=total,
    )


def list_saved_keys(
    db: Session,
    *,
    identity: LearnerIdentity,
    language: str,
) -> VocabSavedKeysOut:
    rows = db.execute(
        select(VocabItem.id, VocabItem.normalized_headword)
        .where(
            owner_clause(identity),
            VocabItem.language == language,
        )
        .order_by(VocabItem.normalized_headword.asc(), VocabItem.id.asc())
    ).all()
    return VocabSavedKeysOut(
        language=language,
        items=[
            VocabSavedKeyOut(id=item_id, normalized_headword=normalized_headword)
            for item_id, normalized_headword in rows
        ],
    )
