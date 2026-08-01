from __future__ import annotations

import builtins
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateIndex

from alembic import command
from app.lexicon.normalize import normalize_vocab_v1
from app.models import VocabItem

BACKEND_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "schema_0001_signature.json"
REVISION_PATH = BACKEND_ROOT / "alembic" / "versions" / "0001_initial_schema.py"
ENV_PATH = BACKEND_ROOT / "alembic" / "env.py"
REVISION_0002_PATH = BACKEND_ROOT / "alembic" / "versions" / "0002_vocabulary_book.py"


def _metadata_signature(metadata: MetaData) -> dict[str, Any]:
    sqlite_dialect = sqlite.dialect()
    postgresql_dialect = postgresql.dialect()
    tables: dict[str, Any] = {}

    for table in sorted(metadata.tables.values(), key=lambda item: item.name):
        tables[table.name] = {
            "columns": [
                {
                    "name": column.name,
                    "nullable": column.nullable,
                    "primary_key": column.primary_key,
                    "types": {
                        "postgresql": column.type.compile(dialect=postgresql_dialect),
                        "sqlite": column.type.compile(dialect=sqlite_dialect),
                    },
                }
                for column in table.columns
            ],
            "primary_key": {
                "name": table.primary_key.name,
                "column_names": [column.name for column in table.primary_key.columns],
            },
            "unique_constraints": sorted(
                (
                    {
                        "name": constraint.name,
                        "column_names": [column.name for column in constraint.columns],
                    }
                    for constraint in table.constraints
                    if constraint.__class__.__name__ == "UniqueConstraint"
                    and constraint.name is not None
                ),
                key=lambda item: item["name"],
            ),
            "indexes": sorted(
                (
                    {
                        "name": index.name,
                        "column_names": [column.name for column in index.columns],
                        "unique": index.unique,
                    }
                    for index in table.indexes
                ),
                key=lambda item: item["name"],
            ),
            "foreign_keys": sorted(
                (
                    {
                        "name": constraint.name,
                        "constrained_columns": [
                            element.parent.name for element in constraint.elements
                        ],
                        "referred_schema": constraint.referred_table.schema,
                        "referred_table": constraint.referred_table.name,
                        "referred_columns": [
                            element.column.name for element in constraint.elements
                        ],
                        "ondelete": next(
                            (element.ondelete for element in constraint.elements),
                            None,
                        ),
                    }
                    for constraint in table.foreign_key_constraints
                ),
                key=lambda item: (
                    item["name"] or "",
                    item["constrained_columns"],
                ),
            ),
        }

    return {"tables": tables}


def _sqlite_fixture_projection(signature: dict[str, Any]) -> dict[str, Any]:
    projection = json.loads(json.dumps(signature))
    for table in projection["tables"].values():
        for column in table["columns"]:
            column["type"] = column.pop("types")["sqlite"]
    return projection


def _inspector_signature(inspector: Any) -> dict[str, Any]:
    tables: dict[str, Any] = {}
    sqlite_dialect = sqlite.dialect()

    for table_name in sorted(
        name for name in inspector.get_table_names() if name != "alembic_version"
    ):
        columns = inspector.get_columns(table_name)
        pk = inspector.get_pk_constraint(table_name)
        tables[table_name] = {
            "columns": [
                {
                    "name": column["name"],
                    "nullable": column["nullable"],
                    "primary_key": column["name"] in pk["constrained_columns"],
                    "type": column["type"].compile(dialect=sqlite_dialect),
                }
                for column in columns
            ],
            "primary_key": {
                "name": pk.get("name"),
                "column_names": pk["constrained_columns"],
            },
            "unique_constraints": sorted(
                (
                    {
                        "name": constraint["name"],
                        "column_names": constraint["column_names"],
                    }
                    for constraint in inspector.get_unique_constraints(table_name)
                    if constraint["name"] is not None
                ),
                key=lambda item: item["name"],
            ),
            "indexes": sorted(
                (
                    {
                        "name": index["name"],
                        "column_names": index["column_names"],
                        "unique": index["unique"],
                    }
                    for index in inspector.get_indexes(table_name)
                ),
                key=lambda item: item["name"],
            ),
            "foreign_keys": sorted(
                (
                    {
                        "name": foreign_key["name"],
                        "constrained_columns": foreign_key["constrained_columns"],
                        "referred_schema": foreign_key["referred_schema"],
                        "referred_table": foreign_key["referred_table"],
                        "referred_columns": foreign_key["referred_columns"],
                        "ondelete": foreign_key.get("options", {}).get("ondelete"),
                    }
                    for foreign_key in inspector.get_foreign_keys(table_name)
                ),
                key=lambda item: (
                    item["name"] or "",
                    item["constrained_columns"],
                ),
            ),
        }

    return {"tables": tables}


def test_revision_0001_is_frozen(tmp_path: Path, monkeypatch: Any) -> None:
    revision_source = REVISION_PATH.read_text(encoding="utf-8")
    env_source = ENV_PATH.read_text(encoding="utf-8")
    isolation_violations = []
    if "app.models" in revision_source or "Base.metadata" in revision_source:
        isolation_violations.append("revision 0001 imports live Base.metadata")
    if 'config.attributes.get("connection")' not in env_source:
        isolation_violations.append("env.py ignores an injected Alembic connection")
    assert not isolation_violations, "; ".join(isolation_violations)

    expected = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    database_path = tmp_path / "revision_0001.sqlite"
    engine = create_engine(f"sqlite:///{database_path}")
    alembic_config = Config(str(BACKEND_ROOT / "alembic.ini"))

    real_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "app.config":
            raise AssertionError(f"migration imported application module {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    with engine.begin() as connection:
        alembic_config.attributes["connection"] = connection
        command.upgrade(alembic_config, "0001_initial")
        inspector = inspect(connection)

        assert "vocab_items" in inspector.get_table_names()
        columns = {column["name"] for column in inspector.get_columns("vocab_items")}
        assert "headword" in columns
        assert "normalized_headword" not in columns
        assert "normalized_gloss" not in columns
        assert "updated_at" not in columns
        assert _inspector_signature(inspector) == _sqlite_fixture_projection(expected)

    from alembic_schema_0001 import metadata

    assert _metadata_signature(metadata) == expected


def _alembic_config() -> Config:
    return Config(str(BACKEND_ROOT / "alembic.ini"))


def _run_migration(connection: Any, revision: str, *, downgrade: bool = False) -> None:
    config = _alembic_config()
    config.attributes["connection"] = connection
    operation = command.downgrade if downgrade else command.upgrade
    operation(config, revision)


def test_vocab_model_has_normalized_fields_and_owner_indexes() -> None:
    table = VocabItem.__table__
    for column_name in ("normalized_headword", "normalized_gloss", "updated_at"):
        assert column_name in table.c
        assert table.c[column_name].nullable is False

    indexes = {index.name: index for index in table.indexes}
    expected_columns = {
        "uq_vocab_anon_word": ["learner_key", "language", "normalized_headword"],
        "uq_vocab_user_word": ["user_id", "language", "normalized_headword"],
        "ix_vocab_anon_recent": ["learner_key", "created_at", "id"],
        "ix_vocab_user_recent": ["user_id", "created_at", "id"],
    }
    assert expected_columns.keys() <= indexes.keys()
    assert "uq_vocab_learner_word" not in {
        constraint.name for constraint in table.constraints
    }

    for index_name, column_names in expected_columns.items():
        index = indexes[index_name]
        assert [column.name for column in index.columns] == column_names
        sqlite_sql = str(CreateIndex(index).compile(dialect=sqlite.dialect()))
        postgresql_sql = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
        expected_predicate = (
            "user_id IS NULL"
            if index_name in {"uq_vocab_anon_word", "ix_vocab_anon_recent"}
            else "user_id IS NOT NULL"
        )
        assert expected_predicate in sqlite_sql
        assert expected_predicate in postgresql_sql
        assert index.unique is index_name.startswith("uq_")


def test_upgrade_0002_merges_collisions_and_preserves_unrelated_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "upgrade_0002.sqlite"
    engine = create_engine(f"sqlite:///{database_path}")

    with engine.begin() as connection:
        _run_migration(connection, "0001_initial")
        connection.execute(
            text(
                """
                INSERT INTO vocab_items (
                    id, learner_key, user_id, language, headword, gloss_en, example,
                    zipf, unit_id, reps, lapses, ease, interval_days, due_at, created_at
                ) VALUES
                    (1, 'learner-a', NULL, 'fr', 'L’EAU', 'water', 'older example',
                     5.0, 77, 2, 1, 2.4, 3.0, '2026-01-03 00:00:00',
                     '2026-01-01 00:00:00'),
                    (2, 'learner-a', NULL, 'fr', 'lʼeau', '   ', '   ',
                     5.1, NULL, 8, 3, 2.1, 12.0, '2026-02-12 00:00:00',
                     '2026-02-01 00:00:00'),
                    (3, 'learner-a', NULL, 'fr', 'côte', 'coast', 'unrelated',
                     4.0, NULL, 1, 0, 2.5, 1.0, '2026-01-02 00:00:00',
                     '2026-01-01 00:00:00')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO attempts (
                    id, exercise_id, learner_key, user_id, response, is_correct,
                    score, feedback, replays, created_at
                ) VALUES (
                    91, 404, 'learner-a', NULL, '{}', 1, 1.0, '{}', 2,
                    '2026-01-15 00:00:00'
                )
                """
            )
        )

        _run_migration(connection, "head")

        vocab_rows = connection.execute(
            text(
                """
                SELECT id, headword, gloss_en, example, unit_id, reps, lapses, ease,
                       interval_days, due_at, normalized_headword, normalized_gloss,
                       updated_at
                FROM vocab_items
                ORDER BY id
                """
            )
        ).mappings().all()
        assert [row["id"] for row in vocab_rows] == [2, 3]

        survivor = vocab_rows[0]
        assert survivor["headword"] == "lʼeau"
        assert survivor["gloss_en"] == "water"
        assert survivor["example"] == "older example"
        assert survivor["unit_id"] == 77
        assert (
            survivor["reps"],
            survivor["lapses"],
            survivor["ease"],
            survivor["interval_days"],
            survivor["due_at"],
        ) == (8, 3, 2.1, 12.0, "2026-02-12 00:00:00")
        assert survivor["normalized_headword"] == "l'eau"
        assert survivor["normalized_gloss"] == "water"
        assert survivor["updated_at"] is not None

        unrelated = vocab_rows[1]
        assert unrelated["normalized_headword"] == "côte"
        assert unrelated["normalized_gloss"] == "coast"
        assert unrelated["updated_at"] is not None

        attempt = connection.execute(
            text("SELECT id, learner_key, replays, created_at FROM attempts WHERE id = 91")
        ).mappings().one()
        assert dict(attempt) == {
            "id": 91,
            "learner_key": "learner-a",
            "replays": 2,
            "created_at": "2026-01-15 00:00:00",
        }

        inspector = inspect(connection)
        columns = {column["name"]: column for column in inspector.get_columns("vocab_items")}
        for column_name in ("normalized_headword", "normalized_gloss", "updated_at"):
            assert columns[column_name]["nullable"] is False

        index_names = {index["name"] for index in inspector.get_indexes("vocab_items")}
        assert {
            "uq_vocab_anon_word",
            "uq_vocab_user_word",
            "ix_vocab_anon_recent",
            "ix_vocab_user_recent",
        } <= index_names
        index_sql = {
            row.name: row.sql
            for row in connection.execute(
                text(
                    """
                    SELECT name, sql
                    FROM sqlite_master
                    WHERE type = 'index' AND tbl_name = 'vocab_items'
                    """
                )
            ).mappings()
        }
        assert "WHERE user_id IS NULL" in index_sql["uq_vocab_anon_word"]
        assert "WHERE user_id IS NOT NULL" in index_sql["uq_vocab_user_word"]
        assert "WHERE user_id IS NULL" in index_sql["ix_vocab_anon_recent"]
        assert "WHERE user_id IS NOT NULL" in index_sql["ix_vocab_user_recent"]

    engine.dispose()


def test_upgrade_0002_rejects_overlong_normalized_headword_before_ddl(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'overlong_normalized.sqlite'}")
    with engine.begin() as connection:
        _run_migration(connection, "0001_initial")
        connection.execute(
            text(
                """
                INSERT INTO vocab_items (
                    id, learner_key, user_id, language, headword, gloss_en, example,
                    zipf, unit_id, reps, lapses, ease, interval_days, due_at, created_at
                ) VALUES (
                    801, 'learner-a', NULL, 'de', :headword, 'street', NULL,
                    NULL, NULL, 0, 0, 2.5, 0.0, NULL, '2026-01-01 00:00:00'
                )
                """
            ),
            {"headword": "ß" * 65},
        )
        schema_before = connection.execute(
            text(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            )
        ).all()
        row_before = dict(
            connection.execute(
                text("SELECT * FROM vocab_items WHERE id = 801")
            ).mappings().one()
        )
        revision_before = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()

        for _attempt in range(2):
            with pytest.raises(
                RuntimeError,
                match=r"vocab_items row 801.*normalized_headword.*128",
            ):
                _run_migration(connection, "head")

            schema_after = connection.execute(
                text(
                    """
                    SELECT type, name, tbl_name, sql
                    FROM sqlite_master
                    WHERE name NOT LIKE 'sqlite_%'
                    ORDER BY type, name
                    """
                )
            ).all()
            row_after = dict(
                connection.execute(
                    text("SELECT * FROM vocab_items WHERE id = 801")
                ).mappings().one()
            )
            assert schema_after == schema_before
            assert row_after == row_before
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == revision_before
                == "0001_initial"
            )
    engine.dispose()


def test_upgrade_empty_database_through_head(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'empty_to_head.sqlite'}")
    with engine.begin() as connection:
        _run_migration(connection, "head")
        # Read the expected head out of the revision graph rather than naming a revision. This
        # test is about the chain applying cleanly to an empty database, not about which revision
        # happens to be last, and hardcoding that made every new migration fail here first.
        expected_head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            expected_head
        )
        assert {
            "normalized_headword",
            "normalized_gloss",
            "updated_at",
        } <= {column["name"] for column in inspect(connection).get_columns("vocab_items")}
    engine.dispose()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" Écouter ", "écouter"),
        ("L’EAU", "l'eau"),
        ("lʼeau", "l'eau"),
        ("mise   en œuvre", "mise en œuvre"),
        ("côte", "côte"),
        ("cote", "cote"),
    ],
)
def test_revision_0002_frozen_normalizer_matches_runtime(value: str, expected: str) -> None:
    spec = importlib.util.spec_from_file_location("revision_0002_vocabulary_book", REVISION_0002_PATH)
    assert spec is not None and spec.loader is not None
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)

    assert revision._normalize_vocab_v1(value) == expected
    assert revision._normalize_vocab_v1(value) == normalize_vocab_v1(value)


def test_downgrade_0002_rejects_authenticated_rows_before_ddl(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'downgrade_guard.sqlite'}")
    with engine.begin() as connection:
        # Upgraded to 0002 exactly, not to head. This test asserts that 0002's own downgrade guard
        # fires *before* it touches anything, by checking the schema is byte-identical afterwards.
        # Starting from head made it a test of the whole downgrade chain instead: later revisions
        # downgrade first and legitimately drop their own tables, so the schema comparison failed
        # on their absence rather than on anything 0002 did.
        _run_migration(connection, "0002_vocabulary_book")
        connection.execute(
            text(
                """
                INSERT INTO vocab_items (
                    id, learner_key, user_id, language, headword, normalized_headword,
                    gloss_en, normalized_gloss, example, zipf, unit_id, reps, lapses,
                    ease, interval_days, due_at, created_at, updated_at
                ) VALUES (
                    1, 'anonymous', '00000000-0000-0000-0000-000000000001',
                    'fr', 'écouter', 'écouter', 'listen', 'listen', NULL, 4.2,
                    NULL, 0, 0, 2.5, 0.0, NULL,
                    '2026-01-01 00:00:00', '2026-01-01 00:00:00'
                )
                """
            )
        )
        schema_before = connection.execute(
            text(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            )
        ).all()
        row_before = connection.execute(
            text("SELECT * FROM vocab_items WHERE id = 1")
        ).mappings().one()

        with pytest.raises(RuntimeError, match="authenticated"):
            _run_migration(connection, "0001_initial", downgrade=True)

        schema_after = connection.execute(
            text(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            )
        ).all()
        row_after = connection.execute(
            text("SELECT * FROM vocab_items WHERE id = 1")
        ).mappings().one()
        assert schema_after == schema_before
        assert dict(row_after) == dict(row_before)
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0002_vocabulary_book"
        )
    engine.dispose()
