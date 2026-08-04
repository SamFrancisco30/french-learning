import { beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * Identity on the very first request of a page load.
 *
 * This is a regression test for a real failure, and the failure was invisible: React runs child
 * effects before parent effects, so a component fetching on mount went out before AuthProvider's
 * effect had registered a header source. The request carried no identity, the gated endpoint
 * answered 402, and the UI rendered that as "locked" — a learner reloading a recording they had
 * unlocked was shown the paywall for it. Nothing looked broken; it looked like lost access.
 */

describe('api transport identity', () => {
  beforeEach(() => {
    vi.resetModules()
    localStorage.clear()
  })

  it('sends the device key before anything has registered a source', async () => {
    localStorage.setItem('learner_key', 'learner_from-storage')
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, headers: new Headers(), json: async () => ({}) })
    vi.stubGlobal('fetch', fetchMock)

    // Imported fresh, and setIdentityHeaderSource deliberately NOT called — this is the state the
    // app is in for the first fetch of every page load.
    const { api } = await import('./api')
    await api.me()

    const headers = new Headers(fetchMock.mock.calls[0][1].headers)
    expect(headers.get('X-Learner-Key')).toBe('learner_from-storage')
  })

  it('prefers a registered source once one exists', async () => {
    localStorage.setItem('learner_key', 'learner_from-storage')
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, headers: new Headers(), json: async () => ({}) })
    vi.stubGlobal('fetch', fetchMock)

    const { api, setIdentityHeaderSource } = await import('./api')
    setIdentityHeaderSource(() => ({
      'X-Learner-Key': 'learner_from-provider',
      Authorization: 'Bearer token-abc',
    }))
    await api.me()

    const headers = new Headers(fetchMock.mock.calls[0][1].headers)
    expect(headers.get('X-Learner-Key')).toBe('learner_from-provider')
    expect(headers.get('Authorization')).toBe('Bearer token-abc')
  })

  it('still sends the request when storage is unavailable', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, headers: new Headers(), json: async () => ({}) })
    vi.stubGlobal('fetch', fetchMock)
    const boom = () => {
      throw new Error('storage blocked')
    }
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(boom)

    const { api } = await import('./api')
    await api.me()

    // A privacy mode that blocks storage must not stop the app: the caller is simply anonymous.
    expect(fetchMock).toHaveBeenCalled()
    const headers = new Headers(fetchMock.mock.calls[0][1].headers)
    expect(headers.get('X-Learner-Key')).toBeNull()
    vi.restoreAllMocks()
  })

  it('surfaces a gated response as LockedError with its detail intact', async () => {
    const detail = { error: 'no_unlocked_units', entitlement: { tier: 'anon', remaining: 2 } }
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 402,
        statusText: 'Payment Required',
        headers: new Headers(),
        json: async () => ({ detail }),
      }),
    )

    const { api, LockedError } = await import('./api')
    const error = await api.me().catch((e: unknown) => e)

    expect(error).toBeInstanceOf(LockedError)
    expect((error as InstanceType<typeof LockedError>).status).toBe(402)
    // The UI needs the tier detail to choose between "unlock" and "upgrade".
    expect((error as InstanceType<typeof LockedError>).detail).toEqual(detail)
  })
})
