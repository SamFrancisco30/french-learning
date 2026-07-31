from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.identity import LearnerIdentity, get_learner_identity

app = FastAPI()


@app.get("/identity")
def read_identity(
    identity: Annotated[LearnerIdentity, Depends(get_learner_identity)],
) -> dict[str, str | None]:
    return {
        "learner_key": identity.learner_key,
        "user_id": identity.user_id,
    }


client = TestClient(app)


@pytest.mark.parametrize(
    "learner_key",
    [
        "learner_abc123",
        "learner_123e4567-e89b-12d3-a456-426614174000",
        f"learner_{'a' * 48}",
    ],
)
def test_accepts_valid_anonymous_learner_keys(learner_key: str) -> None:
    response = client.get("/identity", headers={"X-Learner-Key": learner_key})

    assert response.status_code == 200
    assert response.json() == {"learner_key": learner_key, "user_id": None}


@pytest.mark.parametrize(
    "learner_key",
    [
        None,
        f"learner_{'a' * 49}",
        "learner_bad_key",
        "learner_bad/key",
        "abc123",
    ],
)
def test_rejects_missing_overlong_malformed_or_unprefixed_keys(
    learner_key: str | None,
) -> None:
    headers = {} if learner_key is None else {"X-Learner-Key": learner_key}

    response = client.get("/identity", headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}
    if learner_key is not None:
        assert learner_key not in response.text


@pytest.mark.parametrize("authorization", ["Bearer bearer-secret", ""])
def test_rejects_any_authorization_header_even_with_valid_anonymous_key(
    authorization: str,
) -> None:
    learner_key = "learner_abc123"

    response = client.get(
        "/identity",
        headers={
            "Authorization": authorization,
            "X-Learner-Key": learner_key,
        },
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}
    if authorization:
        assert authorization not in response.text
    assert learner_key not in response.text


@pytest.mark.parametrize(
    "learner_keys",
    [
        ("learner_first", "learner_second"),
        ("learner_valid", "malformed"),
    ],
)
def test_rejects_duplicate_learner_key_headers(
    learner_keys: tuple[str, str],
) -> None:
    response = client.get(
        "/identity",
        headers=[
            ("X-Learner-Key", learner_keys[0]),
            ("X-Learner-Key", learner_keys[1]),
        ],
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}
    for learner_key in learner_keys:
        assert learner_key not in response.text
