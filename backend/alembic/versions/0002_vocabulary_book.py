"""Add normalized vocabulary ownership and recency indexes.

Revision ID: 0002_vocabulary_book
Revises: 0001_initial
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from typing import Any

import sqlalchemy as sa

from alembic import context, op

revision = "0002_vocabulary_book"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

_APOSTROPHES = str.maketrans(
    {
        "\u2019": "'",
        "\u02bc": "'",
        "\uff07": "'",
        "\u0060": "'",
        "\u00b4": "'",
    }
)


def _normalize_vocab_v1(value: str) -> str:
    """Frozen copy of the vocabulary normalizer used for this backfill."""
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.translate(_APOSTROPHES)
    normalized = " ".join(normalized.split())
    return normalized.casefold()


def _require_online_migration() -> None:
    if context.is_offline_mode():
        raise RuntimeError("revision 0002 is data-dependent and cannot run in offline SQL mode")


def _preflight_normalized_headword_lengths(connection: Any) -> None:
    rows = connection.execute(
        sa.text("SELECT id, headword FROM vocab_items ORDER BY id")
    ).mappings()
    for row in rows:
        if len(_normalize_vocab_v1(row["headword"])) > 128:
            raise RuntimeError(
                f"vocab_items row {row['id']} has normalized_headword longer than 128 characters"
            )


def _backfill_normalized_values(connection: Any) -> None:
    vocab_items = sa.table(
        "vocab_items",
        sa.column("id", sa.Integer()),
        sa.column("headword", sa.String()),
        sa.column("gloss_en", sa.Text()),
        sa.column("normalized_headword", sa.String()),
        sa.column("normalized_gloss", sa.Text()),
    )
    rows = connection.execute(
        sa.select(vocab_items.c.id, vocab_items.c.headword, vocab_items.c.gloss_en)
    ).mappings()
    for row in rows:
        gloss = row["gloss_en"] or ""
        connection.execute(
            vocab_items.update()
            .where(vocab_items.c.id == row["id"])
            .values(
                normalized_headword=_normalize_vocab_v1(row["headword"]),
                normalized_gloss=_normalize_vocab_v1(gloss),
            )
        )


def _merge_normalized_collisions(connection: Any) -> None:
    vocab_items = sa.table(
        "vocab_items",
        sa.column("id", sa.Integer()),
        sa.column("learner_key", sa.String()),
        sa.column("user_id", sa.String()),
        sa.column("language", sa.String()),
        sa.column("normalized_headword", sa.String()),
        sa.column("gloss_en", sa.Text()),
        sa.column("normalized_gloss", sa.Text()),
        sa.column("example", sa.Text()),
        sa.column("unit_id", sa.Integer()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    rows = connection.execute(
        sa.select(vocab_items).order_by(
            vocab_items.c.updated_at.desc(),
            vocab_items.c.id.desc(),
        )
    ).mappings()
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        owner = (
            ("anonymous", row["learner_key"])
            if row["user_id"] is None
            else ("authenticated", row["user_id"])
        )
        key = (*owner, row["language"], row["normalized_headword"])
        groups[key].append(dict(row))

    for group in groups.values():
        if len(group) == 1:
            continue

        survivor = group[0]
        final_values: dict[str, Any] = {}
        for field in ("gloss_en", "example"):
            final_values[field] = next(
                (
                    row[field]
                    for row in group
                    if row[field] is not None and _normalize_vocab_v1(row[field])
                ),
                survivor[field],
            )
        final_values["unit_id"] = next(
            (row["unit_id"] for row in group if row["unit_id"] is not None),
            survivor["unit_id"],
        )
        final_values["normalized_gloss"] = _normalize_vocab_v1(
            final_values["gloss_en"] or ""
        )

        connection.execute(
            vocab_items.update()
            .where(vocab_items.c.id == survivor["id"])
            .values(**final_values)
        )
        connection.execute(
            vocab_items.delete().where(
                vocab_items.c.id.in_([row["id"] for row in group[1:]])
            )
        )


def _make_new_columns_non_nullable() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("vocab_items") as batch_op:
            batch_op.drop_constraint("uq_vocab_learner_word", type_="unique")
            batch_op.alter_column(
                "normalized_headword",
                existing_type=sa.String(length=128),
                nullable=False,
            )
            batch_op.alter_column(
                "normalized_gloss",
                existing_type=sa.Text(),
                nullable=False,
            )
            batch_op.alter_column(
                "updated_at",
                existing_type=sa.DateTime(timezone=True),
                nullable=False,
            )
        return

    op.drop_constraint("uq_vocab_learner_word", "vocab_items", type_="unique")
    op.alter_column(
        "vocab_items",
        "normalized_headword",
        existing_type=sa.String(length=128),
        nullable=False,
    )
    op.alter_column(
        "vocab_items",
        "normalized_gloss",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.alter_column(
        "vocab_items",
        "updated_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )


def _create_owner_indexes() -> None:
    predicates = {
        "anonymous": sa.text("user_id IS NULL"),
        "authenticated": sa.text("user_id IS NOT NULL"),
    }
    op.create_index(
        "uq_vocab_anon_word",
        "vocab_items",
        ["learner_key", "language", "normalized_headword"],
        unique=True,
        postgresql_where=predicates["anonymous"],
        sqlite_where=predicates["anonymous"],
    )
    op.create_index(
        "uq_vocab_user_word",
        "vocab_items",
        ["user_id", "language", "normalized_headword"],
        unique=True,
        postgresql_where=predicates["authenticated"],
        sqlite_where=predicates["authenticated"],
    )
    op.create_index(
        "ix_vocab_anon_recent",
        "vocab_items",
        ["learner_key", "created_at", "id"],
        postgresql_where=predicates["anonymous"],
        sqlite_where=predicates["anonymous"],
    )
    op.create_index(
        "ix_vocab_user_recent",
        "vocab_items",
        ["user_id", "created_at", "id"],
        postgresql_where=predicates["authenticated"],
        sqlite_where=predicates["authenticated"],
    )


def upgrade() -> None:
    _require_online_migration()
    connection = op.get_bind()
    _preflight_normalized_headword_lengths(connection)

    op.add_column(
        "vocab_items",
        sa.Column("normalized_headword", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "vocab_items",
        sa.Column("normalized_gloss", sa.Text(), nullable=True),
    )
    _backfill_normalized_values(connection)

    op.add_column(
        "vocab_items",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    connection.execute(
        sa.text("UPDATE vocab_items SET updated_at = created_at WHERE updated_at IS NULL")
    )

    _merge_normalized_collisions(connection)
    _make_new_columns_non_nullable()
    _create_owner_indexes()


def downgrade() -> None:
    _require_online_migration()
    connection = op.get_bind()
    authenticated_row = connection.execute(
        sa.text("SELECT 1 FROM vocab_items WHERE user_id IS NOT NULL LIMIT 1")
    ).first()
    if authenticated_row is not None:
        raise RuntimeError(
            "cannot downgrade vocabulary schema while authenticated vocabulary rows exist"
        )

    for index_name in (
        "ix_vocab_user_recent",
        "ix_vocab_anon_recent",
        "uq_vocab_user_word",
        "uq_vocab_anon_word",
    ):
        op.drop_index(index_name, table_name="vocab_items")

    if connection.dialect.name == "sqlite":
        with op.batch_alter_table("vocab_items") as batch_op:
            batch_op.drop_column("updated_at")
            batch_op.drop_column("normalized_gloss")
            batch_op.drop_column("normalized_headword")
            batch_op.create_unique_constraint(
                "uq_vocab_learner_word",
                ["learner_key", "language", "headword"],
            )
        return

    op.drop_column("vocab_items", "updated_at")
    op.drop_column("vocab_items", "normalized_gloss")
    op.drop_column("vocab_items", "normalized_headword")
    op.create_unique_constraint(
        "uq_vocab_learner_word",
        "vocab_items",
        ["learner_key", "language", "headword"],
    )
