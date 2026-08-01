"""Checkout, the billing portal, and the Stripe webhook."""

from __future__ import annotations

import logging
from typing import Annotated

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..billing import (
    BillingUnavailable,
    apply_event,
    create_checkout_url,
    create_portal_url,
    verify_event,
)
from ..db import get_db
from ..entitlements import ensure_profile
from ..identity import LearnerIdentity, require_account
from ..schemas import CheckoutOut

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/billing", tags=["billing"])

_UNAVAILABLE = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="Subscriptions are not configured on this server",
)


def _profile_or_503(db: Session, identity: LearnerIdentity):
    profile = ensure_profile(db, identity)
    if profile is None:
        raise _UNAVAILABLE
    return profile


@router.post("/checkout", response_model=CheckoutOut)
def start_checkout(
    identity: Annotated[LearnerIdentity, Depends(require_account)],
    db: Annotated[Session, Depends(get_db)],
) -> CheckoutOut:
    """Begin a subscription. Requires an account, so the payment has something to attach to.

    Signing in first is not a hurdle invented here: a subscription has to belong to an identity
    that survives clearing the browser, and an anonymous device key does not.
    """
    try:
        return CheckoutOut(url=create_checkout_url(db, _profile_or_503(db, identity)))
    except BillingUnavailable:
        raise _UNAVAILABLE from None
    except stripe.StripeError as exc:
        # Stripe's own message can name a misconfigured price or a declined account. Logged in full,
        # but not returned: it is written for the operator, not the learner.
        log.warning("stripe checkout failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not start checkout. Please try again.",
        ) from None


@router.post("/portal", response_model=CheckoutOut)
def open_portal(
    identity: Annotated[LearnerIdentity, Depends(require_account)],
    db: Annotated[Session, Depends(get_db)],
) -> CheckoutOut:
    try:
        return CheckoutOut(url=create_portal_url(db, _profile_or_503(db, identity)))
    except BillingUnavailable:
        raise _UNAVAILABLE from None
    except stripe.StripeError as exc:
        log.warning("stripe portal failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not open the billing portal. Please try again.",
        ) from None


@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, str]:
    """The only endpoint that can grant premium.

    Unauthenticated by necessity — Stripe holds no session — and therefore authenticated by
    signature instead. `verify_event` is the whole security boundary: the raw body is read here,
    unparsed, because the signature covers Stripe's exact bytes and re-serialising would break it.

    Verification failures answer 400 so Stripe marks the delivery failed and retries. Everything
    that verifies answers 200, including events for accounts this server does not know about: a 500
    on an event nobody here can act on would have Stripe redelivering it for days.
    """
    payload = await request.body()
    signature = request.headers.get("Stripe-Signature")

    try:
        event = verify_event(payload, signature)
    except BillingUnavailable:
        # Refuse rather than process unverified. Without the secret there is no way to tell a real
        # Stripe delivery from anyone who has read this source and knows the payload shape — and
        # this endpoint's whole job is granting paid access.
        log.error("stripe webhook received but STRIPE_WEBHOOK_SECRET is not configured")
        raise _UNAVAILABLE from None
    except (stripe.SignatureVerificationError, ValueError) as exc:
        log.warning("rejected stripe webhook: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid signature",
        ) from None

    try:
        outcome = apply_event(db, event)
    except Exception:
        # A 500 tells Stripe to retry, which is what we want for a transient database failure.
        log.exception("failed to apply stripe event")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not process event",
        ) from None

    log.info("stripe event applied: %s", outcome)
    return {"status": "ok", "outcome": outcome}
