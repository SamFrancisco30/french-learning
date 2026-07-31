import base64
import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import Column, DateTime, Integer, MetaData, Table, create_engine, select

from app.vocab.cursor import (
    InvalidCursor,
    decode_cursor,
    encode_alphabetical_cursor,
    encode_recent_cursor,
)


def _token(payload: object) -> str:
    raw = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _raw_token(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _recent_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "v": 1,
        "sort": "recent",
        "language": "fr",
        "q": "écouter",
        "last_created_at": "2026-07-30T12:34:56+00:00",
        "last_headword": None,
        "last_id": 42,
    }
    payload.update(changes)
    return payload


def test_recent_cursor_round_trip_preserves_unicode_and_omits_padding() -> None:
    position = datetime(
        2026, 7, 30, 18, 4, 56, tzinfo=timezone(timedelta(hours=5, minutes=30))
    )
    token = encode_recent_cursor(
        language="fr",
        q="écouter",
        last_created_at=position,
        last_id=42,
    )

    assert "=" not in token
    payload = decode_cursor(token, sort="recent", language="fr", q="écouter")
    assert payload.last_created_at == datetime(2026, 7, 30, 12, 34, 56, tzinfo=UTC)
    assert payload.last_created_at.tzinfo is UTC
    assert payload.last_headword is None
    assert payload.last_id == 42


def test_recent_cursor_datetime_binds_in_sqlalchemy_predicate() -> None:
    created_at = datetime(2026, 7, 30, 12, 34, 56, tzinfo=UTC)
    payload = decode_cursor(
        encode_recent_cursor(
            language="fr", q="", last_created_at=created_at, last_id=42
        ),
        sort="recent",
        language="fr",
        q="",
    )
    metadata = MetaData()
    vocab = Table(
        "vocab",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(vocab.insert().values(id=42, created_at=created_at))
        matched = connection.scalar(
            select(vocab.c.id).where(
                vocab.c.created_at == payload.last_created_at,
                vocab.c.id == payload.last_id,
            )
        )
    assert matched == 42


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 7, 30, 12, 34, 56, tzinfo=UTC).replace(tzinfo=None),
        "2026-07-30T12:34:56+00:00",
    ],
)
def test_recent_encoder_rejects_naive_or_non_datetime_positions(value: object) -> None:
    with pytest.raises(ValidationError):
        encode_recent_cursor(
            language="fr", q="", last_created_at=value, last_id=42
        )


def test_alphabetical_cursor_round_trip() -> None:
    token = encode_alphabetical_cursor(
        language=None, q="", last_headword="été", last_id=7
    )

    payload = decode_cursor(token, sort="alphabetical", language=None, q="")
    assert payload.last_created_at is None
    assert payload.last_headword == "été"
    assert payload.last_id == 7


@pytest.mark.parametrize(
    "token",
    [
        _token(_recent_payload(v=2)),
        "not+base64!",
        base64.urlsafe_b64encode(b"{not-json").decode().rstrip("="),
    ],
)
def test_decode_rejects_unsupported_or_malformed_cursor(token: str) -> None:
    with pytest.raises(InvalidCursor):
        decode_cursor(token, sort="recent", language="fr", q="écouter")


@pytest.mark.parametrize(
    "payload",
    [
        _recent_payload(extra=True),
        {key: value for key, value in _recent_payload().items() if key != "v"},
        {key: value for key, value in _recent_payload().items() if key != "last_id"},
        _recent_payload(last_id="42"),
        _recent_payload(last_id=0),
        _recent_payload(q=3),
        _recent_payload(language=3),
        _recent_payload(sort="newest"),
        _recent_payload(last_headword="écouter"),
        _recent_payload(last_created_at=None),
        _recent_payload(last_created_at="not-a-date"),
        _recent_payload(last_created_at="2026-07-30T12:34:56"),
        _recent_payload(v=True),
        {
            **_recent_payload(
                sort="alphabetical",
                last_created_at=None,
                last_headword="écouter",
            ),
            "last_created_at": "2026-07-30T12:34:56+00:00",
        },
        _recent_payload(
            sort="alphabetical", last_created_at=None, last_headword=None
        ),
    ],
)
def test_decode_strictly_rejects_invalid_payloads(payload: object) -> None:
    with pytest.raises(InvalidCursor):
        decode_cursor(
            _token(payload), sort="recent", language="fr", q="écouter"
        )


@pytest.mark.parametrize(
    ("binding", "value"),
    [("sort", "alphabetical"), ("language", "en"), ("q", "ecouter")],
)
def test_decode_rejects_request_binding_mismatch(binding: str, value: object) -> None:
    token = _token(_recent_payload())
    request = {"sort": "recent", "language": "fr", "q": "écouter"}
    request[binding] = value

    with pytest.raises(InvalidCursor):
        decode_cursor(token, **request)


@pytest.mark.parametrize(
    ("changes", "bindings"),
    [
        (
            {"language": "language9"},
            {"sort": "recent", "language": "language9", "q": "écouter"},
        ),
        (
            {"q": "é" * 129},
            {"sort": "recent", "language": "fr", "q": "é" * 129},
        ),
        (
            {
                "sort": "alphabetical",
                "last_created_at": None,
                "last_headword": "",
            },
            {"sort": "alphabetical", "language": "fr", "q": "écouter"},
        ),
        (
            {
                "sort": "alphabetical",
                "last_created_at": None,
                "last_headword": "é" * 129,
            },
            {"sort": "alphabetical", "language": "fr", "q": "écouter"},
        ),
    ],
)
def test_decode_rejects_out_of_domain_cursor_strings(
    changes: dict[str, object], bindings: dict[str, object]
) -> None:
    with pytest.raises(InvalidCursor):
        decode_cursor(_token(_recent_payload(**changes)), **bindings)


@pytest.mark.parametrize("field", ["language", "q", "last_headword"])
def test_decode_rejects_escaped_unpaired_surrogates(field: str) -> None:
    payload = _recent_payload()
    payload[field] = "\ud800"
    bindings: dict[str, object] = {
        "sort": "recent",
        "language": "fr",
        "q": "écouter",
    }
    bindings[field if field != "last_headword" else "sort"] = (
        "\ud800" if field != "last_headword" else "alphabetical"
    )
    if field == "last_headword":
        payload.update(sort="alphabetical", last_created_at=None)
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()

    with pytest.raises(InvalidCursor):
        decode_cursor(_raw_token(raw), **bindings)


def test_decode_rejects_oversized_or_deep_token_before_pathological_json() -> None:
    deep = _raw_token(("[" * 2000 + "]" * 2000).encode())
    with pytest.raises(InvalidCursor):
        decode_cursor(deep, sort="recent", language="fr", q="")


def test_decode_rejects_duplicate_json_object_keys() -> None:
    raw = (
        b'{"v":1,"sort":"recent","language":"fr","q":"","q":"other",'
        b'"last_created_at":"2026-07-30T12:34:56Z",'
        b'"last_headword":null,"last_id":42}'
    )
    with pytest.raises(InvalidCursor):
        decode_cursor(
            _raw_token(raw), sort="recent", language="fr", q="other"
        )


def test_legitimate_maximum_alphabetical_cursor_fits_and_round_trips() -> None:
    q = "😀" * 128
    headword = "🎓" * 128
    token = encode_alphabetical_cursor(
        language="fr-CA-x", q=q, last_headword=headword, last_id=42
    )
    payload = decode_cursor(
        token, sort="alphabetical", language="fr-CA-x", q=q
    )
    assert payload.q == q
    assert payload.last_headword == headword
