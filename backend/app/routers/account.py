"""Accounts, entitlements, unlocks and study sessions.

The gate that the rest of the API hangs off is `require_unit_access`, defined here and depended on
by the listening and dictation routers. Keeping it in one place is the point: an entitlement check
copied into each endpoint is a check that eventually differs between them.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..billing import get_price_summary
from ..config import get_settings
from ..db import get_db
from ..entitlements import (
    Entitlement,
    QuotaExhausted,
    resolve_entitlement,
    unlock_unit,
)
from ..identity import (
    LearnerIdentity,
    get_learner_identity,
    optional_learner_identity,
    owner_clause,
    require_account,
)
from ..models import Attempt, ListeningUnit, StudySession, UnitUnlock, VocabItem, utcnow
from ..schemas import (
    AuthConfigOut,
    ClaimIn,
    ClaimOut,
    EntitlementOut,
    MeOut,
    SessionHeartbeatIn,
    StudySessionOut,
    UnlockOut,
)

router = APIRouter(prefix="/api", tags=["account"])


def entitlement_out(entitlement: Entitlement) -> EntitlementOut:
    return EntitlementOut(
        tier=entitlement.tier,
        unit_limit=entitlement.unit_limit,
        remaining=entitlement.remaining,
        unlocked_unit_ids=list(entitlement.unlocked_unit_ids),
        premium_until=entitlement.premium_until,
    )


def require_unit_access(
    unit_id: int,
    identity: Annotated[LearnerIdentity | None, Depends(optional_learner_identity)],
    db: Annotated[Session, Depends(get_db)],
) -> Entitlement:
    """The gate on listening content: audio, exercises and transcript.

    402 rather than 403. The learner is not forbidden from this recording — they can have it by
    unlocking it or subscribing — and 402 is the one status that says "there is a way through
    this", which is exactly what the client renders. A 403 would read as a permission error with
    no remedy.

    The body carries the tier and what remains, so the paywall can say "you have 1 of 2 recordings
    left, unlock this one?" versus "you have used both, sign in for 5" without a second request.
    """
    entitlement = resolve_entitlement(db, identity)
    if entitlement.allows(unit_id):
        return entitlement
    raise HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail={
            "error": "locked",
            "unit_id": unit_id,
            "can_unlock": entitlement.can_unlock_more(),
            "entitlement": entitlement_out(entitlement).model_dump(mode="json"),
        },
    )


@router.get("/auth/config", response_model=AuthConfigOut)
def auth_config() -> AuthConfigOut:
    """Published deliberately and unauthenticated — this is the *publishable* key, and the client
    needs it before it can possibly be authenticated. The service key is never exposed here."""
    settings = get_settings()
    return AuthConfigOut(
        enabled=settings.auth_enabled,
        url=settings.supabase_url if settings.auth_enabled else None,
        anon_key=settings.supabase_anon_key if settings.auth_enabled else None,
        billing_enabled=settings.billing_enabled,
        anon_unit_limit=settings.free_unit_limit,
        member_unit_limit=settings.member_unit_limit,
        price=get_price_summary(),
    )


@router.get("/me", response_model=MeOut)
def get_me(
    identity: Annotated[LearnerIdentity | None, Depends(optional_learner_identity)],
    db: Annotated[Session, Depends(get_db)],
) -> MeOut:
    """Tier, allowance and unlocks for whoever is calling — signed in or not.

    Deliberately answers for an anonymous caller instead of requiring a login, because the client
    needs the same three facts either way to decide what to lock.
    """
    entitlement = resolve_entitlement(db, identity)
    return MeOut(
        signed_in=identity is not None and identity.is_authenticated,
        user_id=identity.user_id if identity else None,
        email=entitlement.email or (identity.email if identity else None),
        entitlement=entitlement_out(entitlement),
    )


@router.post("/units/{unit_id}/unlock", response_model=UnlockOut)
def unlock(
    unit_id: int,
    identity: Annotated[LearnerIdentity, Depends(get_learner_identity)],
    db: Annotated[Session, Depends(get_db)],
) -> UnlockOut:
    """Spend one allowance slot on this recording.

    An explicit call rather than a side effect of opening the unit. A GET that silently consumed
    part of a two-item allowance would spend it on a mistyped URL, a prefetch, or a back button,
    and the learner would have no idea where it went. This way the UI can ask first.

    Requires *an* identity but not an account: an anonymous learner has an allowance too, and it
    has to be recorded against their device key.
    """
    if db.get(ListeningUnit, unit_id) is None:
        raise HTTPException(status_code=404, detail=f"unit {unit_id} not found")

    try:
        entitlement = unlock_unit(db, identity, unit_id)
    except QuotaExhausted as exc:
        # 409, not 402: the request was understood and refused because this allowance is spent.
        # 402 is reserved for "this content is locked", which the client handles by offering to
        # unlock — an offer that would loop straight back here.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "quota_exhausted",
                "entitlement": entitlement_out(exc.entitlement).model_dump(mode="json"),
            },
        ) from None

    return UnlockOut(unit_id=unit_id, unlocked=True, entitlement=entitlement_out(entitlement))


@router.post("/me/claim", response_model=ClaimOut)
def claim_anonymous_work(
    payload: ClaimIn,
    identity: Annotated[LearnerIdentity, Depends(require_account)],
    db: Annotated[Session, Depends(get_db)],
) -> ClaimOut:
    """Move what a learner did before signing in onto their new account.

    Without this, signing in looks like data loss: the saved words and unlocked recordings from the
    session where they decided to make an account would still be sitting under the device key, and
    the account would start empty.

    Idempotent, because it runs on every sign-in and the second run must be a no-op. Rows are moved
    only where they do not collide with something the account already owns; a collision means the
    account has its own copy, which wins.
    """
    anon = LearnerIdentity(learner_key=payload.learner_key)
    user_id = identity.user_id
    assert user_id is not None  # require_account

    # Words are merged, not overwritten. The unique index is (user_id, language,
    # normalized_headword), so a word saved both anonymously and while signed in would collide;
    # the account's own copy carries its review schedule, so the anonymous duplicate is dropped
    # rather than clobbering it.
    owned_pairs = {
        (language, headword)
        for language, headword in db.execute(
            select(VocabItem.language, VocabItem.normalized_headword).where(
                VocabItem.user_id == user_id
            )
        ).all()
    }

    vocab_moved = 0
    for item in db.scalars(
        select(VocabItem).where(owner_clause(VocabItem, anon))
    ).all():
        if (item.language, item.normalized_headword) in owned_pairs:
            db.delete(item)
            continue
        item.user_id = user_id
        owned_pairs.add((item.language, item.normalized_headword))
        vocab_moved += 1

    # Unlocks: same shape of collision, and the same resolution.
    owned_units = set(
        db.execute(
            select(UnitUnlock.unit_id).where(UnitUnlock.user_id == user_id)
        ).scalars().all()
    )
    unlocks_moved = 0
    for row in db.scalars(select(UnitUnlock).where(owner_clause(UnitUnlock, anon))).all():
        if row.unit_id in owned_units:
            db.delete(row)
            continue
        row.user_id = user_id
        owned_units.add(row.unit_id)
        unlocks_moved += 1

    # Attempts and sessions have no uniqueness to violate, so they move wholesale.
    attempts_moved = db.execute(
        update(Attempt)
        .where(owner_clause(Attempt, anon))
        .values(user_id=user_id)
    ).rowcount
    sessions_moved = db.execute(
        update(StudySession)
        .where(owner_clause(StudySession, anon))
        .values(user_id=user_id)
    ).rowcount

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Could not merge anonymous work; try again",
        ) from None

    return ClaimOut(
        claimed=True,
        vocab_items=vocab_moved,
        attempts=attempts_moved or 0,
        unlocks=unlocks_moved,
        sessions=sessions_moved or 0,
        entitlement=entitlement_out(resolve_entitlement(db, identity)),
    )


@router.post("/sessions/heartbeat", response_model=StudySessionOut)
def session_heartbeat(
    payload: SessionHeartbeatIn,
    identity: Annotated[LearnerIdentity, Depends(get_learner_identity)],
    db: Annotated[Session, Depends(get_db)],
) -> StudySessionOut:
    """Open or extend the caller's current sitting.

    Time is accumulated from client-reported deltas rather than measured as (now - started_at),
    because a tab left open overnight is not eleven hours of study. Each delta is bounded by the
    schema, so the worst a misbehaving client can claim is one bounded step per call.
    """
    session: StudySession | None = None
    if payload.session_id is not None:
        session = db.scalar(
            select(StudySession).where(
                StudySession.id == payload.session_id,
                owner_clause(StudySession, identity),
            )
        )
        if session is None:
            # Someone else's session id, or one that has been cleaned up. Start a fresh one rather
            # than 404ing: the client is mid-lesson and this is bookkeeping, not the lesson.
            session = None

    if session is None:
        session = StudySession(
            learner_key=identity.learner_key,
            user_id=identity.user_id,
            language=payload.language,
            skill=payload.skill,
            unit_id=payload.unit_id,
        )
        db.add(session)

    session.last_seen_at = utcnow()
    session.seconds += payload.seconds
    session.attempts += payload.attempts
    session.correct += payload.correct
    if payload.unit_id is not None:
        session.unit_id = payload.unit_id
    db.commit()
    db.refresh(session)
    return StudySessionOut.model_validate(session)


@router.get("/sessions", response_model=list[StudySessionOut])
def list_sessions(
    identity: Annotated[LearnerIdentity, Depends(get_learner_identity)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[StudySessionOut]:
    rows = db.scalars(
        select(StudySession)
        .where(owner_clause(StudySession, identity))
        .order_by(StudySession.started_at.desc())
        .limit(limit)
    ).all()
    return [StudySessionOut.model_validate(r) for r in rows]
