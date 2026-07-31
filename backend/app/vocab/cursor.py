"""Opaque, request-bound pagination cursors for vocabulary lists."""

from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)


class InvalidCursor(ValueError):
    """Raised when a cursor is malformed, invalid, or bound to another request."""


class CursorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    v: Literal[1]
    sort: Literal["recent", "alphabetical"]
    language: str | None
    q: str
    last_created_at: str | None
    last_headword: str | None
    last_id: int

    @field_validator("v", mode="before")
    @classmethod
    def validate_version_type(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("v must be an integer")
        return value

    @model_validator(mode="after")
    def validate_position(self) -> CursorPayload:
        if self.last_id <= 0:
            raise ValueError("last_id must be positive")
        if self.sort == "recent":
            if self.last_created_at is None or self.last_headword is not None:
                raise ValueError("invalid recent cursor position")
        elif self.last_headword is None or self.last_created_at is not None:
            raise ValueError("invalid alphabetical cursor position")
        return self


def _encode(payload: CursorPayload) -> str:
    serialized = json.dumps(
        payload.model_dump(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(serialized).decode("ascii").rstrip("=")


def encode_recent_cursor(
    *,
    language: str | None,
    q: str,
    last_created_at: str,
    last_id: int,
) -> str:
    return _encode(
        CursorPayload(
            v=1,
            sort="recent",
            language=language,
            q=q,
            last_created_at=last_created_at,
            last_headword=None,
            last_id=last_id,
        )
    )


def encode_alphabetical_cursor(
    *,
    language: str | None,
    q: str,
    last_headword: str,
    last_id: int,
) -> str:
    return _encode(
        CursorPayload(
            v=1,
            sort="alphabetical",
            language=language,
            q=q,
            last_created_at=None,
            last_headword=last_headword,
            last_id=last_id,
        )
    )


def decode_cursor(
    token: str,
    *,
    sort: Literal["recent", "alphabetical"],
    language: str | None,
    q: str,
) -> CursorPayload:
    try:
        if not token or not re.fullmatch(r"[A-Za-z0-9_-]+", token):
            raise ValueError("cursor is not unpadded base64url")
        padding = "=" * (-len(token) % 4)
        raw = base64.b64decode(token + padding, altchars=b"-_", validate=True)
        data = json.loads(raw.decode("utf-8"))
        payload = CursorPayload.model_validate(data)
    except (
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValidationError,
        ValueError,
        TypeError,
    ) as exc:
        raise InvalidCursor("invalid cursor") from exc

    if (
        payload.sort != sort
        or payload.language != language
        or payload.q != q
    ):
        raise InvalidCursor("cursor does not match request")
    return payload
