export type VocabularySort = 'recent' | 'alphabetical'

/**
 * What clicking a word does. `null` is the resting state, where the list is just a list.
 *
 * A mode rather than a pair of buttons on every row. With Edit and Delete repeated per word the
 * list was mostly controls: two buttons under each entry, so a screen of ten words carried twenty
 * of them and the words themselves were the smaller part of the row. Moving the choice up to the
 * toolbar means the list reads as vocabulary again, and the action is stated once.
 */
export type VocabularyMode = 'edit' | 'delete' | null

export function VocabularyToolbar({
  query,
  sort,
  mode,
  onQueryChange,
  onSortChange,
  onModeChange,
  mutationsDisabled,
}: {
  query: string
  sort: VocabularySort
  mode: VocabularyMode
  onQueryChange: (query: string) => void
  onSortChange: (sort: VocabularySort) => void
  onModeChange: (mode: VocabularyMode) => void
  mutationsDisabled?: boolean
}) {
  /** Clicking the active mode leaves it, so there is always a way back to just reading. */
  const toggle = (next: Exclude<VocabularyMode, null>) =>
    onModeChange(mode === next ? null : next)

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

      {/*
        Grouped and labelled, because these two are not commands — they arm the list. `aria-pressed`
        is what tells a screen-reader user that the button is a state they are now in rather than
        something that already happened, which is the whole difference between this and a toolbar of
        actions.
      */}
      <div className="wordbook-modes" role="group" aria-label="What clicking a word does">
        <button
          type="button"
          className={`wordbook-mode ${mode === 'edit' ? 'on' : ''}`}
          aria-pressed={mode === 'edit'}
          disabled={mutationsDisabled}
          onClick={() => toggle('edit')}
        >
          Edit
        </button>
        <button
          type="button"
          className={`wordbook-mode danger ${mode === 'delete' ? 'on' : ''}`}
          aria-pressed={mode === 'delete'}
          disabled={mutationsDisabled}
          onClick={() => toggle('delete')}
        >
          Delete
        </button>
      </div>

      {/*
        Says which mode is armed, in words, right where the next click will happen. A pressed button
        is easy to miss, and the cost of missing it is different for the two modes — which is why
        the delete wording names the consequence rather than the gesture.
      */}
      {mode && (
        <p className={`wordbook-modehint ${mode === 'delete' ? 'danger' : ''}`} role="status">
          {mode === 'edit'
            ? 'Pick a word to edit it.'
            : 'Pick a word to delete it — you will be asked to confirm.'}
        </p>
      )}
    </div>
  )
}
