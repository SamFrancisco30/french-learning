"""Tiered access to listening units.

    anon     2 units
    free     5 units    (signed in)
    premium  unlimited

These tests drive the real HTTP surface rather than the entitlement functions directly, because
what matters is that the *endpoints* are gated. The gate lives in a dependency, and a dependency
that nothing depends on passes every unit test while leaving the content wide open.

Word lookup, sentence analysis and the vocabulary book are asserted to stay reachable on every
tier including signed out — highlighting is free for everyone, and that is a product promise, not
an implementation detail.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.errors import register_database_error_handler
from app.identity import LearnerIdentity, get_learner_identity, optional_learner_identity
from app.models import (
    Base,
    Lesson,
    ListeningUnit,
    Source,
    UnitUnlock,
    UserProfile,
    VocabItem,
)
from app.routers import account, lessons, vocab

ANON = "learner_device-one"
OTHER_ANON = "learner_device-two"
USER = "11111111-2222-3333-4444-555555555555"

app = FastAPI()
register_database_error_handler(app)
app.include_router(lessons.router)
app.include_router(vocab.router)
app.include_router(account.router)


@pytest.fixture
def db() -> Generator[Session, None, None]:
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
def units(db: Session) -> list[int]:
    """Ten units across one lesson — more than any tier's allowance, so a limit can be hit."""
    source = Source(
        provider="youtube",
        provider_id="vid-entitlements",
        url="https://example.invalid/v",
        title="Source",
        language="fr",
    )
    db.add(source)
    db.flush()
    lesson = Lesson(source_id=source.id, title="Lesson", language="fr", skill="listening")
    db.add(lesson)
    db.flush()
    ids = []
    for i in range(10):
        unit = ListeningUnit(
            lesson_id=lesson.id,
            idx=i,
            start_s=i * 60.0,
            end_s=(i + 1) * 60.0,
            text=f"Texte du passage {i}.",
            clip_key=f"clips/unit-{i}.m4a",
            difficulty_detail={},
        )
        db.add(unit)
        db.flush()
        ids.append(unit.id)
    db.commit()
    return ids


@pytest.fixture
def client(db: Session) -> TestClient:
    del db
    return TestClient(app, raise_server_exceptions=False)


def as_anon(learner_key: str = ANON) -> dict[str, str]:
    return {"X-Learner-Key": learner_key}


def sign_in(user_id: str = USER, *, learner_key: str = ANON) -> Generator[None, None, None]:
    """Override identity resolution to a verified account.

    The token path itself is covered in test_auth_tokens.py; overriding here keeps these tests
    about entitlements rather than re-testing JWT verification through every endpoint.
    """
    identity = LearnerIdentity(learner_key=learner_key, user_id=user_id, email="learner@example.com")
    app.dependency_overrides[get_learner_identity] = lambda: identity
    app.dependency_overrides[optional_learner_identity] = lambda: identity


def sign_out() -> None:
    app.dependency_overrides.pop(get_learner_identity, None)
    app.dependency_overrides.pop(optional_learner_identity, None)


@pytest.fixture(autouse=True)
def _signed_out_by_default() -> Generator[None, None, None]:
    yield
    sign_out()


def make_premium(db: Session, *, until: datetime | None = None) -> None:
    db.add(
        UserProfile(
            user_id=USER,
            email="learner@example.com",
            tier="premium",
            premium_until=until,
        )
    )
    db.commit()


# --- the allowance itself -------------------------------------------------------------------


def test_anonymous_learner_gets_two_units(client: TestClient, units: list[int]) -> None:
    me = client.get("/api/me", headers=as_anon()).json()

    assert me["signed_in"] is False
    assert me["entitlement"]["tier"] == "anon"
    assert me["entitlement"]["unit_limit"] == 2
    assert me["entitlement"]["remaining"] == 2


def test_signed_in_learner_gets_five_units(client: TestClient, units: list[int]) -> None:
    sign_in()

    me = client.get("/api/me", headers=as_anon()).json()

    assert me["signed_in"] is True
    assert me["email"] == "learner@example.com"
    assert me["entitlement"]["tier"] == "free"
    assert me["entitlement"]["unit_limit"] == 5
    assert me["entitlement"]["remaining"] == 5


def test_premium_is_unlimited_and_reports_no_numeric_limit(
    client: TestClient, db: Session, units: list[int]
) -> None:
    make_premium(db)
    sign_in()

    me = client.get("/api/me", headers=as_anon()).json()

    assert me["entitlement"]["tier"] == "premium"
    # None, not 0. A client that treats a falsy limit as "no access" would lock out the paying
    # learner, which is the worst possible direction for this bug to fail in.
    assert me["entitlement"]["unit_limit"] is None
    assert me["entitlement"]["remaining"] is None


def test_signing_in_creates_a_profile_on_first_request(
    client: TestClient, db: Session, units: list[int]
) -> None:
    sign_in()
    assert db.get(UserProfile, USER) is None

    client.get("/api/me", headers=as_anon())

    profile = db.get(UserProfile, USER)
    assert profile is not None
    assert profile.tier == "free"
    assert profile.email == "learner@example.com"


# --- the gate ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/api/units/{id}", "/api/units/{id}/clip", "/api/units/{id}/transcript"],
)
def test_locked_unit_is_refused_with_402_on_every_content_endpoint(
    client: TestClient, units: list[int], path: str
) -> None:
    response = client.get(path.format(id=units[0]), headers=as_anon())

    assert response.status_code == 402
    body = response.json()["detail"]
    assert body["error"] == "locked"
    assert body["can_unlock"] is True
    assert body["entitlement"]["remaining"] == 2


@pytest.mark.parametrize(
    "path",
    ["/api/units/{id}", "/api/units/{id}/transcript"],
)
def test_unlocked_unit_is_served(client: TestClient, units: list[int], path: str) -> None:
    assert client.post(f"/api/units/{units[0]}/unlock", headers=as_anon()).status_code == 200

    response = client.get(path.format(id=units[0]), headers=as_anon())

    assert response.status_code == 200


def test_unlocking_is_the_learners_choice_not_the_first_n_units(
    client: TestClient, units: list[int]
) -> None:
    """The allowance is a set of chosen units. Opening the seventh unit must work as well as the
    first — a "first N by id" rule would make which recordings are free depend on ingest order."""
    assert client.post(f"/api/units/{units[6]}/unlock", headers=as_anon()).status_code == 200

    assert client.get(f"/api/units/{units[6]}", headers=as_anon()).status_code == 200
    assert client.get(f"/api/units/{units[0]}", headers=as_anon()).status_code == 402


def test_allowance_runs_out_at_the_limit(client: TestClient, units: list[int]) -> None:
    for unit_id in units[:2]:
        assert client.post(f"/api/units/{unit_id}/unlock", headers=as_anon()).status_code == 200

    third = client.post(f"/api/units/{units[2]}/unlock", headers=as_anon())

    assert third.status_code == 409
    detail = third.json()["detail"]
    assert detail["error"] == "quota_exhausted"
    assert detail["entitlement"]["remaining"] == 0
    # And the units already unlocked keep working.
    assert client.get(f"/api/units/{units[0]}", headers=as_anon()).status_code == 200


def test_locked_response_says_when_there_is_no_way_through(
    client: TestClient, units: list[int]
) -> None:
    """`can_unlock` is what the client uses to choose between offering an unlock and offering an
    upgrade. Once the allowance is spent it must flip, or the UI offers an unlock that 409s."""
    for unit_id in units[:2]:
        client.post(f"/api/units/{unit_id}/unlock", headers=as_anon())

    body = client.get(f"/api/units/{units[5]}", headers=as_anon()).json()["detail"]

    assert body["can_unlock"] is False


def test_unlocking_is_idempotent(client: TestClient, db: Session, units: list[int]) -> None:
    """A double-click or a reload must not cost two of two slots for one recording."""
    for _ in range(3):
        assert client.post(f"/api/units/{units[0]}/unlock", headers=as_anon()).status_code == 200

    rows = db.scalars(select(UnitUnlock).where(UnitUnlock.learner_key == ANON)).all()
    assert len(rows) == 1
    me = client.get("/api/me", headers=as_anon()).json()
    assert me["entitlement"]["remaining"] == 1


def test_premium_needs_no_unlocks_at_all(
    client: TestClient, db: Session, units: list[int]
) -> None:
    make_premium(db)
    sign_in()

    for unit_id in units:
        assert client.get(f"/api/units/{unit_id}", headers=as_anon()).status_code == 200

    assert db.scalars(select(UnitUnlock)).all() == []


def test_expired_premium_falls_back_to_the_member_allowance(
    client: TestClient, db: Session, units: list[int]
) -> None:
    make_premium(db, until=datetime.now(UTC) - timedelta(days=1))
    sign_in()

    me = client.get("/api/me", headers=as_anon()).json()

    assert me["entitlement"]["tier"] == "free"
    assert me["entitlement"]["unit_limit"] == 5
    assert client.get(f"/api/units/{units[0]}", headers=as_anon()).status_code == 402


def test_premium_lasts_until_the_paid_period_ends(
    client: TestClient, db: Session, units: list[int]
) -> None:
    """A cancelled subscription keeps working to the end of the period Stripe was paid for."""
    make_premium(db, until=datetime.now(UTC) + timedelta(days=3))
    sign_in()

    assert client.get("/api/me", headers=as_anon()).json()["entitlement"]["tier"] == "premium"
    assert client.get(f"/api/units/{units[0]}", headers=as_anon()).status_code == 200


def test_one_device_cannot_spend_anothers_allowance(
    client: TestClient, units: list[int]
) -> None:
    client.post(f"/api/units/{units[0]}/unlock", headers=as_anon(ANON))

    assert client.get(f"/api/units/{units[0]}", headers=as_anon(OTHER_ANON)).status_code == 402
    assert client.get("/api/me", headers=as_anon(OTHER_ANON)).json()["entitlement"][
        "remaining"
    ] == 2


def test_unlocking_a_unit_that_does_not_exist_is_404(client: TestClient, units: list[int]) -> None:
    assert client.post("/api/units/999999/unlock", headers=as_anon()).status_code == 404


def test_a_caller_with_no_identity_at_all_still_gets_an_answer(
    client: TestClient, units: list[int]
) -> None:
    """A browser with localStorage blocked cannot produce a device key. It gets the free tier's
    view rather than a 401 — the library stays browsable and word lookup stays available."""
    me = client.get("/api/me")

    assert me.status_code == 200
    assert me.json()["entitlement"]["tier"] == "anon"
    assert me.json()["entitlement"]["unlocked_unit_ids"] == []
    assert client.get(f"/api/units/{units[0]}").status_code == 402


# --- what stays free on every tier ----------------------------------------------------------


def test_browsing_the_library_is_never_gated(client: TestClient, units: list[int]) -> None:
    """You can see everything on offer. Metering the catalogue would hide what there is to buy."""
    assert client.get("/api/lessons").status_code == 200
    listing = client.get("/api/lessons").json()
    assert listing and listing[0]["unit_count"] == 10
    assert client.get(f"/api/lessons/{listing[0]['id']}").status_code == 200


def test_saving_and_listing_words_works_signed_out_with_nothing_unlocked(
    client: TestClient, units: list[int]
) -> None:
    """Highlighting a word is free for everyone. This is the explicit product promise, so it is
    asserted from the hardest position: anonymous, zero unlocks, zero attempts."""
    saved = client.post(
        "/api/vocab",
        headers=as_anon(),
        json={"language": "fr", "headword": "écouter", "gloss_en": "to listen"},
    )

    assert saved.status_code == 200
    listed = client.get("/api/vocab?language=fr", headers=as_anon())
    assert listed.status_code == 200
    assert [i["headword"] for i in listed.json()["items"]] == ["écouter"]


def test_word_book_is_not_metered_by_the_unit_allowance(
    client: TestClient, db: Session, units: list[int]
) -> None:
    """Words can be saved against units the learner has not unlocked. The allowance limits
    listening, not vocabulary — a learner should be able to keep every word they ever look up."""
    for i in range(8):
        response = client.post(
            "/api/vocab",
            headers=as_anon(),
            json={
                "language": "fr",
                "headword": f"mot{i}",
                "gloss_en": "word",
                "unit_id": units[i],
            },
        )
        assert response.status_code == 200

    assert len(db.scalars(select(VocabItem).where(VocabItem.learner_key == ANON)).all()) == 8


# --- claiming anonymous work on sign-in -----------------------------------------------------


def test_signing_in_claims_the_words_and_unlocks_from_before(
    client: TestClient, db: Session, units: list[int]
) -> None:
    """Without this, signing in looks like data loss: everything from the session where the learner
    decided to make an account would stay behind under the device key."""
    client.post(
        "/api/vocab",
        headers=as_anon(),
        json={"language": "fr", "headword": "écouter", "gloss_en": "to listen"},
    )
    client.post(f"/api/units/{units[3]}/unlock", headers=as_anon())

    sign_in()
    claimed = client.post("/api/me/claim", headers=as_anon(), json={"learner_key": ANON})

    assert claimed.status_code == 200
    body = claimed.json()
    assert body["vocab_items"] == 1
    assert body["unlocks"] == 1
    # The unit stays unlocked, and now against the account rather than the device.
    assert client.get(f"/api/units/{units[3]}", headers=as_anon()).status_code == 200
    assert db.scalars(select(VocabItem).where(VocabItem.user_id == USER)).all()
    assert db.scalars(select(UnitUnlock).where(UnitUnlock.user_id == USER)).all()


def test_claiming_is_idempotent(client: TestClient, db: Session, units: list[int]) -> None:
    """It runs on every sign-in, so the second run has to be a no-op rather than a duplicate."""
    client.post(
        "/api/vocab",
        headers=as_anon(),
        json={"language": "fr", "headword": "écouter", "gloss_en": "to listen"},
    )
    client.post(f"/api/units/{units[3]}/unlock", headers=as_anon())
    sign_in()

    first = client.post("/api/me/claim", headers=as_anon(), json={"learner_key": ANON}).json()
    second = client.post("/api/me/claim", headers=as_anon(), json={"learner_key": ANON}).json()

    assert (first["vocab_items"], first["unlocks"]) == (1, 1)
    assert (second["vocab_items"], second["unlocks"]) == (0, 0)
    assert len(db.scalars(select(VocabItem).where(VocabItem.user_id == USER)).all()) == 1
    assert len(db.scalars(select(UnitUnlock).where(UnitUnlock.user_id == USER)).all()) == 1


def test_claiming_a_word_the_account_already_has_keeps_the_accounts_copy(
    client: TestClient, db: Session, units: list[int]
) -> None:
    """The account's copy carries its own review schedule, so the anonymous duplicate is dropped
    rather than overwriting it — a collision must not reset the learner's SRS progress."""
    client.post(
        "/api/vocab",
        headers=as_anon(),
        json={"language": "fr", "headword": "écouter", "gloss_en": "to listen"},
    )
    sign_in()
    client.post(
        "/api/vocab",
        headers=as_anon(),
        json={"language": "fr", "headword": "écouter", "gloss_en": "to hear"},
    )
    owned = db.scalars(select(VocabItem).where(VocabItem.user_id == USER)).one()
    owned.reps = 7
    db.commit()

    result = client.post("/api/me/claim", headers=as_anon(), json={"learner_key": ANON}).json()

    assert result["vocab_items"] == 0
    survivor = db.scalars(select(VocabItem).where(VocabItem.user_id == USER)).one()
    assert survivor.reps == 7
    assert survivor.gloss_en == "to hear"
    # And the anonymous duplicate is gone rather than left orphaned.
    assert db.scalars(select(VocabItem).where(VocabItem.user_id.is_(None))).all() == []


def test_claiming_merges_unlocks_without_exceeding_the_limit(
    client: TestClient, db: Session, units: list[int]
) -> None:
    """Two devices' unlocks merging into one account must not smuggle in extra allowance: the
    account's limit applies to the merged set, so a full account cannot unlock more."""
    for unit_id in units[:2]:
        client.post(f"/api/units/{unit_id}/unlock", headers=as_anon())
    sign_in()
    for unit_id in units[2:5]:
        client.post(f"/api/units/{unit_id}/unlock", headers=as_anon())

    client.post("/api/me/claim", headers=as_anon(), json={"learner_key": ANON})

    me = client.get("/api/me", headers=as_anon()).json()
    assert len(me["entitlement"]["unlocked_unit_ids"]) == 5
    assert me["entitlement"]["remaining"] == 0
    # Already at five, so a sixth is refused even though the merge is what filled it.
    assert client.post(f"/api/units/{units[6]}/unlock", headers=as_anon()).status_code == 409


def test_claiming_requires_an_account(client: TestClient, units: list[int]) -> None:
    response = client.post("/api/me/claim", headers=as_anon(), json={"learner_key": ANON})

    assert response.status_code == 401


def test_claiming_cannot_steal_another_devices_work(
    client: TestClient, db: Session, units: list[int]
) -> None:
    """The learner_key in the body is attacker-controlled, so this is the one place where a caller
    names data they do not obviously own. It is accepted only because a device key is a secret the
    holder generated; what must NOT happen is a claim reaching rows already owned by an account."""
    victim = "11111111-2222-3333-4444-999999999999"
    db.add(UnitUnlock(learner_key=OTHER_ANON, user_id=victim, unit_id=units[7]))
    db.commit()
    sign_in()

    client.post("/api/me/claim", headers=as_anon(), json={"learner_key": OTHER_ANON})

    still_theirs = db.scalars(select(UnitUnlock).where(UnitUnlock.user_id == victim)).all()
    assert len(still_theirs) == 1
    assert still_theirs[0].unit_id == units[7]
    assert db.scalars(select(UnitUnlock).where(UnitUnlock.user_id == USER)).all() == []
