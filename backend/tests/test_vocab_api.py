from __future__ import annotations

import importlib
import inspect
import logging
import os
import re
import sqlite3
import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from alembic import command
from app.db import get_db
from app.identity import LearnerIdentity
from app.models import Base, Lesson, ListeningUnit, Source, VocabItem
from app.routers import lexicon
from app.vocab.cursor import InvalidCursor, encode_recent_cursor

BACKEND_ROOT = Path(__file__).resolve().parents[1]

app = FastAPI()
app.include_router(lexicon.router)
try:
    from app.errors import register_database_error_handler
    from app.routers import vocab
except ModuleNotFoundError:
    vocab = None
else:
    register_database_error_handler(app)
    app.include_router(vocab.router)


@pytest.fixture
def api_db() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)

    def _override_db() -> Generator[Session, None, None]:
        yield session

    app.dependency_overrides[get_db] = _override_db
    try:
        yield session
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()


@pytest.fixture
def client(api_db: Session) -> TestClient:
    del api_db
    return TestClient(app, raise_server_exceptions=False)


def _add_item(
    db: Session,
    *,
    learner_key: str,
    user_id: str | None = None,
    language: str = "fr",
    headword: str,
    normalized_headword: str | None = None,
    gloss: str | None = None,
    normalized_gloss: str | None = None,
    created_at: datetime | None = None,
    unit_id: int | None = None,
) -> VocabItem:
    item = VocabItem(
        learner_key=learner_key,
        user_id=user_id,
        language=language,
        headword=headword,
        normalized_headword=normalized_headword or headword.casefold(),
        gloss_en=gloss,
        normalized_gloss=normalized_gloss or (gloss.casefold() if gloss else ""),
        created_at=created_at or datetime(2026, 7, 30, 12, tzinfo=UTC),
        updated_at=created_at or datetime(2026, 7, 30, 12, tzinfo=UTC),
        unit_id=unit_id,
    )
    db.add(item)
    db.flush()
    return item


def _headers(learner_key: str = "learner_alpha") -> dict[str, str]:
    return {"X-Learner-Key": learner_key}


def test_list_requires_identity(client: TestClient) -> None:
    response = client.get("/api/vocab")

    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}


def test_list_anonymous_owner_is_scoped_by_key_and_null_user(
    client: TestClient, api_db: Session
) -> None:
    owned = _add_item(api_db, learner_key="learner_alpha", headword="owned")
    _add_item(api_db, learner_key="learner_beta", headword="other learner")
    _add_item(
        api_db,
        learner_key="learner_alpha",
        user_id="00000000-0000-0000-0000-000000000001",
        headword="authenticated collision",
    )
    api_db.commit()

    response = client.get("/api/vocab", headers=_headers())

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [owned.id]
    assert response.json()["total"] == 1


def test_list_service_authenticated_owner_uses_only_user_id(api_db: Session) -> None:
    service = importlib.import_module("app.vocab.service")
    user_id = "00000000-0000-0000-0000-000000000001"
    owned = _add_item(
        api_db, learner_key="stale_key", user_id=user_id, headword="owned user row"
    )
    _add_item(
        api_db,
        learner_key="learner_auth",
        user_id="00000000-0000-0000-0000-000000000002",
        headword="wrong user",
    )
    _add_item(api_db, learner_key="learner_auth", headword="anonymous collision")
    api_db.commit()

    result = service.list_vocab(
        api_db,
        identity=LearnerIdentity(learner_key="learner_auth", user_id=user_id),
        language=None,
        q="",
        sort="recent",
        limit=50,
        cursor=None,
    )

    assert [item.id for item in result.items] == [owned.id]
    assert result.total == 1


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("%", {"100% vrai"}),
        ("_", {"sous_score"}),
        ("\\", {"barre\\oblique"}),
        ("ÉCOUTER", {"Écouter"}),
        ("AUDITION", {"ouïe"}),
    ],
)
def test_list_search_is_normalized_and_treats_wildcards_literally(
    query: str, expected: set[str], client: TestClient, api_db: Session
) -> None:
    for headword, normalized_headword, gloss, normalized_gloss in [
        ("100% vrai", "100% vrai", None, ""),
        ("sous_score", "sous_score", "underscore", "underscore"),
        ("barre\\oblique", "barre\\oblique", None, ""),
        ("Écouter", "écouter", "to listen", "to listen"),
        ("ouïe", "ouïe", "Audition", "audition"),
        ("ordinary", "ordinary", None, ""),
    ]:
        _add_item(
            api_db,
            learner_key="learner_alpha",
            headword=headword,
            normalized_headword=normalized_headword,
            gloss=gloss,
            normalized_gloss=normalized_gloss,
        )
    api_db.commit()

    response = client.get("/api/vocab", params={"q": query}, headers=_headers())

    assert response.status_code == 200
    assert {item["headword"] for item in response.json()["items"]} == expected
    assert response.json()["total"] == len(expected)


def test_list_language_filter_and_filtered_total(
    client: TestClient, api_db: Session
) -> None:
    _add_item(api_db, learner_key="learner_alpha", language="fr", headword="bonjour")
    _add_item(api_db, learner_key="learner_alpha", language="ru", headword="привет")
    _add_item(api_db, learner_key="learner_alpha", language="fr", headword="au revoir")
    api_db.commit()

    response = client.get(
        "/api/vocab", params={"language": "fr", "q": "jour"}, headers=_headers()
    )

    assert response.status_code == 200
    assert [item["headword"] for item in response.json()["items"]] == ["bonjour"]
    assert response.json()["total"] == 1


def test_list_recent_keyset_handles_duplicate_timestamps_without_skip_or_duplicate(
    client: TestClient, api_db: Session
) -> None:
    timestamp = datetime(2026, 7, 30, 12, tzinfo=UTC)
    for index in range(5):
        _add_item(
            api_db,
            learner_key="learner_alpha",
            headword=f"mot {index}",
            created_at=timestamp if index < 4 else timestamp - timedelta(seconds=1),
        )
    api_db.commit()

    seen: list[int] = []
    cursor: str | None = None
    for page_number in range(3):
        params = {"sort": "recent", "limit": 2}
        if cursor:
            params["cursor"] = cursor
        response = client.get("/api/vocab", params=params, headers=_headers())
        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == (2 if page_number < 2 else 1)
        assert body["total"] == 5
        seen.extend(item["id"] for item in body["items"])
        cursor = body["next_cursor"]
        assert (cursor is not None) is (page_number < 2)

    assert len(seen) == len(set(seen)) == 5
    assert seen == [4, 3, 2, 1, 5]


def test_list_alphabetical_keyset_handles_duplicate_keys_without_skip_or_duplicate(
    client: TestClient, api_db: Session
) -> None:
    api_db.execute(text("DROP INDEX uq_vocab_anon_word"))
    for headword, normalized in [
        ("A", "same"),
        ("B", "same"),
        ("C", "same"),
        ("D", "z"),
    ]:
        _add_item(
            api_db,
            learner_key="learner_alpha",
            headword=headword,
            normalized_headword=normalized,
        )
    api_db.commit()

    first = client.get(
        "/api/vocab",
        params={"sort": "alphabetical", "limit": 2},
        headers=_headers(),
    )
    second = client.get(
        "/api/vocab",
        params={
            "sort": "alphabetical",
            "limit": 2,
            "cursor": first.json()["next_cursor"],
        },
        headers=_headers(),
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["next_cursor"] is not None
    assert second.json()["next_cursor"] is None
    ids = [item["id"] for item in first.json()["items"] + second.json()["items"]]
    assert len(ids) == len(set(ids)) == 4
    assert ids == sorted(ids)


def test_list_rejects_cursor_bound_to_different_filter(
    client: TestClient, api_db: Session
) -> None:
    for index in range(2):
        _add_item(api_db, learner_key="learner_alpha", headword=f"mot {index}")
    api_db.commit()
    first = client.get(
        "/api/vocab", params={"language": "fr", "limit": 1}, headers=_headers()
    )

    response = client.get(
        "/api/vocab",
        params={"language": "ru", "limit": 1, "cursor": first.json()["next_cursor"]},
        headers=_headers(),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "invalid cursor"}


@pytest.mark.parametrize(
    "cursor",
    [
        "not+base64!",
        encode_recent_cursor(
            language="ru",
            q="",
            last_created_at=datetime(2026, 7, 30, 12, tzinfo=UTC),
            last_id=1,
        ),
    ],
)
def test_list_invalid_cursor_executes_zero_database_statements(
    cursor: str, api_db: Session
) -> None:
    service = importlib.import_module("app.vocab.service")
    statements = 0

    def _count_statement(*_args: object, **_kwargs: object) -> None:
        nonlocal statements
        statements += 1

    engine = api_db.get_bind()
    event.listen(engine, "before_cursor_execute", _count_statement)
    try:
        with pytest.raises(InvalidCursor):
            service.list_vocab(
                api_db,
                identity=LearnerIdentity("learner_alpha"),
                language="fr",
                q="",
                sort="recent",
                limit=50,
                cursor=cursor,
            )
    finally:
        event.remove(engine, "before_cursor_execute", _count_statement)

    assert statements == 0


def test_list_includes_source_and_preserves_item_after_source_deletion(
    client: TestClient, api_db: Session
) -> None:
    source = Source(
        language="fr",
        provider="youtube",
        provider_id="source-1",
        url="https://example.test/video",
        title="Source",
    )
    api_db.add(source)
    api_db.flush()
    lesson = Lesson(
        source_id=source.id,
        language="fr",
        title="A lesson",
    )
    api_db.add(lesson)
    api_db.flush()
    unit = ListeningUnit(
        lesson_id=lesson.id,
        idx=3,
        start_s=0,
        end_s=10,
        text="Bonjour",
    )
    api_db.add(unit)
    api_db.flush()
    sourced = _add_item(
        api_db, learner_key="learner_alpha", headword="bonjour", unit_id=unit.id
    )
    sourceless = _add_item(
        api_db, learner_key="learner_alpha", headword="ailleurs", unit_id=None
    )
    api_db.commit()

    response = client.get("/api/vocab", headers=_headers())
    by_id = {item["id"]: item for item in response.json()["items"]}
    assert by_id[sourced.id]["source"] == {
        "lesson_id": lesson.id,
        "lesson_title": "A lesson",
        "unit_id": unit.id,
        "unit_index": 3,
    }
    assert by_id[sourceless.id]["source"] is None

    api_db.delete(unit)
    api_db.commit()
    response = client.get("/api/vocab", headers=_headers())
    by_id = {item["id"]: item for item in response.json()["items"]}
    assert by_id[sourced.id]["source"] is None
    assert set(by_id) == {sourced.id, sourceless.id}


def test_saved_keys_returns_only_owner_language_and_minimal_shape(
    client: TestClient, api_db: Session
) -> None:
    owned = _add_item(api_db, learner_key="learner_alpha", headword="Écouter")
    _add_item(api_db, learner_key="learner_beta", headword="other")
    _add_item(api_db, learner_key="learner_alpha", language="ru", headword="слово")
    api_db.commit()

    response = client.get(
        "/api/vocab/saved-keys", params={"language": "fr"}, headers=_headers()
    )

    assert response.status_code == 200
    assert response.json() == {
        "language": "fr",
        "items": [{"id": owned.id, "normalized_headword": "écouter"}],
    }


@pytest.mark.parametrize(
    ("path", "params", "status"),
    [
        ("/api/vocab", {"language": "xx"}, 400),
        ("/api/vocab", {"sort": "wrong"}, 422),
        ("/api/vocab", {"limit": 0}, 422),
        ("/api/vocab", {"limit": 101}, 422),
        ("/api/vocab", {"q": "x" * 129}, 422),
        ("/api/vocab", {"language": "x" * 9}, 422),
        ("/api/vocab/saved-keys", {}, 422),
        ("/api/vocab/saved-keys", {"language": "xx"}, 400),
        ("/api/vocab/saved-keys", {"language": "x" * 9}, 422),
    ],
)
def test_list_and_saved_keys_query_validation(
    path: str, params: dict[str, Any], status: int, client: TestClient
) -> None:
    response = client.get(path, params=params, headers=_headers())

    assert response.status_code == status


@pytest.mark.parametrize("path", ["/api/vocab", "/api/vocab/saved-keys"])
def test_list_unsupported_language_error_does_not_echo_input(
    path: str, client: TestClient
) -> None:
    unsupported = "secretx"

    response = client.get(path, params={"language": unsupported}, headers=_headers())

    assert response.status_code == 400
    assert response.json() == {"detail": "unsupported language"}
    assert unsupported not in response.text


def test_saved_keys_static_route_is_not_captured_as_item_id(client: TestClient) -> None:
    response = client.get(
        "/api/vocab/saved-keys", params={"language": "fr"}, headers=_headers()
    )

    assert response.status_code == 200


@pytest.mark.parametrize(
    "database_error",
    [
        OperationalError(
            "postgresql://admin:fake-password@db.example.test/private",
            {"password": "fake-password"},
            Exception("postgresql://admin:fake-password@db.example.test/private"),
        ),
        InterfaceError(
            "postgresql://admin:fake-password@db.example.test/private",
            {"password": "fake-password"},
            Exception("postgresql://admin:fake-password@db.example.test/private"),
        ),
        SQLAlchemyTimeoutError(
            "pool timeout postgresql://admin:fake-password@db.example.test/private"
        ),
    ],
    ids=["operational", "interface", "pool-timeout"],
)
def test_list_database_error_response_and_logs_never_leak_credentials(
    database_error: Exception,
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    router = importlib.import_module("app.routers.vocab")
    secret_url = "postgresql://admin:fake-password@db.example.test/private"

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise database_error

    monkeypatch.setattr(router, "list_vocab_service", _raise)
    caplog.set_level(logging.ERROR)

    response = client.get("/api/vocab", headers=_headers())

    assert response.status_code == 503
    assert response.json() == {"detail": "database temporarily unavailable"}
    combined = response.text + caplog.text + repr(response.headers)
    assert "fake-password" not in combined
    assert secret_url not in combined
    assert response.headers["x-correlation-id"]


def test_escape_like_wildcards_exactly() -> None:
    service = importlib.import_module("app.vocab.service")

    assert service.escape_like(r"a\b%c_d") == r"a\\b\%c\_d"


@pytest.mark.parametrize("dialect", [sqlite.dialect(), postgresql.dialect()])
def test_list_search_statement_compiles_portably_without_database_lower(
    dialect: Any,
) -> None:
    service = importlib.import_module("app.vocab.service")
    statement = service.build_vocab_select(
        identity=LearnerIdentity("learner_alpha"),
        language="fr",
        normalized_q=r"a\b%c_d",
    )

    compiled = statement.compile(dialect=dialect)
    sql = str(compiled).lower()
    assert "normalized_headword" in sql
    assert "normalized_gloss" in sql
    assert " escape " in sql
    assert "lower(" not in sql
    assert compiled.params


def _validate_schema_name(schema: str) -> None:
    if re.fullmatch(r"vocab_test_[0-9a-f]{32}", schema) is None:
        raise ValueError("unsafe PostgreSQL test schema name")


@pytest.fixture(params=["sqlite", "postgresql"])
def search_db(request: pytest.FixtureRequest) -> Generator[Session, None, None]:
    if request.param == "sqlite":
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        session = Session(engine)
        try:
            yield session
        finally:
            session.close()
            engine.dispose()
        return

    postgres_url = os.environ.get("TEST_POSTGRES_URL")
    if not postgres_url:
        pytest.skip("TEST_POSTGRES_URL is not set")
    schema = f"vocab_test_{uuid.uuid4().hex}"
    engine = create_engine(postgres_url)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        _validate_schema_name(schema)
        connection.exec_driver_sql(f"CREATE SCHEMA {schema}")
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql(f"SET search_path TO {schema}")
            alembic_config = Config(str(BACKEND_ROOT / "alembic.ini"))
            alembic_config.attributes["connection"] = connection
            command.upgrade(alembic_config, "head")
            session = Session(connection)
            try:
                yield session
            finally:
                session.close()
    finally:
        try:
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as cleanup:
                _validate_schema_name(schema)
                cleanup.exec_driver_sql(f"DROP SCHEMA {schema} CASCADE")
        finally:
            engine.dispose()


def test_postgres_search_fixture_uses_alembic_not_model_metadata() -> None:
    fixture_source = inspect.getsource(search_db)
    postgres_branch = fixture_source.split('postgres_url =', maxsplit=1)[1]

    assert "command.upgrade" in postgres_branch
    assert "Base.metadata.create_all" not in postgres_branch
    assert "assert re.fullmatch" not in inspect.getsource(_validate_schema_name)


def test_list_search_integration_cross_dialect(search_db: Session) -> None:
    service = importlib.import_module("app.vocab.service")
    matched = _add_item(
        search_db,
        learner_key="learner_alpha",
        headword="100% exact",
        normalized_headword="100% exact",
    )
    _add_item(
        search_db,
        learner_key="learner_alpha",
        headword="ordinary",
        normalized_headword="ordinary",
    )
    search_db.commit()

    result = service.list_vocab(
        search_db,
        identity=LearnerIdentity("learner_alpha"),
        language=None,
        q="%",
        sort="recent",
        limit=50,
        cursor=None,
    )

    assert [item.id for item in result.items] == [matched.id]


def _add_unit(db: Session, *, language: str = "fr") -> ListeningUnit:
    source = Source(
        language=language,
        provider="youtube",
        provider_id=f"source-{uuid.uuid4()}",
        url="https://example.test/video",
        title="Source",
    )
    db.add(source)
    db.flush()
    lesson = Lesson(source_id=source.id, language=language, title="A lesson")
    db.add(lesson)
    db.flush()
    unit = ListeningUnit(
        lesson_id=lesson.id,
        idx=3,
        start_s=0,
        end_s=10,
        text="Bonjour",
    )
    db.add(unit)
    db.flush()
    return unit


@pytest.mark.parametrize(
    "payload",
    [
        {"language": "fr", "headword": "   \t\n"},
        {"language": "fr", "headword": "x" * 129},
        {"language": "fr", "headword": "ß" * 128},
        {"language": "secretx", "headword": "mot"},
    ],
)
def test_save_rejects_invalid_normalized_headword_or_language_without_write(
    payload: dict[str, Any], client: TestClient, api_db: Session
) -> None:
    response = client.post("/api/vocab", json=payload, headers=_headers())

    assert response.status_code == 422
    assert api_db.scalar(text("SELECT count(*) FROM vocab_items")) == 0
    assert "secretx" not in response.text


def test_save_rejects_missing_or_wrong_language_unit_before_write(
    client: TestClient, api_db: Session
) -> None:
    russian_unit = _add_unit(api_db, language="ru")
    api_db.commit()

    missing = client.post(
        "/api/vocab",
        json={"language": "fr", "headword": "mot", "unit_id": 999999},
        headers=_headers(),
    )
    mismatch = client.post(
        "/api/vocab",
        json={"language": "fr", "headword": "mot", "unit_id": russian_unit.id},
        headers=_headers(),
    )

    assert missing.status_code == mismatch.status_code == 422
    assert missing.json() == mismatch.json() == {"detail": "invalid vocabulary input"}
    assert api_db.scalar(text("SELECT count(*) FROM vocab_items")) == 0


def test_save_returns_full_item_and_case_apostrophe_repeat_is_fill_only(
    client: TestClient, api_db: Session
) -> None:
    unit = _add_unit(api_db)
    api_db.commit()
    first = client.post(
        "/api/vocab",
        json={"language": "FR-ca", "headword": "  L\u2019ÉCOLE  "},
        headers=_headers(),
    )
    assert first.status_code == 200
    first_body = first.json()
    item = api_db.get(VocabItem, first_body["id"])
    assert item is not None
    original_updated_at = item.updated_at
    item.reps = 7
    item.lapses = 2
    item.ease = 1.9
    item.interval_days = 11.0
    item.due_at = datetime(2026, 8, 10, tzinfo=UTC)
    api_db.commit()

    second = client.post(
        "/api/vocab",
        json={
            "language": "fr",
            "headword": "l'école",
            "gloss_en": "school",
            "example": "à l'école",
            "unit_id": unit.id,
        },
        headers=_headers(),
    )
    assert second.status_code == 200
    assert second.json()["id"] == first_body["id"]
    assert second.json()["headword"] == "L\u2019ÉCOLE"
    assert second.json()["normalized_headword"] == "l'école"
    assert second.json()["gloss_en"] == "school"
    assert second.json()["example"] == "à l'école"
    assert second.json()["source"]["unit_id"] == unit.id
    api_db.refresh(item)
    assert (
        item.reps,
        item.lapses,
        item.ease,
        item.interval_days,
        item.due_at.replace(tzinfo=UTC),
    ) == (
        7,
        2,
        1.9,
        11.0,
        datetime(2026, 8, 10, tzinfo=UTC),
    )
    assert item.updated_at > original_updated_at

    filled_updated_at = item.updated_at
    third = client.post(
        "/api/vocab",
        json={
            "language": "fr",
            "headword": "L'ÉCOLE",
            "gloss_en": " ",
            "example": "",
        },
        headers=_headers(),
    )
    assert third.status_code == 200
    api_db.refresh(item)
    assert (item.gloss_en, item.example, item.unit_id) == (
        "school",
        "à l'école",
        unit.id,
    )
    assert item.updated_at == filled_updated_at


def test_save_recovers_only_exact_sqlite_owner_unique_race_and_merges(
    api_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = importlib.import_module("app.vocab.service")
    from app.schemas import VocabSaveIn

    original_commit = api_db.commit
    raced = False

    def _racing_commit() -> None:
        nonlocal raced
        if raced:
            original_commit()
            return
        raced = True
        api_db.rollback()
        _add_item(
            api_db,
            learner_key="learner_alpha",
            headword="Winner display",
            normalized_headword="l'école",
            gloss=None,
        )
        original_commit()
        raise IntegrityError(
            "INSERT INTO vocab_items ...",
            {},
            sqlite3.IntegrityError(
                "UNIQUE constraint failed: vocab_items.learner_key, "
                "vocab_items.language, vocab_items.normalized_headword"
            ),
        )

    monkeypatch.setattr(api_db, "commit", _racing_commit)
    result = service.save_vocab(
        api_db,
        identity=LearnerIdentity("learner_alpha"),
        payload=VocabSaveIn(
            language="fr",
            headword="L\u2019ÉCOLE",
            gloss_en="school",
            example="example",
        ),
    )

    assert result.headword == "Winner display"
    assert result.gloss_en == "school"
    assert result.example == "example"
    assert api_db.scalar(text("SELECT count(*) FROM vocab_items")) == 1


def test_save_concurrent_fill_does_not_overwrite_winning_value(
    api_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = importlib.import_module("app.vocab.service")
    from app.schemas import VocabSaveIn

    item = _add_item(
        api_db,
        learner_key="learner_alpha",
        headword="mot",
        gloss=None,
    )
    api_db.commit()
    original_execute = api_db.execute
    injected = False

    def _interleave(statement: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal injected
        if not injected and statement.__class__.__name__ == "Update":
            injected = True
            original_execute(
                text(
                    "UPDATE vocab_items SET gloss_en = 'winner', "
                    "normalized_gloss = 'winner' WHERE id = :item_id"
                ),
                {"item_id": item.id},
            )
            api_db.commit()
        return original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(api_db, "execute", _interleave)
    result = service.save_vocab(
        api_db,
        identity=LearnerIdentity("learner_alpha"),
        payload=VocabSaveIn(
            language="fr",
            headword="MOT",
            gloss_en="loser",
            example="incoming example",
        ),
    )

    assert injected
    assert result.gloss_en == "winner"
    assert result.example == "incoming example"


def test_save_reraises_unrelated_integrity_error(
    api_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = importlib.import_module("app.vocab.service")
    from app.schemas import VocabSaveIn

    unrelated = IntegrityError(
        "INSERT INTO vocab_items ...",
        {},
        sqlite3.IntegrityError("FOREIGN KEY constraint failed"),
    )

    def _raise() -> None:
        raise unrelated

    monkeypatch.setattr(api_db, "commit", _raise)
    with pytest.raises(IntegrityError) as caught:
        service.save_vocab(
            api_db,
            identity=LearnerIdentity("learner_alpha"),
            payload=VocabSaveIn(language="fr", headword="mot"),
        )
    assert caught.value is unrelated


def test_save_unrelated_integrity_error_is_safely_mapped(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    router = importlib.import_module("app.routers.vocab")
    secret = "postgresql://admin:fake-password@db.example.test/private"

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise IntegrityError(secret, {}, sqlite3.IntegrityError(secret))

    monkeypatch.setattr(router, "save_vocab_service", _raise)
    response = client.post(
        "/api/vocab",
        json={"language": "fr", "headword": "mot"},
        headers=_headers(),
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "database temporarily unavailable"}
    assert "fake-password" not in response.text


def test_edit_allows_clear_preserves_omissions_and_scopes_update(
    client: TestClient, api_db: Session
) -> None:
    owned = _add_item(
        api_db,
        learner_key="learner_alpha",
        headword="mot",
        gloss="Meaning",
        normalized_gloss="meaning",
    )
    owned.example = "Example"
    foreign = _add_item(api_db, learner_key="learner_beta", headword="foreign")
    api_db.commit()
    before = owned.updated_at
    statements: list[str] = []

    def _capture(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
        if statement.lstrip().upper().startswith("UPDATE"):
            statements.append(statement.lower())

    engine = api_db.get_bind()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        response = client.patch(
            f"/api/vocab/{owned.id}",
            json={"gloss_en": None},
            headers=_headers(),
        )
        foreign_response = client.patch(
            f"/api/vocab/{foreign.id}",
            json={"example": "stolen"},
            headers=_headers(),
        )
        missing_response = client.patch(
            "/api/vocab/999999",
            json={"example": "missing"},
            headers=_headers(),
        )
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert response.status_code == 200
    assert response.json()["gloss_en"] is None
    assert response.json()["example"] == "Example"
    api_db.refresh(owned)
    assert owned.normalized_gloss == ""
    assert owned.updated_at.replace(tzinfo=UTC) > before
    assert foreign_response.status_code == missing_response.status_code == 404
    assert foreign_response.json() == missing_response.json() == {
        "detail": "vocabulary item not found"
    }
    assert statements
    assert all("vocab_items.id" in sql for sql in statements)
    assert all("vocab_items.learner_key" in sql for sql in statements)
    assert all("vocab_items.user_id is null" in sql for sql in statements)


def test_delete_is_permanent_scoped_and_indistinguishable(
    client: TestClient, api_db: Session
) -> None:
    owned = _add_item(api_db, learner_key="learner_alpha", headword="mot")
    foreign = _add_item(api_db, learner_key="learner_beta", headword="foreign")
    api_db.commit()
    statements: list[str] = []

    def _capture(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
        if statement.lstrip().upper().startswith("DELETE"):
            statements.append(statement.lower())

    engine = api_db.get_bind()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        response = client.delete(f"/api/vocab/{owned.id}", headers=_headers())
        foreign_response = client.delete(
            f"/api/vocab/{foreign.id}", headers=_headers()
        )
        missing_response = client.delete("/api/vocab/999999", headers=_headers())
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert response.status_code == 204
    assert response.content == b""
    assert api_db.get(VocabItem, owned.id) is None
    assert foreign_response.status_code == missing_response.status_code == 404
    assert foreign_response.json() == missing_response.json() == {
        "detail": "vocabulary item not found"
    }
    assert statements
    assert all("vocab_items.id" in sql for sql in statements)
    assert all("vocab_items.learner_key" in sql for sql in statements)
    assert all("vocab_items.user_id is null" in sql for sql in statements)
