import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { createClient, type Session, type SupabaseClient } from '@supabase/supabase-js'
import { LockedError, api, setIdentityHeaderSource } from '../api'
import { useIdentity } from '../identity/IdentityContext'
import type { AuthConfig, Entitlement, Me, Tier } from '../types'

/**
 * Accounts and access tiers.
 *
 * Sign-in is Supabase Auth, reached directly from the browser; our API never sees a password. It
 * verifies the resulting token instead (see backend/app/auth.py), which is what lets the account
 * live in Supabase — as asked — without this application storing password hashes.
 *
 * Configuration is *fetched*, not compiled in. `createClient` needs a project URL and the
 * publishable key, and taking them from `/api/auth/config` keeps SUPABASE_URL configured in one
 * file (backend/.env) instead of duplicated into a VITE_ variable that has to be kept in step and
 * forces a rebuild when the key rotates. The cost is that the client does not exist for the first
 * moment of the app's life, which is what `ready` is for.
 *
 * PKCE rather than the implicit flow, and that choice is about this app specifically: implicit
 * returns tokens in the URL *hash*, and the hash is our router. A confirmation link would arrive as
 * `#access_token=...`, which useHashRoute would read as a route. PKCE returns `?code=` in the query
 * string, which the router never looks at.
 */

/**
 * What to assume before — or instead of — an answer from /api/me.
 *
 * Deliberately built from the server's own advertised anonymous limit rather than hardcoding a
 * number, so the two cannot disagree. The limit is *not* zero: a zero allowance would render every
 * recording as locked, and the one moment this fallback is visible is when the tier is not yet
 * known, where "locked" is the one wrong answer to show.
 */
function fallbackEntitlement(config: AuthConfig | null): Entitlement {
  const limit = config?.anon_unit_limit ?? 2
  return {
    tier: 'anon',
    unit_limit: limit,
    remaining: limit,
    unlocked_unit_ids: [],
    premium_until: null,
  }
}

export type UnlockOutcome = { ok: true } | { ok: false; reason: 'quota' | 'error' }

type Auth = Readonly<{
  /**
   * The tier is known: both the config request and the first /api/me have settled. Lock UI keys off
   * this, so nothing renders a padlock while the answer is still in flight.
   */
  ready: boolean
  config: AuthConfig | null
  /** Accounts are configured on this server. When false the app runs anonymous-only. */
  enabled: boolean
  billingEnabled: boolean
  session: Session | null
  email: string | null
  signedIn: boolean
  me: Me | null
  entitlement: Entitlement
  tier: Tier
  isUnlocked: (unitId: number) => boolean
  signUp: (email: string, password: string) => Promise<{ error?: string; confirmEmail?: boolean }>
  signIn: (email: string, password: string) => Promise<{ error?: string }>
  signOut: () => Promise<void>
  sendPasswordReset: (email: string) => Promise<{ error?: string }>
  refreshMe: () => Promise<void>
  unlock: (unitId: number) => Promise<UnlockOutcome>
  startCheckout: () => Promise<{ error?: string }>
  openBillingPortal: () => Promise<{ error?: string }>
}>

const AuthContext = createContext<Auth | undefined>(undefined)

/** Supabase's messages are written for developers. These are the ones a learner should see. */
function friendlyAuthError(message: string): string {
  const text = message.toLowerCase()
  if (text.includes('invalid login credentials')) return 'That email and password do not match.'
  if (text.includes('email not confirmed')) {
    return 'Check your inbox and confirm your email address first.'
  }
  if (text.includes('user already registered') || text.includes('already been registered')) {
    return 'That email already has an account. Try signing in instead.'
  }
  if (text.includes('password should be at least')) {
    return 'Passwords need at least 6 characters.'
  }
  if (text.includes('rate limit') || text.includes('too many')) {
    return 'Too many attempts just now. Wait a minute and try again.'
  }
  if (text.includes('unable to validate email')) return 'That does not look like a valid email.'
  return message
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const { learnerKey } = useIdentity()
  const [config, setConfig] = useState<AuthConfig | null>(null)
  const [configSettled, setConfigSettled] = useState(false)
  // Separate from `configSettled`: knowing whether accounts exist is not knowing this
  // learner's allowance, and the lock UI needs the second one.
  const [meSettled, setMeSettled] = useState(false)
  const [client, setClient] = useState<SupabaseClient | null>(null)
  const [session, setSession] = useState<Session | null>(null)
  const [me, setMe] = useState<Me | null>(null)

  // The live session, for the header source. A ref as well as state because the header source is
  // registered once and would otherwise close over whatever session existed at registration.
  const sessionRef = useRef<Session | null>(null)
  const clientRef = useRef<SupabaseClient | null>(null)
  const claimedFor = useRef<string | null>(null)

  /**
   * Identity on every request. Registered in a layout-ordered effect before any child fetches, and
   * reading `getSession()` each time so a token refreshed in the background is picked up.
   *
   * The device key is sent alongside the bearer token rather than being dropped on sign-in: the
   * backend uses it to find the anonymous rows that /api/me/claim migrates.
   */
  useEffect(() => {
    setIdentityHeaderSource(async () => {
      const headers: Record<string, string> = { 'X-Learner-Key': learnerKey }
      const active = clientRef.current
      if (active) {
        // getSession refreshes an expired token rather than handing back a dead one.
        const { data } = await active.auth.getSession()
        const token = data.session?.access_token
        if (token) headers.Authorization = `Bearer ${token}`
      } else if (sessionRef.current?.access_token) {
        headers.Authorization = `Bearer ${sessionRef.current.access_token}`
      }
      return headers
    })
  }, [learnerKey])

  // Boot: find out whether accounts exist, and build the client if they do.
  useEffect(() => {
    let cancelled = false
    api
      .authConfig()
      .then((loaded) => {
        if (cancelled) return
        setConfig(loaded)
        if (loaded.enabled && loaded.url && loaded.anon_key) {
          const created = createClient(loaded.url, loaded.anon_key, {
            auth: {
              persistSession: true,
              autoRefreshToken: true,
              // See the note above: PKCE keeps auth out of the hash the router owns.
              flowType: 'pkce',
              detectSessionInUrl: true,
            },
          })
          clientRef.current = created
          setClient(created)
        }
      })
      .catch(() => {
        // A server that cannot answer this is a server without accounts. The app still works.
        if (!cancelled) setConfig(null)
      })
      .finally(() => {
        if (!cancelled) setConfigSettled(true)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const refreshMe = useCallback(async () => {
    try {
      setMe(await api.me())
    } catch {
      // A failed read leaves the app on the fallback allowance rather than stuck: the endpoint
      // answers for anonymous callers too, so a failure here is a transport problem, not a signal
      // that the learner has no access.
      setMe(null)
    } finally {
      setMeSettled(true)
    }
  }, [])

  /**
   * Move anonymous work onto the account. Runs once per account per page life — `claimedFor` guards
   * it, because onAuthStateChange also fires on every token refresh, and claiming on each of those
   * would be a pointless write every hour.
   */
  const claimAnonymousWork = useCallback(
    async (userId: string) => {
      if (claimedFor.current === userId) return
      claimedFor.current = userId
      try {
        await api.claim(learnerKey)
      } catch {
        // Not fatal: the account is usable, the anonymous rows are simply still unclaimed and the
        // next sign-in will try again.
      }
    },
    [learnerKey],
  )

  // Track the session. getSession() first for a returning learner, then subscribe.
  useEffect(() => {
    if (!client) return
    let cancelled = false

    client.auth.getSession().then(({ data }) => {
      if (cancelled) return
      sessionRef.current = data.session
      setSession(data.session)
      if (data.session?.user?.id) void claimAnonymousWork(data.session.user.id)
      void refreshMe()
    })

    const { data: subscription } = client.auth.onAuthStateChange((event, next) => {
      sessionRef.current = next
      setSession(next)
      if (event === 'SIGNED_IN' && next?.user?.id) {
        void claimAnonymousWork(next.user.id).then(refreshMe)
        return
      }
      if (event === 'SIGNED_OUT') {
        claimedFor.current = null
      }
      // TOKEN_REFRESHED and USER_UPDATED do not change entitlements, but refreshing is cheap and
      // keeps the tier correct if a subscription changed in another tab.
      void refreshMe()
    })

    return () => {
      cancelled = true
      subscription.subscription.unsubscribe()
    }
  }, [client, claimAnonymousWork, refreshMe])

  // Anonymous learners need their tier too — /api/me answers for them, and it is what says how many
  // of the free unlocks are left.
  useEffect(() => {
    if (!configSettled || client) return
    void refreshMe()
  }, [configSettled, client, refreshMe])

  const signIn = useCallback(
    async (email: string, password: string) => {
      if (!client) return { error: 'Accounts are not available on this server.' }
      const { error } = await client.auth.signInWithPassword({ email, password })
      return error ? { error: friendlyAuthError(error.message) } : {}
    },
    [client],
  )

  const signUp = useCallback(
    async (email: string, password: string) => {
      if (!client) return { error: 'Accounts are not available on this server.' }
      const { data, error } = await client.auth.signUp({
        email,
        password,
        options: { emailRedirectTo: `${window.location.origin}/#/account` },
      })
      if (error) return { error: friendlyAuthError(error.message) }
      // With email confirmation on, signUp returns a user but no session. Saying so is the
      // difference between "nothing happened" and "go and check your inbox".
      return { confirmEmail: data.session == null }
    },
    [client],
  )

  const signOut = useCallback(async () => {
    if (client) await client.auth.signOut()
    sessionRef.current = null
    claimedFor.current = null
    setSession(null)
    await refreshMe()
  }, [client, refreshMe])

  const sendPasswordReset = useCallback(
    async (email: string) => {
      if (!client) return { error: 'Accounts are not available on this server.' }
      const { error } = await client.auth.resetPasswordForEmail(email, {
        redirectTo: `${window.location.origin}/#/account`,
      })
      return error ? { error: friendlyAuthError(error.message) } : {}
    },
    [client],
  )

  const unlock = useCallback(
    async (unitId: number): Promise<UnlockOutcome> => {
      try {
        const result = await api.unlockUnit(unitId)
        setMe((current) =>
          current ? { ...current, entitlement: result.entitlement } : current,
        )
        return { ok: true }
      } catch (error) {
        if (error instanceof LockedError && error.status === 409) {
          // The allowance is spent. Refresh so the UI switches from "unlock" to "upgrade".
          await refreshMe()
          return { ok: false, reason: 'quota' }
        }
        return { ok: false, reason: 'error' }
      }
    },
    [refreshMe],
  )

  const startCheckout = useCallback(async () => {
    try {
      const { url } = await api.checkout()
      window.location.href = url
      return {}
    } catch {
      return { error: 'Could not open checkout. Please try again.' }
    }
  }, [])

  const openBillingPortal = useCallback(async () => {
    try {
      const { url } = await api.billingPortal()
      window.location.href = url
      return {}
    } catch {
      return { error: 'Could not open the billing portal. Please try again.' }
    }
  }, [])

  const entitlement = me?.entitlement ?? fallbackEntitlement(config)
  const ready = configSettled && meSettled
  const unlockedSet = useMemo(
    () => new Set(entitlement.unlocked_unit_ids),
    [entitlement.unlocked_unit_ids],
  )
  const isUnlocked = useCallback(
    // Premium has no unlock rows at all, by design — it is not metered, so everything is open.
    (unitId: number) => entitlement.unit_limit === null || unlockedSet.has(unitId),
    [entitlement.unit_limit, unlockedSet],
  )

  const value = useMemo<Auth>(
    () => ({
      ready,
      config,
      enabled: config?.enabled === true,
      billingEnabled: config?.billing_enabled === true,
      session,
      email: me?.email ?? session?.user?.email ?? null,
      signedIn: me?.signed_in === true || session != null,
      me,
      entitlement,
      tier: entitlement.tier,
      isUnlocked,
      signUp,
      signIn,
      signOut,
      sendPasswordReset,
      refreshMe,
      unlock,
      startCheckout,
      openBillingPortal,
    }),
    [
      ready,
      config,
      session,
      me,
      entitlement,
      isUnlocked,
      signUp,
      signIn,
      signOut,
      sendPasswordReset,
      refreshMe,
      unlock,
      startCheckout,
      openBillingPortal,
    ],
  )

  return <AuthContext value={value}>{children}</AuthContext>
}

// Provider and hook are one focused API, as with IdentityContext.
// oxlint-disable-next-line react/only-export-components
export function useAuth(): Auth {
  const auth = useContext(AuthContext)
  if (auth === undefined) throw new Error('useAuth must be used within an AuthProvider')
  return auth
}
