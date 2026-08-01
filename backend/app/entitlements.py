"""Who may open which listening units.

Three tiers, and the shape of the allowance matters as much as its size:

    anon     2 units    no account
    free     5 units    signed in
    premium  unlimited  paid

The allowance is a set of *chosen* units, not the first N in the library. A learner picks which
recordings to spend it on, and what they picked stays theirs — a "first N by id" rule would silently
change which units were free every time something new was ingested, retroactively taking away a
recording someone was halfway through.

What is deliberately NOT metered: word lookup, sentence analysis, expressions and the whole
vocabulary book. Highlighting a word is available on every tier including signed out, so those
endpoints take no entitlement check at all. Metering them would gate the one feature that makes the
free tier worth using, and the free tier is what sells the paid one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import get_settings
from .identity import LearnerIdentity, owner_clause
from .models import UnitUnlock, UserProfile

TIER_ANON = "anon"
TIER_FREE = "free"
TIER_PREMIUM = "premium"


class QuotaExhausted(Exception):
    """The learner has spent their allowance and this unit is not one of the ones they opened."""

    def __init__(self, entitlement: Entitlement) -> None:
        super().__init__("Listening allowance exhausted")
        self.entitlement = entitlement


@dataclass(frozen=True)
class Entitlement:
    tier: str
    # None means unlimited. Distinguished from 0 on purpose: `not limit` would treat premium as
    # having no access at all.
    unit_limit: int | None
    unlocked_unit_ids: tuple[int, ...]
    email: str | None = None
    premium_until: datetime | None = None

    @property
    def is_premium(self) -> bool:
        return self.unit_limit is None

    @property
    def remaining(self) -> int | None:
        if self.unit_limit is None:
            return None
        return max(0, self.unit_limit - len(self.unlocked_unit_ids))

    def allows(self, unit_id: int) -> bool:
        return self.is_premium or unit_id in self.unlocked_unit_ids

    def can_unlock_more(self) -> bool:
        return self.unit_limit is None or len(self.unlocked_unit_ids) < self.unit_limit


def _premium_is_current(profile: UserProfile, now: datetime | None = None) -> bool:
    """Premium with an expiry in the past is not premium.

    `premium_until` holds the end of the period Stripe has actually been paid for, so a cancelled
    subscription keeps working until then rather than being cut off at the moment of cancellation.
    A NULL expiry means an open-ended grant (a comp account, or a manual upgrade) and never lapses.
    """
    if profile.tier != TIER_PREMIUM:
        return False
    if profile.premium_until is None:
        return True
    now = now or datetime.now(UTC)
    until = profile.premium_until
    # Postgres hands back an aware datetime; SQLite drops the offset. Assume UTC for the naive
    # case rather than letting the comparison raise.
    if until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    return until > now


def get_profile(db: Session, user_id: str) -> UserProfile | None:
    return db.get(UserProfile, user_id)


def ensure_profile(db: Session, identity: LearnerIdentity) -> UserProfile | None:
    """Fetch this account's profile, creating it on first sight.

    Lazily on first authenticated request rather than from a Supabase signup webhook: an account
    created in the Supabase dashboard, or one that existed before this table did, then behaves
    identically to one created through the app instead of hitting a missing-profile error.

    Never sets `tier` to anything but the default. Promotion happens only in the billing webhook.
    """
    if identity.user_id is None:
        return None

    profile = db.get(UserProfile, identity.user_id)
    if profile is not None:
        # Keep the cached email in step with the token, so a learner who changes their address in
        # Supabase does not leave a stale one behind on invoices and in the account panel.
        if identity.email and profile.email != identity.email:
            profile.email = identity.email
            db.commit()
        return profile

    profile = UserProfile(user_id=identity.user_id, email=identity.email, tier=TIER_FREE)
    db.add(profile)
    try:
        db.commit()
    except IntegrityError:
        # Two requests from the same fresh sign-in raced to create the row. Whoever lost simply
        # reads the winner's.
        db.rollback()
        profile = db.get(UserProfile, identity.user_id)
    if profile is not None:
        db.refresh(profile)
    return profile


def unlocked_unit_ids(db: Session, identity: LearnerIdentity) -> tuple[int, ...]:
    rows = db.scalars(
        select(UnitUnlock.unit_id)
        .where(owner_clause(UnitUnlock, identity))
        .order_by(UnitUnlock.created_at, UnitUnlock.id)
    ).all()
    return tuple(rows)


def resolve_entitlement(db: Session, identity: LearnerIdentity | None) -> Entitlement:
    """The caller's tier, allowance and the units they have already opened.

    A caller with no identity at all — a browser with localStorage blocked, so it cannot even hold
    a device key — gets the anonymous tier with nothing unlocked, rather than an error. They can
    still browse the library and use word lookup.
    """
    settings = get_settings()
    if identity is None:
        return Entitlement(
            tier=TIER_ANON,
            unit_limit=settings.free_unit_limit,
            unlocked_unit_ids=(),
        )

    if identity.user_id is None:
        return Entitlement(
            tier=TIER_ANON,
            unit_limit=settings.free_unit_limit,
            unlocked_unit_ids=unlocked_unit_ids(db, identity),
        )

    profile = ensure_profile(db, identity)
    premium = profile is not None and _premium_is_current(profile)
    return Entitlement(
        tier=TIER_PREMIUM if premium else TIER_FREE,
        unit_limit=None if premium else settings.member_unit_limit,
        # Premium learners are not metered, so their unlock rows are not consulted. Any rows they
        # accumulated while on the free tier are left alone, so downgrading restores exactly the
        # units they had chosen rather than clearing them.
        unlocked_unit_ids=() if premium else unlocked_unit_ids(db, identity),
        email=profile.email if profile is not None else identity.email,
        premium_until=profile.premium_until if profile is not None else None,
    )


def unlock_unit(db: Session, identity: LearnerIdentity | None, unit_id: int) -> Entitlement:
    """Spend an allowance slot on a unit, or raise QuotaExhausted.

    Idempotent: unlocking a unit that is already unlocked costs nothing and succeeds, so a
    double-click or a page reload cannot consume two slots for one recording.
    """
    entitlement = resolve_entitlement(db, identity)
    if identity is None:
        # Nothing to write the unlock against — there is no key to own it.
        raise QuotaExhausted(entitlement)
    if entitlement.allows(unit_id):
        return entitlement
    if not entitlement.can_unlock_more():
        raise QuotaExhausted(entitlement)

    db.add(
        UnitUnlock(
            learner_key=identity.learner_key,
            user_id=identity.user_id,
            unit_id=unit_id,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        # The partial unique index caught a concurrent unlock of this same unit. That is the
        # idempotent case arriving twice at once, not a quota failure.
        db.rollback()
    return resolve_entitlement(db, identity)
