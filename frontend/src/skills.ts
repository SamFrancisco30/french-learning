/**
 * The five-skill split, with honest build status.
 *
 * `status` drives both the nav dot and the status pages. It is deliberately not
 * aspirational — a page that claims a module works when it doesn't wastes the learner's
 * time and hides what actually needs building.
 */

export type SkillStatus = 'live' | 'partial' | 'none'

export interface Skill {
  key: string
  route: string
  label: string
  /** Chinese label, matching the planning notes this was specced from. */
  native: string
  /**
   * The skill's name in each target language, shown as the page title.
   * "Écoute" literally means listening, so a fixed title would be wrong on every
   * page but one — the title has to follow both the skill and the chosen language.
   * Keyed by language code; falls back to `label` for an unlisted language.
   */
  titles: Record<string, string>
  status: SkillStatus
  /** One line for the page header. */
  blurb: string
  /** Percentage complete, for the status bar. Only used when status !== 'live'. */
  progress: number
  /** What already exists that this module reuses. */
  have: string[]
  /** What is genuinely missing. */
  need: string[]
}

export const SKILLS: Skill[] = [
  {
    key: 'listening',
    route: '/listening',
    label: 'Listening',
    native: '听力',
    titles: { fr: 'Écoute', ru: 'Аудирование', zh: '听力' },
    status: 'live',
    blurb:
      'Authentic media turned into calibrated comprehension practice — cloze, multiple ' +
      'choice, true/false, vocabulary and ordering, every item anchored to the audio.',
    progress: 100,
    have: [],
    need: [],
  },
  {
    key: 'dictation',
    route: '/dictation',
    label: 'Dictation',
    native: '听写',
    titles: { fr: 'Dictée', ru: 'Диктант', zh: '听写' },
    status: 'partial',
    blurb:
      'Write down what you hear. Word-level dictation works today through the listening ' +
      'module; full-sentence dictation is the missing half.',
    progress: 60,
    have: [
      'Fill-in-from-audio (听音频填词) — already shipping as the cloze exercise',
      'Level-based blank selection (根据级别) — frequency-band targeting, word bank at A1–A2',
      'Per-blank audio replay from word-level timestamps',
      'Diacritic- and typo-tolerant grading',
    ],
    need: [
      'Full-sentence dictation (整句听写) — a free-text input over a whole segment',
      'A word-level diff view so a learner sees exactly what they missed',
      'Its own page: pick a segment, hear it, type it, get scored',
    ],
  },
  {
    key: 'reading',
    route: '/reading',
    label: 'Reading',
    native: '阅读',
    titles: { fr: 'Lecture', ru: 'Чтение', zh: '阅读' },
    status: 'partial',
    blurb:
      'Read any French text with expression-aware lookup. Select a word for its meaning ' +
      'in context — and the idiom it belongs to, if it belongs to one.',
    progress: 45,
    have: [
      'Smart translation on arbitrary text — working on this page now',
      'Multiword-expression detection with inflection handling',
      'Save words and expressions to the review queue',
    ],
    need: [
      'A curated text library with difficulty scoring, like the listening one',
      'Comprehension questions generated per passage',
      'Reading-speed tracking',
    ],
  },
  {
    key: 'writing',
    route: '/writing',
    label: 'Writing',
    native: '写作',
    titles: { fr: 'Écriture', ru: 'Письмо', zh: '写作' },
    status: 'none',
    blurb:
      'Produce French and get it corrected. Not built — it needs free-text grading, which ' +
      'is a genuinely different problem from checking an answer against a key.',
    progress: 0,
    have: [
      'Attempt storage and per-skill scoring already generalise to it',
      'Structured LLM output with schema validation',
      'The vocabulary a learner has saved, to prompt writing tasks from',
    ],
    need: [
      'Rubric-based grading — grammar, register, task completion scored separately',
      'Error typing (agreement, tense, gender) so mistakes can be tracked over time',
      'Inline correction UI showing the original alongside the fix',
      'Prompts calibrated to CEFR level',
    ],
  },
  {
    key: 'speaking',
    route: '/speaking',
    label: 'Speaking',
    native: '口语',
    titles: { fr: 'Parole', ru: 'Говорение', zh: '口语' },
    status: 'none',
    blurb:
      'Say it out loud and get pronunciation feedback. The heaviest module — it needs ' +
      'microphone capture and forced alignment, neither of which exists yet.',
    progress: 0,
    have: [
      'Reference audio with word-level timestamps, to align a learner against',
      'The ASR layer, which can transcribe learner speech as well as source media',
      'Clip extraction for shadowing a specific phrase',
    ],
    need: [
      'Microphone capture and upload',
      'Forced alignment of learner speech against the reference',
      'Per-phoneme scoring, not just a transcript diff',
      'Latency low enough to feel conversational',
    ],
  },
]

export const SKILL_BY_KEY = Object.fromEntries(SKILLS.map((s) => [s.key, s]))

export function skillFromPath(segments: string[]): Skill {
  return SKILL_BY_KEY[segments[0] ?? ''] ?? SKILLS[0]
}

/** The skill's name in the target language, e.g. Écoute / Dictée / Lecture for French. */
export function skillTitle(skill: Skill, language: string): string {
  return skill.titles[language] ?? skill.label
}
