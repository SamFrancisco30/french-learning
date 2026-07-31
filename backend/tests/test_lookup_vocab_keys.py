from __future__ import annotations

from collections.abc import Generator
from copy import deepcopy
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.languages import get_language
from app.lexicon import lemmas, resolver, translate
from app.lexicon.normalize import normalize_vocab_v1
from app.llm.openai_client import LLMError
from app.models import Base, Expression, GlossCache, Lesson, ListeningUnit, Source
from app.routers import lexicon
from app.schemas import LookupIn


class _CommitTrackingDb:
    def __init__(self) -> None:
        self.commit_calls = 0

    def commit(self) -> None:
        self.commit_calls += 1


@pytest.fixture
def lookup_client() -> tuple[TestClient, _CommitTrackingDb]:
    app = FastAPI()
    app.include_router(lexicon.router)
    db = _CommitTrackingDb()
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app, raise_server_exceptions=False), db
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def real_lookup_client() -> Generator[tuple[TestClient, Session], None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(lexicon.router)
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app, raise_server_exceptions=False), db
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def _word(*, lemma: str | None, surface: str = "surface") -> dict[str, Any]:
    return {
        "surface": surface,
        "lemma": lemma,
        "pos": "NOUN",
        "gloss_en": "meaning",
        "other_senses": [],
        "note": None,
        "zipf": 4.2,
    }


def _expression(
    canonical: str,
    *,
    source: str,
    surface: str = "surface expression",
) -> dict[str, Any]:
    return {
        "id": None if source == "live" else 17,
        "canonical": canonical,
        "surface": surface,
        "kind": "expression",
        "gloss_en": "expression meaning",
        "literal_en": None,
        "note": None,
        "component_spans": [[1, 8]],
        "char_start": 1,
        "char_end": 8,
        "confidence": 0.91,
        "source": source,
    }


def _resolver_result(
    *,
    source: str,
    selection: str,
    lemma: str | None,
    expressions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "language": "fr",
        "selection": selection,
        "char_start": 1,
        "char_end": 8,
        "context": f"context for {selection}",
        "is_sentence": False,
        "constructions": [],
        "audio_start_s": None,
        "audio_end_s": None,
        "word": _word(lemma=lemma, surface=selection),
        "expressions": expressions or [],
        "source": source,
        "unit_id": None,
        "lemmatizer": "headword",
        "inferred": any(expression["source"] == "inferred" for expression in (expressions or [])),
        "error": "provider unavailable" if source == "error" else None,
    }


def _assert_vocab_keys(body: dict[str, Any]) -> None:
    lemma = body["word"]["lemma"]
    word_headword = lemma if isinstance(lemma, str) and lemma.strip() else body["selection"]
    assert "normalized_headword" in body["word"]
    assert body["word"]["normalized_headword"] == normalize_vocab_v1(word_headword)
    for expression in body["expressions"]:
        assert "normalized_headword" in expression
        assert expression["normalized_headword"] == normalize_vocab_v1(expression["canonical"])


def _post_lookup(
    client: TestClient,
    *,
    text: str,
    selection: str,
    unit_id: int | None = None,
) -> dict[str, Any]:
    char_start = text.index(selection)
    response = client.post(
        "/api/lookup",
        json={
            "language": "fr",
            "text": text,
            "char_start": char_start,
            "char_end": char_start + len(selection),
            "unit_id": unit_id,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    _assert_vocab_keys(body)
    return body


def _add_unit(db: Session, text: str) -> ListeningUnit:
    source = Source(
        language="fr",
        provider="test",
        provider_id="lookup-vocab-keys",
        url="https://example.test/source",
        title="Lookup vocabulary keys",
    )
    db.add(source)
    db.flush()
    lesson = Lesson(
        source_id=source.id,
        language="fr",
        skill="listening",
        title="Lookup integration",
    )
    db.add(lesson)
    db.flush()
    unit = ListeningUnit(
        lesson_id=lesson.id,
        idx=0,
        start_s=0.0,
        end_s=10.0,
        text=text,
        words_json=[],
    )
    db.add(unit)
    db.flush()
    return unit


@pytest.mark.parametrize(
    ("case_id", "resolver_result"),
    [
        pytest.param(
            "live-with-self-expression",
            _resolver_result(
                source="live",
                selection="L\u2019Expression",
                lemma="  L\u02bcEXPRESSION\u00a0 ",
                expressions=[
                    _expression(
                        "  L\uff07Expression\u00a0 EN   SOI  ",
                        source="live",
                    )
                ],
            ),
            id="live-with-self-expression",
        ),
        pytest.param(
            "cached-live-gloss",
            _resolver_result(
                source="cache",
                selection="\u00c9coutait",
                lemma="  \u00c9COUTER\t",
            ),
            id="cached-live-gloss",
        ),
        pytest.param(
            "precomputed-expression",
            _resolver_result(
                source="offline",
                selection="Feu",
                lemma="FEU",
                expressions=[
                    _expression(
                        "  Mettre\u00a0LE   FEU  ",
                        source="precomputed",
                    )
                ],
            ),
            id="precomputed-expression",
        ),
        pytest.param(
            "lemma-inferred-expression",
            _resolver_result(
                source="offline",
                selection="Pomme",
                lemma="pomme",
                expressions=[
                    _expression(
                        "  Tomber\u00a0DANS les POMMES ",
                        source="inferred",
                    )
                ],
            ),
            id="lemma-inferred-expression",
        ),
        pytest.param(
            "offline-empty-lemma-selection-fallback",
            _resolver_result(
                source="offline",
                selection="  L\u2019AMOUR\u00a0 FOU  ",
                lemma="",
            ),
            id="offline-empty-lemma-selection-fallback",
        ),
        pytest.param(
            "offline-whitespace-lemma-selection-fallback",
            _resolver_result(
                source="offline",
                selection="  L\u2019AMOUR\u00a0 FOU  ",
                lemma="\t \u00a0\n",
            ),
            id="offline-whitespace-lemma-selection-fallback",
        ),
        pytest.param(
            "error-none-lemma-with-existing-expression",
            _resolver_result(
                source="error",
                selection="  D\u02bcACCORD\t",
                lemma=None,
                expressions=[
                    _expression(
                        "  D\u00b4ACCORD\u00a0 AVEC ",
                        source="precomputed",
                    )
                ],
            ),
            id="error-none-lemma-with-existing-expression",
        ),
    ],
)
def test_lookup_adds_required_normalized_keys_for_every_resolver_path(
    case_id: str,
    resolver_result: dict[str, Any],
    lookup_client: tuple[TestClient, _CommitTrackingDb],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db = lookup_client
    original_result = deepcopy(resolver_result)
    monkeypatch.setattr(lexicon, "resolve_selection", lambda *_args, **_kwargs: resolver_result)

    response = client.post(
        "/api/lookup",
        json={
            "language": "fr",
            "text": f"ignored input for {case_id}",
            "char_start": 0,
            "char_end": 1,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    _assert_vocab_keys(body)
    assert db.commit_calls == 1
    assert resolver_result == original_result


def test_lookup_validates_enriched_result_before_committing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _CommitTrackingDb()
    malformed = _resolver_result(
        source="offline",
        selection="mot",
        lemma="mot",
    )
    del malformed["lemmatizer"]
    monkeypatch.setattr(lexicon, "resolve_selection", lambda *_args, **_kwargs: malformed)

    with pytest.raises(ValidationError):
        lexicon.lookup(
            LookupIn(
                language="fr",
                text="mot",
                char_start=0,
                char_end=3,
            ),
            db=db,  # type: ignore[arg-type]
        )

    assert db.commit_calls == 0


def test_actual_resolver_offline_word_path_adds_normalized_key(
    real_lookup_client: tuple[TestClient, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _db = real_lookup_client
    monkeypatch.setattr(lexicon.settings, "openai_api_key", None)

    body = _post_lookup(
        client,
        text="Elle \u00c9COUTAIT attentivement.",
        selection="\u00c9COUTAIT",
    )

    assert body["source"] == "offline"
    assert body["expressions"] == []


def test_actual_resolver_cache_hit_persists_hits_and_adds_normalized_key(
    real_lookup_client: tuple[TestClient, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db = real_lookup_client
    monkeypatch.setattr(lexicon.settings, "openai_api_key", None)
    lang = get_language("fr")
    text = "Elle \u00c9COUTAIT attentivement."
    selection = "\u00c9COUTAIT"
    cached = GlossCache(
        language="fr",
        surface_key=lang.normalize_answer(selection, fold_diacritics=True),
        context_key=translate._context_key(lang, text),
        surface=selection,
        lemma="  \u00c9COUTER\u00a0 ",
        pos="verb",
        gloss_en="was listening",
        senses=[],
        hits=3,
    )
    db.add(cached)
    db.commit()

    body = _post_lookup(client, text=text, selection=selection)

    assert body["source"] == "cache"
    db.refresh(cached)
    assert cached.hits == 4


def test_actual_resolver_returns_orm_precomputed_expression_with_key(
    real_lookup_client: tuple[TestClient, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db = real_lookup_client
    monkeypatch.setattr(lexicon.settings, "openai_api_key", None)
    text = "Il a mis le FEU hier."
    unit = _add_unit(db, text)
    selection = "FEU"
    start = text.index(selection)
    expression = Expression(
        unit_id=unit.id,
        language="fr",
        surface="mis le FEU",
        canonical="  Mettre\u00a0LE   FEU  ",
        lemma_key="feu|mettre",
        kind="idiom",
        gloss_en="set fire to",
        char_start=text.index("mis"),
        char_end=start + len(selection),
        component_spans=[[text.index("mis"), text.index("mis") + 3], [start, start + 3]],
        confidence=0.97,
    )
    db.add(expression)
    db.commit()

    body = _post_lookup(client, text=text, selection=selection, unit_id=unit.id)

    assert [candidate["source"] for candidate in body["expressions"]] == ["precomputed"]
    assert body["expressions"][0]["id"] == expression.id


def test_actual_resolver_returns_orm_inferred_expression_with_key(
    real_lookup_client: tuple[TestClient, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db = real_lookup_client
    monkeypatch.setattr(lexicon.settings, "openai_api_key", None)
    monkeypatch.setattr(lemmas, "_load_pipeline", lambda _model: None)
    expression = Expression(
        unit_id=None,
        language="fr",
        surface="pomme rouge",
        canonical="  POMME\u00a0 ROUGE  ",
        lemma_key="pomme|rouge",
        kind="collocation",
        gloss_en="red apple",
        char_start=0,
        char_end=11,
        component_spans=[[0, 5], [6, 11]],
        confidence=0.88,
    )
    db.add(expression)
    db.commit()

    body = _post_lookup(
        client,
        text="Cette pomme rouge m\u00fbrit.",
        selection="pomme",
    )

    assert body["inferred"] is True
    assert [candidate["source"] for candidate in body["expressions"]] == ["inferred"]
    assert body["expressions"][0]["id"] == expression.id


def test_actual_resolver_error_fallback_adds_word_key(
    real_lookup_client: tuple[TestClient, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _db = real_lookup_client

    def _fail_gloss(*_args: Any, **_kwargs: Any) -> Any:
        raise LLMError("forced provider failure")

    monkeypatch.setattr(resolver, "gloss_selection", _fail_gloss)

    body = _post_lookup(
        client,
        text="Nous sommes D\u02bcACCORD.",
        selection="D\u02bcACCORD",
    )

    assert body["source"] == "error"
    assert body["error"] == "forced provider failure"


def test_actual_resolver_live_self_expression_adds_keys_and_persists_cache(
    real_lookup_client: tuple[TestClient, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db = real_lookup_client
    monkeypatch.setattr(lexicon.settings, "openai_api_key", "test-key")

    def _complete_json(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "gloss_en": "fainted",
            "lemma": "  TOMBER\u00a0 DANS LES POMMES  ",
            "pos": "phrase",
            "is_expression": True,
            "canonical": "  Tomber\u00a0DANS les POMMES  ",
            "literal_en": "fall into the apples",
            "note": "",
            "other_senses": [],
        }

    monkeypatch.setattr(translate.StructuredLLM, "complete_json", _complete_json)

    body = _post_lookup(
        client,
        text="Elle est tomb\u00e9e dans les pommes.",
        selection="tomb\u00e9e dans les pommes",
    )

    assert body["source"] == "live"
    assert [candidate["source"] for candidate in body["expressions"]] == ["live"]
    assert db.scalar(select(func.count()).select_from(GlossCache)) == 1
