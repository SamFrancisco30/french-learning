import { useCallback, useEffect, useRef, useState } from 'react'
import { VocabularyEmpty } from '../components/vocab/VocabularyEmpty'
import { VocabularyRow } from '../components/vocab/VocabularyRow'
import {
  VocabularyToolbar,
  type VocabularyMode,
  type VocabularySort,
} from '../components/vocab/VocabularyToolbar'
import type { VocabEditInput, VocabItem } from '../types'
import { useVocab } from '../vocab/VocabContext'

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : 'Unable to load saved words'
}

type CursorState = {
  value: string
  criteria: string
}

type CriteriaError = {
  message: string
  criteria: string
}

export function VocabularyPage({
  language,
  navigate,
}: {
  language: string
  navigate: (to: string) => void
}) {
  const { list, edit, remove } = useVocab()
  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [sort, setSort] = useState<VocabularySort>('recent')
  // What clicking a word does. Lives here rather than in the toolbar because every row needs it,
  // and it resets to null on a new search so a delete armed for one list cannot fire on another.
  const [mode, setMode] = useState<VocabularyMode>(null)
  const [items, setItems] = useState<VocabItem[]>([])
  const [itemsCriteria, setItemsCriteria] = useState<string | null>(null)
  const [cursor, setCursor] = useState<CursorState | null>(null)
  const [hasLoaded, setHasLoaded] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)
  const [loadMoreError, setLoadMoreError] = useState<CriteriaError | null>(null)
  const requestId = useRef(0)
  const mounted = useRef(false)
  const lastAutoCriteria = useRef<string | null>(null)
  const activeCriteria = `${language}\u0000${debouncedQuery}\u0000${sort}`
  const viewCriteria = `${language}\u0000${query.trim()}\u0000${sort}`
  const activeCriteriaRef = useRef(activeCriteria)
  activeCriteriaRef.current = activeCriteria

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query.trim()), 300)
    return () => window.clearTimeout(timer)
  }, [query])

  const loadFirst = useCallback(async () => {
    const id = ++requestId.current
    const criteria = activeCriteriaRef.current
    setCursor(null)
    setLoadMoreError(null)
    setLoadingMore(false)
    setLoading(true)
    setError(null)
    try {
      const response = await list({
        language,
        ...(debouncedQuery ? { q: debouncedQuery } : {}),
        sort,
        limit: 50,
      })
      if (
        !mounted.current ||
        requestId.current !== id ||
        activeCriteriaRef.current !== criteria
      ) return
      setItems(response.items)
      setItemsCriteria(criteria)
      setCursor(response.next_cursor ? { value: response.next_cursor, criteria } : null)
      setHasLoaded(true)
    } catch (reason) {
      if (
        !mounted.current ||
        requestId.current !== id ||
        activeCriteriaRef.current !== criteria
      ) return
      setError(errorMessage(reason))
    } finally {
      if (
        mounted.current &&
        requestId.current === id &&
        activeCriteriaRef.current === criteria
      ) setLoading(false)
    }
  }, [debouncedQuery, language, list, sort])

  useEffect(() => {
    if (lastAutoCriteria.current === activeCriteria) return
    lastAutoCriteria.current = activeCriteria
    void loadFirst()
  }, [activeCriteria, loadFirst])

  const loadMore = async () => {
    if (
      !cursor ||
      cursor.criteria !== activeCriteria ||
      activeCriteria !== viewCriteria ||
      loadingMore
    ) return
    const activeRequest = requestId.current
    const criteria = activeCriteria
    const requestedCursor = cursor.value
    setLoadingMore(true)
    setLoadMoreError(null)
    try {
      const response = await list({
        language,
        ...(debouncedQuery ? { q: debouncedQuery } : {}),
        sort,
        limit: 50,
        cursor: requestedCursor,
      })
      if (
        !mounted.current ||
        requestId.current !== activeRequest ||
        activeCriteriaRef.current !== criteria
      ) return
      setItems((current) => [...current, ...response.items])
      setCursor(
        response.next_cursor ? { value: response.next_cursor, criteria } : null,
      )
    } catch (reason) {
      if (
        !mounted.current ||
        requestId.current !== activeRequest ||
        activeCriteriaRef.current !== criteria
      ) return
      setLoadMoreError({ message: errorMessage(reason), criteria })
    } finally {
      if (
        mounted.current &&
        requestId.current === activeRequest &&
        activeCriteriaRef.current === criteria
      ) setLoadingMore(false)
    }
  }

  const updateItem = async (id: number, input: VocabEditInput) => {
    const updated = await edit(id, input)
    if (!mounted.current) return
    setItems((current) => current.map((item) => (item.id === id ? updated : item)))
  }

  const deleteItem = async (item: VocabItem) => {
    await remove(item)
    if (!mounted.current) return
    setItems((current) => current.filter((candidate) => candidate.id !== item.id))
  }

  const hasItems = items.length > 0
  const isStale = hasItems && itemsCriteria !== viewCriteria

  return (
    <main className="wordbook-page">
      <div className="pagehead wordbook-pagehead">
        <div>
          <h2>My Words</h2>
          <p>Search and refine the words you saved while learning.</p>
        </div>
        {hasLoaded && <span className="wordbook-count">{items.length} shown</span>}
      </div>

      <VocabularyToolbar
        query={query}
        sort={sort}
        mode={mode}
        onQueryChange={(next) => {
          // Disarm on a new search. The list about to arrive is a different set of words, and a
          // delete aimed at the old one would land on whatever now occupies that position.
          setMode(null)
          setQuery(next)
        }}
        onSortChange={(next) => {
          setMode(null)
          setSort(next)
        }}
        onModeChange={setMode}
        mutationsDisabled={isStale}
      />

      {loading && !hasLoaded && (
        <div className="wordbook-state" role="status">
          Loading saved words…
        </div>
      )}

      {loading && hasLoaded && (
        <div className="wordbook-refresh" role="status">
          Refreshing saved words…
        </div>
      )}

      {error && (
        <div className="wordbook-error" role="alert">
          <span>{error}</span>
          <button type="button" onClick={() => void loadFirst()}>
            Retry
          </button>
        </div>
      )}

      {isStale && (
        <div
          className="wordbook-stale"
          role="status"
          aria-label="Showing previous results while this view refreshes"
        >
          Showing previous results while this view refreshes
        </div>
      )}

      {hasItems && (
        <div className="wordbook-list">
          {items.map((item) => (
            <VocabularyRow
              key={item.id}
              item={item}
              navigate={navigate}
              mode={mode}
              onEdit={updateItem}
              onDelete={deleteItem}
              mutationsDisabled={isStale}
            />
          ))}
        </div>
      )}

      {hasLoaded && !loading && !error && !hasItems && (
        <VocabularyEmpty searching={Boolean(debouncedQuery)} />
      )}

      {loadMoreError?.criteria === activeCriteria && activeCriteria === viewCriteria && (
        <div className="wordbook-error wordbook-more-error" role="alert">
          <span>{loadMoreError.message}</span>
          <button type="button" onClick={() => void loadMore()}>
            Retry loading more
          </button>
        </div>
      )}

      {cursor?.criteria === activeCriteria && activeCriteria === viewCriteria && !loading && (
        <div className="wordbook-more">
          <button type="button" onClick={() => void loadMore()} disabled={loadingMore}>
            {loadingMore ? 'Loading more' : 'Load more'}
          </button>
        </div>
      )}
    </main>
  )
}
