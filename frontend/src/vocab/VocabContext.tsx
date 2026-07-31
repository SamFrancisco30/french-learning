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
import { vocab, type VocabListParams } from '../api'
import { useIdentity } from '../identity/IdentityContext'
import type { VocabEditInput, VocabItem, VocabList, VocabSaveInput } from '../types'

export type KeyState = {
  status: 'idle' | 'loading' | 'ready' | 'error'
  keys: Set<string>
  localAdds: Set<string>
  localDeletes: Set<string>
  error: Error | null
}

export type SavedStatus = 'saved' | 'not-saved' | 'unknown'

type VocabContextValue = {
  ensureKeys: (language: string) => Promise<void>
  savedStatus: (language: string, normalizedHeadword: string) => SavedStatus
  keyState: (language: string) => KeyState
  save: (input: VocabSaveInput) => Promise<VocabItem>
  edit: (id: number, input: VocabEditInput) => Promise<VocabItem>
  remove: (item: VocabItem) => Promise<void>
  list: (params: VocabListParams) => Promise<VocabList>
}

const VocabContext = createContext<VocabContextValue | undefined>(undefined)

function idleState(): KeyState {
  return {
    status: 'idle',
    keys: new Set(),
    localAdds: new Set(),
    localDeletes: new Set(),
    error: null,
  }
}

function asError(reason: unknown): Error {
  return reason instanceof Error ? reason : new Error(String(reason))
}

export function VocabProvider({ children }: { children: ReactNode }) {
  const { vocabHeaders } = useIdentity()
  const [states, setStates] = useState<Map<string, KeyState>>(() => new Map())
  const statesRef = useRef(states)
  const inFlight = useRef(new Map<string, Promise<void>>())
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const updateState = useCallback(
    (language: string, update: (current: KeyState) => KeyState): void => {
      if (!mounted.current) return
      const next = new Map(statesRef.current)
      next.set(language, update(next.get(language) ?? idleState()))
      statesRef.current = next
      setStates(next)
    },
    [],
  )

  const ensureKeys = useCallback(
    (language: string): Promise<void> => {
      if (statesRef.current.get(language)?.status === 'ready') return Promise.resolve()

      const pending = inFlight.current.get(language)
      if (pending) return pending

      updateState(language, (current) => ({ ...current, status: 'loading', error: null }))

      let request!: Promise<void>
      request = Promise.resolve()
        .then(() => vocab.savedKeys(language, vocabHeaders))
        .then((response) => {
          updateState(language, (current) => {
            const keys = new Set(response.items.map((item) => item.normalized_headword))
            current.localAdds.forEach((key) => keys.add(key))
            current.localDeletes.forEach((key) => keys.delete(key))
            return { ...current, status: 'ready', keys, error: null }
          })
        })
        .catch((reason: unknown) => {
          const error = asError(reason)
          updateState(language, (current) => ({ ...current, status: 'error', error }))
          throw reason
        })
        .finally(() => {
          if (inFlight.current.get(language) === request) {
            inFlight.current.delete(language)
          }
        })

      inFlight.current.set(language, request)
      return request
    },
    [updateState, vocabHeaders],
  )

  const savedStatus = useCallback(
    (language: string, normalizedHeadword: string): SavedStatus => {
      const state = statesRef.current.get(language)
      if (!state) return 'unknown'
      if (state.localAdds.has(normalizedHeadword)) return 'saved'
      if (state.localDeletes.has(normalizedHeadword)) return 'not-saved'
      if (state.status !== 'ready') return 'unknown'
      return state.keys.has(normalizedHeadword) ? 'saved' : 'not-saved'
    },
    [],
  )

  const keyState = useCallback(
    (language: string): KeyState => statesRef.current.get(language) ?? idleState(),
    [],
  )

  const save = useCallback(
    async (input: VocabSaveInput): Promise<VocabItem> => {
      const saved = await vocab.save(input, vocabHeaders)
      updateState(saved.language, (current) => {
        const keys = new Set(current.keys)
        const localAdds = new Set(current.localAdds)
        const localDeletes = new Set(current.localDeletes)
        keys.add(saved.normalized_headword)
        localAdds.add(saved.normalized_headword)
        localDeletes.delete(saved.normalized_headword)
        return { ...current, keys, localAdds, localDeletes }
      })
      return saved
    },
    [updateState, vocabHeaders],
  )

  const edit = useCallback(
    (id: number, input: VocabEditInput): Promise<VocabItem> =>
      vocab.edit(id, input, vocabHeaders),
    [vocabHeaders],
  )

  const remove = useCallback(
    async (item: VocabItem): Promise<void> => {
      await vocab.remove(item.id, vocabHeaders)
      updateState(item.language, (current) => {
        const keys = new Set(current.keys)
        const localAdds = new Set(current.localAdds)
        const localDeletes = new Set(current.localDeletes)
        keys.delete(item.normalized_headword)
        localAdds.delete(item.normalized_headword)
        localDeletes.add(item.normalized_headword)
        return { ...current, keys, localAdds, localDeletes }
      })
    },
    [updateState, vocabHeaders],
  )

  const list = useCallback(
    (params: VocabListParams): Promise<VocabList> => vocab.list(params, vocabHeaders),
    [vocabHeaders],
  )

  const value = useMemo<VocabContextValue>(
    () => {
      // State changes invalidate the context value so consumers re-read the ref-backed cache,
      // while the methods themselves remain referentially stable.
      void states
      return { ensureKeys, savedStatus, keyState, save, edit, remove, list }
    },
    [states, ensureKeys, savedStatus, keyState, save, edit, remove, list],
  )

  return <VocabContext value={value}>{children}</VocabContext>
}

// oxlint-disable-next-line react/only-export-components
export function useVocab(): VocabContextValue {
  const context = useContext(VocabContext)
  if (context === undefined) {
    throw new Error('useVocab must be used within a VocabProvider')
  }
  return context
}
