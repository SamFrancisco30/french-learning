import { useEffect, useMemo, useState } from 'react'
import { formatPrice, useAuth } from '../auth/AuthContext'
import { GoogleMark } from '../components/icons'

/**
 * One page for signing in and for the account itself.
 *
 * Not two routes, because they are the same destination seen from either side of a single boundary:
 * the star and the "sign in" prompt both lead here, and after signing in the learner should land on
 * their account rather than be bounced somewhere else. What changes is which half renders.
 *
 * Sign-up and sign-in share one form. They ask for exactly the same two fields, and a learner who
 * cannot remember whether they already have an account is the common case — so the mode is a toggle
 * above the form rather than a separate page reached from a link at the bottom.
 */

type Mode = 'signin' | 'signup' | 'reset'

const COPY: Record<Mode, { title: string; action: string; hint: string }> = {
  signin: {
    title: 'Sign in',
    action: 'Sign in',
    hint: 'Your saved words and progress follow your account to any device.',
  },
  signup: {
    title: 'Create an account',
    action: 'Create account',
    hint: 'Free, and it raises your unlocked recordings from 2 to 5.',
  },
  reset: {
    title: 'Reset your password',
    action: 'Send reset link',
    hint: 'We will email you a link to choose a new password.',
  },
}

function checkoutOutcome(): 'success' | 'cancelled' | null {
  // Stripe returns to /#/account?checkout=... — but the query lands *before* the hash, so it is
  // window.location.search that carries it, not anything the hash router parsed.
  const fromSearch = new URLSearchParams(window.location.search).get('checkout')
  if (fromSearch === 'success' || fromSearch === 'cancelled') return fromSearch
  // Some Stripe configurations append it after the fragment instead.
  const hash = window.location.hash
  const q = hash.indexOf('?')
  if (q === -1) return null
  const value = new URLSearchParams(hash.slice(q + 1)).get('checkout')
  return value === 'success' || value === 'cancelled' ? value : null
}

export function AccountPage({ navigate }: { navigate: (to: string) => void }) {
  const auth = useAuth()
  const [mode, setMode] = useState<Mode>('signin')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const outcome = useMemo(checkoutOutcome, [])

  /**
   * A learner returning from Stripe arrives before the webhook has necessarily landed, so the tier
   * shown a moment after checkout can still be the old one. Rather than claim success from the URL
   * — which proves nothing, anyone can visit it — poll /api/me a few times and let the real tier
   * appear. If it does not, the message says so instead of lying.
   */
  const [awaitingUpgrade, setAwaitingUpgrade] = useState(outcome === 'success')
  useEffect(() => {
    if (!awaitingUpgrade) return
    if (auth.tier === 'premium') {
      setAwaitingUpgrade(false)
      return
    }
    let tries = 0
    const timer = window.setInterval(() => {
      tries += 1
      void auth.refreshMe()
      if (tries >= 6) {
        window.clearInterval(timer)
        setAwaitingUpgrade(false)
      }
    }, 1500)
    return () => window.clearInterval(timer)
  }, [awaitingUpgrade, auth])

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setError(null)
    setNotice(null)
    setBusy(true)
    try {
      if (mode === 'reset') {
        const { error: failed } = await auth.sendPasswordReset(email.trim())
        if (failed) setError(failed)
        else setNotice(`If ${email.trim()} has an account, a reset link is on its way.`)
        return
      }
      if (mode === 'signup') {
        const { error: failed, confirmEmail } = await auth.signUp(email.trim(), password)
        if (failed) setError(failed)
        else if (confirmEmail) {
          setNotice(`Almost there — confirm your address from the email we sent to ${email.trim()}.`)
        }
        return
      }
      const { error: failed } = await auth.signIn(email.trim(), password)
      if (failed) setError(failed)
      else setPassword('')
    } finally {
      setBusy(false)
    }
  }

  if (!auth.ready) {
    return (
      <div className="accountcard">
        <p className="muted">Loading…</p>
      </div>
    )
  }

  if (!auth.enabled) {
    return (
      <div className="accountcard">
        <h2>Accounts are not set up</h2>
        <p className="muted">
          This server has no Supabase project configured, so there is nothing to sign in to.
          Everything else works: you have {auth.entitlement.unit_limit ?? 0} unlocked recordings,
          and saving words is unaffected.
        </p>
        <div className="actions">
          <button className="btn ghost" onClick={() => navigate('/listening')}>
            Back to listening →
          </button>
        </div>
      </div>
    )
  }

  if (auth.signedIn) {
    const premium = auth.tier === 'premium'
    const until = auth.entitlement.premium_until
    return (
      <div className="accountcard">
        <h2>Your account</h2>
        <p className="account-email">{auth.email}</p>

        <div className="tierrow">
          <span className={`chip ${premium ? 'good' : ''}`}>
            {premium ? 'Premium' : 'Free account'}
          </span>
          {premium ? (
            <span className="muted">
              Everything unlocked
              {until ? ` · renews ${new Date(until).toLocaleDateString()}` : ''}
            </span>
          ) : (
            <span className="muted">
              {auth.entitlement.remaining ?? 0} of {auth.entitlement.unit_limit} recordings left to
              unlock
            </span>
          )}
        </div>

        {awaitingUpgrade && (
          <p className="notice">Confirming your subscription with Stripe…</p>
        )}
        {outcome === 'cancelled' && !premium && (
          <p className="muted">Checkout cancelled — nothing was charged.</p>
        )}

        {!premium && auth.billingEnabled && (
          <div className="upgradebox">
            <h3>
              Unlock everything
              {/* The price comes from Stripe rather than being written here, so what the paywall
                  says and what the card is charged cannot drift apart. Omitted rather than guessed
                  when Stripe could not be reached. */}
              {formatPrice(auth.config?.price) && (
                <span className="pricetag">{formatPrice(auth.config?.price)}</span>
              )}
            </h3>
            <p className="muted">
              Every recording, every dictation, no allowance. Word lookup and your saved words stay
              free either way.
            </p>
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
          </div>
        )}

        {error && <p className="formerror">{error}</p>}

        <div className="actions">
          {premium && auth.billingEnabled && (
            <button
              className="btn ghost"
              onClick={async () => {
                const { error: failed } = await auth.openBillingPortal()
                if (failed) setError(failed)
              }}
            >
              Manage billing
            </button>
          )}
          <button className="btn ghost" onClick={() => navigate('/listening')}>
            Back to listening →
          </button>
          <button
            className="btn ghost"
            onClick={async () => {
              await auth.signOut()
              navigate('/listening')
            }}
          >
            Sign out
          </button>
        </div>
      </div>
    )
  }

  const copy = COPY[mode]
  return (
    <div className="accountcard">
      <div className="modeswitch" role="tablist" aria-label="Sign in or create an account">
        <button
          role="tab"
          aria-selected={mode === 'signin'}
          className={mode === 'signin' ? 'on' : ''}
          onClick={() => {
            setMode('signin')
            setError(null)
            setNotice(null)
          }}
        >
          Sign in
        </button>
        <button
          role="tab"
          aria-selected={mode === 'signup'}
          className={mode === 'signup' ? 'on' : ''}
          onClick={() => {
            setMode('signup')
            setError(null)
            setNotice(null)
          }}
        >
          Create account
        </button>
      </div>

      <h2>{copy.title}</h2>
      <p className="muted">{copy.hint}</p>

      {/*
        Google first, and above the form rather than below it.

        Ordering is the whole point: someone who signed up with Google has no password to
        remember, and burying the button under a password field they will fail to fill is how
        people end up creating a second, duplicate account. It is hidden on the reset step, where
        a Google account has no password to reset in the first place.

        One button for both modes. Google does not distinguish signing up from signing in — the
        first time through it creates the account, afterwards it signs in — so labelling it
        "Continue with" rather than either one is the honest wording.
      */}
      {mode !== 'reset' && auth.googleEnabled && (
        <>
          <button
            type="button"
            className="oauthbtn"
            disabled={busy}
            onClick={async () => {
              setError(null)
              setBusy(true)
              const { error: failed } = await auth.signInWithGoogle()
              // On success the browser is already navigating to Google, so `busy` is only ever
              // cleared on the failure path.
              if (failed) {
                setError(failed)
                setBusy(false)
              }
            }}
          >
            <GoogleMark />
            Continue with Google
          </button>
          <div className="authdivider">
            <span>or use your email</span>
          </div>
        </>
      )}

      <form className="authform" onSubmit={submit}>
        <label>
          Email
          <input
            type="email"
            name="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
          />
        </label>

        {mode !== 'reset' && (
          <label>
            Password
            <input
              type="password"
              name="password"
              // Tells a password manager to offer a new password on sign-up and the saved one on
              // sign-in. Without it, managers save the wrong value or fail to offer at all.
              autoComplete={mode === 'signup' ? 'new-password' : 'current-password'}
              required
              minLength={6}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={mode === 'signup' ? 'At least 6 characters' : ''}
            />
          </label>
        )}

        {error && <p className="formerror">{error}</p>}
        {notice && <p className="notice">{notice}</p>}

        <button className="btn" type="submit" disabled={busy}>
          {busy ? 'Working…' : copy.action}
        </button>
      </form>

      <div className="authfoot">
        {mode === 'reset' ? (
          <button className="linkbtn" onClick={() => setMode('signin')}>
            ← Back to sign in
          </button>
        ) : (
          <button className="linkbtn" onClick={() => setMode('reset')}>
            Forgotten your password?
          </button>
        )}
        <button className="linkbtn" onClick={() => navigate('/listening')}>
          Keep practising without an account →
        </button>
      </div>
    </div>
  )
}
