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
from app.identity import LearnerIdentity, get_learner_identity
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


@pytest.mark.parametrize(
    ("headword", "normalized"),
    [
        ("X", "x"),
        (" X ", "x"),
        ("É" * 128, "é" * 128),
    ],
)
def test_save_accepts_normalized_boundaries_and_raw_128(
    headword: str, normalized: str, client: TestClient
) -> None:
    response = client.post(
        "/api/vocab",
        json={"language": "fr", "headword": headword},
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.json()["normalized_headword"] == normalized
    assert len(response.json()["normalized_headword"]) in {1, 128}


@pytest.mark.parametrize("invalid_source", ["missing", "mismatch"])
def test_repeat_save_validates_source_before_any_fill(
    invalid_source: str, client: TestClient, api_db: Session
) -> None:
    existing = _add_item(
        api_db,
        learner_key="learner_alpha",
        headword="mot",
        gloss=None,
        unit_id=None,
    )
    existing.example = None
    invalid_unit_id = 999999
    if invalid_source == "mismatch":
        invalid_unit_id = _add_unit(api_db, language="ru").id
    api_db.commit()
    original_updated_at = existing.updated_at

    response = client.post(
        "/api/vocab",
        json={
            "language": "fr",
            "headword": "MOT",
            "gloss_en": "meaning",
            "example": "example",
            "unit_id": invalid_unit_id,
        },
        headers=_headers(),
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "invalid vocabulary input"}
    assert not api_db.in_transaction()
    api_db.refresh(existing)
    assert existing.gloss_en is None
    assert existing.normalized_gloss == ""
    assert existing.example is None
    assert existing.unit_id is None
    assert existing.updated_at.replace(tzinfo=UTC) == original_updated_at


def _assert_exact_vocab_output(
    body: dict[str, Any],
    *,
    item: VocabItem,
    source: dict[str, Any] | None,
) -> None:
    assert set(body) == {
        "id",
        "language",
        "headword",
        "normalized_headword",
        "gloss_en",
        "example",
        "zipf",
        "reps",
        "due_at",
        "created_at",
        "updated_at",
        "source",
    }
    assert body == {
        "id": item.id,
        "language": item.language,
        "headword": item.headword,
        "normalized_headword": item.normalized_headword,
        "gloss_en": item.gloss_en,
        "example": item.example,
        "zipf": item.zipf,
        "reps": item.reps,
        "due_at": item.due_at.isoformat() if item.due_at else None,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
        "source": source,
    }
    datetime.fromisoformat(body["created_at"])
    datetime.fromisoformat(body["updated_at"])


def test_save_new_and_repeat_return_exact_full_output(
    client: TestClient, api_db: Session
) -> None:
    unit = _add_unit(api_db)
    api_db.commit()

    created = client.post(
        "/api/vocab",
        json={"language": "fr", "headword": "  École  "},
        headers=_headers(),
    )
    assert created.status_code == 200
    item = api_db.get(VocabItem, created.json()["id"])
    assert item is not None
    _assert_exact_vocab_output(created.json(), item=item, source=None)
    assert created.json()["reps"] == 0
    assert created.json()["due_at"] is None

    repeated = client.post(
        "/api/vocab",
        json={
            "language": "fr",
            "headword": "ÉCOLE",
            "gloss_en": "school",
            "example": "une école",
            "unit_id": unit.id,
        },
        headers=_headers(),
    )
    assert repeated.status_code == 200
    api_db.refresh(item)
    _assert_exact_vocab_output(
        repeated.json(),
        item=item,
        source={
            "lesson_id": unit.lesson_id,
            "lesson_title": "A lesson",
            "unit_id": unit.id,
            "unit_index": 3,
        },
    )


class _FakePostgresDiagnostic:
    def __init__(self, constraint_name: str) -> None:
        self.constraint_name = constraint_name


class _FakePostgresError:
    def __init__(self, sqlstate: str, constraint_name: str) -> None:
        self.sqlstate = sqlstate
        self.diag = _FakePostgresDiagnostic(constraint_name)


@pytest.mark.parametrize(
    ("identity", "message"),
    [
        (
            LearnerIdentity("learner_alpha"),
            (
                "UNIQUE constraint failed: vocab_items.learner_key, "
                "vocab_items.language, vocab_items.normalized_headword"
            ),
        ),
        (
            LearnerIdentity(
                "learner_alpha", "00000000-0000-0000-0000-000000000001"
            ),
            (
                "UNIQUE constraint failed: vocab_items.user_id, "
                "vocab_items.language, vocab_items.normalized_headword"
            ),
        ),
    ],
)
def test_unique_race_classifier_accepts_exact_sqlite_owner_columns(
    identity: LearnerIdentity, message: str
) -> None:
    service = importlib.import_module("app.vocab.service")
    error = IntegrityError("INSERT", {}, sqlite3.IntegrityError(message))

    assert service._is_owner_unique_race(error, identity)


@pytest.mark.parametrize(
    ("identity", "constraint_name"),
    [
        (LearnerIdentity("learner_alpha"), "uq_vocab_anon_word"),
        (
            LearnerIdentity(
                "learner_alpha", "00000000-0000-0000-0000-000000000001"
            ),
            "uq_vocab_user_word",
        ),
    ],
)
def test_unique_race_classifier_accepts_exact_postgres_owner_constraint(
    identity: LearnerIdentity, constraint_name: str
) -> None:
    service = importlib.import_module("app.vocab.service")
    error = IntegrityError(
        "INSERT",
        {},
        _FakePostgresError("23505", constraint_name),
    )

    assert service._is_owner_unique_race(error, identity)


@pytest.mark.parametrize(
    ("identity", "original"),
    [
        (
            LearnerIdentity("learner_alpha"),
            _FakePostgresError("23505", "uq_vocab_user_word"),
        ),
        (
            LearnerIdentity("learner_alpha"),
            _FakePostgresError("23503", "uq_vocab_anon_word"),
        ),
        (
            LearnerIdentity("learner_alpha"),
            sqlite3.IntegrityError(
                "UNIQUE constraint failed: vocab_items.language, "
                "vocab_items.learner_key, vocab_items.normalized_headword"
            ),
        ),
        (
            LearnerIdentity("learner_alpha"),
            sqlite3.IntegrityError(
                "UNIQUE constraint failed: vocab_items.learner_key, "
                "vocab_items.language"
            ),
        ),
        (
            LearnerIdentity("learner_alpha"),
            sqlite3.IntegrityError(
                "UNIQUE constraint failed: vocab_items.learner_key, "
                "vocab_items.language, vocab_items.normalized_headword_extra"
            ),
        ),
        (
            LearnerIdentity("learner_alpha"),
            sqlite3.IntegrityError("FOREIGN KEY constraint failed"),
        ),
    ],
)
def test_unique_race_classifier_rejects_near_misses(
    identity: LearnerIdentity, original: BaseException
) -> None:
    service = importlib.import_module("app.vocab.service")
    error = IntegrityError("INSERT", {}, original)

    assert not service._is_owner_unique_race(error, identity)


@pytest.mark.parametrize(
    "original",
    [
        _FakePostgresError("23505", "uq_vocab_user_word"),
        _FakePostgresError("23503", "uq_vocab_anon_word"),
        sqlite3.IntegrityError(
            "UNIQUE constraint failed: vocab_items.language, "
            "vocab_items.learner_key, vocab_items.normalized_headword"
        ),
        sqlite3.IntegrityError(
            "UNIQUE constraint failed: vocab_items.learner_key, "
            "vocab_items.language"
        ),
        sqlite3.IntegrityError(
            "UNIQUE constraint failed: vocab_items.learner_key, "
            "vocab_items.language, vocab_items.normalized_headword_extra"
        ),
        sqlite3.IntegrityError("FOREIGN KEY constraint failed"),
    ],
)
def test_save_reraises_every_unrecognized_integrity_error(
    original: BaseException,
    api_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = importlib.import_module("app.vocab.service")
    from app.schemas import VocabSaveIn

    error = IntegrityError("INSERT", {}, original)

    def _raise() -> None:
        raise error

    monkeypatch.setattr(api_db, "commit", _raise)
    with pytest.raises(IntegrityError) as caught:
        service.save_vocab(
            api_db,
            identity=LearnerIdentity("learner_alpha"),
            payload=VocabSaveIn(language="fr", headword="mot"),
        )

    assert caught.value is error


def test_authenticated_save_uses_user_id_across_learner_key_changes(
    api_db: Session,
) -> None:
    service = importlib.import_module("app.vocab.service")
    from app.schemas import VocabSaveIn

    user_id = "00000000-0000-0000-0000-000000000001"
    owned = _add_item(
        api_db,
        learner_key="old_key",
        user_id=user_id,
        headword="mot",
        gloss=None,
    )
    anonymous = _add_item(
        api_db,
        learner_key="learner_current",
        headword="anonymous display",
        normalized_headword="mot",
    )
    foreign = _add_item(
        api_db,
        learner_key="learner_current",
        user_id="00000000-0000-0000-0000-000000000002",
        headword="foreign display",
        normalized_headword="mot",
    )
    api_db.commit()

    result = service.save_vocab(
        api_db,
        identity=LearnerIdentity("learner_current", user_id),
        payload=VocabSaveIn(
            language="fr",
            headword="MOT",
            gloss_en="meaning",
        ),
    )

    assert result.id == owned.id
    assert result.id not in {anonymous.id, foreign.id}
    assert result.gloss_en == "meaning"
    assert api_db.scalar(text("SELECT count(*) FROM vocab_items")) == 3


def test_authenticated_unique_race_reselects_only_winning_user_row(
    api_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = importlib.import_module("app.vocab.service")
    from app.schemas import VocabSaveIn

    user_id = "00000000-0000-0000-0000-000000000001"
    anonymous = _add_item(
        api_db,
        learner_key="learner_current",
        headword="anonymous display",
        normalized_headword="mot",
    )
    foreign = _add_item(
        api_db,
        learner_key="learner_current",
        user_id="00000000-0000-0000-0000-000000000002",
        headword="foreign display",
        normalized_headword="mot",
    )
    api_db.commit()
    original_commit = api_db.commit
    raced = False
    winner: VocabItem | None = None

    def _racing_commit() -> None:
        nonlocal raced, winner
        if raced:
            original_commit()
            return
        raced = True
        api_db.rollback()
        winner = _add_item(
            api_db,
            learner_key="winner_old_key",
            user_id=user_id,
            headword="winner display",
            normalized_headword="mot",
            gloss=None,
        )
        original_commit()
        raise IntegrityError(
            "INSERT",
            {},
            sqlite3.IntegrityError(
                "UNIQUE constraint failed: vocab_items.user_id, "
                "vocab_items.language, vocab_items.normalized_headword"
            ),
        )

    monkeypatch.setattr(api_db, "commit", _racing_commit)
    result = service.save_vocab(
        api_db,
        identity=LearnerIdentity("learner_current", user_id),
        payload=VocabSaveIn(
            language="fr",
            headword="MOT",
            gloss_en="meaning",
        ),
    )

    assert winner is not None
    assert result.id == winner.id
    assert result.id not in {anonymous.id, foreign.id}
    assert result.headword == "winner display"
    assert result.gloss_en == "meaning"


def test_edit_empty_clear_and_nonempty_gloss_recompute_normalized_gloss(
    client: TestClient, api_db: Session
) -> None:
    item = _add_item(
        api_db,
        learner_key="learner_alpha",
        headword="mot",
        gloss="old",
        normalized_gloss="old",
    )
    api_db.commit()

    updated = client.patch(
        f"/api/vocab/{item.id}",
        json={"gloss_en": "  ÉCOUTER  "},
        headers=_headers(),
    )
    assert updated.status_code == 200
    api_db.refresh(item)
    assert item.gloss_en == "  ÉCOUTER  "
    assert item.normalized_gloss == "écouter"

    cleared = client.patch(
        f"/api/vocab/{item.id}",
        json={"gloss_en": ""},
        headers=_headers(),
    )
    assert cleared.status_code == 200
    assert cleared.json()["gloss_en"] == ""
    api_db.refresh(item)
    assert item.gloss_en == ""
    assert item.normalized_gloss == ""


def test_authenticated_edit_delete_sql_and_404_are_user_scoped(
    client: TestClient, api_db: Session
) -> None:
    user_id = "00000000-0000-0000-0000-000000000001"
    identity = LearnerIdentity("learner_current", user_id)
    edited = _add_item(
        api_db,
        learner_key="old_key",
        user_id=user_id,
        headword="edit me",
    )
    deleted = _add_item(
        api_db,
        learner_key="old_key",
        user_id=user_id,
        headword="delete me",
    )
    foreign = _add_item(
        api_db,
        learner_key="learner_current",
        user_id="00000000-0000-0000-0000-000000000002",
        headword="foreign",
    )
    api_db.commit()
    updates: list[str] = []
    deletes: list[str] = []

    def _capture(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
        sql = statement.lstrip().lower()
        if sql.startswith("update"):
            updates.append(sql)
        elif sql.startswith("delete"):
            deletes.append(sql)

    app.dependency_overrides[get_learner_identity] = lambda: identity
    engine = api_db.get_bind()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        edit_response = client.patch(
            f"/api/vocab/{edited.id}",
            json={"example": "edited"},
        )
        edit_foreign = client.patch(
            f"/api/vocab/{foreign.id}",
            json={"example": "forbidden"},
        )
        edit_missing = client.patch(
            "/api/vocab/999998",
            json={"example": "missing"},
        )
        delete_response = client.delete(f"/api/vocab/{deleted.id}")
        delete_foreign = client.delete(f"/api/vocab/{foreign.id}")
        delete_missing = client.delete("/api/vocab/999999")
    finally:
        event.remove(engine, "before_cursor_execute", _capture)
        app.dependency_overrides.pop(get_learner_identity, None)

    assert edit_response.status_code == 200
    assert delete_response.status_code == 204
    assert edit_foreign.status_code == edit_missing.status_code == 404
    assert edit_foreign.json() == edit_missing.json() == {
        "detail": "vocabulary item not found"
    }
    assert delete_foreign.status_code == delete_missing.status_code == 404
    assert delete_foreign.json() == delete_missing.json() == {
        "detail": "vocabulary item not found"
    }
    assert updates and deletes
    for statement in updates + deletes:
        assert "vocab_items.id" in statement
        assert "vocab_items.user_id" in statement
        assert "learner_key" not in statement


def _operational_write_error() -> OperationalError:
    return OperationalError("write failed", {}, sqlite3.OperationalError("write failed"))


@pytest.mark.parametrize("error_kind", ["operational", "integrity"])
@pytest.mark.parametrize("operation", ["save", "fill", "edit", "delete"])
def test_mutation_write_error_rolls_back_and_session_remains_usable(
    operation: str,
    error_kind: str,
    api_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = importlib.import_module("app.vocab.service")
    from app.schemas import VocabEditIn, VocabSaveIn

    identity = LearnerIdentity("learner_alpha")
    item: VocabItem | None = None
    if operation != "save":
        item = _add_item(
            api_db,
            learner_key=identity.learner_key,
            headword="mot",
            gloss=None,
        )
        api_db.commit()

    original_rollback = api_db.rollback
    rollback_calls = 0

    def _rollback() -> None:
        nonlocal rollback_calls
        rollback_calls += 1
        original_rollback()

    monkeypatch.setattr(api_db, "rollback", _rollback)
    error: IntegrityError | OperationalError
    if error_kind == "integrity":
        error = IntegrityError(
            "write failed",
            {},
            sqlite3.IntegrityError("CHECK constraint failed: unrelated"),
        )
    else:
        error = _operational_write_error()
    if operation == "save":
        original_commit = api_db.commit
        failed = False

        def _fail_commit_once() -> None:
            nonlocal failed
            if not failed:
                failed = True
                raise error
            original_commit()

        monkeypatch.setattr(api_db, "commit", _fail_commit_once)
    else:
        original_execute = api_db.execute
        failed = False
        expected_statement = "Delete" if operation == "delete" else "Update"

        def _fail_execute_once(statement: Any, *args: Any, **kwargs: Any) -> Any:
            nonlocal failed
            if not failed and statement.__class__.__name__ == expected_statement:
                failed = True
                raise error
            return original_execute(statement, *args, **kwargs)

        monkeypatch.setattr(api_db, "execute", _fail_execute_once)

    with pytest.raises(type(error)) as caught:
        if operation == "save":
            service.save_vocab(
                api_db,
                identity=identity,
                payload=VocabSaveIn(language="fr", headword="new"),
            )
        elif operation == "fill":
            service.save_vocab(
                api_db,
                identity=identity,
                payload=VocabSaveIn(
                    language="fr",
                    headword="MOT",
                    gloss_en="meaning",
                ),
            )
        elif operation == "edit":
            assert item is not None
            service.edit_vocab(
                api_db,
                identity=identity,
                item_id=item.id,
                payload=VocabEditIn(example="example"),
            )
        else:
            assert item is not None
            service.delete_vocab(api_db, identity=identity, item_id=item.id)

    assert caught.value is error
    assert rollback_calls == 1
    assert api_db.scalar(text("SELECT 1")) == 1


@pytest.mark.parametrize("operation", ["save", "fill", "patch"])
def test_mutation_builds_response_snapshot_before_commit(
    operation: str,
    api_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = importlib.import_module("app.vocab.service")
    from app.schemas import VocabEditIn, VocabSaveIn

    identity = LearnerIdentity("learner_alpha")
    item: VocabItem | None = None
    if operation in {"fill", "patch"}:
        item = _add_item(
            api_db,
            learner_key=identity.learner_key,
            headword="mot",
            gloss=None,
        )
        api_db.commit()

    events: list[str] = []
    original_to_vocab_out = service.to_vocab_out
    original_commit = api_db.commit

    def _snapshot(row: Any) -> Any:
        events.append("snapshot")
        return original_to_vocab_out(row)

    def _commit() -> None:
        events.append("commit")
        original_commit()

    monkeypatch.setattr(service, "to_vocab_out", _snapshot)
    monkeypatch.setattr(api_db, "commit", _commit)
    if operation == "save":
        service.save_vocab(
            api_db,
            identity=identity,
            payload=VocabSaveIn(language="fr", headword="new"),
        )
    elif operation == "fill":
        service.save_vocab(
            api_db,
            identity=identity,
            payload=VocabSaveIn(
                language="fr",
                headword="MOT",
                gloss_en="meaning",
            ),
        )
    else:
        assert item is not None
        service.edit_vocab(
            api_db,
            identity=identity,
            item_id=item.id,
            payload=VocabEditIn(example="example"),
        )

    assert events.index("snapshot") < events.index("commit")


@pytest.mark.parametrize("operation", ["save", "fill", "patch"])
def test_commit_adjacent_delete_cannot_break_successful_response(
    operation: str,
    api_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = importlib.import_module("app.vocab.service")
    from app.schemas import VocabEditIn, VocabSaveIn

    identity = LearnerIdentity("learner_alpha")
    item: VocabItem | None = None
    if operation in {"fill", "patch"}:
        item = _add_item(
            api_db,
            learner_key=identity.learner_key,
            headword="mot",
            gloss=None,
        )
        api_db.commit()

    original_commit = api_db.commit
    original_execute = api_db.execute
    deleted = False

    def _commit_then_delete() -> None:
        nonlocal deleted
        original_commit()
        if not deleted:
            deleted = True
            original_execute(text("DELETE FROM vocab_items"))
            original_commit()

    monkeypatch.setattr(api_db, "commit", _commit_then_delete)
    if operation == "save":
        result = service.save_vocab(
            api_db,
            identity=identity,
            payload=VocabSaveIn(language="fr", headword="new"),
        )
        assert result.headword == "new"
    elif operation == "fill":
        result = service.save_vocab(
            api_db,
            identity=identity,
            payload=VocabSaveIn(
                language="fr",
                headword="MOT",
                gloss_en="meaning",
            ),
        )
        assert result.gloss_en == "meaning"
    else:
        assert item is not None
        result = service.edit_vocab(
            api_db,
            identity=identity,
            item_id=item.id,
            payload=VocabEditIn(example="example"),
        )
        assert result.id == item.id
        assert result.example == "example"

    assert deleted
    assert api_db.scalar(text("SELECT count(*) FROM vocab_items")) == 0


def test_recognized_race_with_missing_winner_retries_insert_once(
    api_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = importlib.import_module("app.vocab.service")
    from app.schemas import VocabSaveIn

    original_flush = api_db.flush
    flush_calls = 0
    race = IntegrityError(
        "INSERT",
        {},
        sqlite3.IntegrityError(
            "UNIQUE constraint failed: vocab_items.learner_key, "
            "vocab_items.language, vocab_items.normalized_headword"
        ),
    )

    def _race_then_flush(*args: Any, **kwargs: Any) -> None:
        nonlocal flush_calls
        if not api_db.new:
            original_flush(*args, **kwargs)
            return
        flush_calls += 1
        if flush_calls == 1:
            raise race
        original_flush(*args, **kwargs)

    monkeypatch.setattr(api_db, "flush", _race_then_flush)
    result = service.save_vocab(
        api_db,
        identity=LearnerIdentity("learner_alpha"),
        payload=VocabSaveIn(language="fr", headword="mot"),
    )

    assert flush_calls == 2
    assert result.headword == "mot"
    assert api_db.scalar(text("SELECT count(*) FROM vocab_items")) == 1


def test_recognized_race_retry_is_bounded_and_reraises_current_error(
    api_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = importlib.import_module("app.vocab.service")
    from app.schemas import VocabSaveIn

    errors = [
        IntegrityError(
            "INSERT first",
            {},
            sqlite3.IntegrityError(
                "UNIQUE constraint failed: vocab_items.learner_key, "
                "vocab_items.language, vocab_items.normalized_headword"
            ),
        ),
        IntegrityError(
            "INSERT second",
            {},
            sqlite3.IntegrityError(
                "UNIQUE constraint failed: vocab_items.learner_key, "
                "vocab_items.language, vocab_items.normalized_headword"
            ),
        ),
    ]
    flush_calls = 0

    def _always_race(*_args: Any, **_kwargs: Any) -> None:
        nonlocal flush_calls
        if not api_db.new:
            return
        error = errors[flush_calls]
        flush_calls += 1
        raise error

    monkeypatch.setattr(api_db, "flush", _always_race)
    with pytest.raises(IntegrityError) as caught:
        service.save_vocab(
            api_db,
            identity=LearnerIdentity("learner_alpha"),
            payload=VocabSaveIn(language="fr", headword="mot"),
        )

    assert flush_calls == 2
    assert caught.value is errors[1]
    assert api_db.scalar(text("SELECT 1")) == 1


def test_final_recognized_race_reselects_and_fills_present_winner(
    api_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = importlib.import_module("app.vocab.service")
    from app.schemas import VocabSaveIn

    errors = [
        IntegrityError(
            "INSERT first",
            {},
            sqlite3.IntegrityError(
                "UNIQUE constraint failed: vocab_items.learner_key, "
                "vocab_items.language, vocab_items.normalized_headword"
            ),
        ),
        IntegrityError(
            "INSERT second",
            {},
            sqlite3.IntegrityError(
                "UNIQUE constraint failed: vocab_items.learner_key, "
                "vocab_items.language, vocab_items.normalized_headword"
            ),
        ),
    ]
    original_flush = api_db.flush
    original_commit = api_db.commit
    insert_attempts = 0
    creating_winner = False
    winner: VocabItem | None = None

    def _race_with_final_winner(*args: Any, **kwargs: Any) -> None:
        nonlocal insert_attempts, creating_winner, winner
        if creating_winner or not api_db.new:
            original_flush(*args, **kwargs)
            return
        error = errors[insert_attempts]
        insert_attempts += 1
        if insert_attempts == 2:
            api_db.rollback()
            creating_winner = True
            winner = _add_item(
                api_db,
                learner_key="learner_alpha",
                headword="winner display",
                normalized_headword="mot",
                gloss=None,
            )
            original_commit()
            creating_winner = False
        raise error

    monkeypatch.setattr(api_db, "flush", _race_with_final_winner)
    result = service.save_vocab(
        api_db,
        identity=LearnerIdentity("learner_alpha"),
        payload=VocabSaveIn(
            language="fr",
            headword="MOT",
            gloss_en="meaning",
        ),
    )

    assert insert_attempts == 2
    assert winner is not None
    assert result.id == winner.id
    assert result.headword == "winner display"
    assert result.gloss_en == "meaning"
    assert api_db.scalar(text("SELECT count(*) FROM vocab_items")) == 1


def test_fill_patch_delete_use_returning_and_exact_owner_predicates(
    api_db: Session,
) -> None:
    service = importlib.import_module("app.vocab.service")
    from app.schemas import VocabEditIn, VocabSaveIn

    identity = LearnerIdentity("learner_alpha")
    filled = _add_item(
        api_db,
        learner_key=identity.learner_key,
        headword="fill",
        gloss=None,
    )
    edited = _add_item(api_db, learner_key=identity.learner_key, headword="edit")
    deleted = _add_item(api_db, learner_key=identity.learner_key, headword="delete")
    api_db.commit()
    statements: list[str] = []

    def _capture(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
        sql = statement.lstrip().lower()
        if sql.startswith(("update", "delete")):
            statements.append(sql)

    engine = api_db.get_bind()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        service.save_vocab(
            api_db,
            identity=identity,
            payload=VocabSaveIn(
                language="fr",
                headword=filled.headword,
                gloss_en="meaning",
            ),
        )
        service.edit_vocab(
            api_db,
            identity=identity,
            item_id=edited.id,
            payload=VocabEditIn(example="example"),
        )
        service.delete_vocab(api_db, identity=identity, item_id=deleted.id)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert len(statements) == 3
    for statement in statements:
        assert "returning id" in statement
        assert "vocab_items.id" in statement
        assert "vocab_items.learner_key" in statement
        assert "vocab_items.user_id is null" in statement


@pytest.mark.parametrize(
    ("field", "competing_value", "normalized_value"),
    [
        ("gloss_en", "", ""),
        ("gloss_en", "   ", ""),
        ("example", "", None),
        ("example", "   ", None),
    ],
)
def test_fill_uses_current_database_semantic_empty_state(
    field: str,
    competing_value: str,
    normalized_value: str | None,
    api_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = importlib.import_module("app.vocab.service")
    from app.schemas import VocabSaveIn

    item = _add_item(
        api_db,
        learner_key="learner_alpha",
        headword="mot",
        gloss=None,
    )
    item.example = None
    api_db.commit()
    original_execute = api_db.execute
    injected = False

    def _interleave(statement: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal injected
        if not injected and statement.__class__.__name__ == "Update":
            injected = True
            if field == "gloss_en":
                original_execute(
                    text(
                        "UPDATE vocab_items SET gloss_en = :value, "
                        "normalized_gloss = :normalized WHERE id = :item_id"
                    ),
                    {
                        "value": competing_value,
                        "normalized": normalized_value,
                        "item_id": item.id,
                    },
                )
            else:
                original_execute(
                    text(
                        "UPDATE vocab_items SET example = :value WHERE id = :item_id"
                    ),
                    {"value": competing_value, "item_id": item.id},
                )
            api_db.commit()
        return original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(api_db, "execute", _interleave)
    payload_kwargs = {field: "incoming"}
    result = service.save_vocab(
        api_db,
        identity=LearnerIdentity("learner_alpha"),
        payload=VocabSaveIn(language="fr", headword="MOT", **payload_kwargs),
    )

    assert injected
    assert getattr(result, field) == "incoming"


def test_fill_losing_every_field_race_returns_winner_instead_of_404(
    api_db: Session,
    monkeypatch: pytest.MonkeyPatch,
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
        ),
    )

    assert injected
    assert result.id == item.id
    assert result.gloss_en == "winner"


def test_existing_row_deleted_before_fill_retries_insert_once(
    api_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = importlib.import_module("app.vocab.service")
    from app.schemas import VocabSaveIn

    deleted = _add_item(
        api_db,
        learner_key="learner_alpha",
        headword="mot",
        gloss=None,
    )
    api_db.commit()
    deleted_id = deleted.id
    original_execute = api_db.execute
    injected = False

    def _interleave(statement: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal injected
        if not injected and statement.__class__.__name__ == "Update":
            injected = True
            original_execute(
                text("DELETE FROM vocab_items WHERE id = :item_id"),
                {"item_id": deleted_id},
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
            gloss_en="meaning",
        ),
    )

    assert injected
    assert result.headword == "MOT"
    assert result.gloss_en == "meaning"
    assert api_db.scalar(text("SELECT count(*) FROM vocab_items")) == 1


def test_source_validation_statement_locks_unit_and_lesson_on_postgres(
    api_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = importlib.import_module("app.vocab.service")
    statement: Any = None

    def _capture_scalar(candidate: Any) -> str:
        nonlocal statement
        statement = candidate
        return "fr"

    monkeypatch.setattr(api_db, "scalar", _capture_scalar)
    service._validate_source(api_db, unit_id=123, language="fr")

    assert statement is not None
    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()
    assert "for update" in compiled
    assert "of lessons, listening_units" in compiled
