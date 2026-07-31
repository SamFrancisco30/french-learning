import { StrictMode, type ReactNode } from 'react'
import { render, renderHook, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { IdentityProvider, useIdentity } from './IdentityContext'

function wrapper({ children }: { children: ReactNode }) {
  return <IdentityProvider>{children}</IdentityProvider>
}

function IdentityProbe() {
  const { learnerKey, vocabHeaders } = useIdentity()
  return (
    <>
      <output data-testid="learner-key">{learnerKey}</output>
      <output data-testid="vocab-header">{vocabHeaders['X-Learner-Key']}</output>
    </>
  )
}

describe('IdentityProvider', () => {
  it('preserves an existing learner key exactly without generating or replacing it', () => {
    localStorage.setItem('learner_key', 'learner_abc123')
    const randomUUID = vi.spyOn(crypto, 'randomUUID')
    const setItem = vi.spyOn(Storage.prototype, 'setItem')

    const { result } = renderHook(() => useIdentity(), { wrapper })

    expect(result.current.learnerKey).toBe('learner_abc123')
    expect(localStorage.getItem('learner_key')).toBe('learner_abc123')
    expect(randomUUID).not.toHaveBeenCalled()
    expect(setItem).not.toHaveBeenCalled()
  })

  it('creates and stores a learner key from crypto.randomUUID when storage is empty', () => {
    vi.spyOn(crypto, 'randomUUID').mockReturnValue('abc123' as `${string}-${string}-${string}-${string}-${string}`)

    const { result } = renderHook(() => useIdentity(), { wrapper })

    expect(result.current.learnerKey).toBe('learner_abc123')
    expect(localStorage.getItem('learner_key')).toBe('learner_abc123')
  })

  it('writes once and keeps the key and vocab headers stable across StrictMode rerenders', () => {
    vi.spyOn(crypto, 'randomUUID').mockReturnValue('stable-id' as `${string}-${string}-${string}-${string}-${string}`)
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    const seenHeaders: object[] = []

    function StabilityProbe() {
      const identity = useIdentity()
      seenHeaders.push(identity.vocabHeaders)
      return <IdentityProbe />
    }

    const { rerender } = render(
      <StrictMode>
        <IdentityProvider>
          <StabilityProbe />
        </IdentityProvider>
      </StrictMode>,
    )
    rerender(
      <StrictMode>
        <IdentityProvider>
          <StabilityProbe />
        </IdentityProvider>
      </StrictMode>,
    )

    expect(screen.getByTestId('learner-key')).toHaveTextContent('learner_stable-id')
    expect(setItem).toHaveBeenCalledTimes(1)
    expect(seenHeaders.every((headers) => headers === seenHeaders[0])).toBe(true)
  })

  it('exposes the raw learner key and exact vocab request headers', () => {
    localStorage.setItem('learner_key', 'legacy-attempt-key')

    const { result } = renderHook(() => useIdentity(), { wrapper })

    expect(result.current.learnerKey).toBe('legacy-attempt-key')
    expect(result.current.vocabHeaders).toEqual({
      'X-Learner-Key': 'legacy-attempt-key',
    })
  })

  it('throws a clear developer error when used outside IdentityProvider', () => {
    expect(() => renderHook(() => useIdentity())).toThrow(
      'useIdentity must be used within an IdentityProvider',
    )
  })
})
