import { StrictMode, type ReactNode } from 'react'
import { render, renderHook, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { IdentityProvider, useIdentity } from './IdentityContext'

const LEARNER_KEY_PATTERN = /^learner_[A-Za-z0-9-]{1,48}$/
const GENERATED_UUID = '123e4567-e89b-42d3-a456-426614174000'
const STRICT_MODE_UUID = '123e4567-e89b-42d3-a456-426614174001'

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
    vi.spyOn(crypto, 'randomUUID').mockReturnValue(GENERATED_UUID)

    const { result } = renderHook(() => useIdentity(), { wrapper })

    expect(result.current.learnerKey).toBe(`learner_${GENERATED_UUID}`)
    expect(localStorage.getItem('learner_key')).toBe(`learner_${GENERATED_UUID}`)
  })

  it('writes once and keeps the key and vocab headers stable across StrictMode rerenders', () => {
    vi.spyOn(crypto, 'randomUUID').mockReturnValue(STRICT_MODE_UUID)
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

    expect(screen.getByTestId('learner-key')).toHaveTextContent(
      `learner_${STRICT_MODE_UUID}`,
    )
    expect(setItem).toHaveBeenCalledTimes(1)
    expect(seenHeaders.every((headers) => headers === seenHeaders[0])).toBe(true)
  })

  it('exposes the raw learner key and exact vocab request headers', () => {
    localStorage.setItem('learner_key', 'learner_legacy-attempt-key')

    const { result } = renderHook(() => useIdentity(), { wrapper })

    expect(result.current.learnerKey).toBe('learner_legacy-attempt-key')
    expect(result.current.vocabHeaders).toEqual({
      'X-Learner-Key': 'learner_legacy-attempt-key',
    })
  })

  it.each([
    ['blank', ''],
    ['malformed', 'learner_bad!key'],
    ['unprefixed', 'abc123'],
    ['underscore-containing suffix', 'learner_bad_key'],
    ['overlong', `learner_${'a'.repeat(49)}`],
  ])('replaces a %s stored value with one compatible key for all API contracts', (_, stored) => {
    localStorage.setItem('learner_key', stored)
    vi.spyOn(crypto, 'randomUUID').mockReturnValue(GENERATED_UUID)

    const { result } = renderHook(() => useIdentity(), { wrapper })

    const expected = `learner_${GENERATED_UUID}`
    expect(result.current.learnerKey).toBe(expected)
    expect(result.current.vocabHeaders).toEqual({ 'X-Learner-Key': expected })
    expect(localStorage.getItem('learner_key')).toBe(expected)
  })

  it('uses a stable in-memory identity when reading storage throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('Blocked', 'SecurityError')
    })
    vi.spyOn(crypto, 'randomUUID').mockReturnValue(GENERATED_UUID)

    const { result, rerender } = renderHook(() => useIdentity(), { wrapper })
    const firstIdentity = result.current
    rerender()

    expect(firstIdentity.learnerKey).toBe(`learner_${GENERATED_UUID}`)
    expect(firstIdentity.vocabHeaders).toEqual({
      'X-Learner-Key': `learner_${GENERATED_UUID}`,
    })
    expect(result.current).toBe(firstIdentity)
  })

  it('uses a stable in-memory identity when writing storage throws', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('Blocked', 'SecurityError')
    })
    vi.spyOn(crypto, 'randomUUID').mockReturnValue(GENERATED_UUID)

    const { result, rerender } = renderHook(() => useIdentity(), { wrapper })
    const firstIdentity = result.current
    rerender()

    expect(firstIdentity.learnerKey).toBe(`learner_${GENERATED_UUID}`)
    expect(firstIdentity.vocabHeaders).toEqual({
      'X-Learner-Key': `learner_${GENERATED_UUID}`,
    })
    expect(result.current).toBe(firstIdentity)
  })

  it.each([
    ['is missing', undefined],
    ['throws', vi.fn(() => { throw new Error('Unavailable') })],
  ])('uses getRandomValues when crypto.randomUUID %s', (_, randomUUID) => {
    const getRandomValues = vi.fn((bytes: Uint8Array) => {
      bytes.set(Array.from({ length: 16 }, (__, index) => index))
      return bytes
    })
    vi.stubGlobal('crypto', { randomUUID, getRandomValues })

    const { result, rerender } = renderHook(() => useIdentity(), { wrapper })
    const firstKey = result.current.learnerKey
    rerender()

    expect(getRandomValues).toHaveBeenCalledTimes(1)
    expect(firstKey).toBe('learner_00010203-0405-4607-8809-0a0b0c0d0e0f')
    expect(firstKey).toMatch(LEARNER_KEY_PATTERN)
    expect(result.current.learnerKey).toBe(firstKey)
    expect(result.current.vocabHeaders).toEqual({ 'X-Learner-Key': firstKey })
  })

  it.each([
    ['is unavailable', undefined],
    ['throws', {
      randomUUID: vi.fn(() => { throw new Error('Unavailable') }),
      getRandomValues: vi.fn(() => { throw new Error('Unavailable') }),
    }],
  ])('uses a valid stable pseudonymous fallback when Web Crypto %s', (_, cryptoValue) => {
    vi.stubGlobal('crypto', cryptoValue)
    vi.spyOn(Date, 'now').mockReturnValue(1_754_000_000_000)
    vi.spyOn(Math, 'random').mockReturnValue(0.123456789)

    const { result, rerender } = renderHook(() => useIdentity(), { wrapper })
    const firstKey = result.current.learnerKey
    rerender()

    expect(firstKey).toMatch(LEARNER_KEY_PATTERN)
    expect(result.current.learnerKey).toBe(firstKey)
    expect(result.current.vocabHeaders).toEqual({ 'X-Learner-Key': firstKey })
  })

  it('throws a clear developer error when used outside IdentityProvider', () => {
    expect(() => renderHook(() => useIdentity())).toThrow(
      'useIdentity must be used within an IdentityProvider',
    )
  })
})
