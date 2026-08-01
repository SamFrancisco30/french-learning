"""Stripe subscriptions: the only thing that grants the premium tier.

Two properties this module exists to hold onto.

**No card data ever reaches this application.** The browser is redirected to Stripe-hosted Checkout
and comes back; there is no Stripe.js in the bundle and no card field in our HTML. That is why the
frontend needs no Stripe key at all, and why this file is the entire payment surface.

**The webhook is the only writer of `tier`.** Nothing a client can say promotes an account — not a
success redirect, not a query parameter, not user metadata. A learner who lands on the success URL
without a signed webhook event is still on the free tier, because the success URL is just a URL and
anyone can visit it. `stripe.Webhook.construct_event` is what makes an event trustworthy, and it is
never skipped: with no webhook secret configured the endpoint refuses to process rather than
processing unverified.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import stripe
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .entitlements import TIER_FREE, TIER_PREMIUM
from .models import UserProfile, utcnow

log = logging.getLogger(__name__)

# Subscription states that entitle. "past_due" is included deliberately: it is Stripe's dunning
# window, when a renewal has failed but is still being retried. Cutting a paying learner off on the
# first failed charge — often an expired card they will update — is the wrong trade, and the period
# end already recorded still bounds how long the grace lasts.
ENTITLING_STATUSES = frozenset({"active", "trialing", "past_due"})


class BillingUnavailable(RuntimeError):
    """Stripe is not configured on this server."""


def _field(obj: Any, name: str) -> Any:
    """Read a field from a Stripe object.

    Stripe's SDK objects support attribute access, while webhook payloads reconstructed from JSON —
    and the ones tests build — are plain dicts. Both appear in this module, so every read goes
    through here rather than repeating the pair of accessors at each use.
    """
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _id_of(value: Any) -> str | None:
    """Stripe fields hold either an id or the expanded object. Normalise to the id."""
    if value is None:
        return None
    expanded = _field(value, "id")
    return str(expanded if expanded is not None else value)


def _api() -> Any:
    settings = get_settings()
    if not settings.stripe_secret_key:
        raise BillingUnavailable("STRIPE_SECRET_KEY is not set")
    # Assigned per call rather than at import: the key can arrive from the environment after this
    # module is imported, and a module-level read would cache its absence for the process lifetime.
    stripe.api_key = settings.stripe_secret_key
    return stripe


def _period_end(subscription: Any) -> int | None:
    """Unix seconds when the paid period ends.

    Falls back to the subscription *item* when the top-level field is absent. Stripe moved
    `current_period_end` off the subscription and onto its items, so depending on API version a
    single-price subscription reports it in one place or the other — reading only one would silently
    yield `premium_until = None`, which `_premium_is_current` treats as a grant that never lapses.
    """
    end = _field(subscription, "current_period_end")
    if end is not None:
        return int(end)

    items = _field(subscription, "items")
    data = _field(items, "data") if items is not None else None
    if data:
        end = _field(data[0], "current_period_end")
    return int(end) if end is not None else None


def _as_utc(unix_seconds: int | None) -> datetime | None:
    if unix_seconds is None:
        return None
    return datetime.fromtimestamp(unix_seconds, tz=UTC)


def ensure_customer(db: Session, profile: UserProfile) -> str:
    """This account's Stripe customer id, creating the customer on first checkout.

    Stored so a returning subscriber is the same customer rather than a new one each time, which is
    what keeps their invoices, payment methods and subscription history together.
    """
    if profile.stripe_customer_id:
        return profile.stripe_customer_id

    customer = _api().Customer.create(
        email=profile.email or None,
        # So a row in the Stripe dashboard can be traced to an account without a lookup table.
        metadata={"user_id": profile.user_id},
    )
    profile.stripe_customer_id = customer.id
    db.commit()
    return customer.id


def create_checkout_url(db: Session, profile: UserProfile) -> str:
    settings = get_settings()
    if not settings.stripe_price_id:
        raise BillingUnavailable("STRIPE_PRICE_ID is not set")

    base = settings.app_base_url.rstrip("/")
    session = _api().checkout.Session.create(
        mode="subscription",
        customer=ensure_customer(db, profile),
        line_items=[{"price": settings.stripe_price_id, "quantity": 1}],
        # The authoritative link back to the account, read in the webhook rather than trusting
        # anything the returning browser carries.
        client_reference_id=profile.user_id,
        metadata={"user_id": profile.user_id},
        subscription_data={"metadata": {"user_id": profile.user_id}},
        # Hash fragments, to match the app's hash router. The success page confirms nothing by
        # itself — it polls /api/me, which reflects what the webhook actually wrote.
        success_url=f"{base}/#/account?checkout=success",
        cancel_url=f"{base}/#/account?checkout=cancelled",
        allow_promotion_codes=True,
    )
    return session.url


def create_portal_url(db: Session, profile: UserProfile) -> str:
    """Stripe's own billing portal: change card, see invoices, cancel.

    Deliberately not reimplemented. Cancellation, proration and dunning are Stripe's to get right,
    and a hand-built cancel button that only flipped our `tier` column would leave the subscription
    still billing.
    """
    base = get_settings().app_base_url.rstrip("/")
    session = _api().billing_portal.Session.create(
        customer=ensure_customer(db, profile),
        return_url=f"{base}/#/account",
    )
    return session.url


def verify_event(payload: bytes, signature: str | None) -> Any:
    """Construct a Stripe event from a raw request body, or raise.

    The raw bytes matter: the signature covers the exact body Stripe sent, so re-serialising parsed
    JSON changes the whitespace and invalidates it. That is why the route reads `await
    request.body()` instead of taking a parsed model.
    """
    settings = get_settings()
    if not settings.stripe_webhook_secret:
        raise BillingUnavailable("STRIPE_WEBHOOK_SECRET is not set")
    if not signature:
        raise stripe.SignatureVerificationError("missing Stripe-Signature header", None)
    return stripe.Webhook.construct_event(payload, signature, settings.stripe_webhook_secret)


def _profile_for_event(db: Session, obj: Any) -> UserProfile | None:
    """Find the account an event belongs to.

    `client_reference_id` and metadata come first because we set them at checkout, so they survive
    a customer being recreated. The customer lookup is the fallback for subscription lifecycle
    events, which carry no reference of ours.
    """
    user_id = _field(obj, "client_reference_id")
    if not user_id:
        metadata = _field(obj, "metadata") or {}
        user_id = _field(metadata, "user_id")
    if user_id:
        profile = db.get(UserProfile, str(user_id))
        if profile is not None:
            return profile

    customer_id = _id_of(_field(obj, "customer"))
    if customer_id:
        return db.scalar(
            select(UserProfile).where(UserProfile.stripe_customer_id == customer_id)
        )
    return None


def apply_event(db: Session, event: Any) -> str:
    """Update an account's tier from a *verified* event. Returns what was done, for logging.

    Idempotent by construction: every branch assigns absolute state rather than incrementing, so
    Stripe's at-least-once delivery — and its retries after a timeout — cannot double-apply.
    """
    kind = _field(event, "type")
    data = _field(event, "data") or {}
    obj = _field(data, "object")
    if obj is None:
        return "ignored: no object"

    profile = _profile_for_event(db, obj)
    if profile is None:
        # Not an error: a Stripe account can hold customers this application has never seen, and
        # answering 2xx anyway stops Stripe retrying an event nobody here can act on.
        log.info("stripe event %s had no matching profile", kind)
        return f"ignored: {kind} with no matching account"

    if kind == "checkout.session.completed":
        subscription_id = _id_of(_field(obj, "subscription"))
        customer_id = _id_of(_field(obj, "customer"))
        profile.stripe_subscription_id = subscription_id
        if customer_id:
            profile.stripe_customer_id = customer_id

        # Retrieved rather than assumed. The session carries no billing period, and granting
        # premium with no expiry would make one successful checkout permanent even if the
        # subscription later lapsed and every subsequent webhook was missed.
        profile.premium_until = None
        if subscription_id:
            try:
                profile.premium_until = _as_utc(
                    _period_end(_api().Subscription.retrieve(subscription_id))
                )
            except Exception:
                log.warning("could not retrieve subscription %s", subscription_id, exc_info=True)
        profile.tier = TIER_PREMIUM
        profile.updated_at = utcnow()
        db.commit()
        return "granted premium"

    if kind in {"customer.subscription.created", "customer.subscription.updated"}:
        status = _field(obj, "status")
        subscription_id = _id_of(_field(obj, "id"))
        if subscription_id:
            profile.stripe_subscription_id = subscription_id
        profile.premium_until = _as_utc(_period_end(obj))
        profile.tier = TIER_PREMIUM if status in ENTITLING_STATUSES else TIER_FREE
        profile.updated_at = utcnow()
        db.commit()
        return f"subscription {status}"

    if kind == "customer.subscription.deleted":
        # Fires when the subscription actually ends, not when cancellation is requested — a
        # cancel-at-period-end arrives as an `updated` event first, so access is not cut off early.
        profile.tier = TIER_FREE
        profile.premium_until = None
        profile.stripe_subscription_id = None
        profile.updated_at = utcnow()
        db.commit()
        return "premium ended"

    if kind in {"invoice.paid", "invoice.payment_succeeded"}:
        # A renewal. `customer.subscription.updated` normally carries the new period end, but
        # handling the invoice too means one missed subscription event does not quietly expire a
        # learner who has in fact paid.
        if profile.stripe_subscription_id:
            try:
                subscription = _api().Subscription.retrieve(profile.stripe_subscription_id)
            except Exception:
                log.warning("could not refresh subscription after invoice", exc_info=True)
            else:
                profile.premium_until = _as_utc(_period_end(subscription))
                profile.tier = TIER_PREMIUM
                profile.updated_at = utcnow()
                db.commit()
                return "renewed"
        return "ignored: invoice with no subscription"

    return f"ignored: {kind}"
