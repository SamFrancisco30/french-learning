import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'

const STORAGE_KEY = 'learner_key'
const LEARNER_KEY_PATTERN = /^learner_[A-Za-z0-9-]{1,48}$/

type VocabHeaders = Readonly<{
  'X-Learner-Key': string
}>

type Identity = Readonly<{
  learnerKey: string
  vocabHeaders: VocabHeaders
}>

const IdentityContext = createContext<Identity | undefined>(undefined)

function uuidFromRandomValues(webCrypto: Crypto): string {
  const bytes = new Uint8Array(16)
  webCrypto.getRandomValues(bytes)
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80

  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0'))
  return [
    hex.slice(0, 4).join(''),
    hex.slice(4, 6).join(''),
    hex.slice(6, 8).join(''),
    hex.slice(8, 10).join(''),
    hex.slice(10, 16).join(''),
  ].join('-')
}

function pseudonymousFallback(): string {
  const timestamp = Date.now().toString(36)
  const random = Math.random().toString(36).slice(2)
  return `${timestamp}-${random}`.slice(0, 48)
}

function generateLearnerKey(): string {
  let webCrypto: Crypto | undefined
  try {
    webCrypto = globalThis.crypto
  } catch {
    webCrypto = undefined
  }

  if (webCrypto) {
    try {
      if (typeof webCrypto.randomUUID === 'function') {
        const learnerKey = `learner_${webCrypto.randomUUID()}`
        if (LEARNER_KEY_PATTERN.test(learnerKey)) return learnerKey
      }
    } catch {
      // Continue to getRandomValues when randomUUID is unavailable.
    }

    try {
      return `learner_${uuidFromRandomValues(webCrypto)}`
    } catch {
      // This device-local identifier is not authentication; a safe alphabet is enough.
    }
  }

  return `learner_${pseudonymousFallback()}`
}

function getInitialLearnerKey(): string {
  let existing: string | null = null
  try {
    existing = globalThis.localStorage.getItem(STORAGE_KEY)
  } catch {
    existing = null
  }
  if (existing !== null && LEARNER_KEY_PATTERN.test(existing)) return existing

  return generateLearnerKey()
}

export function IdentityProvider({ children }: { children: ReactNode }) {
  const [learnerKey] = useState(getInitialLearnerKey)
  const persistenceAttempted = useRef(false)
  const vocabHeaders = useMemo<VocabHeaders>(
    () => Object.freeze({ 'X-Learner-Key': learnerKey }),
    [learnerKey],
  )
  const identity = useMemo<Identity>(
    () => ({ learnerKey, vocabHeaders }),
    [learnerKey, vocabHeaders],
  )

  useEffect(() => {
    if (persistenceAttempted.current) return
    persistenceAttempted.current = true

    try {
      if (globalThis.localStorage.getItem(STORAGE_KEY) === learnerKey) return
    } catch {
      // A failed read does not prevent an independent write attempt.
    }

    try {
      globalThis.localStorage.setItem(STORAGE_KEY, learnerKey)
    } catch {
      // Storage can be unavailable in privacy modes; retain this key in provider state.
    }
  }, [learnerKey])

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
