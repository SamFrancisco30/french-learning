import type {
  AttemptResult,
  Language,
  LessonDetail,
  LessonSummary,
  Progress,
  Transcript,
  UnitDetail,
  VocabEditInput,
  VocabItem,
  VocabList,
  VocabSavedKeys,
  VocabSaveInput,
} from './types'

async function request<T>(
  path: string,
  init?: RequestInit,
  expectsJson = true,
): Promise<T> {
  const res = await fetch(path, init)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${path}`)
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

  saveVocab: ({
    learner_key,
    ...input
  }: VocabSaveInput & {
    learner_key?: string
  }) =>
    vocab.save(
      input,
      learner_key === undefined ? undefined : { 'X-Learner-Key': learner_key },
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

export const clips = {
  /** Naturally-slowed variant of a unit's clip. speed 1.0 returns the original. */
  variant: (unitId: number, speed: number) =>
    get<import('./types').ClipVariant>(`/api/units/${unitId}/clip?speed=${speed}`),
}
