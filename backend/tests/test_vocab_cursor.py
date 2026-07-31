import base64
import json

import pytest

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
    token = encode_recent_cursor(
        language="fr",
        q="écouter",
        last_created_at="2026-07-30T12:34:56+00:00",
        last_id=42,
    )

    assert "=" not in token
    payload = decode_cursor(token, sort="recent", language="fr", q="écouter")
    assert payload.last_created_at == "2026-07-30T12:34:56+00:00"
    assert payload.last_headword is None
    assert payload.last_id == 42


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
