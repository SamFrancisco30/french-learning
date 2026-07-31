import { act, renderHook } from '@testing-library/react'
import { StrictMode, useEffect, type ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { VocabItem, VocabSavedKeys } from '../types'
import { IdentityProvider } from '../identity/IdentityContext'
import { VocabProvider, useVocab } from './VocabContext'

const apiMocks = vi.hoisted(() => ({
  list: vi.fn(),
  savedKeys: vi.fn(),
  save: vi.fn(),
  edit: vi.fn(),
  remove: vi.fn(),
}))

vi.mock('../api', () => ({ vocab: apiMocks }))

function wrapper({ children }: { children: ReactNode }) {
  return (
    <IdentityProvider>
      <VocabProvider>{children}</VocabProvider>
    </IdentityProvider>
  )
}

function strictWrapper({ children }: { children: ReactNode }) {
  return (
    <StrictMode>
      <IdentityProvider>
        <VocabProvider>{children}</VocabProvider>
      </IdentityProvider>
    </StrictMode>
  )
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function savedKeys(language: string, ...keys: string[]): VocabSavedKeys {
  return {
    language,
    items: keys.map((normalized_headword, index) => ({
      id: index + 1,
      normalized_headword,
    })),
  }
}

function item(overrides: Partial<VocabItem> = {}): VocabItem {
  return {
    id: 7,
    language: 'fr',
    headword: 'Écouter',
    normalized_headword: 'écouter',
    gloss_en: 'to listen',
    example: null,
    zipf: 4.3,
    reps: 0,
    due_at: null,
    created_at: '2026-07-30T10:00:00Z',
    updated_at: '2026-07-30T10:00:00Z',
    source: null,
    ...overrides,
  }
}

describe('VocabProvider key synchronization', () => {
  beforeEach(() => {
    Object.values(apiMocks).forEach((mock) => mock.mockReset())
    localStorage.setItem('learner_key', 'learner_vocab-test')
  })

  it('loads lazily per language and compares exact server keys', async () => {
    apiMocks.savedKeys.mockResolvedValue(savedKeys('fr', 'écouter'))
    const { result } = renderHook(() => useVocab(), { wrapper })

    expect(apiMocks.savedKeys).not.toHaveBeenCalled()
    expect(result.current.savedStatus('fr', 'écouter')).toBe('unknown')

    await act(() => result.current.ensureKeys('fr'))

    expect(apiMocks.savedKeys).toHaveBeenCalledOnce()
    expect(apiMocks.savedKeys).toHaveBeenCalledWith('fr', {
      'X-Learner-Key': 'learner_vocab-test',
    })
    expect(result.current.savedStatus('fr', 'écouter')).toBe('saved')
    expect(result.current.savedStatus('fr', 'ÉCOUTER')).toBe('not-saved')
    expect(result.current.keyState('en').status).toBe('idle')
  })

  it('deduplicates concurrent loads and independently loads another language', async () => {
    const fr = deferred<VocabSavedKeys>()
    apiMocks.savedKeys.mockImplementation((language: string) =>
      language === 'fr' ? fr.promise : Promise.resolve(savedKeys(language, 'listen')),
    )
    const { result } = renderHook(() => useVocab(), { wrapper })

    let first!: Promise<void>
    let second!: Promise<void>
    act(() => {
      first = result.current.ensureKeys('fr')
      second = result.current.ensureKeys('fr')
    })
    expect(apiMocks.savedKeys).not.toHaveBeenCalled()

    await act(async () => {
      await result.current.ensureKeys('en')
    })
    expect(apiMocks.savedKeys).toHaveBeenCalledTimes(2)

    fr.resolve(savedKeys('fr', 'écouter'))
    await act(async () => {
      await Promise.all([first, second])
    })
    expect(result.current.savedStatus('fr', 'écouter')).toBe('saved')
    expect(result.current.savedStatus('en', 'listen')).toBe('saved')
  })

  it('exposes load errors, propagates rejection, and retries after failure', async () => {
    const failure = new Error('keys unavailable')
    apiMocks.savedKeys.mockRejectedValueOnce(failure).mockResolvedValueOnce(savedKeys('fr'))
    const { result } = renderHook(() => useVocab(), { wrapper })

    await expect(act(() => result.current.ensureKeys('fr'))).rejects.toBe(failure)
    expect(result.current.keyState('fr')).toMatchObject({ status: 'error', error: failure })
    expect(result.current.savedStatus('fr', 'absent')).toBe('unknown')

    await act(() => result.current.ensureKeys('fr'))
    expect(apiMocks.savedKeys).toHaveBeenCalledTimes(2)
    expect(result.current.keyState('fr')).toMatchObject({ status: 'ready', error: null })
  })

  it('preserves a successful save made before the initial server load', async () => {
    const loading = deferred<VocabSavedKeys>()
    apiMocks.savedKeys.mockReturnValue(loading.promise)
    apiMocks.save.mockResolvedValue(item())
    const { result } = renderHook(() => useVocab(), { wrapper })

    let load!: Promise<void>
    act(() => {
      load = result.current.ensureKeys('fr')
    })
    await act(() =>
      result.current.save({ language: 'fr', headword: 'Écouter', gloss_en: 'to listen' }),
    )
    expect(result.current.savedStatus('fr', 'écouter')).toBe('saved')

    loading.resolve(savedKeys('fr', 'serveur'))
    await act(() => load)
    expect(result.current.savedStatus('fr', 'écouter')).toBe('saved')
    expect(result.current.savedStatus('fr', 'serveur')).toBe('saved')
  })

  it('preserves a successful delete made before the initial server load', async () => {
    const loading = deferred<VocabSavedKeys>()
    apiMocks.savedKeys.mockReturnValue(loading.promise)
    apiMocks.remove.mockResolvedValue(undefined)
    const removed = item()
    const { result } = renderHook(() => useVocab(), { wrapper })

    let load!: Promise<void>
    act(() => {
      load = result.current.ensureKeys('fr')
    })
    await act(() => result.current.remove(removed))
    expect(result.current.savedStatus('fr', 'écouter')).toBe('not-saved')

    loading.resolve(savedKeys('fr', 'écouter', 'serveur'))
    await act(() => load)
    expect(result.current.savedStatus('fr', 'écouter')).toBe('not-saved')
    expect(result.current.savedStatus('fr', 'serveur')).toBe('saved')
  })

  it('keeps the normalized key stable on edit and removes it on delete', async () => {
    apiMocks.savedKeys.mockResolvedValue(savedKeys('fr', 'écouter'))
    apiMocks.edit.mockResolvedValue(item({ gloss_en: 'hear attentively' }))
    apiMocks.remove.mockResolvedValue(undefined)
    const { result } = renderHook(() => useVocab(), { wrapper })
    await act(() => result.current.ensureKeys('fr'))

    const edited = await act(() => result.current.edit(7, { gloss_en: 'hear attentively' }))
    expect(edited.gloss_en).toBe('hear attentively')
    expect(result.current.savedStatus('fr', 'écouter')).toBe('saved')

    await act(() => result.current.remove(item()))
    expect(result.current.savedStatus('fr', 'écouter')).toBe('not-saved')
  })

  it('reports only confirmed local changes while state is not ready', async () => {
    const loading = deferred<VocabSavedKeys>()
    apiMocks.savedKeys.mockReturnValue(loading.promise)
    apiMocks.save.mockResolvedValue(item())
    apiMocks.remove.mockResolvedValue(undefined)
    const { result } = renderHook(() => useVocab(), { wrapper })

    await act(() => result.current.save({ language: 'fr', headword: 'Écouter' }))
    await act(() => result.current.remove(item({ normalized_headword: 'supprimer' })))
    expect(result.current.savedStatus('fr', 'écouter')).toBe('saved')
    expect(result.current.savedStatus('fr', 'supprimer')).toBe('not-saved')
    expect(result.current.savedStatus('fr', 'autre')).toBe('unknown')

    act(() => {
      void result.current.ensureKeys('fr')
    })
    expect(result.current.savedStatus('fr', 'autre')).toBe('unknown')
    loading.resolve(savedKeys('fr'))
    await act(async () => {
      await loading.promise
    })
  })

  it('propagates list and mutation errors without changing cache truth', async () => {
    const failure = new Error('network secret')
    apiMocks.list.mockRejectedValue(failure)
    apiMocks.save.mockRejectedValue(failure)
    apiMocks.edit.mockRejectedValue(failure)
    apiMocks.remove.mockRejectedValue(failure)
    const { result } = renderHook(() => useVocab(), { wrapper })

    await expect(result.current.list({ language: 'fr' })).rejects.toBe(failure)
    await expect(result.current.save({ language: 'fr', headword: 'mot' })).rejects.toBe(failure)
    await expect(result.current.edit(7, { example: null })).rejects.toBe(failure)
    await expect(result.current.remove(item())).rejects.toBe(failure)
    expect(result.current.savedStatus('fr', 'mot')).toBe('unknown')
    expect(result.current.savedStatus('fr', 'écouter')).toBe('unknown')
  })

  it('throws a clear error outside the provider', () => {
    expect(() => renderHook(() => useVocab())).toThrow(
      'useVocab must be used within a VocabProvider',
    )
  })

  it('continues updating cache state under React Strict Mode', async () => {
    const loading = deferred<VocabSavedKeys>()
    apiMocks.savedKeys.mockReturnValue(loading.promise)
    const { result } = renderHook(
      () => {
        const context = useVocab()
        const { ensureKeys } = context
        useEffect(() => {
          void ensureKeys('fr')
        }, [ensureKeys])
        return context
      },
      { wrapper: strictWrapper },
    )

    loading.resolve(savedKeys('fr', 'écouter'))
    await act(async () => {
      await loading.promise
      await Promise.resolve()
    })

    expect(result.current.keyState('fr').status).toBe('ready')
    expect(result.current.savedStatus('fr', 'écouter')).toBe('saved')
  })
})
