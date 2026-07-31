import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api, vocab } from './api'
import type { VocabEditInput, VocabSaveInput } from './types'

const fetchMock = vi.fn()

function response(body: unknown = {}, init: { ok?: boolean; status?: number; statusText?: string } = {}) {
  return {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    statusText: init.statusText ?? 'OK',
    json: vi.fn().mockResolvedValue(body),
  }
}

function requestAt(index = 0): [string, RequestInit | undefined] {
  return fetchMock.mock.calls[index] as [string, RequestInit | undefined]
}

describe('API request boundaries', () => {
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })

  it('keeps attempt identity in JSON and URL-encodes progress identity', async () => {
    fetchMock.mockResolvedValue(response())

    await api.submit(3, { answer: 'oui' }, 2, 'learner_a&admin=true')
    await api.progress('learner_a&admin=true')

    expect(JSON.parse(String(requestAt(0)[1]?.body))).toEqual({
      exercise_id: 3,
      response: { answer: 'oui' },
      replays: 2,
      learner_key: 'learner_a&admin=true',
    })
    expect(requestAt(1)[0]).toBe('/api/progress?learner_key=learner_a%26admin%3Dtrue')
  })

  it('encodes every vocabulary list parameter independently', async () => {
    fetchMock.mockResolvedValue(response({ items: [], next_cursor: null, total: 0 }))

    await vocab.list(
      {
        language: 'fr/CA & test',
        q: 'été & café=oui',
        sort: 'alphabetical',
        limit: 17,
        cursor: 'opaque+/=&? é',
      },
      { 'X-Learner-Key': 'learner_only-header' },
    )

    const [url] = requestAt()
    const parsed = new URL(url, 'https://example.test')
    expect(parsed.pathname).toBe('/api/vocab')
    expect(Object.fromEntries(parsed.searchParams)).toEqual({
      language: 'fr/CA & test',
      q: 'été & café=oui',
      sort: 'alphabetical',
      limit: '17',
      cursor: 'opaque+/=&? é',
    })
    expect(url).not.toContain('learner_key')
    expect(url).not.toContain('user_id')
  })

  it('omits absent filters and treats an empty q as no filter', async () => {
    fetchMock.mockResolvedValue(response({ items: [], next_cursor: null, total: 0 }))

    await vocab.list(
      { language: null, q: '', cursor: undefined, sort: 'recent', limit: 50 },
      { 'X-Learner-Key': 'learner_header' },
    )

    expect(requestAt()[0]).toBe('/api/vocab?sort=recent&limit=50')
  })

  it('sends vocabulary identity only in the header and exact save/edit bodies', async () => {
    fetchMock.mockResolvedValue(response())
    const saveInput: VocabSaveInput = {
      language: 'fr',
      headword: 'écouter',
      gloss_en: null,
      example: 'J’écoute.',
      unit_id: 9,
    }
    const editInput: VocabEditInput = { gloss_en: 'listen', example: null }
    const headers = { 'X-Learner-Key': 'learner_header-only' }

    await vocab.save(saveInput, headers)
    await vocab.edit(42, editInput, headers)
    await vocab.savedKeys('fr&x=y', headers)

    expect(JSON.parse(String(requestAt(0)[1]?.body))).toEqual(saveInput)
    expect(JSON.parse(String(requestAt(1)[1]?.body))).toEqual(editInput)
    for (const index of [0, 1, 2]) {
      const [url, init] = requestAt(index)
      const requestHeaders = new Headers(init?.headers)
      expect(requestHeaders.get('X-Learner-Key')).toBe('learner_header-only')
      expect(`${url}${String(init?.body ?? '')}`).not.toContain('learner_key')
      expect(`${url}${String(init?.body ?? '')}`).not.toContain('user_id')
    }
    expect(requestAt(2)[0]).toBe('/api/vocab/saved-keys?language=fr%26x%3Dy')
  })

  it('merges HeadersInit forms with JSON content type without overwriting callers', async () => {
    fetchMock.mockResolvedValue(response())

    await vocab.save(
      { language: 'fr', headword: 'mot' },
      new Headers([['X-Learner-Key', 'learner_headers']]),
    )
    await vocab.edit(
      1,
      { example: null },
      [
        ['X-Learner-Key', 'learner_tuple'],
        ['Content-Type', 'application/custom+json'],
      ],
    )

    const first = new Headers(requestAt(0)[1]?.headers)
    expect(first.get('X-Learner-Key')).toBe('learner_headers')
    expect(first.get('Content-Type')).toBe('application/json')
    const second = new Headers(requestAt(1)[1]?.headers)
    expect(second.get('X-Learner-Key')).toBe('learner_tuple')
    expect(second.get('Content-Type')).toBe('application/custom+json')
  })

  it('resolves DELETE 204 without parsing JSON', async () => {
    const noContent = response(undefined, { status: 204, statusText: 'No Content' })
    fetchMock.mockResolvedValue(noContent)

    await expect(
      vocab.remove(42, { 'X-Learner-Key': 'learner_delete' }),
    ).resolves.toBeUndefined()

    expect(requestAt()[0]).toBe('/api/vocab/42')
    expect(requestAt()[1]?.method).toBe('DELETE')
    expect(noContent.json).not.toHaveBeenCalled()
  })

  it('rejects non-success responses with status and pathname but not response secrets', async () => {
    const failed = response({ detail: 'private database secret' }, {
      ok: false,
      status: 503,
      statusText: 'Unavailable',
    })
    fetchMock.mockResolvedValue(failed)

    await expect(
      vocab.remove(42, { 'X-Learner-Key': 'learner_delete' }),
    ).rejects.toThrow('503 Unavailable — /api/vocab/42')
    expect(failed.json).not.toHaveBeenCalled()
  })

  it('redacts query parameters and learner identity from request errors', async () => {
    const failed = response({ detail: 'private response body' }, {
      ok: false,
      status: 503,
      statusText: 'Unavailable',
    })
    fetchMock.mockResolvedValue(failed)

    const error = await api.progress('learner_secret&admin=true').catch((reason: unknown) => reason)
    expect(error).toBeInstanceOf(Error)
    const message = error instanceof Error ? error.message : String(error)
    expect(message).toBe('503 Unavailable — /api/progress')
    const [, init] = requestAt()
    expect(requestAt()[0]).toBe('/api/progress?learner_key=learner_secret%26admin%3Dtrue')
    expect(init).toBeUndefined()
    expect(message).not.toContain('learner_secret')
    expect(message).not.toContain('learner_key')
    expect(message).not.toContain('?')
    expect(message).not.toContain('private response body')
    expect(failed.json).not.toHaveBeenCalled()
  })

})
