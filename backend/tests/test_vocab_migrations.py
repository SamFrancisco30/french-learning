from __future__ import annotations

import builtins
import json
from pathlib import Path
from typing import Any

from alembic.config import Config
from sqlalchemy import MetaData, create_engine, inspect
from sqlalchemy.dialects import postgresql, sqlite

from alembic import command

BACKEND_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "schema_0001_signature.json"
REVISION_PATH = BACKEND_ROOT / "alembic" / "versions" / "0001_initial_schema.py"
ENV_PATH = BACKEND_ROOT / "alembic" / "env.py"


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
