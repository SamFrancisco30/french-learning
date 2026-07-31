import {
  createContext,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

const STORAGE_KEY = 'learner_key'

type VocabHeaders = Readonly<{
  'X-Learner-Key': string
}>

type Identity = Readonly<{
  learnerKey: string
  vocabHeaders: VocabHeaders
}>

const IdentityContext = createContext<Identity | undefined>(undefined)

function getOrCreateLearnerKey(): string {
  const existing = localStorage.getItem(STORAGE_KEY)
  if (existing !== null && existing.length > 0) return existing

  const learnerKey = `learner_${crypto.randomUUID()}`
  localStorage.setItem(STORAGE_KEY, learnerKey)
  return learnerKey
}

export function IdentityProvider({ children }: { children: ReactNode }) {
  const [learnerKey] = useState(getOrCreateLearnerKey)
  const vocabHeaders = useMemo<VocabHeaders>(
    () => Object.freeze({ 'X-Learner-Key': learnerKey }),
    [learnerKey],
  )
  const identity = useMemo<Identity>(
    () => ({ learnerKey, vocabHeaders }),
    [learnerKey, vocabHeaders],
  )

  return <IdentityContext value={identity}>{children}</IdentityContext>
}

// Provider and hook intentionally stay together as one focused identity API.
// oxlint-disable-next-line react/only-export-components
export function useIdentity(): Identity {
  const identity = useContext(IdentityContext)
  if (identity === undefined) {
    throw new Error('useIdentity must be used within an IdentityProvider')
  }
  return identity
}
