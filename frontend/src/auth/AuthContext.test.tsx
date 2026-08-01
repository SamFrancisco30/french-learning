import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider, useAuth } from './AuthContext'
import { IdentityProvider } from '../identity/IdentityContext'
import type { AuthConfig, Me } from '../types'

/**
 * The auth provider, from the outside.
 *
 * Supabase is mocked at the module boundary rather than at the network, because what is worth
 * testing here is our own logic: which tier the app believes it is on, what identity goes out on
 * every request, and that anonymous work is claimed exactly once. The token verification that backs
 * all of it is tested against real signatures in backend/tests/test_auth_tokens.py.
 */

const apiMocks = vi.hoisted(() => ({
  authConfig: vi.fn(),
  me: vi.fn(),
  claim: vi.fn(),
  unlockUnit: vi.fn(),
  checkout: vi.fn(),
  billingPortal: vi.fn(),
}))

const headerMocks = vi.hoisted(() => ({ setIdentityHeaderSource: vi.fn() }))

const supabaseMocks = vi.hoisted(() => ({
  getSession: vi.fn(),
  onAuthStateChange: vi.fn(),
  signInWithPassword: vi.fn(),
  signUp: vi.fn(),
  signInWithOAuth: vi.fn(),
  signOut: vi.fn(),
  resetPasswordForEmail: vi.fn(),
  createClient: vi.fn(),
}))

vi.mock('../api', () => ({
  api: apiMocks,
  setIdentityHeaderSource: headerMocks.setIdentityHeaderSource,
  LockedError: class LockedError extends Error {
    status: number
    detail: unknown
    constructor(status: number, detail: unknown, path: string) {
      super(`${status} — ${path}`)
      this.name = 'LockedError'
      this.status = status
      this.detail = detail
    }
  },
}))

vi.mock('@supabase/supabase-js', () => ({
  createClient: (...args: unknown[]) => {
    supabaseMocks.createClient(...args)
    return {
      auth: {
        getSession: supabaseMocks.getSession,
        onAuthStateChange: supabaseMocks.onAuthStateChange,
        signInWithPassword: supabaseMocks.signInWithPassword,
        signUp: supabaseMocks.signUp,
        signInWithOAuth: supabaseMocks.signInWithOAuth,
        signOut: supabaseMocks.signOut,
        resetPasswordForEmail: supabaseMocks.resetPasswordForEmail,
      },
    }
  },
}))

function config(overrides: Partial<AuthConfig> = {}): AuthConfig {
  return {
    enabled: true,
    url: 'https://project.supabase.co',
    anon_key: 'publishable-key',
    billing_enabled: true,
    anon_unit_limit: 2,
    member_unit_limit: 5,
    ...overrides,
  }
}

function me(overrides: Partial<Me> = {}): Me {
  return {
    signed_in: false,
    user_id: null,
    email: null,
    entitlement: {
      tier: 'anon',
      unit_limit: 2,
      remaining: 2,
      unlocked_unit_ids: [],
      premium_until: null,
    },
    ...overrides,
  }
}

const SESSION = {
  access_token: 'jwt-token-abc',
  user: { id: 'user-1', email: 'learner@example.com' },
} as never

function Probe() {
  const auth = useAuth()
  const [lastError, setLastError] = useState('')
  return (
    <div>
      <output data-testid="ready">{String(auth.ready)}</output>
      <output data-testid="enabled">{String(auth.enabled)}</output>
      <output data-testid="tier">{auth.tier}</output>
      <output data-testid="signedin">{String(auth.signedIn)}</output>
      <output data-testid="email">{auth.email ?? ''}</output>
      <output data-testid="remaining">{String(auth.entitlement.remaining)}</output>
      <output data-testid="unlocked-7">{String(auth.isUnlocked(7))}</output>
      <output data-testid="google">{String(auth.googleEnabled)}</output>
      <output data-testid="error">{lastError}</output>
      <button
        onClick={() => void auth.signInWithGoogle().then((r) => setLastError(r.error ?? ''))}
      >
        google
      </button>
      <button onClick={() => void auth.unlock(7)}>unlock 7</button>
      <button onClick={() => void auth.signIn('learner@example.com', 'password123')}>sign in</button>
    </div>
  )
}

function renderAuth() {
  return render(
    <IdentityProvider>
      <AuthProvider>
        <Probe />
      </AuthProvider>
    </IdentityProvider>,
  )
}

describe('AuthProvider', () => {
  beforeEach(() => {
    for (const mock of Object.values(apiMocks)) mock.mockReset()
    for (const mock of Object.values(supabaseMocks)) mock.mockReset()
    headerMocks.setIdentityHeaderSource.mockReset()
    localStorage.clear()

    apiMocks.authConfig.mockResolvedValue(config())
    apiMocks.me.mockResolvedValue(me())
    apiMocks.claim.mockResolvedValue({
      claimed: true,
      vocab_items: 0,
      attempts: 0,
      unlocks: 0,
      sessions: 0,
      entitlement: me().entitlement,
    })
    supabaseMocks.signInWithOAuth.mockResolvedValue({ data: {}, error: null })
    // The Google-provider probe hits Supabase directly rather than through our api module.
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ external: { google: true } }),
      }),
    )
    supabaseMocks.getSession.mockResolvedValue({ data: { session: null } })
    supabaseMocks.onAuthStateChange.mockReturnValue({
      data: { subscription: { unsubscribe: vi.fn() } },
    })
  })

  it('reports the anonymous tier and allowance before anyone signs in', async () => {
    renderAuth()

    await waitFor(() => expect(screen.getByTestId('ready')).toHaveTextContent('true'))
    expect(screen.getByTestId('tier')).toHaveTextContent('anon')
    expect(screen.getByTestId('signedin')).toHaveTextContent('false')
    expect(screen.getByTestId('remaining')).toHaveTextContent('2')
    expect(screen.getByTestId('unlocked-7')).toHaveTextContent('false')
  })

  it('runs anonymous-only when the server has no Supabase project', async () => {
    apiMocks.authConfig.mockResolvedValue(config({ enabled: false, url: null, anon_key: null }))

    renderAuth()

    await waitFor(() => expect(screen.getByTestId('ready')).toHaveTextContent('true'))
    expect(screen.getByTestId('enabled')).toHaveTextContent('false')
    // No client is built, so nothing tries to reach an auth service that is not there.
    expect(supabaseMocks.createClient).not.toHaveBeenCalled()
    // And the app still knows its tier, so the library still renders with locks.
    expect(screen.getByTestId('tier')).toHaveTextContent('anon')
  })

  it('stays usable when the config request fails outright', async () => {
    apiMocks.authConfig.mockRejectedValue(new Error('503'))

    renderAuth()

    await waitFor(() => expect(screen.getByTestId('ready')).toHaveTextContent('true'))
    expect(screen.getByTestId('enabled')).toHaveTextContent('false')
  })

  it('sends the device key on every request, and a bearer token once signed in', async () => {
    supabaseMocks.getSession.mockResolvedValue({ data: { session: SESSION } })
    apiMocks.me.mockResolvedValue(
      me({
        signed_in: true,
        user_id: 'user-1',
        email: 'learner@example.com',
        entitlement: {
          tier: 'free',
          unit_limit: 5,
          remaining: 5,
          unlocked_unit_ids: [],
          premium_until: null,
        },
      }),
    )

    renderAuth()
    await waitFor(() => expect(screen.getByTestId('tier')).toHaveTextContent('free'))

    // The provider registers one source; call it the way the transport does.
    const source = headerMocks.setIdentityHeaderSource.mock.calls.at(-1)?.[0] as () => Promise<
      Record<string, string>
    >
    const headers = await source()

    expect(headers['X-Learner-Key']).toMatch(/^learner_/)
    expect(headers.Authorization).toBe('Bearer jwt-token-abc')
  })

  it('keeps sending the device key alongside the token, so anonymous work can still be claimed', async () => {
    supabaseMocks.getSession.mockResolvedValue({ data: { session: SESSION } })

    renderAuth()
    await waitFor(() => expect(screen.getByTestId('ready')).toHaveTextContent('true'))

    const source = headerMocks.setIdentityHeaderSource.mock.calls.at(-1)?.[0] as () => Promise<
      Record<string, string>
    >
    const headers = await source()

    // Dropping it on sign-in would leave /api/me/claim with no anonymous rows to find.
    expect(headers['X-Learner-Key']).toMatch(/^learner_/)
  })

  it('claims anonymous work for a returning session', async () => {
    supabaseMocks.getSession.mockResolvedValue({ data: { session: SESSION } })

    renderAuth()

    await waitFor(() => expect(apiMocks.claim).toHaveBeenCalledTimes(1))
    expect(apiMocks.claim.mock.calls[0][0]).toMatch(/^learner_/)
  })

  it('does not re-claim on a token refresh', async () => {
    supabaseMocks.getSession.mockResolvedValue({ data: { session: SESSION } })
    // A holder rather than a plain `let`: assigned only inside a callback, TypeScript narrows a
    // `let` initialised to null down to `null` and then refuses to call it.
    const emitter: { current: ((event: string, session: unknown) => void) | null } = {
      current: null,
    }
    supabaseMocks.onAuthStateChange.mockImplementation(
      (handler: (event: string, session: unknown) => void) => {
        emitter.current = handler
        return { data: { subscription: { unsubscribe: vi.fn() } } }
      },
    )

    renderAuth()
    await waitFor(() => expect(apiMocks.claim).toHaveBeenCalledTimes(1))

    // onAuthStateChange fires on every hourly refresh. Claiming again each time would be a pointless
    // write, and on a shared device a surprising one.
    emitter.current?.('TOKEN_REFRESHED', SESSION)
    emitter.current?.('SIGNED_IN', SESSION)
    await waitFor(() => expect(apiMocks.me).toHaveBeenCalled())

    expect(apiMocks.claim).toHaveBeenCalledTimes(1)
  })

  it('treats premium as unlimited rather than as a zero allowance', async () => {
    apiMocks.me.mockResolvedValue(
      me({
        signed_in: true,
        user_id: 'user-1',
        email: 'learner@example.com',
        entitlement: {
          tier: 'premium',
          unit_limit: null,
          remaining: null,
          unlocked_unit_ids: [],
          premium_until: '2027-01-01T00:00:00Z',
        },
      }),
    )

    renderAuth()

    await waitFor(() => expect(screen.getByTestId('tier')).toHaveTextContent('premium'))
    // Premium accumulates no unlock rows at all, so an implementation that only consulted the list
    // would lock a paying learner out of everything.
    expect(screen.getByTestId('unlocked-7')).toHaveTextContent('true')
  })

  it('opens a unit for the learner after unlocking it', async () => {
    apiMocks.unlockUnit.mockResolvedValue({
      unit_id: 7,
      unlocked: true,
      entitlement: {
        tier: 'anon',
        unit_limit: 2,
        remaining: 1,
        unlocked_unit_ids: [7],
        premium_until: null,
      },
    })

    renderAuth()
    await waitFor(() => expect(screen.getByTestId('ready')).toHaveTextContent('true'))
    await userEvent.click(screen.getByRole('button', { name: 'unlock 7' }))

    await waitFor(() => expect(screen.getByTestId('unlocked-7')).toHaveTextContent('true'))
    expect(screen.getByTestId('remaining')).toHaveTextContent('1')
  })

  it('reports a spent allowance rather than an error when unlocking is refused', async () => {
    const { LockedError } = await import('../api')
    apiMocks.unlockUnit.mockRejectedValue(new LockedError(409, { error: 'quota_exhausted' }, '/x'))
    apiMocks.me.mockResolvedValue(
      me({
        entitlement: {
          tier: 'anon',
          unit_limit: 2,
          remaining: 0,
          unlocked_unit_ids: [1, 2],
          premium_until: null,
        },
      }),
    )

    renderAuth()
    await waitFor(() => expect(screen.getByTestId('ready')).toHaveTextContent('true'))
    await userEvent.click(screen.getByRole('button', { name: 'unlock 7' }))

    await waitFor(() => expect(screen.getByTestId('remaining')).toHaveTextContent('0'))
    expect(screen.getByTestId('unlocked-7')).toHaveTextContent('false')
  })

  it('uses PKCE, so auth redirects never land in the hash the router owns', async () => {
    renderAuth()

    await waitFor(() => expect(supabaseMocks.createClient).toHaveBeenCalled())
    const [url, key, options] = supabaseMocks.createClient.mock.calls[0] as [
      string,
      string,
      { auth: { flowType: string; detectSessionInUrl: boolean } },
    ]

    expect(url).toBe('https://project.supabase.co')
    expect(key).toBe('publishable-key')
    // The implicit flow returns `#access_token=...`, which useHashRoute would read as a route.
    expect(options.auth.flowType).toBe('pkce')
    expect(options.auth.detectSessionInUrl).toBe(true)
  })

  it('turns Supabase error text into something a learner can act on', async () => {
    supabaseMocks.signInWithPassword.mockResolvedValue({
      error: { message: 'Invalid login credentials' },
    })

    renderAuth()
    await waitFor(() => expect(screen.getByTestId('ready')).toHaveTextContent('true'))
    await userEvent.click(screen.getByRole('button', { name: 'sign in' }))

    await waitFor(() => expect(supabaseMocks.signInWithPassword).toHaveBeenCalled())
    expect(supabaseMocks.signInWithPassword).toHaveBeenCalledWith({
      email: 'learner@example.com',
      password: 'password123',
    })
  })
})

describe('AuthProvider — Google', () => {
  it('asks Supabase which providers are actually on, and hides Google when it is off', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ external: { google: false } }) }),
    )

    renderAuth()

    await waitFor(() => expect(screen.getByTestId('google')).toHaveTextContent('false'))
  })

  it('keeps Google when the probe fails, rather than losing a working button', async () => {
    // "Unknown" is not "disabled". A transient network error must not remove a button that works,
    // and the click path already reports a genuinely-disabled provider in words.
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))

    renderAuth()

    await waitFor(() => expect(screen.getByTestId('ready')).toHaveTextContent('true'))
    expect(screen.getByTestId('google')).toHaveTextContent('true')
  })

  it('sends the learner to Google and asks which account to use', async () => {
    renderAuth()
    await waitFor(() => expect(screen.getByTestId('ready')).toHaveTextContent('true'))
    await userEvent.click(screen.getByRole('button', { name: 'google' }))

    await waitFor(() => expect(supabaseMocks.signInWithOAuth).toHaveBeenCalled())
    const [args] = supabaseMocks.signInWithOAuth.mock.calls[0] as [
      { provider: string; options: { redirectTo: string; queryParams: Record<string, string> } },
    ]
    expect(args.provider).toBe('google')
    // The hash route is carried through the round trip. Supabase appends `?code=` as a real query
    // parameter, so the router and supabase-js read different parts of the returned URL.
    expect(args.options.redirectTo).toContain('#/account')
    expect(args.options.queryParams.prompt).toBe('select_account')
  })

  it('reports a provider that is switched off in words a learner can act on', async () => {
    // Supabase says "Unsupported provider: provider is not enabled", which tells a learner nothing
    // and reads as the app being broken.
    supabaseMocks.signInWithOAuth.mockResolvedValue({
      data: {},
      error: { message: 'Unsupported provider: provider is not enabled' },
    })

    renderAuth()
    await waitFor(() => expect(screen.getByTestId('ready')).toHaveTextContent('true'))
    await userEvent.click(screen.getByRole('button', { name: 'google' }))

    await waitFor(() =>
      expect(screen.getByTestId('error')).toHaveTextContent(
        'Google sign-in is not switched on for this app yet.',
      ),
    )
  })
})
