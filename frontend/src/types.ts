export type ExerciseKind = 'cloze' | 'mcq' | 'true_false' | 'vocab_match' | 'ordering'

export interface Language {
  code: string
  name_en: string
  name_native: string
}

export interface Source {
  id: number
  provider: string
  provider_id: string
  url: string
  title: string
  channel: string | null
  duration_s: number | null
  license_name: string | null
  upload_date: string | null
}

export interface Blank {
  index: number
  char_start: number
  char_end: number
  length: number
  /** Original-video timeline. Subtract unit.start_s before seeking inside a clip. */
  audio_start_s: number
  audio_end_s: number
  word_start_s: number
  word_end_s: number
  zipf: number
  hint_initial: string
}

export interface ExercisePayload {
  // cloze
  text?: string
  masked_text?: string
  blanks?: Blank[]
  word_bank?: string[]
  // mcq
  options?: string[]
  quote?: string
  // vocab_match
  words?: string[]
  glosses?: string[]
  details?: { word: string; gloss_en: string; definition_target: string; example: string; zipf: number }[]
  // ordering
  items?: string[]
}

export interface Exercise {
  id: number
  kind: ExerciseKind
  order_idx: number
  prompt: string
  payload: ExercisePayload
  cefr: string | null
  /** Original-video timeline. */
  audio_start_s: number | null
  audio_end_s: number | null
  generator: string
}

export interface UnitSummary {
  id: number
  idx: number
  start_s: number
  end_s: number
  duration_s: number
  cefr: string | null
  wpm: number | null
  difficulty_score: number | null
  gist: string | null
  clip_url: string | null
  exercise_count: number
}

export interface UnitDetail extends UnitSummary {
  exercises: Exercise[]
  difficulty_detail: Record<string, unknown>
}

export interface LessonSummary {
  id: number
  title: string
  language: string
  skill: string
  topic: string | null
  cefr: string | null
  difficulty_score: number | null
  unit_count: number
  exercise_count: number
  duration_s: number | null
  source: Source
  created_at: string
}

export interface LessonDetail extends LessonSummary {
  units: UnitSummary[]
}

export interface AttemptResult {
  id: number
  exercise_id: number
  is_correct: boolean
  score: number
  feedback: {
    blanks?: {
      index: number
      given: string
      expected: string
      correct: boolean
      tolerance: string | null
      message: string | null
    }[]
    correct_count?: number
    total?: number
    correct_index?: number
    correct_value?: unknown
    pairs?: Record<string, { given: string; expected: string; correct: boolean }>
    correct_order?: string[]
  }
  explanation: string | null
  answer: Record<string, unknown>
  audio_start_s: number | null
  audio_end_s: number | null
}

export interface Transcript {
  unit_id: number
  text: string
  words: { word: string; start: number; end: number }[]
  asr_backend: string
  asr_model: string
}

export interface Progress {
  learner_key: string
  attempts: number
  correct: number
  accuracy: number
  mean_score: number
  by_kind: Record<string, { attempts: number; correct: number; accuracy: number; mean_score: number }>
  units_touched: number
}

// ---------------------------------------------------------------- smart translation

export interface Sense {
  gloss_en: string
  when: string
}

export interface WordGloss {
  surface: string
  lemma: string | null
  pos: string | null
  gloss_en: string
  other_senses: Sense[]
  note: string | null
  zipf: number | null
}

export type ExpressionKind =
  | 'idiom'
  | 'collocation'
  | 'phrasal_verb'
  | 'fixed_phrase'
  | 'compound'
  | 'proper_noun'

export interface ExpressionHit {
  id: number | null
  canonical: string
  surface: string
  kind: ExpressionKind
  gloss_en: string
  literal_en: string | null
  note: string | null
  /** Spans of the expression's own words — several when it's discontinuous. */
  component_spans: number[][]
  char_start: number
  char_end: number
  confidence: number
  source: 'precomputed' | 'inferred' | 'live'
}

export interface LookupResult {
  language: string
  selection: string
  char_start: number
  char_end: number
  context: string
  /** Original-video timeline, null when not locatable in the audio. */
  audio_start_s: number | null
  audio_end_s: number | null
  word: WordGloss
  expressions: ExpressionHit[]
  source: 'precomputed' | 'cache' | 'live' | 'offline' | 'error'
  unit_id: number | null
  lemmatizer: string
  inferred: boolean
  error: string | null
}

export interface UnitExpressionSpan {
  id: number
  canonical: string
  kind: ExpressionKind
  component_spans: number[][]
  char_start: number
  char_end: number
}

export interface SavedVocab {
  id: number
  language: string
  headword: string
  gloss_en: string | null
  example: string | null
  zipf: number | null
  reps: number
  due_at: string | null
}
