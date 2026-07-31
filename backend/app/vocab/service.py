"""Ownership-scoped vocabulary read operations."""

from __future__ import annotations

from typing import Literal

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from ..identity import LearnerIdentity
from ..lexicon.normalize import normalize_vocab_v1
from ..models import Lesson, ListeningUnit, VocabItem
from ..schemas import (
    VocabItemOut,
    VocabListOut,
    VocabSavedKeyOut,
    VocabSavedKeysOut,
    VocabSourceOut,
)
from .cursor import (
    decode_cursor,
    encode_alphabetical_cursor,
    encode_recent_cursor,
)

VocabSort = Literal["recent", "alphabetical"]


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
