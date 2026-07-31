from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import get_db
from app.lexicon.normalize import normalize_vocab_v1
from app.routers import lexicon


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
    assert "normalized_headword" in body["word"]
    assert body["word"]["normalized_headword"] == normalize_vocab_v1(
        body["word"]["lemma"] or body["selection"]
    )
    for expression in body["expressions"]:
        assert "normalized_headword" in expression
        assert expression["normalized_headword"] == normalize_vocab_v1(expression["canonical"])
    assert db.commit_calls == 1
    assert resolver_result == original_result
