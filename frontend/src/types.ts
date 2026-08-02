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
    // dictation
    words?: DictationWord[]
    counts?: Record<string, number>
    exact?: number
    typed?: number
    punctuation_missing?: Record<string, number>
    punctuation_scored?: boolean
    reference?: string
  }
  explanation: string | null
  answer: Record<string, unknown>
  audio_start_s: number | null
  audio_end_s: number | null
}

/**
 * One spoken word, located in both timelines at once: `start`/`end` are original-video
 * seconds, `char_start`/`char_end` its span in `Transcript.text`. That pairing is what
 * makes follow-along highlighting possible — the server aligns bare ASR tokens onto the
 * punctuated text so the client never has to guess where a word begins.
 */
export interface TimedWord {
  word: string
  start: number
  end: number
  char_start: number
  char_end: number
}

export interface Transcript {
  unit_id: number
  text: string
  words: TimedWord[]
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
  normalized_headword: string
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
  normalized_headword: string
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
  /** True when the selection is long enough to warrant sentence analysis. */
  is_sentence: boolean
  /** Deterministic matches — arrive with the lookup, no extra round trip. */
  constructions: ConstructionHit[]
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

export interface VocabSource {
  lesson_id: number
  lesson_title: string
  unit_id: number
  unit_index: number
}

export interface VocabItem {
  id: number
  language: string
  headword: string
  normalized_headword: string
  gloss_en: string | null
  example: string | null
  zipf: number | null
  reps: number
  due_at: string | null
  created_at: string
  updated_at: string
  source: VocabSource | null
}

export interface VocabList {
  items: VocabItem[]
  next_cursor: string | null
  total: number
}

export interface VocabSavedKey {
  id: number
  normalized_headword: string
}

export interface VocabSavedKeys {
  language: string
  items: VocabSavedKey[]
}

export interface VocabSaveInput {
  language: string
  headword: string
  gloss_en?: string | null
  example?: string | null
  unit_id?: number | null
}

export interface VocabEditInput {
  gloss_en?: string | null
  example?: string | null
}

export type SavedVocab = VocabItem

// ---------------------------------------------------------------- sentence grammar

export interface ConstructionHit {
  key: string
  schema_form: string
  name_en: string
  meaning_en: string
  why_opaque: string
  literal_trap: string | null
  cefr: string
  example_fr: string
  example_en: string
  register_note: string
  char_start: number
  char_end: number
  marker_spans: number[][]
}

export interface Structure {
  key: string
  schema_form: string
  name_en: string
  meaning_en: string
  why_opaque: string
  literal_trap: string | null
  in_this_sentence: string
  quote: string
  cefr: string
  char_start: number
  char_end: number
  marker_spans: number[][]
  /** pattern = matched deterministically; llm = model-proposed, less certain. */
  source: 'pattern' | 'llm'
}

export interface Practice {
  construction_key: string
  schema_form: string
  prompt_en: string
  hint_en: string | null
  required_markers: string[]
}

export interface SentenceAnalysis {
  text: string
  translation_en: string
  register_note: string | null
  structures: Structure[]
  practices: Practice[]
  notes: string | null
  source: string
}

export interface PracticeCheck {
  correct: boolean
  score: number
  headline: string
  structure: {
    checked: boolean
    used: boolean
    missing_markers: string[]
    schema_form: string | null
  }
  meaning_ok: boolean | null
  grammar_ok: boolean | null
  issues: { fragment: string; problem: string; fix: string }[]
  corrected_fr: string | null
  note_en: string | null
  tolerance: string | null
  reference_fr: string
  better_than_reference: boolean
  judged: boolean
}

// ---------------------------------------------------------------- natural slow playback

export interface ClipVariant {
  unit_id: number
  speed: number
  url: string | null
  duration_s: number
  /** True for the untouched original; false for a reshaped slow variant. */
  natural: boolean
  word_factor: number | null
  inserted_silence_s: number | null
  /** Pauses the added time was spread across; with inserted_silence_s, the mean gap. */
  pauses: number | null
  /** [[original_clip_s, stretched_clip_s], ...] at word starts. */
  time_map: number[][]
}

// ---------------------------------------------------------------- dictation

export type DictationMode = 'sentence' | 'paragraph'

export interface DictationLevel {
  level: string
  mode: DictationMode
  attempts: number
  recent_mean: number | null
  /** Why the level is what it is — shown to the learner, so it never feels arbitrary. */
  reason: string
}

export interface DictationItem {
  exercise_id: number
  mode: DictationMode
  prompt: string
  cefr: string | null
  difficulty_score: number | null
  word_count: number | null
  sentence_count: number | null
  /** Original-video timeline, as everywhere else. */
  audio_start_s: number | null
  audio_end_s: number | null
  unit_id: number
  unit_start_s: number
  unit_end_s: number
  clip_url: string | null
  /** So the source link can reach this passage in the listening drill. */
  lesson_id: number | null
  lesson_title: string | null
  topic: string | null
}

export interface DictationNext {
  item: DictationItem
  level: DictationLevel
  /** What was actually served — differs from the target when a level has nothing left. */
  served_level: string
  off_level: boolean
  repeat: boolean
  remaining_at_level: number
}

export interface DictationInventory {
  language: string
  by_mode: Record<DictationMode, Record<string, number>>
  totals: Record<DictationMode, number>
  levels: string[]
}

/** One aligned word in a graded dictation. `expected` empty means the learner added it. */
export interface DictationWord {
  expected: string
  given: string
  verdict:
    | 'exact'
    | 'case'
    | 'accent'
    | 'typo'
    | 'elision'
    | 'ending'
    | 'homophone'
    | 'wrong'
    | 'missing'
    | 'added'
  credit: number
  note: string | null
}

export interface DictationAudio {
  exercise_id: number
  url: string | null
  speed: number
  punctuation: boolean
  /** Null when cached — read the real length off the audio element instead. */
  duration_s: number | null
  cached: boolean
}

// --- accounts and entitlements ---

/** What the browser needs to reach Supabase Auth, served by the backend rather than baked in. */
export type AuthConfig = {
  enabled: boolean
  url: string | null
  anon_key: string | null
  billing_enabled: boolean
  anon_unit_limit: number
  member_unit_limit: number
  /** What premium costs, read from Stripe. Null when billing is off or Stripe was unreachable. */
  price: Price | null
}

export type Price = {
  amount_cents: number | null
  currency: string
  interval: string | null
}

export type Tier = 'anon' | 'free' | 'premium'

export type Entitlement = {
  tier: Tier
  /** null means unlimited. Must not be treated as zero — that would lock out paying learners. */
  unit_limit: number | null
  remaining: number | null
  unlocked_unit_ids: number[]
  premium_until: string | null
}

export type Me = {
  signed_in: boolean
  user_id: string | null
  email: string | null
  entitlement: Entitlement
}

export type UnlockResult = {
  unit_id: number
  unlocked: boolean
  entitlement: Entitlement
}

export type ClaimResult = {
  claimed: boolean
  vocab_items: number
  attempts: number
  unlocks: number
  sessions: number
  entitlement: Entitlement
}
