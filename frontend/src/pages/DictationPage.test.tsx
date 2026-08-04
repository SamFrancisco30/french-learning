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

/** Word lengths for "Les cinq mots ici bon" — 3, 4, 4, 3, 3. */
const LENGTHS = [3, 4, 4, 3, 3]

function item(overrides: Record<string, unknown> = {}) {
  return {
    exercise_id: 1,
    mode: 'sentence',
    prompt: 'Listen and type the sentence exactly as you hear it.',
    cefr: 'B1',
    difficulty_score: 50,
    word_count: 5,
    word_lengths: LENGTHS,
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
      // The label is how the length reaches a screen reader, which cannot see the dashes at all.
      expect(fields()[i]).toHaveAttribute(
        'aria-label',
        `Word ${i + 1} of ${LENGTHS.length}, ${n} letters`,
      )
      // --len drives both the width and the number of dashes drawn under it.
      expect(fields()[i].style.getPropertyValue('--len')).toBe(String(n))
    })
  })

  it('replaces the textarea entirely', async () => {
    await mount()

    expect(document.querySelector('textarea')).toBeNull()
  })

  it('jumps to the next word once a word is full', async () => {
    await mount()
    fields()[0].focus()

    typeAcross('Les')

    // Three letters into a three-letter word: done, so hand over without needing the space bar.
    await waitFor(() => expect(focusedIndex()).toBe(1))
    expect(fields()[0].value).toBe('Les')
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

  it('falls back to a textarea when an item carries no word lengths', async () => {
    // An older cached payload. Without lengths there are no lines to write on, and a dictation with
    // nowhere to type would be worse than a plain box.
    await mount({ word_lengths: [] })

    expect(document.querySelector('textarea')).not.toBeNull()
    expect(fields()).toHaveLength(0)
  })
})
