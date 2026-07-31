export type VocabularySort = 'recent' | 'alphabetical'

export function VocabularyToolbar({
  query,
  sort,
  onQueryChange,
  onSortChange,
}: {
  query: string
  sort: VocabularySort
  onQueryChange: (query: string) => void
  onSortChange: (sort: VocabularySort) => void
}) {
  return (
    <div className="wordbook-toolbar">
      <label>
        <span>Search</span>
        <input
          type="search"
          aria-label="Search saved words"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="Search headwords or meanings"
        />
      </label>
      <label>
        <span>Sort</span>
        <select
          aria-label="Sort saved words"
          value={sort}
          onChange={(event) => onSortChange(event.target.value as VocabularySort)}
        >
          <option value="recent">Recently saved</option>
          <option value="alphabetical">Alphabetical</option>
        </select>
      </label>
    </div>
  )
}
