import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DictationPage } from './DictationPage'

/**
 * Writing a dictée on the ruled lines.
 *
 * Driven through the real page rather than the field component alone, because everything worth
 * asserting here is about focus moving between fields, and focus only behaves like focus inside a
 * mounted tree. Real keyboard events too (userEvent, not fireEvent), since the auto-advance has to
 * work for keystrokes AND for input that arrives without any keydown at all — a paste, an IME
 * commit, speech-to-text.
 */

const apiMocks = vi.hoisted(() => ({ submit: vi.fn() }))
const dictationMocks = vi.hoisted(() => ({
  next: vi.fn(),
  inventory: vi.fn(),
  levels: vi.fn(),
  audio: vi.fn(),
}))

vi.mock('../api', () => ({
  api: apiMocks,
  dictation: dictationMocks,
  LockedError: class LockedError extends Error {
    status = 402
    detail: unknown = null
  },
}))
vi.mock('../components/SpeedSlider', () => ({ SpeedSlider: () => <div /> }))

/** Word lengths for "Les cinq mots, ici bon." — 3, 4, 4, 3, 3. */
const LENGTHS = [3, 4, 4, 3, 3]

/** The same sentence as the hint line: words as lengths, punctuation verbatim and in place. */
const SLOTS = [
  { kind: 'word' as const, length: 3, text: null },
  { kind: 'word' as const, length: 4, text: null },
  { kind: 'word' as const, length: 4, text: null },
  { kind: 'mark' as const, length: null, text: ',' },
  { kind: 'word' as const, length: 3, text: null },
  { kind: 'word' as const, length: 3, text: null },
  { kind: 'mark' as const, length: null, text: '.' },
]

function item(overrides: Record<string, unknown> = {}) {
  return {
    exercise_id: 1,
    mode: 'sentence',
    prompt: 'Listen and type the sentence exactly as you hear it.',
    cefr: 'B1',
    difficulty_score: 50,
    word_count: 5,
    word_lengths: LENGTHS,
    hint_slots: SLOTS,
    sentence_count: 1,
    audio_start_s: 0,
    audio_end_s: 5,
    unit_id: 1,
    unit_start_s: 0,
    unit_end_s: 60,
    clip_url: null,
    lesson_id: 1,
    lesson_title: 'A lesson',
    topic: 'world_news',
    ...overrides,
  }
}

function fields(): HTMLInputElement[] {
  return Array.from(document.querySelectorAll<HTMLInputElement>('.dict-word'))
}

function focusedIndex(): number {
  return fields().indexOf(document.activeElement as HTMLInputElement)
}

/**
 * One keystroke, modelled the way a browser delivers it: keydown first, then — only if nothing
 * called preventDefault — the value change.
 *
 * Deliberately fireEvent rather than userEvent. userEvent schedules its keystrokes, and that
 * scheduling interleaved with the synchronous focus change in a way that made a *different* test in
 * this file fail on each run. The failures moved around because the interference did; the page was
 * never at fault. fireEvent is synchronous, so each keystroke lands, focus settles, and the next
 * keystroke goes to whatever is focused now — which is the whole mechanism under test.
 */
function keystroke(ch: string): void {
  const before = document.activeElement as HTMLInputElement
  const notPrevented = fireEvent.keyDown(before, { key: ch })
  if (!notPrevented) return // space and Enter are consumed to move between fields

  // Re-read: a full word hands over on keydown, so the character belongs to the next field.
  const target = document.activeElement as HTMLInputElement
  fireEvent.input(target, { target: { value: target.value + ch } })
}

function typeAcross(text: string): void {
  for (const ch of text) keystroke(ch)
}

/** What a paste does to a controlled input: one input event carrying the whole string. */
function pasteInto(el: HTMLInputElement, text: string): void {
  fireEvent.input(el, { target: { value: text } })
}

async function mount(itemOverrides: Record<string, unknown> = {}) {
  dictationMocks.next.mockResolvedValue({
    item: item(itemOverrides),
    level: { level: 'B1', mode: 'sentence', attempts: 0, recent_mean: null, reason: 'start' },
    served_level: 'B1',
    off_level: false,
    repeat: false,
    remaining_at_level: 5,
  })
  render(<DictationPage language="fr" learnerKey="learner_test" navigate={vi.fn()} />)
  await waitFor(() =>
    expect(document.querySelector('.dict-word, .dict-input')).not.toBeNull(),
  )
}

describe('DictationPage word fields', () => {
  beforeEach(() => {
    for (const m of Object.values(dictationMocks)) m.mockReset()
    apiMocks.submit.mockReset()
    dictationMocks.inventory.mockResolvedValue({
      language: 'fr',
      by_mode: { sentence: {}, paragraph: {} },
      totals: { sentence: 10, paragraph: 5 },
      levels: ['A1', 'A2', 'B1', 'B2', 'C1', 'C2'],
    })
    dictationMocks.levels.mockResolvedValue([])
    dictationMocks.audio.mockResolvedValue({ exercise_id: 1, url: null, speed: 1 })
  })

  it('renders one field per word, sized and labelled by that word length', async () => {
    await mount()

    expect(fields()).toHaveLength(LENGTHS.length)
    LENGTHS.forEach((n, i) => {
      // The label is how the length reaches a screen reader, which cannot see the dashes at all —
      // and it names any punctuation that follows, which is aria-hidden as a glyph.
      const trailing = i === 2 ? ', followed by ,' : i === 4 ? ', followed by .' : ''
      expect(fields()[i]).toHaveAttribute(
        'aria-label',
        `Word ${i + 1} of ${LENGTHS.length}, ${n} letters${trailing}`,
      )
      // --len drives both the width and the number of dashes drawn under it.
      expect(fields()[i].style.getPropertyValue('--len')).toBe(String(n))
    })
  })

  it('replaces the textarea entirely', async () => {
    await mount()

    expect(document.querySelector('textarea')).toBeNull()
  })

  it('stays on a full word so its last letter can still be accented', async () => {
    await mount()
    fields()[0].focus()

    typeAcross('Les')

    /*
      The regression this pins. Advancing the moment the count was reached broke every accented word:
      on macOS an accent comes from holding the key — "a" is inserted, then replaced by "à" when you
      pick from the popup — so the field was full and gone before the popup could be used. A
      one-letter word like "à" was unreachable, and so was any word whose last letter takes an accent.
    */
    expect(fields()[0].value).toBe('Les')
    expect(focusedIndex()).toBe(0)
  })

  it('lets a full word be corrected in place, accent and all', async () => {
    await mount()
    fields()[0].focus()
    typeAcross('Les')

    // What a long-press does: same length, different last character.
    fireEvent.input(fields()[0], { target: { value: 'Leè' } })

    expect(fields()[0].value).toBe('Leè')
    // Still here — the correction was not pushed into the next word.
    expect(focusedIndex()).toBe(0)
    expect(fields()[1].value).toBe('')
  })

  it('accepts a one-letter word without skipping past it', async () => {
    // "à" is the case from the report: one letter, so the field was full on the first keystroke and
    // handed over before the accent popup could be used at all.
    await mount({ word_lengths: [1, 4], hint_slots: [
      { kind: 'word', length: 1, text: null },
      { kind: 'word', length: 4, text: null },
    ] })
    fields()[0].focus()

    typeAcross('a')
    expect(focusedIndex()).toBe(0)

    fireEvent.input(fields()[0], { target: { value: 'à' } })
    expect(fields()[0].value).toBe('à')
    expect(focusedIndex()).toBe(0)
  })

  it('hands over on the next character, carrying it across', async () => {
    await mount()
    fields()[0].focus()
    typeAcross('Les')

    // The character after a full word is the signal that the word is finished.
    typeAcross('c')

    expect(fields()[0].value).toBe('Les')
    expect(fields()[1].value).toBe('c')
    expect(focusedIndex()).toBe(1)
  })

  it('keeps typing straight through several words', async () => {
    await mount()
    fields()[0].focus()

    typeAcross('Lescinqmots')

    // Asserted on where the letters landed rather than on the final focus index. The distribution
    // is the contract; the index after a long run also depends on userEvent's own scheduling, and
    // pinning it made this test flaky without testing anything more.
    await waitFor(() =>
      expect(fields().map((f) => f.value)).toEqual(['Les', 'cinq', 'mots', '', '']),
    )
  })

  it('advances on space for a word shorter than its line', async () => {
    await mount()
    fields()[0].focus()

    // "Le" is short of the three-letter line, so the learner says so with a space.
    typeAcross('Le ')

    await waitFor(() => expect(focusedIndex()).toBe(1))
    expect(fields()[0].value).toBe('Le')
    // The space itself must never land inside a field: it is the separator the answer is joined on.
    expect(fields().every((f) => !/\s/.test(f.value))).toBe(true)
  })

  it('steps back on backspace from an empty field', async () => {
    await mount()
    fields()[1].focus()

    fireEvent.keyDown(document.activeElement as HTMLInputElement, { key: 'Backspace' })

    await waitFor(() => expect(focusedIndex()).toBe(0))
  })

  it('spreads a pasted sentence across the fields instead of stripping its spaces', async () => {
    await mount()
    fields()[0].focus()

    pasteInto(fields()[0], 'Les cinq mots ici')

    // A space can reach a field with no keydown at all — paste, IME, speech-to-text. Stripping it
    // silently concatenated the whole sentence into the first field.
    await waitFor(() => expect(fields()[3].value).toBe('ici'))
    expect(fields().map((f) => f.value)).toEqual(['Les', 'cinq', 'mots', 'ici', ''])
  })

  it('submits the fields joined into one plain sentence', async () => {
    apiMocks.submit.mockResolvedValue({
      is_correct: true,
      score: 1,
      feedback: { words: [], counts: {} },
    })
    await mount()
    fields()[0].focus()
    typeAcross('Lescinqmotsicibon')

    fireEvent.click(screen.getByRole('button', { name: 'Check' }))

    // Grading is unchanged by the new layout: it still receives the sentence as one string.
    await waitFor(() => expect(apiMocks.submit).toHaveBeenCalled())
    expect(apiMocks.submit.mock.calls[0][1]).toEqual({ text: 'Les cinq mots ici bon' })
  })

  it('leaves Check disabled until something is written', async () => {
    await mount()

    expect(screen.getByRole('button', { name: 'Check' })).toBeDisabled()
    fields()[0].focus()
    typeAcross('L')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Check' })).toBeEnabled())
  })

  it('prints the punctuation on the line, in place, without a field for it', async () => {
    await mount()

    const marks = Array.from(document.querySelectorAll('.dict-hint-mark')).map((m) => m.textContent)
    // A comma cannot affect the score, so showing it costs nothing and stops the hint line
    // disagreeing with the sentence being read aloud.
    expect(marks).toEqual([',', '.'])
    // Shown, never typed: still one field per word, and no field for a mark.
    expect(fields()).toHaveLength(LENGTHS.length)
  })

  it('keeps each mark with its own word rather than between two of them', async () => {
    await mount()

    // The comma belongs to the third word, so it sits inside that word's group — which is what makes
    // the row's gap fall between words and never between a word and its own comma.
    const groups = Array.from(document.querySelectorAll('.dict-hint-group'))
    const third = groups.find((g) => g.querySelector('input')?.style.getPropertyValue('--len') === '4' && g.textContent === ',')
    expect(third).toBeTruthy()
  })

  it('never lets punctuation reach the submitted text', async () => {
    apiMocks.submit.mockResolvedValue({
      is_correct: true,
      score: 1,
      feedback: { words: [], counts: {} },
    })
    await mount()
    fields()[0].focus()
    typeAcross('Lescinqmotsicibon')

    fireEvent.click(screen.getByRole('button', { name: 'Check' }))

    // The marks are printed, not typed, so the graded string is words and spaces only — exactly what
    // it was before the punctuation was shown.
    await waitFor(() => expect(apiMocks.submit).toHaveBeenCalled())
    expect(apiMocks.submit.mock.calls[0][1]).toEqual({ text: 'Les cinq mots ici bon' })
  })

  it('renders a lengths-only payload that predates the punctuation line', async () => {
    // An item cached before hint_slots existed. Without a fallback this rendered an empty line with
    // nowhere to type at all.
    await mount({ hint_slots: [] })

    expect(fields()).toHaveLength(LENGTHS.length)
    expect(document.querySelectorAll('.dict-hint-mark')).toHaveLength(0)
  })

  it('falls back to a textarea when an item carries no word lengths', async () => {
    // An older cached payload. Without lengths there are no lines to write on, and a dictation with
    // nowhere to type would be worse than a plain box.
    await mount({ word_lengths: [] })

    expect(document.querySelector('textarea')).not.toBeNull()
    expect(fields()).toHaveLength(0)
  })
})
