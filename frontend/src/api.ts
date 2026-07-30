import type {
  AttemptResult,
  Language,
  LessonDetail,
  LessonSummary,
  Progress,
  Transcript,
  UnitDetail,
} from './types'

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${path}`)
  return res.json() as Promise<T>
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${path}`)
  return res.json() as Promise<T>
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

  saveVocab: (body: {
    language: string
    headword: string
    gloss_en?: string | null
    example?: string | null
    unit_id?: number | null
    learner_key?: string
  }) => post<import('./types').SavedVocab>('/api/vocab', body),
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
