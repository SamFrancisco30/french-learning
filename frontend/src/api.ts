import type {
  AttemptResult,
  AuthConfig,
  ClaimResult,
  Language,
  LessonDetail,
  LessonSummary,
  Me,
  Progress,
  Transcript,
  UnitDetail,
  UnlockResult,
  VocabEditInput,
  VocabItem,
  VocabList,
  VocabSavedKeys,
  VocabSaveInput,
} from './types'

function requestPathname(path: string): string {
  try {
    return new URL(path, 'http://local.invalid').pathname
  } catch {
    return path.split(/[?#]/, 1)[0] || '/'
  }
}

/**
 * Identity headers attached to every request: the anonymous device key, and a bearer token once
 * the learner has signed in.
 *
 * A registered function rather than a parameter because entitlements made identity relevant to
 * nearly every endpoint — units, clips, transcripts, dictation — where before only the six vocab
 * calls needed it. Threading a `headers` argument through each of those call sites would mean any
 * new call is anonymous by omission, which for a gated endpoint reads as "locked" rather than as a
 * bug. One injection point makes carrying identity the default.
 *
 * Async, and read per call rather than captured once: the access token expires roughly hourly and
 * supabase-js refreshes it in the background, so a token snapshotted at mount would start failing
 * mid-session.
 */
export type IdentityHeaders = Record<string, string>

/**
 * The anonymous device key, read straight from storage.
 *
 * This is the *default* source, and it exists because the registered one cannot be relied on for
 * the very first request of a page load: React runs child effects before parent effects, so a
 * component that fetches on mount goes out before a provider above it has registered anything. A
 * gated endpoint answers 402 to a request carrying no identity, which the UI renders as "locked" —
 * so a missing header did not look like a bug, it looked like the learner had lost access to a
 * recording they had unlocked.
 *
 * Reading storage directly needs no React at all, so the device key is on every request from the
 * first one. IdentityContext owns writing this value; this only ever reads it.
 */
const LEARNER_KEY_STORAGE = 'learner_key'

function deviceKeyHeaders(): IdentityHeaders {
  try {
    const key = globalThis.localStorage?.getItem(LEARNER_KEY_STORAGE)
    return key ? { 'X-Learner-Key': key } : {}
  } catch {
    // Storage can be unavailable in privacy modes. An anonymous request still gets the free tier.
    return {}
  }
}

let identityHeaderSource: () => IdentityHeaders | Promise<IdentityHeaders> = deviceKeyHeaders

export function setIdentityHeaderSource(
  source: () => IdentityHeaders | Promise<IdentityHeaders>,
): void {
  identityHeaderSource = source
}

/** The error thrown for a gated response, carrying the tier detail the paywall renders. */
export class LockedError extends Error {
  readonly status: number
  readonly detail: unknown

  constructor(status: number, detail: unknown, path: string) {
    super(`${status} — ${path}`)
    this.name = 'LockedError'
    this.status = status
    this.detail = detail
  }
}

async function request<T>(
  path: string,
  init?: RequestInit,
  expectsJson = true,
): Promise<T> {
  let injected: IdentityHeaders = {}
  try {
    injected = await identityHeaderSource()
  } catch {
    // A failure to read the session must not stop the request. Sent without identity it is simply
    // an anonymous call, which every endpoint still answers — just with the anonymous tier.
    injected = {}
  }

  const headers = new Headers(injected)
  // Explicit headers win: a caller passing its own X-Learner-Key is being deliberate, and the
  // vocab calls still do exactly that.
  new Headers(init?.headers).forEach((value, key) => headers.set(key, value))

  const res = await fetch(path, { ...init, headers })
  if (!res.ok) {
    // 402 and 409 are the entitlement responses and carry a structured body the UI needs, so they
    // are not flattened into a plain message like every other failure.
    if (res.status === 402 || res.status === 409) {
      const detail = await res
        .json()
        .then((body) => body?.detail ?? body)
        .catch(() => null)
      throw new LockedError(res.status, detail, requestPathname(path))
    }
    throw new Error(`${res.status} ${res.statusText} — ${requestPathname(path)}`)
  }
  if (
    !expectsJson ||
    res.status === 204 ||
    res.status === 205 ||
    res.headers?.get('Content-Length') === '0'
  ) {
    return undefined as T
  }
  return res.json() as Promise<T>
}

function jsonHeaders(headers?: HeadersInit): Headers {
  const merged = new Headers(headers)
  if (!merged.has('Content-Type')) merged.set('Content-Type', 'application/json')
  return merged
}

function get<T>(path: string, headers?: HeadersInit): Promise<T> {
  return request<T>(path, headers ? { headers: new Headers(headers) } : undefined)
}

function sendJson<T>(
  method: 'POST' | 'PATCH',
  path: string,
  body: unknown,
  headers?: HeadersInit,
): Promise<T> {
  return request<T>(path, {
    method,
    headers: jsonHeaders(headers),
    body: JSON.stringify(body),
  })
}

function post<T>(path: string, body: unknown, headers?: HeadersInit): Promise<T> {
  return sendJson<T>('POST', path, body, headers)
}

function patch<T>(path: string, body: unknown, headers?: HeadersInit): Promise<T> {
  return sendJson<T>('PATCH', path, body, headers)
}

function remove(path: string, headers?: HeadersInit): Promise<void> {
  return request<void>(
    path,
    { method: 'DELETE', headers: headers ? new Headers(headers) : undefined },
    false,
  )
}

export const api = {
  languages: () => get<Language[]>('/api/languages'),
  lessons: (language = 'fr') => get<LessonSummary[]>(`/api/lessons?language=${language}`),
  lesson: (id: number) => get<LessonDetail>(`/api/lessons/${id}`),
  unit: (id: number) => get<UnitDetail>(`/api/units/${id}`),
  transcript: (id: number) => get<Transcript>(`/api/units/${id}/transcript`),
  progress: (learnerKey = 'anonymous') =>
    get<Progress>(`/api/progress?learner_key=${encodeURIComponent(learnerKey)}`),
  submit: (exerciseId: number, response: unknown, replays = 0, learnerKey = 'anonymous') =>
    post<AttemptResult>('/api/attempts', {
      exercise_id: exerciseId,
      response,
      replays,
      learner_key: learnerKey,
    }),
  ingest: (url: string, language = 'fr', topic?: string) =>
    post<{ job_id: string; status: string }>('/api/ingest', { url, language, topic }),
  job: (id: string) =>
    get<{ job_id: string; status: string; message: string | null; lesson_id: number | null }>(
      `/api/ingest/${id}`,
    ),

  // --- accounts, entitlements and billing ---
  authConfig: () => get<AuthConfig>('/api/auth/config'),
  me: () => get<Me>('/api/me'),
  /** Spend one allowance slot on a recording. Throws LockedError(409) when the allowance is gone. */
  unlockUnit: (id: number) => post<UnlockResult>(`/api/units/${id}/unlock`, {}),
  /** Move this device's anonymous work onto the account that just signed in. */
  claim: (learnerKey: string) => post<ClaimResult>('/api/me/claim', { learner_key: learnerKey }),
  checkout: () => post<{ url: string }>('/api/billing/checkout', {}),
  billingPortal: () => post<{ url: string }>('/api/billing/portal', {}),
}

export type VocabListParams = {
  language?: string | null
  q?: string | null
  sort?: 'recent' | 'alphabetical'
  limit?: number
  cursor?: string | null
}

export const vocab = {
  list: (params: VocabListParams = {}, headers?: HeadersInit) => {
    const query = new URLSearchParams()
    if (params.language != null && params.language !== '') query.set('language', params.language)
    if (params.q != null && params.q !== '') query.set('q', params.q)
    if (params.sort !== undefined) query.set('sort', params.sort)
    if (params.limit !== undefined) query.set('limit', String(params.limit))
    if (params.cursor != null && params.cursor !== '') query.set('cursor', params.cursor)
    const suffix = query.size > 0 ? `?${query.toString()}` : ''
    return get<VocabList>(`/api/vocab${suffix}`, headers)
  },

  savedKeys: (language: string, headers?: HeadersInit) => {
    const query = new URLSearchParams({ language })
    return get<VocabSavedKeys>(`/api/vocab/saved-keys?${query.toString()}`, headers)
  },

  save: (input: VocabSaveInput, headers?: HeadersInit) =>
    post<VocabItem>('/api/vocab', input, headers),

  edit: (id: number, input: VocabEditInput, headers?: HeadersInit) =>
    patch<VocabItem>(`/api/vocab/${id}`, input, headers),

  remove: (id: number, headers?: HeadersInit) => remove(`/api/vocab/${id}`, headers),
}

export const lexicon = {
  lookup: (body: {
    language: string
    text: string
    char_start: number
    char_end: number
    unit_id?: number | null
  }) => post<import('./types').LookupResult>('/api/lookup', body),

  unitExpressions: (unitId: number) =>
    get<{ unit_id: number; expressions: import('./types').UnitExpressionSpan[] }>(
      `/api/units/${unitId}/expressions`,
    ),
}

export const grammar = {
  sentence: (language: string, text: string) =>
    post<import('./types').SentenceAnalysis>('/api/sentence', { language, text }),

  checkPractice: (body: {
    language: string
    sentence: string
    practice_index: number
    answer: string
  }) => post<import('./types').PracticeCheck>('/api/practice/check', body),
}

export const dictation = {
  /** The learner's derived level in each mode. */
  levels: (learnerKey = 'anonymous') =>
    get<import('./types').DictationLevel[]>(
      `/api/dictation/levels?learner_key=${encodeURIComponent(learnerKey)}`,
    ),
  inventory: (language = 'fr') =>
    get<import('./types').DictationInventory>(`/api/dictation/inventory?language=${language}`),
  /**
   * The item's own audio: its window, optionally slowed and with the punctuation read aloud.
   * Generated on demand and cached server-side, so the first call for a variant is slow.
   */
  audio: (exerciseId: number, speed: number, punctuation: boolean) =>
    get<import('./types').DictationAudio>(
      `/api/dictation/items/${exerciseId}/audio?speed=${speed}&punctuation=${punctuation}`,
    ),
  /** One item at the learner's level. `level` overrides the derived one. */
  next: (mode: string, learnerKey = 'anonymous', language = 'fr', level?: string | null) =>
    get<import('./types').DictationNext>(
      `/api/dictation/next?mode=${mode}&learner_key=${encodeURIComponent(learnerKey)}` +
        `&language=${language}${level ? `&level=${level}` : ''}`,
    ),
}

export const clips = {
  /** Naturally-slowed variant of a unit's clip. speed 1.0 returns the original. */
  variant: (unitId: number, speed: number) =>
    get<import('./types').ClipVariant>(`/api/units/${unitId}/clip?speed=${speed}`),
}

export const drill = {
  /** Banks the vendor published, with how many items each has after de-duplication. */
  collections: (skill?: string) =>
    get<import('./types').DrillCollection[]>(
      `/api/drill/collections${skill ? `?skill=${encodeURIComponent(skill)}` : ''}`,
    ),
  /**
   * The next item to practise. Prefers ones this learner has not attempted, but repeats
   * rather than 404s once a level is exhausted — identity travels in the headers, so the
   * "not attempted" part only works for a caller with a device key or a session.
   */
  next: (params: { skill: string; level?: string | null; collectionId?: number | null }) => {
    const query = new URLSearchParams({ skill: params.skill })
    if (params.level) query.set('level', params.level)
    if (params.collectionId) query.set('collection_id', String(params.collectionId))
    return get<import('./types').DrillQuestion>(`/api/drill/next?${query}`)
  },
  question: (id: number) => get<import('./types').DrillQuestion>(`/api/drill/questions/${id}`),
  /**
   * Submit an answer. This is also the only call that returns the key, the explanation and
   * the translation — they are not on the question payload at all, so nothing can render
   * them before the learner has committed.
   */
  submit: (body: {
    question_id: number
    selected?: string | null
    elapsed_ms?: number | null
    response?: Record<string, unknown>
  }) => post<import('./types').DrillResult>('/api/drill/attempts', body),
  progress: () => get<import('./types').DrillProgress[]>('/api/drill/progress'),
}
