"""Who is calling: an anonymous device, or a signed-in Supabase user.

Two identities coexist deliberately, and neither replaces the other. A learner can save words and
open their free units before they have an account — the whole app works signed out — so the
anonymous device key remains a first-class identity rather than a degraded one. Signing in adds a
`user_id` on top; it does not switch the app into a different mode.

Rows carry both columns for exactly that reason: `user_id` when there is an account, and
`learner_key` with a NULL `user_id` when there is not. `owner_clause` is the single place that
decides which of the two a query scopes by, so no endpoint has to remember the rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Protocol

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import ColumnElement, and_

from .auth import AuthUnavailable, verify_access_token
from .config import get_settings


@dataclass(frozen=True)
class LearnerIdentity:
    learner_key: str
    user_id: str | None = None
    email: str | None = None

    @property
    def is_authenticated(self) -> bool:
        return self.user_id is not None


class Owned(Protocol):
    """A table with the two ownership columns. Structural, so it covers the models added for
    accounts as well as the ones that predate them without a shared base class."""

    user_id: ColumnElement[str | None]
    learner_key: ColumnElement[str]


def owner_clause(model: type[Owned], identity: LearnerIdentity) -> ColumnElement[bool]:
    """Scope a query to this caller's rows.

    The `user_id IS NULL` half is not redundant. Without it an anonymous query would also match
    rows that have since been claimed by an account which happened to start from this device key,
    so a learner who signed in on a shared browser could see the account holder's words after
    signing out.
    """
    if identity.user_id is not None:
        return model.user_id == identity.user_id
    return and_(model.user_id.is_(None), model.learner_key == identity.learner_key)


_ANON_KEY = re.compile(r"^learner_[A-Za-z0-9-]{1,48}$")
_BEARER = re.compile(r"^Bearer\s+(\S+)$", re.IGNORECASE)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Unauthorized",
)


def _anon_key(x_learner_key: list[str] | None) -> str | None:
    """The device key, if exactly one well-formed one was sent.

    Exactly one: a repeated header arrives as a list, and picking an element would let a caller
    submit two identities and leave the choice of whose data to touch up to us.
    """
    if x_learner_key is None or len(x_learner_key) != 1:
        return None
    key = x_learner_key[0]
    return key if _ANON_KEY.fullmatch(key) else None


def get_learner_identity(
    x_learner_key: Annotated[
        list[str] | None,
        Header(alias="X-Learner-Key"),
    ] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> LearnerIdentity:
    """Resolve the caller, requiring one identity or the other.

    Before Supabase Auth existed this rejected any request carrying an `Authorization` header
    outright, as a guard against a half-built auth path silently accepting tokens. That guard has
    now been replaced by real verification rather than removed.
    """
    if authorization is not None:
        match = _BEARER.fullmatch(authorization.strip())
        if match is None:
            raise _UNAUTHORIZED
        try:
            claims = verify_access_token(match.group(1))
        except AuthUnavailable:
            # A token arrived but this server has no project to verify it against. That is a
            # deployment mistake, not a bad credential: answering 401 would send the client off to
            # sign in again and again against auth that cannot work.
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Accounts are not configured on this server",
            ) from None

        # The device key is kept alongside the account when the client still has one, which is what
        # lets /api/me/claim find the anonymous rows to migrate. When it is absent the column still
        # has to hold something non-null, and a value derived from the user id keeps it unique
        # without colliding with the `learner_` namespace real device keys live in.
        return LearnerIdentity(
            learner_key=_anon_key(x_learner_key) or f"user_{claims.user_id}",
            user_id=claims.user_id,
            email=claims.email,
        )

    key = _anon_key(x_learner_key)
    if key is None:
        raise _UNAUTHORIZED
    return LearnerIdentity(learner_key=key)


def require_account(
    identity: Annotated[LearnerIdentity, Depends(get_learner_identity)],
) -> LearnerIdentity:
    """For endpoints that genuinely need an account, such as billing."""
    if identity.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to continue",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return identity


def optional_learner_identity(
    x_learner_key: Annotated[
        list[str] | None,
        Header(alias="X-Learner-Key"),
    ] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> LearnerIdentity | None:
    """Same resolution, but a caller with no usable identity is None instead of a 401.

    For the gated *content* endpoints. They were public before entitlements existed, and a browser
    that blocks localStorage cannot produce a device key at all; answering 401 there would turn a
    privacy setting into a locked app. Such a caller simply has no unlocks and gets the free tier's
    view. An `Authorization` header that is present but bad is still a 401 — that is a real
    credential failure and worth reporting rather than silently downgrading.
    """
    if authorization is not None:
        return get_learner_identity(x_learner_key, authorization)
    key = _anon_key(x_learner_key)
    return LearnerIdentity(learner_key=key) if key else None


def auth_configured() -> bool:
    return get_settings().auth_enabled
