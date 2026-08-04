import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Profiler, StrictMode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { AuthProvider } from '../auth/AuthContext'
import { IdentityProvider } from '../identity/IdentityContext'
import type { VocabItem, VocabList } from '../types'
import { VocabularyPage } from './VocabularyPage'

const apiMocks = vi.hoisted(() => ({
  languages: vi.fn(),
  authConfig: vi.fn(),
  me: vi.fn(),
}))

const vocabMocks = vi.hoisted(() => ({
  list: vi.fn(),
  edit: vi.fn(),
  remove: vi.fn(),
}))

vi.mock('../api', () => ({
  api: apiMocks,
  setIdentityHeaderSource: vi.fn(),
  LockedError: class LockedError extends Error {},
}))
vi.mock('../vocab/VocabContext', () => ({
  useVocab: () => vocabMocks,
}))
vi.mock('./ListeningPage', () => ({
  ListeningPage: () => <div>Listening library</div>,
}))
vi.mock('./ReadingPage', () => ({
  ReadingPage: () => <div>Reading page</div>,
}))
vi.mock('./SkillStatusPage', () => ({
  SkillStatusPage: () => <div>Skill status</div>,
}))

function word(overrides: Partial<VocabItem> = {}): VocabItem {
  return {
    id: 7,
    language: 'fr',
    headword: 'écouter',
    normalized_headword: 'écouter',
    gloss_en: 'to listen',
    example: 'Écoutez bien.',
    zipf: 4.3,
    reps: 0,
    due_at: null,
    created_at: '2026-07-28T10:00:00Z',
    updated_at: '2026-07-28T10:00:00Z',
    source: {
      lesson_id: 12,
      lesson_title: 'Morning news',
      unit_id: 34,
      unit_index: 2,
    },
    ...overrides,
  }
}

function page(items: VocabItem[] = [], nextCursor: string | null = null): VocabList {
  return { items, next_cursor: nextCursor, total: items.length }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function renderPage(language = 'fr', navigate = vi.fn()) {
  return {
    navigate,
    ...render(<VocabularyPage language={language} navigate={navigate} />),
  }
}

function renderApp() {
  return render(
    <IdentityProvider>
      <AuthProvider>
        <App />
      </AuthProvider>
    </IdentityProvider>,
  )
}

/**
 * Arm a mode from the toolbar. Rows are inert until one is chosen.
 *
 * Edit and Delete used to be a pair of buttons on every row; they are now one choice in the toolbar
 * that says what clicking a word does. So a test that wants to edit or delete has to say so first,
 * exactly as a learner does.
 */
async function armMode(mode: 'Edit' | 'Delete'): Promise<void> {
  // Exact name: the row's own click target is labelled "Edit écouter", the toolbar control "Edit".
  const button = screen.getByRole('button', { name: mode })
  // Idempotent, because the control is a toggle: clicking an already-armed mode leaves it. A mode
  // also survives the action — finishing an edit returns to the list with Edit still armed — so a
  // test that arms twice would disarm without this check.
  if (button.getAttribute('aria-pressed') !== 'true') {
    // fireEvent, not userEvent: one test here runs on fake timers, and userEvent waits on real ones,
    // so arming through it hung for the full timeout. A plain button needs no pointer simulation.
    fireEvent.click(button)
  }
}

describe('My Words route and navigation', () => {
  beforeEach(() => {
    apiMocks.languages.mockReset()
    apiMocks.authConfig.mockReset()
    apiMocks.me.mockReset()
    apiMocks.authConfig.mockResolvedValue({
      enabled: false,
      url: null,
      anon_key: null,
      billing_enabled: false,
      anon_unit_limit: 2,
      member_unit_limit: 5,
    })
    apiMocks.me.mockResolvedValue({
      signed_in: false,
      user_id: null,
      email: null,
      entitlement: {
        tier: 'anon',
        unit_limit: 2,
        remaining: 2,
        unlocked_unit_ids: [],
        premium_until: null,
      },
    })
    vocabMocks.list.mockReset()
    vocabMocks.edit.mockReset()
    vocabMocks.remove.mockReset()
    apiMocks.languages.mockResolvedValue([
      { code: 'fr', name_en: 'French', name_native: 'Français' },
      { code: 'ru', name_en: 'Russian', name_native: 'Русский' },
    ])
    vocabMocks.list.mockResolvedValue(page())
    window.location.hash = '#/vocabulary'
  })

  it('renders the utility route with exact branding and no active skill', async () => {
    renderApp()

    expect(screen.getByRole('heading', { level: 1, name: 'My Words' })).toBeInTheDocument()
    expect(screen.queryByText('Listening library')).not.toBeInTheDocument()
    const utility = screen.getByRole('button', { name: 'My Words' })
    expect(utility).toHaveAttribute('aria-current', 'page')
    for (const skill of ['Listening', 'Dictation', 'Reading', 'Writing', 'Speaking']) {
      const tab = screen.getByRole('button', { name: new RegExp(`^${skill}`) })
      expect(tab).not.toHaveAttribute('aria-current')
      // Each tab still explains what its page is for, but through the styled `.tip` panel that
      // replaced the `title` attribute this used to read. `title` has no styling, waits about a
      // second to appear and cannot hold more than a line comfortably. The panel is aria-hidden
      // because the tab's own label already names the skill, so it is read from the DOM rather
      // than through the accessible name.
      const tip = tab.querySelector('.tip')
      expect(tip).not.toBeNull()
      expect(tip?.textContent?.trim()).toBeTruthy()
    }
  })

  it('enters My Words from a skill and follows browser back and forward history', async () => {
    window.history.replaceState(null, '', '#/listening')
    renderApp()

    expect(screen.getByText('Listening library')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'My Words' }))
    await waitFor(() => expect(window.location.hash).toBe('#/vocabulary'))
    await screen.findByRole('heading', { level: 1, name: 'My Words' })

    await act(async () => window.history.back())
    await waitFor(() => expect(window.location.hash).toBe('#/listening'))
    expect(await screen.findByText('Listening library')).toBeInTheDocument()

    await act(async () => window.history.forward())
    await waitFor(() => expect(window.location.hash).toBe('#/vocabulary'))
    expect(
      await screen.findByRole('heading', { level: 1, name: 'My Words' }),
    ).toBeInTheDocument()
  })

  it('keeps the utility route when switching language and reloads that language', async () => {
    renderApp()

    // The language control is a flag dropdown now rather than one button per code, so the
    // interaction is open-then-pick. The trigger carries the current language in its accessible
    // name, because a flag on its own says nothing to a screen reader.
    const trigger = await screen.findByRole('button', { name: /language: french/i })
    await userEvent.click(trigger)
    expect(trigger).toHaveAttribute('aria-expanded', 'true')

    await userEvent.click(await screen.findByRole('menuitem', { name: /Russian/ }))

    expect(window.location.hash).toBe('#/vocabulary')
    await waitFor(() =>
      expect(vocabMocks.list).toHaveBeenLastCalledWith({
        language: 'ru',
        sort: 'recent',
        limit: 50,
      }),
    )
  })
})

describe('VocabularyPage', () => {
  beforeEach(() => {
    vocabMocks.list.mockReset()
    vocabMocks.edit.mockReset()
    vocabMocks.remove.mockReset()
  })

  it('shows loading, empty, and initial error states with retry', async () => {
    const initial = deferred<VocabList>()
    vocabMocks.list.mockReturnValueOnce(initial.promise)
    const view = renderPage()
    expect(screen.getByRole('status')).toHaveTextContent('Loading saved words')

    initial.resolve(page())
    expect(await screen.findByText('No saved words yet')).toBeInTheDocument()

    view.unmount()
    vocabMocks.list.mockRejectedValueOnce(new Error('Words unavailable')).mockResolvedValueOnce(page())
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent('Words unavailable')
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('No saved words yet')).toBeInTheDocument()
    expect(vocabMocks.list).toHaveBeenCalledTimes(3)
  })

  it('renders the word and its meaning, and links to its source from the headword', async () => {
    const navigate = vi.fn()
    vocabMocks.list.mockResolvedValue(page([word()]))
    renderPage('fr', navigate)

    expect(await screen.findByRole('heading', { name: 'écouter' })).toBeInTheDocument()
    expect(screen.getByText('to listen')).toBeInTheDocument()
    // The row is now the word and its meaning, and nothing else. The save date is gone — "recently
    // saved" is a sort option, so the ordering already says what the date said — and the example
    // moved into the edit form, where one word is being looked at rather than many scanned.
    expect(screen.queryByText(/Saved/)).not.toBeInTheDocument()
    expect(screen.queryByText('Écoutez bien.')).not.toBeInTheDocument()
    // The provenance moved onto the headword, so the way back to the lesson costs no extra line.
    // The word first, then where it came from — see the aria-label in VocabularyRow.
    const source = screen.getByRole('link', { name: /^écouter — open Morning news, Unit 2$/ })
    expect(source).toHaveAttribute('href', '#/listening/lesson/12/unit/34')
    source.focus()
    expect(source).toHaveFocus()
    await userEvent.click(source)
    expect(navigate).toHaveBeenCalledWith('/listening/lesson/12/unit/34')
  })

  it('loads the current language by default and replaces rows when it changes', async () => {
    vocabMocks.list
      .mockResolvedValueOnce(page([word()]))
      .mockResolvedValueOnce(
        page([word({ id: 8, language: 'ru', headword: 'слушать', normalized_headword: 'слушать' })]),
      )
    const view = renderPage('fr')
    expect(await screen.findByText('écouter')).toBeInTheDocument()
    expect(vocabMocks.list).toHaveBeenNthCalledWith(1, {
      language: 'fr',
      sort: 'recent',
      limit: 50,
    })

    view.rerender(<VocabularyPage language="ru" navigate={view.navigate} />)
    expect(await screen.findByText('слушать')).toBeInTheDocument()
    expect(screen.queryByText('écouter')).not.toBeInTheDocument()
  })

  it('debounces controlled search and changes sort as a fresh query', async () => {
    vi.useFakeTimers()
    try {
      vocabMocks.list.mockResolvedValue(page())
      renderPage()
      await act(async () => Promise.resolve())
      expect(vocabMocks.list).toHaveBeenCalledTimes(1)
      const search = screen.getByRole('searchbox', { name: 'Search saved words' })
      const sortControl = screen.getByRole('combobox', { name: 'Sort saved words' })
      expect(screen.getByText('Search').closest('label')).toContainElement(search)
      expect(screen.getByText('Sort').closest('label')).toContainElement(sortControl)

      fireEvent.change(search, {
        target: { value: 'écou' },
      })
      expect(screen.getByRole('searchbox')).toHaveValue('écou')
      await act(async () => vi.advanceTimersByTimeAsync(299))
      expect(vocabMocks.list).toHaveBeenCalledTimes(1)
      await act(async () => vi.advanceTimersByTimeAsync(1))
      expect(vocabMocks.list).toHaveBeenLastCalledWith({
        language: 'fr',
        q: 'écou',
        sort: 'recent',
        limit: 50,
      })

      fireEvent.change(sortControl, {
        target: { value: 'alphabetical' },
      })
      await act(async () => Promise.resolve())
      expect(vocabMocks.list).toHaveBeenLastCalledWith({
        language: 'fr',
        q: 'écou',
        sort: 'alphabetical',
        limit: 50,
      })
    } finally {
      vi.useRealTimers()
    }
  })

  it('appends next pages and retries a failed load more without losing its cursor', async () => {
    vocabMocks.list
      .mockResolvedValueOnce(page([word()], 'next-page'))
      .mockRejectedValueOnce(new Error('More unavailable'))
      .mockResolvedValueOnce(
        page([word({ id: 8, headword: 'parler', normalized_headword: 'parler' })]),
      )
    renderPage()
    await screen.findByText('écouter')

    await userEvent.click(screen.getByRole('button', { name: 'Load more' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('More unavailable')
    expect(screen.getByText('écouter')).toBeInTheDocument()
    expect(
      screen.queryByText('Showing previous results while this view refreshes'),
    ).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Delete' })).toBeEnabled()

    await userEvent.click(screen.getByRole('button', { name: 'Retry loading more' }))
    expect(await screen.findByText('parler')).toBeInTheDocument()
    expect(screen.getByText('écouter')).toBeInTheDocument()
    expect(vocabMocks.list).toHaveBeenLastCalledWith({
      language: 'fr',
      sort: 'recent',
      limit: 50,
      cursor: 'next-page',
    })
  })

  it('invalidates a paginating cursor in the first sort commit and ignores its response', async () => {
    const oldPage = deferred<VocabList>()
    const newFirstPage = deferred<VocabList>()
    vocabMocks.list
      .mockResolvedValueOnce(page([word()], 'old-cursor'))
      .mockReturnValueOnce(oldPage.promise)
      .mockReturnValueOnce(newFirstPage.promise)
    const sortCommits: boolean[] = []

    render(
      <Profiler
        id="sort-race"
        onRender={() => {
          const select = document.querySelector<HTMLSelectElement>(
            'select[aria-label="Sort saved words"]',
          )
          if (select?.value === 'alphabetical') {
            sortCommits.push(
              Boolean(screen.queryByRole('button', { name: /Load(?:ing)? more/ })),
            )
          }
        }}
      >
        <VocabularyPage language="fr" navigate={vi.fn()} />
      </Profiler>,
    )
    await screen.findByText('écouter')
    await userEvent.click(screen.getByRole('button', { name: 'Load more' }))

    fireEvent.change(screen.getByRole('combobox', { name: 'Sort saved words' }), {
      target: { value: 'alphabetical' },
    })
    expect(sortCommits[0]).toBe(false)

    oldPage.resolve(page([word({ id: 8, headword: 'stale sort word' })]))
    await act(async () => oldPage.promise)
    expect(screen.queryByText('stale sort word')).not.toBeInTheDocument()

    newFirstPage.resolve(page([word({ id: 9, headword: 'sorted word' })]))
    expect(await screen.findByText('sorted word')).toBeInTheDocument()
  })

  it('invalidates a paginating cursor in the first language commit and ignores its response', async () => {
    const oldPage = deferred<VocabList>()
    const newFirstPage = deferred<VocabList>()
    vocabMocks.list
      .mockResolvedValueOnce(page([word()], 'old-cursor'))
      .mockReturnValueOnce(oldPage.promise)
      .mockReturnValueOnce(newFirstPage.promise)
    const languageCommits: boolean[] = []
    let observeRussian = false
    const onRender = () => {
      if (observeRussian) {
        languageCommits.push(
          Boolean(screen.queryByRole('button', { name: /Load(?:ing)? more/ })),
        )
      }
    }
    const view = render(
      <Profiler id="language-race" onRender={onRender}>
        <VocabularyPage language="fr" navigate={vi.fn()} />
      </Profiler>,
    )
    await screen.findByText('écouter')
    await userEvent.click(screen.getByRole('button', { name: 'Load more' }))

    observeRussian = true
    view.rerender(
      <Profiler id="language-race" onRender={onRender}>
        <VocabularyPage language="ru" navigate={vi.fn()} />
      </Profiler>,
    )
    expect(languageCommits[0]).toBe(false)

    oldPage.resolve(page([word({ id: 8, headword: 'stale language word' })]))
    await act(async () => oldPage.promise)
    expect(screen.queryByText('stale language word')).not.toBeInTheDocument()

    newFirstPage.resolve(
      page([word({ id: 9, language: 'ru', headword: 'слово', normalized_headword: 'слово' })]),
    )
    expect(await screen.findByText('слово')).toBeInTheDocument()
  })

  it('clears the old cursor immediately and replaces rather than appends on a new query', async () => {
    vi.useFakeTimers()
    try {
      const search = deferred<VocabList>()
      vocabMocks.list
        .mockResolvedValueOnce(page([word()], 'old-cursor'))
        .mockReturnValueOnce(search.promise)
      renderPage()
      await act(async () => Promise.resolve())
      expect(screen.getByText('écouter')).toBeInTheDocument()

      fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'par' } })
      expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
      await act(async () => vi.advanceTimersByTimeAsync(300))
      expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()

      search.resolve(page([word({ id: 8, headword: 'parler', normalized_headword: 'parler' })]))
      await act(async () => search.promise)
      expect(screen.getByText('parler')).toBeInTheDocument()
      expect(screen.queryByText('écouter')).not.toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it('marks retained rows stale as soon as the visible search changes', async () => {
    vi.useFakeTimers()
    try {
      const search = deferred<VocabList>()
      vocabMocks.list
        .mockResolvedValueOnce(page([word()]))
        .mockReturnValueOnce(search.promise)
      renderPage()
      await act(async () => Promise.resolve())
      expect(screen.getByText('écouter')).toBeInTheDocument()

      await armMode('Delete')
      fireEvent.click(screen.getByRole('button', { name: 'Delete écouter' }))
      expect(screen.getByRole('alertdialog', { name: /Delete écouter/i })).toBeInTheDocument()

      fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'par' } })
      expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
      expect(
        screen.getByRole('status', {
          name: 'Showing previous results while this view refreshes',
        }),
      ).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Edit' })).toBeDisabled()
      expect(screen.getByRole('button', { name: 'Delete' })).toBeDisabled()

      await act(async () => vi.advanceTimersByTimeAsync(300))
      search.resolve(page([word({ id: 8, headword: 'parler', normalized_headword: 'parler' })]))
      await act(async () => search.promise)
      expect(screen.getByText('parler')).toBeInTheDocument()
      expect(
        screen.queryByText('Showing previous results while this view refreshes'),
      ).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Edit' })).toBeEnabled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('lets a pending initial request complete when raw search is trim-equivalent', async () => {
    vi.useFakeTimers()
    try {
      const initial = deferred<VocabList>()
      vocabMocks.list.mockReturnValue(initial.promise)
      renderPage()
      expect(vocabMocks.list).toHaveBeenCalledOnce()

      fireEvent.change(screen.getByRole('searchbox'), { target: { value: '   ' } })
      await act(async () => vi.advanceTimersByTimeAsync(300))
      initial.resolve(page())
      await act(async () => initial.promise)

      expect(vocabMocks.list).toHaveBeenCalledOnce()
      expect(screen.getByText('No saved words yet')).toBeInTheDocument()
      expect(screen.queryByText('Loading saved words')).not.toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it('preserves an equivalent cursor and restores it when raw search is undone', async () => {
    vi.useFakeTimers()
    try {
      vocabMocks.list.mockResolvedValue(page([word()], 'stable-cursor'))
      renderPage()
      await act(async () => Promise.resolve())
      expect(screen.getByRole('button', { name: 'Load more' })).toBeInTheDocument()

      fireEvent.change(screen.getByRole('searchbox'), { target: { value: '   ' } })
      expect(screen.getByRole('button', { name: 'Load more' })).toBeInTheDocument()

      fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'different' } })
      expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Edit' })).toBeDisabled()

      await act(async () => vi.advanceTimersByTimeAsync(100))
      fireEvent.change(screen.getByRole('searchbox'), { target: { value: '' } })
      expect(screen.getByRole('button', { name: 'Load more' })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Edit' })).toBeEnabled()

      await act(async () => vi.advanceTimersByTimeAsync(300))
      expect(vocabMocks.list).toHaveBeenCalledOnce()
    } finally {
      vi.useRealTimers()
    }
  })

  it('ignores a slower stale search response', async () => {
    vi.useFakeTimers()
    try {
      const oldSearch = deferred<VocabList>()
      const newSearch = deferred<VocabList>()
      vocabMocks.list
        .mockResolvedValueOnce(page())
        .mockReturnValueOnce(oldSearch.promise)
        .mockReturnValueOnce(newSearch.promise)
      renderPage()
      await act(async () => Promise.resolve())

      fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'old' } })
      await act(async () => vi.advanceTimersByTimeAsync(300))
      fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'new' } })
      await act(async () => vi.advanceTimersByTimeAsync(300))

      newSearch.resolve(page([word({ headword: 'new result' })]))
      await act(async () => newSearch.promise)
      oldSearch.resolve(page([word({ headword: 'stale result' })]))
      await act(async () => oldSearch.promise)
      expect(screen.getByText('new result')).toBeInTheDocument()
      expect(screen.queryByText('stale result')).not.toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps successful rows visible when a refresh fails and retries contextually', async () => {
    vocabMocks.list
      .mockResolvedValueOnce(page([word()]))
      .mockRejectedValueOnce(new Error('Refresh unavailable'))
      .mockResolvedValueOnce(page([word({ gloss_en: 'hear carefully' })]))
    renderPage()
    await screen.findByText('écouter')

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'alphabetical' } })
    expect(await screen.findByRole('alert')).toHaveTextContent('Refresh unavailable')
    expect(screen.getByText('écouter')).toBeInTheDocument()
    expect(
      screen.getByRole('status', {
        name: 'Showing previous results while this view refreshes',
      }),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Delete' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('hear carefully')).toBeInTheDocument()
    expect(
      screen.queryByText('Showing previous results while this view refreshes'),
    ).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Delete' })).toBeEnabled()
  })

  it('labels retained rows stale after a language failure and restores actions after retry', async () => {
    vocabMocks.list
      .mockResolvedValueOnce(page([word()]))
      .mockRejectedValueOnce(new Error('Russian words unavailable'))
      .mockResolvedValueOnce(
        page([
          word({
            id: 8,
            language: 'ru',
            headword: 'слушать',
            normalized_headword: 'слушать',
          }),
        ]),
      )
    const view = renderPage('fr')
    await screen.findByText('écouter')

    view.rerender(<VocabularyPage language="ru" navigate={view.navigate} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Russian words unavailable')
    expect(screen.getByText('écouter')).toBeInTheDocument()
    expect(
      screen.getByRole('status', {
        name: 'Showing previous results while this view refreshes',
      }),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Delete' })).toBeDisabled()

    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('слушать')).toBeInTheDocument()
    expect(screen.queryByText('écouter')).not.toBeInTheDocument()
    expect(
      screen.queryByText('Showing previous results while this view refreshes'),
    ).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Delete' })).toBeEnabled()
  })

  it('automatically lists an exact criteria only once in Strict Mode', async () => {
    const initial = deferred<VocabList>()
    vocabMocks.list.mockReturnValue(initial.promise)

    render(
      <StrictMode>
        <VocabularyPage language="fr" navigate={vi.fn()} />
      </StrictMode>,
    )
    await act(async () => Promise.resolve())

    expect(vocabMocks.list).toHaveBeenCalledOnce()
    initial.resolve(page())
    expect(await screen.findByText('No saved words yet')).toBeInTheDocument()
  })

  it('updates an edited row only after success and preserves its draft on failure', async () => {
    const saving = deferred<VocabItem>()
    vocabMocks.list.mockResolvedValue(page([word()]))
    vocabMocks.edit.mockReturnValueOnce(saving.promise)
    renderPage()
    await screen.findByText('écouter')

    await armMode('Edit')
    await userEvent.click(screen.getByRole('button', { name: 'Edit écouter' }))
    const gloss = screen.getByRole('textbox', { name: 'Gloss' })
    expect(screen.getByText('Gloss').closest('label')).toContainElement(gloss)
    await userEvent.clear(gloss)
    await userEvent.type(gloss, 'hear carefully')
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(screen.getByRole('button', { name: 'Saving' })).toBeDisabled()
    expect(screen.queryByText('hear carefully')).not.toBeInTheDocument()

    saving.resolve(word({ gloss_en: 'hear carefully' }))
    expect(await screen.findByText('hear carefully')).toBeInTheDocument()

    vocabMocks.edit.mockRejectedValueOnce(new Error('Edit unavailable'))
    await armMode('Edit')
    await userEvent.click(screen.getByRole('button', { name: 'Edit écouter' }))
    const example = screen.getByRole('textbox', { name: 'Example' })
    expect(screen.getByText('Example').closest('label')).toContainElement(example)
    await userEvent.clear(example)
    await userEvent.type(example, 'Draft stays here')
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    const row = screen.getByRole('article', { name: 'écouter' })
    const editError = await within(row).findByRole('alert')
    expect(editError).toHaveTextContent('Edit unavailable')
    expect(editError).toHaveAttribute('aria-live', 'assertive')
    expect(within(row).getByRole('textbox', { name: 'Example' })).toHaveValue('Draft stays here')
  })

  it('opens an accessible permanent-delete confirmation and restores focus on cancel or Escape', async () => {
    vocabMocks.list.mockResolvedValue(page([word()]))
    renderPage()
    await screen.findByText('écouter')

    await armMode('Delete')
    // Re-queried rather than held, at every step. The row's click target unmounts while the
    // confirmation is open — the row is no longer armed — so a reference captured beforehand is a
    // detached node afterwards, and asserting focus on it would fail even though focus was restored.
    const pick = () => screen.getByRole('button', { name: 'Delete écouter' })

    await userEvent.click(pick())
    expect(vocabMocks.remove).not.toHaveBeenCalled()
    const dialog = screen.getByRole('alertdialog', { name: /Delete écouter/i })
    expect(dialog).toHaveAccessibleDescription(/permanently/i)
    expect(screen.getByRole('button', { name: 'Confirm delete' })).toHaveFocus()

    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    // Cancelling puts focus back on the word, so a keyboard user is not dropped to the top of the
    // list and made to tab all the way back in.
    expect(pick()).toHaveFocus()

    await userEvent.click(pick())
    fireEvent.keyDown(screen.getByRole('alertdialog'), { key: 'Escape' })
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(pick()).toHaveFocus()
    expect(vocabMocks.remove).not.toHaveBeenCalled()
  })

  it('deletes only after confirmation and keeps a failed row in a retryable live dialog', async () => {
    const removing = deferred<void>()
    vocabMocks.list.mockResolvedValue(page([word()]))
    vocabMocks.remove.mockReturnValueOnce(removing.promise)
    renderPage()
    await screen.findByText('écouter')

    await armMode('Delete')
    await userEvent.click(screen.getByRole('button', { name: 'Delete écouter' }))
    expect(vocabMocks.remove).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Confirm delete' }))
    expect(screen.getByRole('button', { name: 'Deleting' })).toBeDisabled()
    expect(screen.getByText('écouter')).toBeInTheDocument()
    removing.resolve()
    await waitFor(() => expect(screen.queryByText('écouter')).not.toBeInTheDocument())

    vocabMocks.list.mockResolvedValueOnce(page([word({ id: 9, headword: 'rester' })]))
    vocabMocks.remove.mockRejectedValueOnce(new Error('Delete unavailable'))
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'alphabetical' } })
    await screen.findByText('rester')
    await armMode('Delete')
    await userEvent.click(screen.getByRole('button', { name: 'Delete rester' }))
    await userEvent.click(screen.getByRole('button', { name: 'Confirm delete' }))
    const deleteError = await screen.findByRole('alert')
    expect(deleteError).toHaveTextContent('Delete unavailable')
    expect(deleteError).toHaveAttribute('aria-live', 'assertive')
    expect(screen.getByRole('alertdialog')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Confirm delete' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeEnabled()
    expect(screen.getByText('rester')).toBeInTheDocument()
  })
})
