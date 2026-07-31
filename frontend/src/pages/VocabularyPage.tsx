import { useCallback, useEffect, useRef, useState } from 'react'
import { VocabularyEmpty } from '../components/vocab/VocabularyEmpty'
import { VocabularyRow } from '../components/vocab/VocabularyRow'
import {
  VocabularyToolbar,
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
  const [items, setItems] = useState<VocabItem[]>([])
  const [cursor, setCursor] = useState<CursorState | null>(null)
  const [hasLoaded, setHasLoaded] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null)
  const requestId = useRef(0)
  const activeCriteria = `${language}\u0000${debouncedQuery}\u0000${sort}`
  const activeCriteriaRef = useRef(activeCriteria)
  activeCriteriaRef.current = activeCriteria

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
      if (requestId.current !== id || activeCriteriaRef.current !== criteria) return
      setItems(response.items)
      setCursor(response.next_cursor ? { value: response.next_cursor, criteria } : null)
      setHasLoaded(true)
    } catch (reason) {
      if (requestId.current !== id || activeCriteriaRef.current !== criteria) return
      setError(errorMessage(reason))
    } finally {
      if (requestId.current === id && activeCriteriaRef.current === criteria) setLoading(false)
    }
  }, [debouncedQuery, language, list, sort])

  useEffect(() => {
    void loadFirst()
  }, [loadFirst])

  const loadMore = async () => {
    if (!cursor || cursor.criteria !== activeCriteria || loadingMore) return
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
        requestId.current !== activeRequest ||
        activeCriteriaRef.current !== criteria
      ) return
      setItems((current) => [...current, ...response.items])
      setCursor(
        response.next_cursor ? { value: response.next_cursor, criteria } : null,
      )
    } catch (reason) {
      if (
        requestId.current !== activeRequest ||
        activeCriteriaRef.current !== criteria
      ) return
      setLoadMoreError(errorMessage(reason))
    } finally {
      if (
        requestId.current === activeRequest &&
        activeCriteriaRef.current === criteria
      ) setLoadingMore(false)
    }
  }

  const updateItem = async (id: number, input: VocabEditInput) => {
    const updated = await edit(id, input)
    setItems((current) => current.map((item) => (item.id === id ? updated : item)))
  }

  const deleteItem = async (item: VocabItem) => {
    await remove(item)
    setItems((current) => current.filter((candidate) => candidate.id !== item.id))
  }

  const hasItems = items.length > 0

  const changeQuery = (nextQuery: string) => {
    requestId.current += 1
    setCursor(null)
    setLoadMoreError(null)
    setLoadingMore(false)
    setQuery(nextQuery)
  }

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
        onQueryChange={changeQuery}
        onSortChange={setSort}
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

      {hasItems && (
        <div className="wordbook-list">
          {items.map((item) => (
            <VocabularyRow
              key={item.id}
              item={item}
              navigate={navigate}
              onEdit={updateItem}
              onDelete={deleteItem}
            />
          ))}
        </div>
      )}

      {hasLoaded && !loading && !error && !hasItems && (
        <VocabularyEmpty searching={Boolean(debouncedQuery)} />
      )}

      {loadMoreError && (
        <div className="wordbook-error wordbook-more-error" role="alert">
          <span>{loadMoreError}</span>
          <button type="button" onClick={() => void loadMore()}>
            Retry loading more
          </button>
        </div>
      )}

      {cursor?.criteria === activeCriteria && !loading && (
        <div className="wordbook-more">
          <button type="button" onClick={() => void loadMore()} disabled={loadingMore}>
            {loadingMore ? 'Loading more' : 'Load more'}
          </button>
        </div>
      )}
    </main>
  )
}
