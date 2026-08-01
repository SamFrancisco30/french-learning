"""Add accounts, study sessions and listening-unit entitlements.

Revision ID: 0003_accounts
Revises: 0002_vocabulary_book

Purely additive: three new tables, no changes to existing ones. `attempts.user_id` and
`vocab_items.user_id` already exist from revision 0001, so signing in needs no backfill — an
anonymous learner's rows keep their NULL `user_id` until they claim them.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003_accounts"
down_revision = "0002_vocabulary_book"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_profiles",
        # The Supabase user id (`sub`) is the primary key. No FK to auth.users: that table belongs
        # to Supabase's own schema, which this migration chain does not own.
        sa.Column("user_id", sa.String(length=36), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("tier", sa.String(length=16), nullable=False, server_default="free"),
        sa.Column("stripe_customer_id", sa.String(length=64), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(length=64), nullable=True),
        sa.Column("premium_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_user_profiles_email", "user_profiles", ["email"])
    op.create_index(
        "ix_user_profiles_stripe_customer_id", "user_profiles", ["stripe_customer_id"]
    )

    op.create_table(
        "unit_unlocks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("learner_key", sa.String(length=64), nullable=False, server_default="anonymous"),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column(
            "unit_id",
            sa.Integer(),
            sa.ForeignKey("listening_units.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_unit_unlocks_learner_key", "unit_unlocks", ["learner_key"])
    op.create_index("ix_unit_unlocks_user_id", "unit_unlocks", ["user_id"])
    op.create_index("ix_unit_unlocks_unit_id", "unit_unlocks", ["unit_id"])
    # Two partial unique indexes rather than one three-column index. A plain unique index over
    # (learner_key, user_id, unit_id) would not prevent duplicates for either identity, because
    # NULL never compares equal to NULL in SQL: an anonymous learner could unlock the same unit
    # any number of times and each row would satisfy the constraint.
    op.create_index(
        "uq_unlock_anon_unit",
        "unit_unlocks",
        ["learner_key", "unit_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NULL"),
        sqlite_where=sa.text("user_id IS NULL"),
    )
    op.create_index(
        "uq_unlock_user_unit",
        "unit_unlocks",
        ["user_id", "unit_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
        sqlite_where=sa.text("user_id IS NOT NULL"),
    )

    op.create_table(
        "study_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("learner_key", sa.String(length=64), nullable=False, server_default="anonymous"),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("language", sa.String(length=8), nullable=False, server_default="fr"),
        sa.Column("skill", sa.String(length=16), nullable=False, server_default="listening"),
        sa.Column(
            "unit_id",
            sa.Integer(),
            sa.ForeignKey("listening_units.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("seconds", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_study_sessions_learner_key", "study_sessions", ["learner_key"])
    op.create_index("ix_study_sessions_user_id", "study_sessions", ["user_id"])
    op.create_index("ix_study_sessions_language", "study_sessions", ["language"])
    op.create_index("ix_study_sessions_skill", "study_sessions", ["skill"])
    op.create_index("ix_session_owner_started", "study_sessions", ["learner_key", "started_at"])
    op.create_index("ix_session_user_started", "study_sessions", ["user_id", "started_at"])


def downgrade() -> None:
    op.drop_table("study_sessions")
    op.drop_table("unit_unlocks")
    op.drop_table("user_profiles")
