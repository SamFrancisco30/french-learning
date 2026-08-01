"""Stripe billing: the webhook is the only thing that can grant premium.

Webhook payloads here are signed with a real HMAC in the format Stripe uses, so
`stripe.Webhook.construct_event` does its actual verification rather than being stubbed. That is the
one security boundary in this feature — the endpoint is unauthenticated by necessity — so a test
that bypassed the signature would be testing nothing worth testing.

No network: `billing._api` is replaced with a fake Stripe surface. Nothing here reaches Stripe, and
none of it needs a Stripe account to run.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import billing
from app.config import get_settings
from app.db import get_db
from app.entitlements import _premium_is_current
from app.identity import LearnerIdentity, get_learner_identity, optional_learner_identity
from app.models import Base, UserProfile
from app.routers import account
from app.routers import billing as billing_router

USER = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CUSTOMER = "cus_test123"
SUBSCRIPTION = "sub_test123"
WEBHOOK_SECRET = "whsec_testsecret"

app = FastAPI()
app.include_router(account.router)
app.include_router(billing_router.router)


@pytest.fixture
def db() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
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
def client(db: Session) -> TestClient:
    del db
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def profile(db: Session) -> UserProfile:
    row = UserProfile(user_id=USER, email="learner@example.com", tier="free")
    db.add(row)
    db.commit()
    return row


def sign_in() -> None:
    identity = LearnerIdentity(
        learner_key="learner_device-one", user_id=USER, email="learner@example.com"
    )
    app.dependency_overrides[get_learner_identity] = lambda: identity
    app.dependency_overrides[optional_learner_identity] = lambda: identity


@pytest.fixture(autouse=True)
def _reset_settings() -> Generator[None, None, None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def stripe_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_test_x")
    get_settings.cache_clear()


PERIOD_END = int((datetime.now(UTC) + timedelta(days=30)).timestamp())


class FakeStripe:
    """Just enough Stripe surface for the paths under test, and a record of what was asked for."""

    def __init__(self, *, period_end: int | None = PERIOD_END, on_items: bool = False) -> None:
        self.created_customers: list[dict] = []
        self.created_sessions: list[dict] = []
        self.created_portals: list[dict] = []
        self._period_end = period_end
        self._on_items = on_items

        outer = self

        class Customer:
            @staticmethod
            def create(**kwargs):
                outer.created_customers.append(kwargs)
                return type("C", (), {"id": CUSTOMER})()

        class Session_:
            @staticmethod
            def create(**kwargs):
                outer.created_sessions.append(kwargs)
                return type("S", (), {"url": "https://checkout.stripe.test/session"})()

        class Checkout:
            Session = Session_

        class Portal:
            @staticmethod
            def create(**kwargs):
                outer.created_portals.append(kwargs)
                return type("P", (), {"url": "https://billing.stripe.test/portal"})()

        class BillingPortal:
            Session = Portal

        class Subscription:
            @staticmethod
            def retrieve(subscription_id: str):
                # Exercises both shapes Stripe uses for the billing period: the legacy top-level
                # field and the newer per-item one.
                if outer._on_items:
                    return {"id": subscription_id, "items": {"data": [{"current_period_end": outer._period_end}]}}
                return {"id": subscription_id, "current_period_end": outer._period_end}

        self.Customer = Customer
        self.checkout = Checkout
        self.billing_portal = BillingPortal
        self.Subscription = Subscription


@pytest.fixture
def fake_stripe(monkeypatch: pytest.MonkeyPatch) -> FakeStripe:
    fake = FakeStripe()
    monkeypatch.setattr(billing, "_api", lambda: fake)
    return fake


def signed_headers(payload: bytes, *, secret: str = WEBHOOK_SECRET, timestamp: int | None = None) -> dict[str, str]:
    """Build a genuine Stripe-Signature header, the way Stripe's servers do."""
    ts = timestamp if timestamp is not None else int(time.time())
    signature = hmac.new(
        secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return {"Stripe-Signature": f"t={ts},v1={signature}"}


def event_bytes(kind: str, obj: dict[str, Any]) -> bytes:
    """A faithful Stripe event envelope.

    `"object": "event"` is not decoration: the SDK reads it to tell a v1 event from a v2 one, and a
    payload without it raises before the handler is reached. Real deliveries always carry it, so
    omitting it here would have made every test fail for a reason Stripe never produces.
    """
    return json.dumps(
        {
            "id": "evt_1",
            "object": "event",
            "api_version": "2024-06-20",
            "type": kind,
            "data": {"object": obj},
        }
    ).encode()


def post_event(client: TestClient, kind: str, obj: dict[str, Any], **header_kwargs):
    payload = event_bytes(kind, obj)
    return client.post(
        "/api/billing/webhook",
        content=payload,
        headers={"Content-Type": "application/json", **signed_headers(payload, **header_kwargs)},
    )


# --- the security boundary -------------------------------------------------------------------


def test_webhook_rejects_an_unsigned_payload(
    client: TestClient, profile: UserProfile, db: Session, stripe_configured: None
) -> None:
    response = client.post(
        "/api/billing/webhook",
        content=event_bytes("checkout.session.completed", {"client_reference_id": USER}),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    db.refresh(profile)
    assert profile.tier == "free"


def test_webhook_rejects_a_forged_signature(
    client: TestClient, profile: UserProfile, db: Session, stripe_configured: None
) -> None:
    """The attack this stops: anyone who has read this source knows the payload shape, and the
    endpoint has to be reachable without a session. The signature is the only thing in the way."""
    response = post_event(
        client,
        "checkout.session.completed",
        {"client_reference_id": USER, "subscription": SUBSCRIPTION, "customer": CUSTOMER},
        secret="whsec_wrongsecret",
    )

    assert response.status_code == 400
    db.refresh(profile)
    assert profile.tier == "free"


def test_webhook_rejects_a_replayed_payload_signed_long_ago(
    client: TestClient, profile: UserProfile, db: Session, stripe_configured: None
) -> None:
    """Stripe's tolerance window. A captured body with its original signature must not be
    replayable forever."""
    response = post_event(
        client,
        "checkout.session.completed",
        {"client_reference_id": USER, "subscription": SUBSCRIPTION},
        timestamp=int(time.time()) - 86_400,
    )

    assert response.status_code == 400
    db.refresh(profile)
    assert profile.tier == "free"


def test_webhook_refuses_to_process_when_no_secret_is_configured(
    client: TestClient, profile: UserProfile, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails closed. With no secret there is no way to distinguish Stripe from anyone else, and
    this is the endpoint that hands out paid access."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(
        billing, "get_settings", lambda: type(get_settings())(_env_file=None, stripe_secret_key="sk_test_x")
    )

    response = post_event(client, "checkout.session.completed", {"client_reference_id": USER})

    assert response.status_code == 503
    db.refresh(profile)
    assert profile.tier == "free"


# --- granting and revoking -------------------------------------------------------------------


def test_completed_checkout_grants_premium_with_an_expiry(
    client: TestClient, profile: UserProfile, db: Session, stripe_configured: None, fake_stripe: FakeStripe
) -> None:
    response = post_event(
        client,
        "checkout.session.completed",
        {"client_reference_id": USER, "subscription": SUBSCRIPTION, "customer": CUSTOMER},
    )

    assert response.status_code == 200
    db.refresh(profile)
    assert profile.tier == "premium"
    assert profile.stripe_subscription_id == SUBSCRIPTION
    assert profile.stripe_customer_id == CUSTOMER
    # An expiry, not an open-ended grant: one successful checkout must not outlive the subscription.
    assert profile.premium_until is not None


def test_period_end_is_read_from_the_subscription_item_when_thats_where_it_is(
    client: TestClient, profile: UserProfile, db: Session, stripe_configured: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stripe moved `current_period_end` onto subscription items. Reading only the old location
    would leave premium_until NULL, which the entitlement layer treats as never expiring."""
    monkeypatch.setattr(billing, "_api", lambda: FakeStripe(on_items=True))

    post_event(
        client,
        "checkout.session.completed",
        {"client_reference_id": USER, "subscription": SUBSCRIPTION, "customer": CUSTOMER},
    )

    db.refresh(profile)
    assert profile.premium_until is not None


def test_subscription_deleted_revokes_premium(
    client: TestClient, profile: UserProfile, db: Session, stripe_configured: None, fake_stripe: FakeStripe
) -> None:
    profile.tier = "premium"
    profile.stripe_customer_id = CUSTOMER
    profile.stripe_subscription_id = SUBSCRIPTION
    db.commit()

    response = post_event(
        client, "customer.subscription.deleted", {"id": SUBSCRIPTION, "customer": CUSTOMER}
    )

    assert response.status_code == 200
    db.refresh(profile)
    assert profile.tier == "free"
    assert profile.premium_until is None


@pytest.mark.parametrize(
    ("status_value", "expected_tier"),
    [
        ("active", "premium"),
        ("trialing", "premium"),
        # Dunning: a failed renewal that Stripe is still retrying keeps access, bounded by the
        # period end already recorded.
        ("past_due", "premium"),
        ("canceled", "free"),
        ("unpaid", "free"),
        ("incomplete_expired", "free"),
    ],
)
def test_subscription_status_decides_the_tier(
    client: TestClient, profile: UserProfile, db: Session, stripe_configured: None,
    fake_stripe: FakeStripe, status_value: str, expected_tier: str,
) -> None:
    profile.stripe_customer_id = CUSTOMER
    db.commit()

    post_event(
        client,
        "customer.subscription.updated",
        {
            "id": SUBSCRIPTION,
            "customer": CUSTOMER,
            "status": status_value,
            "current_period_end": PERIOD_END,
        },
    )

    db.refresh(profile)
    assert profile.tier == expected_tier


def test_applying_the_same_event_twice_is_the_same_as_once(
    client: TestClient, profile: UserProfile, db: Session, stripe_configured: None, fake_stripe: FakeStripe
) -> None:
    """Stripe delivers at least once and retries on timeout, so redelivery must not compound."""
    obj = {"client_reference_id": USER, "subscription": SUBSCRIPTION, "customer": CUSTOMER}

    post_event(client, "checkout.session.completed", obj)
    db.refresh(profile)
    first = (profile.tier, profile.premium_until, profile.stripe_subscription_id)
    post_event(client, "checkout.session.completed", obj)
    db.refresh(profile)

    assert (profile.tier, profile.premium_until, profile.stripe_subscription_id) == first


def test_an_event_for_an_unknown_customer_is_acknowledged_not_retried(
    client: TestClient, db: Session, stripe_configured: None, fake_stripe: FakeStripe
) -> None:
    """2xx on purpose. A Stripe account can hold customers this app has never seen, and a 500 would
    have Stripe redelivering an event nobody here can act on for days."""
    response = post_event(
        client,
        "customer.subscription.updated",
        {"id": "sub_other", "customer": "cus_unknown", "status": "active"},
    )

    assert response.status_code == 200
    assert "no matching account" in response.json()["outcome"]


def test_the_customer_id_links_an_event_with_no_reference_of_ours(
    client: TestClient, profile: UserProfile, db: Session, stripe_configured: None, fake_stripe: FakeStripe
) -> None:
    """Subscription lifecycle events carry no client_reference_id, so the stored customer id is the
    only link back to the account."""
    profile.stripe_customer_id = CUSTOMER
    db.commit()

    post_event(
        client,
        "customer.subscription.updated",
        {"id": SUBSCRIPTION, "customer": CUSTOMER, "status": "active", "current_period_end": PERIOD_END},
    )

    db.refresh(profile)
    assert profile.tier == "premium"


def test_renewal_invoice_extends_the_period(
    client: TestClient, profile: UserProfile, db: Session, stripe_configured: None, fake_stripe: FakeStripe
) -> None:
    profile.tier = "premium"
    profile.stripe_customer_id = CUSTOMER
    profile.stripe_subscription_id = SUBSCRIPTION
    profile.premium_until = datetime.now(UTC) - timedelta(days=1)
    db.commit()

    response = post_event(
        client, "invoice.paid", {"customer": CUSTOMER, "subscription": SUBSCRIPTION}
    )

    assert response.status_code == 200
    db.refresh(profile)
    assert profile.premium_until is not None
    # Asserted through the entitlement predicate rather than by comparing the column directly:
    # SQLite hands the timestamp back naive where Postgres returns it aware, and it is
    # `_premium_is_current` that reconciles the two. Comparing raw datetimes here would be testing
    # the driver's tzinfo handling instead of whether the learner actually has access.
    assert _premium_is_current(profile) is True


# --- checkout and portal ---------------------------------------------------------------------


def test_checkout_requires_an_account(client: TestClient, stripe_configured: None) -> None:
    """An anonymous device key cannot own a subscription: it does not survive clearing the browser,
    and the learner would have no way to prove they had paid."""
    response = client.post("/api/billing/checkout", headers={"X-Learner-Key": "learner_device-one"})

    assert response.status_code == 401


def test_checkout_returns_a_stripe_hosted_url(
    client: TestClient, profile: UserProfile, stripe_configured: None, fake_stripe: FakeStripe
) -> None:
    sign_in()

    response = client.post("/api/billing/checkout")

    assert response.status_code == 200
    assert response.json()["url"].startswith("https://checkout.stripe.test/")
    session = fake_stripe.created_sessions[0]
    assert session["mode"] == "subscription"
    # The account id travels with the session, so the webhook can attribute the payment without
    # trusting the browser that comes back.
    assert session["client_reference_id"] == USER


def test_checkout_is_unavailable_rather_than_broken_without_stripe_keys(
    client: TestClient, profile: UserProfile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The app has to run with no Stripe account at all — only the upgrade path is missing."""
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_ID", raising=False)
    monkeypatch.setattr(billing, "get_settings", lambda: type(get_settings())(_env_file=None))
    sign_in()

    response = client.post("/api/billing/checkout")

    assert response.status_code == 503


def test_portal_reuses_the_stored_customer(
    client: TestClient, profile: UserProfile, db: Session, stripe_configured: None, fake_stripe: FakeStripe
) -> None:
    """A returning subscriber must be the same Stripe customer, or their invoices and payment
    methods scatter across duplicates."""
    profile.stripe_customer_id = CUSTOMER
    db.commit()
    sign_in()

    response = client.post("/api/billing/portal")

    assert response.status_code == 200
    assert fake_stripe.created_customers == []
    assert fake_stripe.created_portals[0]["customer"] == CUSTOMER
