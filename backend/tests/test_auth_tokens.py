"""Verification of Supabase access tokens.

Tokens are signed here with a throwaway P-256 key and the JWKS lookup is replaced, so these tests
never reach the network and do not depend on the real project's keys. What is being tested is our
`jwt.decode` configuration, which is the part that decides whether a forged token is honoured.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException

from app import auth
from app.config import Settings, get_settings

PROJECT_URL = "https://test-project.supabase.co"
ISSUER = f"{PROJECT_URL}/auth/v1"
USER_ID = "123e4567-e89b-12d3-a456-426614174000"


def _b64(raw: bytes) -> bytes:
    """base64url with the padding stripped, as JWT requires."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


@pytest.fixture
def signing_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture(autouse=True)
def auth_settings(monkeypatch: pytest.MonkeyPatch, signing_key) -> None:
    """Point the verifier at a fake project and at our own key.

    Environment variables rather than object patching, because Settings reads backend/.env and env
    vars take precedence over it — otherwise these tests would inherit the developer's real
    SUPABASE_URL and the issuer assertions would be meaningless.
    """
    monkeypatch.setenv("SUPABASE_URL", PROJECT_URL)
    monkeypatch.setenv("SUPABASE_ANON_KEY", "publishable-test-key")
    get_settings.cache_clear()
    auth._jwk_client.cache_clear()
    monkeypatch.setattr(auth, "_signing_key", lambda _token: signing_key.public_key())
    yield
    get_settings.cache_clear()
    auth._jwk_client.cache_clear()


def make_token(
    signing_key,
    *,
    alg: str = "ES256",
    issuer: str = ISSUER,
    audience: str = "authenticated",
    subject: str | None = USER_ID,
    email: str | None = "learner@example.com",
    expires_in_s: int = 3600,
    key=None,
    **extra,
) -> str:
    now = dt.datetime.now(dt.UTC)
    payload: dict = {
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + dt.timedelta(seconds=expires_in_s),
        "session_id": "sess-1",
        **extra,
    }
    if subject is not None:
        payload["sub"] = subject
    if email is not None:
        payload["email"] = email
    return jwt.encode(payload, key if key is not None else signing_key, algorithm=alg)


def test_accepts_a_valid_token_and_returns_its_identity(signing_key) -> None:
    claims = auth.verify_access_token(make_token(signing_key))

    assert claims.user_id == USER_ID
    assert claims.email == "learner@example.com"
    assert claims.session_id == "sess-1"


def test_email_is_optional(signing_key) -> None:
    # Supabase omits `email` for some sign-in methods; the account is still valid.
    claims = auth.verify_access_token(make_token(signing_key, email=None))

    assert claims.user_id == USER_ID
    assert claims.email is None


@pytest.mark.parametrize(
    ("case", "kwargs"),
    [
        ("expired", {"expires_in_s": -60}),
        ("wrong issuer", {"issuer": "https://attacker.supabase.co/auth/v1"}),
        ("issuer missing path", {"issuer": PROJECT_URL}),
        ("anon-key audience replayed as a login", {"audience": "anon"}),
        ("service-role audience", {"audience": "service_role"}),
        ("no subject", {"subject": None}),
        ("subject too long for the column", {"subject": "x" * 37}),
        ("subject not a string", {"subject": 12345}),
        ("empty subject", {"subject": ""}),
    ],
)
def test_rejects_bad_claims(signing_key, case: str, kwargs: dict) -> None:
    with pytest.raises(HTTPException) as raised:
        auth.verify_access_token(make_token(signing_key, **kwargs))

    assert raised.value.status_code == 401, case


def test_rejects_a_token_signed_by_a_different_key(signing_key) -> None:
    # The shape of a stolen-then-re-signed token: every claim is right, the signature is not.
    other = ec.generate_private_key(ec.SECP256R1())

    with pytest.raises(HTTPException) as raised:
        auth.verify_access_token(make_token(signing_key, key=other))

    assert raised.value.status_code == 401


def test_rejects_symmetric_algorithm_confusion(signing_key) -> None:
    """The classic JWT attack: re-sign with HS256 using the *public* key as the shared secret.

    A verifier that trusts the token's own `alg` header would hand the public key to HMAC and
    accept it, since the public key is not secret. ALLOWED_ALGORITHMS names only asymmetric
    algorithms, so the algorithm is never taken from the token.
    """
    public_pem = signing_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    # Assembled by hand rather than with jwt.encode, which refuses to use an asymmetric key as an
    # HMAC secret. That refusal is PyJWT protecting the *signer*; an attacker has no such scruples,
    # so testing through encode() would test the guard rail instead of our verifier.
    claims = {
        "iss": ISSUER,
        "aud": "authenticated",
        "sub": USER_ID,
        "exp": int((dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)).timestamp()),
    }
    segments = b".".join(
        _b64(json.dumps(part, separators=(",", ":")).encode())
        for part in ({"alg": "HS256", "typ": "JWT"}, claims)
    )
    signature = hmac.new(public_pem, segments, hashlib.sha256).digest()
    forged = (segments + b"." + _b64(signature)).decode()

    with pytest.raises(HTTPException) as raised:
        auth.verify_access_token(forged)

    assert raised.value.status_code == 401
    assert "HS256" not in auth.ALLOWED_ALGORITHMS


def test_rejects_an_unsigned_token(signing_key) -> None:
    forged = jwt.encode(
        {"iss": ISSUER, "aud": "authenticated", "sub": USER_ID, "exp": 9999999999},
        key="",
        algorithm="none",
    )

    with pytest.raises(HTTPException) as raised:
        auth.verify_access_token(forged)

    assert raised.value.status_code == 401


def test_failures_do_not_disclose_which_check_failed(signing_key) -> None:
    """One message for every failure, so a forged token gets no feedback to iterate against."""
    details = set()
    for kwargs in ({"expires_in_s": -60}, {"issuer": "https://x.supabase.co/auth/v1"}, {"audience": "anon"}):
        with pytest.raises(HTTPException) as raised:
            auth.verify_access_token(make_token(signing_key, **kwargs))
        details.add(raised.value.detail)

    assert details == {"Invalid or expired session"}


def test_reports_unavailable_rather_than_unauthorized_without_a_project(
    monkeypatch: pytest.MonkeyPatch, signing_key
) -> None:
    """A server with no SUPABASE_URL cannot verify anything. That is a deployment fault, and
    answering 401 would send a client with a perfectly good token into a sign-in loop."""
    # `_env_file=None` as well as delenv: clearing the environment variable alone is not enough,
    # because pydantic-settings then falls back to backend/.env, which on a configured developer
    # machine supplies the very value this test needs absent.
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setattr(auth, "get_settings", lambda: Settings(_env_file=None))

    with pytest.raises(auth.AuthUnavailable):
        auth.verify_access_token(make_token(signing_key))
