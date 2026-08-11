"""Drill mode's API, against a bank built in memory.

The load-bearing test here is the leak check. An exam item gives its answer away three
separate ways — the `answer` letter, the option flagged `is_correct`, and an explanation
that routinely names it outright — so `test_question_payload_reveals_nothing` searches the
serialised response for all three rather than asserting on one field. Withholding is a
property of `DrillQuestionOut` having no field they could travel in, and this is what
would catch someone adding one.
"""

from collections.abc import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.drill.models import (
    KIND_GUIDE,
    KIND_MCQ,
    KIND_PRODUCTION,
    DrillAttempt,
    DrillCollection,
    DrillOption,
    DrillQuestion,
)
from app.errors import register_database_error_handler
from app.models import Base
from app.routers import drill

ANON = "learner_device-one"
OTHER = "learner_device-two"

app = FastAPI()
register_database_error_handler(app)
app.include_router(drill.router)


@pytest.fixture
def db() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
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


@pytest.fixture(autouse=True)
def no_signing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Media keys are not signed in tests; there is no bucket behind them."""
    monkeypatch.setattr(drill, "_signed", lambda key: f"signed://{key}" if key else None)


def _add_question(
    db: Session,
    collection: DrillCollection,
    *,
    external_id: int,
    skill: str = "reading",
    kind: str = KIND_MCQ,
    level: str | None = "B1",
    answer: str | None = "B",
    canonical: bool = True,
    document: str = "Le texte du document.",
    question: str | None = "Que dit ce texte ?",
    explanation: str | None = "正确答案：B。因为……",
    document_zh: str | None = "文档的中文翻译。",
    model_answer: str | None = None,
    provenance: dict | None = None,
) -> DrillQuestion:
    q = DrillQuestion(
        collection_id=collection.id,
        external_id=external_id,
        skill=skill,
        kind=kind,
        level=level,
        answer=answer,
        canonical=canonical,
        document=document,
        question=question,
        explanation=explanation,
        document_zh=document_zh,
        model_answer=model_answer,
        # Both skills carry images in the real bank — 2576 reading, 154 listening — and
        # they mean different things, which is what the serving policy turns on. The
        # fixture has to have both or the policy tests pass for the wrong reason.
        image_key="drill/aa/aaaa.png" if skill in ("reading", "listening") else None,
        audio_key="drill/bb/bbbb.mp3" if skill == "listening" else None,
        provenance=provenance or {"answer": "json"},
        warnings=[],
        corrections=[],
    )
    db.add(q)
    db.flush()
    if kind == KIND_MCQ:
        for label in "ABCD":
            db.add(
                DrillOption(
                    question_id=q.id,
                    label=label,
                    text=f"Option {label}",
                    is_correct=label == answer,
                )
            )
    db.commit()
    return q


@pytest.fixture
def bank(db: Session) -> dict[str, DrillQuestion]:
    reading = DrillCollection(skill="reading", name="TCF/READING 15分题库", level="B1")
    listening = DrillCollection(skill="listening", name="TCF/LISTENING test01")
    writing = DrillCollection(skill="writing", name="TCF/WRITING Tâche1")
    db.add_all([reading, listening, writing])
    db.flush()
    out = {
        "reading": _add_question(db, reading, external_id=1),
        "duplicate": _add_question(db, reading, external_id=2, canonical=False),
        "guide": _add_question(
            db, writing, external_id=3, skill="writing", kind=KIND_GUIDE, level=None,
            answer=None, question=None,
        ),
        "listening": _add_question(db, listening, external_id=4, skill="listening"),
        "writing": _add_question(
            db, writing, external_id=5, skill="writing", kind=KIND_PRODUCTION,
            level=None, answer=None, question=None, explanation=None,
            model_answer="Voici une réponse modèle.",
        ),
        "inferred": _add_question(
            db, reading, external_id=6, level="C1",
            provenance={"answer": "inferred:gemini-3.5-flash-lite"},
        ),
        # The bank ships this kind with four blank options because the choices are
        # spoken; the wording here was recovered from the explanation.
        "spoken": _add_question(
            db, listening, external_id=7, skill="listening", level="A2",
            document="", question=None,
            provenance={"answer": "json", "options": "explanation:list"},
        ),
    }
    for c in (reading, listening, writing):
        c.item_count = len(
            db.scalars(select(DrillQuestion).where(DrillQuestion.collection_id == c.id)).all()
        )
    db.commit()
    return out


@pytest.fixture
def client(db: Session) -> TestClient:
    del db
    return TestClient(app, raise_server_exceptions=False)


def as_anon(key: str = ANON) -> dict[str, str]:
    return {"X-Learner-Key": key}


# --------------------------------------------------------------- withholding the answer


def test_question_payload_reveals_nothing(client: TestClient, bank) -> None:
    res = client.get("/api/drill/next", params={"skill": "reading"}, headers=as_anon())
    assert res.status_code == 200
    blob = res.text.lower()
    assert "answer" not in blob
    assert "is_correct" not in blob and '"correct"' not in blob
    assert "explanation" not in blob
    assert "document_zh" not in blob
    assert "provenance" not in blob and "warnings" not in blob


def test_attempt_returns_the_key_and_the_commentary(client: TestClient, bank) -> None:
    qid = bank["reading"].id
    res = client.post(
        "/api/drill/attempts", json={"question_id": qid, "selected": "A"}, headers=as_anon()
    )
    assert res.status_code == 200
    body = res.json()
    assert body["answer"] == "B"
    assert body["correct"] is False
    assert body["explanation"]
    assert body["document_zh"]


def test_a_right_answer_is_marked_right(client: TestClient, bank) -> None:
    res = client.post(
        "/api/drill/attempts",
        json={"question_id": bank["reading"].id, "selected": "b"},
        headers=as_anon(),
    )
    # Case is normalised: the letter is an identifier, not the learner's typing.
    assert res.json()["correct"] is True


def test_a_skip_is_recorded_as_incorrect(client: TestClient, bank, db: Session) -> None:
    res = client.post(
        "/api/drill/attempts",
        json={"question_id": bank["reading"].id, "selected": None},
        headers=as_anon(),
    )
    assert res.json()["correct"] is False
    attempt = db.scalars(select(DrillAttempt)).one()
    assert attempt.selected is None and attempt.is_correct is False


def test_a_letter_outside_the_options_is_rejected(client: TestClient, bank) -> None:
    res = client.post(
        "/api/drill/attempts",
        json={"question_id": bank["reading"].id, "selected": "Z"},
        headers=as_anon(),
    )
    assert res.status_code == 400


# --------------------------------------------------------------- production tasks


def test_production_correctness_is_unknown_not_false(client: TestClient, bank) -> None:
    """A written answer has no key. Marking it wrong would be a false statement about it."""
    res = client.post(
        "/api/drill/attempts",
        json={"question_id": bank["writing"].id, "response": {"text": "Bonjour."}},
        headers=as_anon(),
    )
    body = res.json()
    assert body["correct"] is None
    assert body["model_answer"] == "Voici une réponse modèle."


def test_production_is_left_out_of_the_accuracy_ratio(client: TestClient, bank) -> None:
    """Otherwise a learner who wrote three essays is reported as 0% correct."""
    client.post(
        "/api/drill/attempts",
        json={"question_id": bank["writing"].id, "response": {"text": "Bonjour."}},
        headers=as_anon(),
    )
    rows = client.get("/api/drill/progress", headers=as_anon()).json()
    writing = next(r for r in rows if r["skill"] == "writing")
    assert writing["attempted"] == 1
    assert writing["graded"] == 0
    assert writing["accuracy"] is None


# --------------------------------------------------------------- sampling


def test_guides_are_never_drawn(client: TestClient, bank) -> None:
    """A guide is the vendor explaining how to answer a task — not something to answer."""
    for _ in range(20):
        res = client.get("/api/drill/next", params={"skill": "writing"}, headers=as_anon())
        if res.status_code == 200:
            assert res.json()["kind"] != KIND_GUIDE


def test_duplicates_are_never_drawn(client: TestClient, bank) -> None:
    seen = set()
    for _ in range(20):
        res = client.get("/api/drill/next", params={"skill": "reading"}, headers=as_anon())
        if res.status_code == 200:
            seen.add(res.json()["id"])
    assert bank["duplicate"].id not in seen


def test_an_answered_item_is_repeated_rather_than_404(client: TestClient, bank) -> None:
    """A level's bank is finite. Once it is used up, seeing an item again beats nothing."""
    only = bank["inferred"]  # the single canonical C1 item
    client.post(
        "/api/drill/attempts",
        json={"question_id": only.id, "selected": "B"},
        headers=as_anon(),
    )
    res = client.get(
        "/api/drill/next", params={"skill": "reading", "level": "C1"}, headers=as_anon()
    )
    assert res.status_code == 200
    assert res.json()["id"] == only.id


def test_unknown_skill_and_level_are_rejected(client: TestClient, bank) -> None:
    assert client.get("/api/drill/next", params={"skill": "cooking"}).status_code == 400
    assert (
        client.get("/api/drill/next", params={"skill": "reading", "level": "Z9"}).status_code
        == 400
    )


def test_no_match_is_a_404(client: TestClient, bank) -> None:
    res = client.get(
        "/api/drill/next", params={"skill": "listening", "level": "C2"}, headers=as_anon()
    )
    assert res.status_code == 404


# --------------------------------------------------------------- identity


def test_a_caller_without_a_device_key_can_still_practise(client: TestClient, bank) -> None:
    """optional_learner_identity yields None for a browser that blocks storage. The bank is
    readable without an identity, and progress is empty rather than a 500."""
    assert client.get("/api/drill/next", params={"skill": "reading"}).status_code == 200
    res = client.get("/api/drill/progress")
    assert res.status_code == 200 and res.json() == []


def test_progress_does_not_leak_between_learners(client: TestClient, bank) -> None:
    client.post(
        "/api/drill/attempts",
        json={"question_id": bank["reading"].id, "selected": "B"},
        headers=as_anon(ANON),
    )
    assert client.get("/api/drill/progress", headers=as_anon(OTHER)).json() == []
    mine = client.get("/api/drill/progress", headers=as_anon(ANON)).json()
    assert mine and mine[0]["correct"] == 1


# --------------------------------------------------------------- provenance


def test_an_inferred_key_is_surfaced_as_such(client: TestClient, bank) -> None:
    """An answer a model worked out is a different kind of fact from one the bank shipped,
    and a learner weighing a surprising key should be able to tell which they have."""
    res = client.post(
        "/api/drill/attempts",
        json={"question_id": bank["inferred"].id, "selected": "B"},
        headers=as_anon(),
    )
    assert res.json()["answer_source"] == "inferred:gemini-3.5-flash-lite"


def test_a_bank_shipped_key_reports_no_source(client: TestClient, bank) -> None:
    res = client.post(
        "/api/drill/attempts",
        json={"question_id": bank["reading"].id, "selected": "B"},
        headers=as_anon(),
    )
    assert res.json()["answer_source"] is None


# --------------------------------------------------------------- collections


def test_collections_count_distinct_items_not_rows(client: TestClient, bank) -> None:
    rows = client.get("/api/drill/collections", params={"skill": "reading"}).json()
    reading = next(r for r in rows if r["skill"] == "reading")
    # Three reading rows exist, one of them a duplicate.
    assert reading["item_count"] == 3
    assert reading["distinct_count"] == 2


def test_listening_transcript_is_flagged_as_a_spoiler(client: TestClient, bank) -> None:
    res = client.get("/api/drill/next", params={"skill": "listening"}, headers=as_anon())
    assert res.json()["document_is_spoiler"] is True


def test_reading_document_is_not_a_spoiler(client: TestClient, bank) -> None:
    res = client.get("/api/drill/next", params={"skill": "reading"}, headers=as_anon())
    assert res.json()["document_is_spoiler"] is False


def test_a_listening_picture_is_served(client: TestClient, bank) -> None:
    """For those items the picture is the question, so it has to reach the client."""
    res = client.get("/api/drill/next", params={"skill": "listening"}, headers=as_anon())
    assert res.json()["image_url"]


def test_a_reading_image_is_not_served(client: TestClient, bank) -> None:
    """It is the vendor's typeset render of text this endpoint already sends, watermark
    and all — no information, and a Storage round trip to sign something discarded."""
    res = client.get("/api/drill/next", params={"skill": "reading"}, headers=as_anon())
    assert res.json()["image_url"] is None


# --------------------------------------------------------------- spoken options


def test_spoken_options_arrive_blank(client: TestClient, bank) -> None:
    """Their wording is not in the payload at all. Printing it would turn "understand four
    spoken statements" into "match four written ones", which is a much easier task — and
    hiding it client-side would leave it readable to anyone who looked."""
    res = client.get(
        "/api/drill/next", params={"skill": "listening", "level": "A2"}, headers=as_anon()
    )
    body = res.json()
    assert body["options_are_spoken"] is True
    assert [o["label"] for o in body["options"]] == ["A", "B", "C", "D"]
    assert all(o["text"] == "" for o in body["options"])
    assert "Option A" not in res.text


def test_spoken_options_are_revealed_by_the_attempt(client: TestClient, bank) -> None:
    res = client.post(
        "/api/drill/attempts",
        json={"question_id": bank["spoken"].id, "selected": "B"},
        headers=as_anon(),
    )
    body = res.json()
    assert [o["text"] for o in body["options"]] == [
        "Option A", "Option B", "Option C", "Option D"
    ]


def test_written_options_are_not_marked_spoken(client: TestClient, bank) -> None:
    res = client.get("/api/drill/next", params={"skill": "reading"}, headers=as_anon())
    body = res.json()
    assert body["options_are_spoken"] is False
    assert all(o["text"] for o in body["options"])
