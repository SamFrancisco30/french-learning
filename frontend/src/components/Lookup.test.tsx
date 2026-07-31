import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { IdentityProvider } from '../identity/IdentityContext'
import type {
  ExpressionHit,
  LookupResult,
  VocabItem,
  VocabSavedKeys,
} from '../types'
import { VocabProvider } from '../vocab/VocabContext'
import { LookupProvider, SelectableText } from './Lookup'

const apiMocks = vi.hoisted(() => ({
  lookup: vi.fn(),
  unitExpressions: vi.fn(),
  savedKeys: vi.fn(),
  save: vi.fn(),
  list: vi.fn(),
  edit: vi.fn(),
  remove: vi.fn(),
  sentence: vi.fn(),
  checkPractice: vi.fn(),
}))

vi.mock('../api', () => ({
  lexicon: {
    lookup: apiMocks.lookup,
    unitExpressions: apiMocks.unitExpressions,
  },
  vocab: {
    savedKeys: apiMocks.savedKeys,
    save: apiMocks.save,
    list: apiMocks.list,
    edit: apiMocks.edit,
    remove: apiMocks.remove,
  },
  grammar: {
    sentence: apiMocks.sentence,
    checkPractice: apiMocks.checkPractice,
  },
}))

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function savedKeys(...keys: string[]): VocabSavedKeys {
  return {
    language: 'fr',
    items: keys.map((normalized_headword, index) => ({
      id: index + 1,
      normalized_headword,
    })),
  }
}

function expression(overrides: Partial<ExpressionHit> = {}): ExpressionHit {
  return {
    id: 11,
    canonical: "l'amour",
    normalized_headword: "l'amour",
    surface: 'L’Amour',
    kind: 'fixed_phrase',
    gloss_en: 'love',
    literal_en: null,
    note: null,
    component_spans: [[0, 7]],
    char_start: 0,
    char_end: 7,
    confidence: 1,
    source: 'live',
    ...overrides,
  }
}

function lookupResult(overrides: Partial<LookupResult> = {}): LookupResult {
  return {
    language: 'fr',
    selection: 'Écoutais',
    char_start: 0,
    char_end: 8,
    context: "J'écoutais la radio.",
    audio_start_s: null,
    audio_end_s: null,
    is_sentence: false,
    constructions: [],
    word: {
      surface: 'Écoutais',
      normalized_headword: 'écouter',
      lemma: 'Écouter',
      pos: 'verb',
      gloss_en: 'to listen',
      other_senses: [],
      note: null,
      zipf: 4.3,
    },
    expressions: [],
    source: 'live',
    unit_id: 42,
    lemmatizer: 'test',
    inferred: false,
    error: null,
    ...overrides,
  }
}

function vocabItem(
  headword: string,
  normalizedHeadword: string,
  overrides: Partial<VocabItem> = {},
): VocabItem {
  return {
    id: 7,
    language: 'fr',
    headword,
    normalized_headword: normalizedHeadword,
    gloss_en: null,
    example: null,
    zipf: null,
    reps: 0,
    due_at: null,
    created_at: '2026-07-31T10:00:00Z',
    updated_at: '2026-07-31T10:00:00Z',
    source: null,
    ...overrides,
  }
}

function Harness({
  text = 'Écoutais',
  language = 'fr',
  unitId = 42,
}: {
  text?: string
  language?: string
  unitId?: number | null
}) {
  return (
    <IdentityProvider>
      <VocabProvider>
        {/* No learnerKey prop: vocabulary identity belongs to the shared providers. */}
        <LookupProvider language={language} unitId={unitId}>
          <SelectableText text={text} />
        </LookupProvider>
      </VocabProvider>
    </IdentityProvider>
  )
}

async function selectText(text: string, start = 0, end = text.length) {
  const priorLookupCount = apiMocks.lookup.mock.calls.length
  const root = document.querySelector('.selectable')
  if (!(root instanceof HTMLElement)) throw new Error('selectable text not rendered')
  const node = root.querySelector('[data-off]')?.firstChild
  if (!node) throw new Error('selectable text node not rendered')

  const range = document.createRange()
  range.setStart(node, start)
  range.setEnd(node, end)
  Object.defineProperty(range, 'getBoundingClientRect', {
    value: () => ({
      x: 20,
      y: 20,
      top: 20,
      left: 20,
      right: 100,
      bottom: 40,
      width: 80,
      height: 20,
      toJSON: () => ({}),
    }),
  })
  const selection = window.getSelection()
  selection?.removeAllRanges()
  selection?.addRange(range)
  fireEvent.mouseUp(root)

  await waitFor(() => expect(apiMocks.lookup).toHaveBeenCalledTimes(priorLookupCount + 1))
}

describe('Lookup vocabulary synchronization', () => {
  beforeEach(() => {
    Object.values(apiMocks).forEach((mock) => mock.mockReset())
    localStorage.setItem('learner_key', 'learner_lookup-test')
    apiMocks.savedKeys.mockResolvedValue(savedKeys())
    apiMocks.lookup.mockResolvedValue(lookupResult())
  })

  it('automatically loads saved keys for the active language without a learnerKey prop', async () => {
    const { rerender } = render(<Harness language="fr" />)

    await waitFor(() =>
      expect(apiMocks.savedKeys).toHaveBeenCalledWith('fr', {
        'X-Learner-Key': 'learner_lookup-test',
      }),
    )

    rerender(<Harness language="en" />)
    await waitFor(() =>
      expect(apiMocks.savedKeys).toHaveBeenCalledWith('en', {
        'X-Learner-Key': 'learner_lookup-test',
      }),
    )
  })

  it('uses the exact server normalized key for case and apostrophe-equivalent saved state', async () => {
    apiMocks.savedKeys.mockResolvedValue(savedKeys("l'amour"))
    apiMocks.lookup.mockResolvedValue(
      lookupResult({
        selection: 'L’Amour',
        expressions: [expression({ canonical: 'L’Amour', normalized_headword: "l'amour" })],
      }),
    )
    render(<Harness text="L’Amour" />)
    await selectText('L’Amour')

    expect(await screen.findByRole('button', { name: 'expression saved' })).toHaveTextContent(
      '✓ saved',
    )
  })

  it('shows neutral loading state instead of claiming an unknown key is not saved', async () => {
    const loading = deferred<VocabSavedKeys>()
    apiMocks.savedKeys.mockReturnValue(loading.promise)
    render(<Harness />)
    await selectText('Écoutais')

    const button = await screen.findByRole('button', { name: 'checking saved status for word' })
    expect(button).toBeDisabled()
    expect(button).toHaveTextContent('checking saved words')
    expect(screen.queryByRole('button', { name: /save word/i })).not.toBeInTheDocument()

    loading.resolve(savedKeys())
    await act(() => loading.promise)
  })

  it('shows saved-key load errors with Retry and updates controls after retry succeeds', async () => {
    apiMocks.savedKeys
      .mockRejectedValueOnce(new Error('saved keys unavailable'))
      .mockResolvedValueOnce(savedKeys())
    render(<Harness />)
    await selectText('Écoutais')

    expect(await screen.findByText(/saved keys unavailable/i)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /retry saved words/i }))

    expect(await screen.findByRole('button', { name: /save word/i })).toBeEnabled()
    expect(apiMocks.savedKeys).toHaveBeenCalledTimes(2)
  })

  it('saves expression and word independently with strict inputs and shared cache status', async () => {
    apiMocks.lookup.mockResolvedValue(
      lookupResult({
        expressions: [
          expression({
            canonical: "prendre l'air",
            normalized_headword: "prendre l'air",
            gloss_en: 'get some fresh air',
          }),
        ],
      }),
    )
    apiMocks.save.mockImplementation(
      async (input: {
        headword: string
        gloss_en?: string | null
        example?: string | null
        unit_id?: number | null
      }) =>
        input.headword === "prendre l'air"
          ? vocabItem(input.headword, "prendre l'air", input)
          : vocabItem(input.headword, 'écouter', input),
    )
    render(<Harness />)
    await selectText('Écoutais')

    await userEvent.click(await screen.findByRole('button', { name: /save expression/i }))
    expect(await screen.findByRole('button', { name: 'expression saved' })).toHaveTextContent(
      '✓ saved',
    )
    expect(screen.getByRole('button', { name: /save word/i })).toBeEnabled()

    await userEvent.click(screen.getByRole('button', { name: /save word/i }))
    expect(await screen.findByRole('button', { name: 'word saved' })).toHaveTextContent('✓ saved')
    expect(apiMocks.save).toHaveBeenNthCalledWith(
      1,
      {
        language: 'fr',
        headword: "prendre l'air",
        gloss_en: 'get some fresh air',
        example: "J'écoutais la radio.",
        unit_id: 42,
      },
      { 'X-Learner-Key': 'learner_lookup-test' },
    )
    expect(apiMocks.save).toHaveBeenNthCalledWith(
      2,
      {
        language: 'fr',
        headword: 'Écouter',
        gloss_en: 'to listen',
        example: "J'écoutais la radio.",
        unit_id: 42,
      },
      { 'X-Learner-Key': 'learner_lookup-test' },
    )
  })

  it('falls back to the selection only when the word has no lemma', async () => {
    apiMocks.lookup.mockResolvedValue(
      lookupResult({
        selection: 'Inédit',
        context: 'Inédit.',
        word: {
          ...lookupResult().word,
          surface: 'Inédit',
          normalized_headword: 'inédit',
          lemma: null,
          gloss_en: 'unpublished',
        },
      }),
    )
    apiMocks.save.mockResolvedValue(vocabItem('Inédit', 'inédit'))
    render(<Harness text="Inédit" unitId={null} />)
    await selectText('Inédit')
    await userEvent.click(await screen.findByRole('button', { name: /save word/i }))

    expect(apiMocks.save).toHaveBeenCalledWith(
      {
        language: 'fr',
        headword: 'Inédit',
        gloss_en: 'unpublished',
        example: 'Inédit.',
        unit_id: null,
      },
      { 'X-Learner-Key': 'learner_lookup-test' },
    )
  })

  it('shows a candidate-scoped save error and retries the same input without duplicates', async () => {
    apiMocks.save
      .mockRejectedValueOnce(new Error('save temporarily unavailable'))
      .mockResolvedValueOnce(vocabItem('Écouter', 'écouter'))
    render(<Harness />)
    await selectText('Écoutais')
    await userEvent.click(await screen.findByRole('button', { name: /save word/i }))

    const error = await screen.findByRole('alert')
    expect(error).toHaveTextContent('save temporarily unavailable')
    const retry = within(error).getByRole('button', { name: /retry save word/i })
    await userEvent.click(retry)

    expect(await screen.findByRole('button', { name: 'word saved' })).toHaveTextContent('✓ saved')
    expect(apiMocks.save).toHaveBeenCalledTimes(2)
    expect(apiMocks.save.mock.calls[1]).toEqual(apiMocks.save.mock.calls[0])
  })

  it('clears transient candidate errors on a new selection while retaining shared saved truth', async () => {
    apiMocks.lookup
      .mockResolvedValueOnce(lookupResult({ expressions: [expression()] }))
      .mockResolvedValueOnce(
        lookupResult({
          selection: 'ÉCOUTER',
          word: {
            ...lookupResult().word,
            surface: 'ÉCOUTER',
            lemma: 'ÉCOUTER',
            normalized_headword: 'écouter',
          },
        }),
      )
    apiMocks.save
      .mockResolvedValueOnce(vocabItem('Écouter', 'écouter'))
      .mockRejectedValueOnce(new Error('old expression failure'))
    const { rerender } = render(<Harness />)
    await selectText('Écoutais')
    await userEvent.click(await screen.findByRole('button', { name: /save word/i }))
    expect(await screen.findByRole('button', { name: 'word saved' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: /save expression/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent('old expression failure')

    rerender(<Harness text="ÉCOUTER" />)
    await selectText('ÉCOUTER')

    expect(await screen.findByRole('button', { name: 'word saved' })).toHaveTextContent('✓ saved')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('does not attach an older pending save failure to a newer selection', async () => {
    const oldSave = deferred<VocabItem>()
    apiMocks.lookup
      .mockResolvedValueOnce(lookupResult())
      .mockResolvedValueOnce(
        lookupResult({
          selection: 'Parlais',
          context: 'Je parlais.',
          word: {
            ...lookupResult().word,
            surface: 'Parlais',
            lemma: 'Parler',
            normalized_headword: 'parler',
            gloss_en: 'to speak',
          },
        }),
      )
    apiMocks.save.mockReturnValue(oldSave.promise)
    const { rerender } = render(<Harness />)
    await selectText('Écoutais')
    await userEvent.click(await screen.findByRole('button', { name: /save word/i }))
    expect(apiMocks.save).toHaveBeenCalledOnce()

    rerender(<Harness text="Parlais" />)
    await selectText('Parlais')
    oldSave.reject(new Error('old save failed'))
    await act(async () => {
      try {
        await oldSave.promise
      } catch {
        // The UI consumes the rejection; this await only drains the controlled promise.
      }
      await Promise.resolve()
    })

    expect(await screen.findByRole('button', { name: /save word/i })).toBeEnabled()
    expect(screen.queryByText(/old save failed/i)).not.toBeInTheDocument()
  })

  it('invalidates a deferred lookup when the active language changes', async () => {
    const oldLookup = deferred<LookupResult>()
    apiMocks.lookup.mockReturnValueOnce(oldLookup.promise)
    const { rerender } = render(<Harness language="fr" />)
    await selectText('Écoutais')
    expect(screen.getByRole('dialog', { name: 'Translation' })).toBeInTheDocument()

    rerender(<Harness language="en" />)
    expect(screen.queryByRole('dialog', { name: 'Translation' })).not.toBeInTheDocument()

    oldLookup.resolve(lookupResult())
    await act(async () => {
      await oldLookup.promise
      await Promise.resolve()
    })
    expect(screen.queryByRole('dialog', { name: 'Translation' })).not.toBeInTheDocument()
  })

  it('closes an old popup on scope change and uses the new language and unit after reselection', async () => {
    apiMocks.lookup
      .mockResolvedValueOnce(lookupResult())
      .mockResolvedValueOnce(
        lookupResult({
          language: 'en',
          selection: 'Speaking',
          context: 'I was speaking.',
          unit_id: 43,
          word: {
            ...lookupResult().word,
            surface: 'Speaking',
            lemma: 'Speak',
            normalized_headword: 'speak',
            gloss_en: 'to talk',
          },
        }),
      )
    apiMocks.save.mockResolvedValue(
      vocabItem('Speak', 'speak', {
        language: 'en',
        gloss_en: 'to talk',
        example: 'I was speaking.',
      }),
    )
    const { rerender } = render(<Harness language="fr" unitId={42} />)
    await selectText('Écoutais')
    expect(await screen.findByRole('button', { name: /save word/i })).toBeEnabled()

    rerender(<Harness language="en" unitId={43} text="Speaking" />)
    expect(screen.queryByRole('dialog', { name: 'Translation' })).not.toBeInTheDocument()

    await selectText('Speaking')
    expect(apiMocks.lookup).toHaveBeenLastCalledWith({
      language: 'en',
      text: 'Speaking',
      char_start: 0,
      char_end: 8,
      unit_id: 43,
    })
    await userEvent.click(await screen.findByRole('button', { name: /save word/i }))
    expect(apiMocks.save).toHaveBeenLastCalledWith(
      {
        language: 'en',
        headword: 'Speak',
        gloss_en: 'to talk',
        example: 'I was speaking.',
        unit_id: 43,
      },
      { 'X-Learner-Key': 'learner_lookup-test' },
    )
  })

  it('lets authoritative shared saved truth replace a stale candidate error for the same key', async () => {
    apiMocks.lookup.mockResolvedValue(
      lookupResult({
        word: {
          ...lookupResult().word,
          normalized_headword: 'écouter',
        },
        expressions: [
          expression({
            canonical: 'Écouter',
            normalized_headword: 'écouter',
            gloss_en: 'to listen',
          }),
        ],
      }),
    )
    apiMocks.save
      .mockRejectedValueOnce(new Error('word save failed'))
      .mockResolvedValueOnce(vocabItem('Écouter', 'écouter'))
    render(<Harness />)
    await selectText('Écoutais')

    await userEvent.click(await screen.findByRole('button', { name: /save word/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent('word save failed')

    await userEvent.click(screen.getByRole('button', { name: /save expression/i }))

    expect(await screen.findByRole('button', { name: 'word saved' })).toHaveTextContent('✓ saved')
    expect(screen.getByRole('button', { name: 'expression saved' })).toHaveTextContent('✓ saved')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /retry save word/i })).not.toBeInTheDocument()
  })
})
