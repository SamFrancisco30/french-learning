import { useEffect, useRef, useState } from 'react'
import { useAuth } from '../auth/AuthContext'

/**
 * What the allowance looks like to a learner.
 *
 * The rule this UI is built around: a locked recording is never opened by accident. Clicking one
 * asks first, because the anonymous allowance is two — spending one on a misclick is a quarter of
 * everything the learner gets to try, and they would have no idea where it went. So the click opens
 * a decision rather than consuming a slot.
 *
 * The other rule: nothing here gates highlighting. Word lookup, sentence analysis and the saved
 * words book are free on every tier including signed out, so they have no lock state to render.
 */

/** How many recordings are left, and what to do about it. Shown above a lesson's unit list. */
export function QuotaBar({ onSignIn }: { onSignIn: () => void }) {
  const auth = useAuth()
  if (!auth.ready || auth.tier === 'premium') return null

  const limit = auth.entitlement.unit_limit ?? 0
  const remaining = auth.entitlement.remaining ?? 0
  const used = Math.max(0, limit - remaining)

  return (
    <div className={`quotabar ${remaining === 0 ? 'is-spent' : ''}`}>
      <div className="quotameter" role="img" aria-label={`${used} of ${limit} recordings unlocked`}>
        {/* Pips rather than a percentage bar. The numbers here are two and five, and a bar filled
            "50%" communicates less than two dots of which one is filled. */}
        {Array.from({ length: limit }, (_, i) => (
          <span key={i} className={i < used ? 'pip on' : 'pip'} />
        ))}
      </div>
      <span className="quotatext">
        {remaining > 0 ? (
          <>
            {remaining} of {limit} recording{limit === 1 ? '' : 's'} left to unlock
          </>
        ) : (
          <>You have unlocked all {limit} of your recordings</>
        )}
      </span>
      {!auth.signedIn && auth.enabled && (
        <button className="linkbtn" onClick={onSignIn}>
          Sign in for {auth.config?.member_unit_limit ?? 5} →
        </button>
      )}
      {auth.signedIn && auth.billingEnabled && remaining === 0 && (
        <button className="linkbtn" onClick={onSignIn}>
          Go premium for everything →
        </button>
      )}
    </div>
  )
}

/** The lock marker on a unit row. Nothing when the unit is open to this learner. */
export function LockChip({ unitId }: { unitId: number }) {
  const auth = useAuth()
  if (!auth.ready || auth.isUnlocked(unitId)) return null
  const canUnlock = (auth.entitlement.remaining ?? 0) > 0
  return (
    <span className={`chip lockchip ${canUnlock ? '' : 'spent'}`}>
      {canUnlock ? 'Locked · 1 slot' : 'Locked'}
    </span>
  )
}

type GateProps = {
  unitId: number
  unitLabel: string
  onUnlocked: () => void
  onClose: () => void
  onSignIn: () => void
}

/**
 * The decision shown when a locked recording is clicked.
 *
 * Three states, and which one appears depends only on whether there is a way through: spend a slot,
 * sign in for more, or subscribe. It never shows an "unlock" button that would fail — the backend
 * answers 409 in that case, and offering an action that 409s is worse than offering none.
 */
export function UnlockGate({ unitId, unitLabel, onUnlocked, onClose, onSignIn }: GateProps) {
  const auth = useAuth()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const closeRef = useRef<HTMLButtonElement | null>(null)

  // Escape closes, and focus starts inside the dialog so a keyboard user is not left behind it.
  useEffect(() => {
    closeRef.current?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const remaining = auth.entitlement.remaining ?? 0
  const limit = auth.entitlement.unit_limit ?? 0
  const canSpend = remaining > 0

  const spend = async () => {
    setBusy(true)
    setError(null)
    const result = await auth.unlock(unitId)
    setBusy(false)
    if (result.ok) {
      onUnlocked()
      return
    }
    setError(
      result.reason === 'quota'
        ? 'That used your last slot elsewhere — this recording is still locked.'
        : 'Could not unlock that. Please try again.',
    )
  }

  return (
    <div
      className="gateveil"
      // Clicking the backdrop closes; clicks inside the card must not bubble out to it.
      onClick={onClose}
    >
      <div
        className="gatecard"
        role="dialog"
        aria-modal="true"
        aria-labelledby="gate-title"
        onClick={(event) => event.stopPropagation()}
      >
        <h3 id="gate-title">{canSpend ? 'Unlock this recording?' : 'You have used your recordings'}</h3>
        <p className="muted">{unitLabel}</p>

        {canSpend ? (
          <p className="gatebody">
            This uses 1 of your {limit} recordings. You will have {remaining - 1} left, and this one
            stays unlocked for good.
          </p>
        ) : !auth.signedIn ? (
          <p className="gatebody">
            You have opened all {limit} recordings available without an account. A free account
            raises that to {auth.config?.member_unit_limit ?? 5}, and keeps your saved words across
            devices.
          </p>
        ) : (
          <p className="gatebody">
            You have opened all {limit} recordings included with a free account. Premium removes the
            limit entirely.
          </p>
        )}

        {/* True on every tier, and worth saying at exactly the moment a limit is in the way. */}
        <p className="gatefree">
          Looking up and saving words stays free — including on the recordings you have already
          unlocked.
        </p>

        {error && <p className="formerror">{error}</p>}

        <div className="actions">
          {canSpend ? (
            <button className="btn" disabled={busy} onClick={spend}>
              {busy ? 'Unlocking…' : 'Unlock this recording'}
            </button>
          ) : !auth.signedIn && auth.enabled ? (
            <button className="btn" onClick={onSignIn}>
              Create a free account
            </button>
          ) : auth.billingEnabled ? (
            <button
              className="btn"
              disabled={busy}
              onClick={async () => {
                setBusy(true)
                const { error: failed } = await auth.startCheckout()
                if (failed) setError(failed)
                setBusy(false)
              }}
            >
              Go premium
            </button>
          ) : null}
          <button className="btn ghost" ref={closeRef} onClick={onClose}>
            Not now
          </button>
        </div>
      </div>
    </div>
  )
}
