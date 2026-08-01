"""Verification of Supabase Auth access tokens.

The Supabase project signs access tokens **asymmetrically** (ES256) and publishes the verifying
public key as a JWKS at ``{SUPABASE_URL}/auth/v1/.well-known/jwks.json``. That is why there is no
``SUPABASE_JWT_SECRET`` anywhere in this codebase: verification needs only a public key, so the API
validates a token entirely offline and adding auth introduced no new secret to leak. The one
Supabase secret we hold — ``supabase_service_key`` — is unrelated and stays out of this path.

Verification is deliberately strict. Signature, expiry, issuer and audience are all checked, and
``sub`` must be present, because ``sub`` is the identity every owned row is keyed by: a token that
passes here is allowed to read and write that user's data.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import HTTPException, status
from jwt import PyJWKClient

from .config import get_settings

# Supabase's asymmetric signing keys. RS256 is accepted alongside ES256 because a project can be
# issued either, and the JWKS says which one is actually in use — the list only bounds what we are
# willing to honour. HS256 is absent on purpose: accepting it would mean accepting a *symmetric*
# algorithm, and the classic JWT confusion attack is to re-sign a token with HS256 using a public
# key as the shared secret. Naming only asymmetric algorithms here makes that unrepresentable.
ALLOWED_ALGORITHMS = ("ES256", "RS256")

# Supabase stamps every end-user access token with this audience. Service and anon keys carry a
# different one, so requiring it stops an API key from being replayed here as if it were a login.
EXPECTED_AUDIENCE = "authenticated"


class AuthUnavailable(RuntimeError):
    """Raised when a token arrives but the server has no Supabase project configured."""


@dataclass(frozen=True)
class AuthClaims:
    """The parts of a verified token this application acts on."""

    user_id: str
    email: str | None
    session_id: str | None


@lru_cache(maxsize=1)
def _jwk_client() -> PyJWKClient:
    """One client per process, holding the fetched key set.

    Cached because a fresh client would fetch the JWKS on *every* request — an outbound HTTPS
    round trip in the middle of each authenticated call, and a hard dependency on Supabase being
    reachable to read your own saved words. ``lifespan`` still expires the cache, so a rotated key
    is picked up without a restart.
    """
    settings = get_settings()
    if settings.jwks_url is None:
        raise AuthUnavailable("SUPABASE_URL is not configured")
    return PyJWKClient(
        settings.jwks_url,
        cache_keys=True,
        lifespan=settings.auth_jwks_ttl_s,
        timeout=10,
    )


def _signing_key(token: str):
    """Resolve the public key for a token's ``kid``. Seam for tests, which sign with their own
    throwaway EC key rather than reaching the network."""
    return _jwk_client().get_signing_key_from_jwt(token).key


def verify_access_token(token: str) -> AuthClaims:
    """Return the claims of a valid token, or raise 401.

    Every failure answers the same 401 with the same body. Distinguishing "expired" from "bad
    signature" from "wrong issuer" in the response would tell an attacker which part of a forged
    token to fix next, and the client's recovery is identical in all three cases: sign in again.
    """
    settings = get_settings()
    issuer = settings.auth_issuer
    if issuer is None:
        raise AuthUnavailable("SUPABASE_URL is not configured")

    try:
        claims = jwt.decode(
            token,
            _signing_key(token),
            algorithms=list(ALLOWED_ALGORITHMS),
            audience=EXPECTED_AUDIENCE,
            issuer=issuer,
            options={"require": ["exp", "sub", "aud", "iss"]},
        )
    except (jwt.PyJWTError, jwt.PyJWKClientError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    subject = claims.get("sub")
    # Guarded rather than trusted: `sub` lands in a String(36) column and is the key every owned
    # row hangs off, so a token carrying something surprising there must not reach the database.
    if not isinstance(subject, str) or not 1 <= len(subject) <= 36:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
            headers={"WWW-Authenticate": "Bearer"},
        )

    email = claims.get("email")
    session_id = claims.get("session_id")
    return AuthClaims(
        user_id=subject,
        email=email if isinstance(email, str) and email else None,
        session_id=session_id if isinstance(session_id, str) and session_id else None,
    )
