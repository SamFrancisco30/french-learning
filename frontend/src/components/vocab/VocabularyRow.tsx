import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import type { VocabEditInput, VocabItem } from '../../types'

function message(reason: unknown): string {
  return reason instanceof Error ? reason.message : 'Something went wrong'
}

function savedDate(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }).format(parsed)
}

export function VocabularyRow({
  item,
  navigate,
  onEdit,
  onDelete,
  mutationsDisabled,
}: {
  item: VocabItem
  navigate: (to: string) => void
  onEdit: (id: number, input: VocabEditInput) => Promise<void>
  onDelete: (item: VocabItem) => Promise<void>
  mutationsDisabled?: boolean
}) {
  const [editing, setEditing] = useState(false)
  const [gloss, setGloss] = useState(item.gloss_en ?? '')
  const [example, setExample] = useState(item.example ?? '')
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const deleteButtonRef = useRef<HTMLButtonElement | null>(null)
  const confirmButtonRef = useRef<HTMLButtonElement | null>(null)
  const returnFocus = useRef(false)
  const confirmTitleId = useId()
  const confirmDescriptionId = useId()

  useEffect(() => {
    if (editing) return
    setGloss(item.gloss_en ?? '')
    setExample(item.example ?? '')
  }, [editing, item.example, item.gloss_en])

  useEffect(() => {
    if (confirming) {
      confirmButtonRef.current?.focus()
    } else if (returnFocus.current) {
      returnFocus.current = false
      deleteButtonRef.current?.focus()
    }
  }, [confirming])

  useEffect(() => {
    if (!mutationsDisabled || !confirming) return
    returnFocus.current = false
    setConfirming(false)
    setError(null)
  }, [confirming, mutationsDisabled])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (saving || mutationsDisabled) return
    setSaving(true)
    setError(null)
    try {
      await onEdit(item.id, {
        gloss_en: gloss.trim() || null,
        example: example.trim() || null,
      })
      setEditing(false)
    } catch (reason) {
      setError(message(reason))
    } finally {
      setSaving(false)
    }
  }

  const deleteItem = async () => {
    if (deleting || mutationsDisabled) return
    setDeleting(true)
    setError(null)
    try {
      await onDelete(item)
    } catch (reason) {
      setError(message(reason))
      setDeleting(false)
    }
  }

  const cancelDelete = () => {
    if (deleting) return
    returnFocus.current = true
    setConfirming(false)
    setError(null)
  }

  return (
    <article className="wordbook-row" aria-label={item.headword}>
      <div className="wordbook-word">
        <h3 lang={item.language}>{item.headword}</h3>
        <span>Saved {savedDate(item.created_at)}</span>
      </div>

      {editing ? (
        <form className="wordbook-edit" onSubmit={submit}>
          <label>
            <span>Gloss</span>
            <input
              aria-label="Gloss"
              value={gloss}
              onChange={(event) => setGloss(event.target.value)}
              disabled={saving || mutationsDisabled}
            />
          </label>
          <label>
            <span>Example</span>
            <textarea
              aria-label="Example"
              value={example}
              onChange={(event) => setExample(event.target.value)}
              rows={2}
              disabled={saving || mutationsDisabled}
            />
          </label>
          {error && (
            <p className="wordbook-row-error" role="alert" aria-live="assertive">
              {error}
            </p>
          )}
          <div className="wordbook-row-actions">
            <button
              className="wordbook-primary"
              type="submit"
              disabled={saving || mutationsDisabled}
            >
              {saving ? 'Saving' : 'Save changes'}
            </button>
            <button
              type="button"
              disabled={saving}
              onClick={() => {
                setGloss(item.gloss_en ?? '')
                setExample(item.example ?? '')
                setError(null)
                setEditing(false)
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <div className="wordbook-details">
          <p className="wordbook-gloss">{item.gloss_en || 'No gloss added'}</p>
          {item.example && (
            <p className="wordbook-example" lang={item.language}>
              {item.example}
            </p>
          )}
          {item.source && (
            <a
              className="wordbook-source"
              href={`#/listening/lesson/${item.source.lesson_id}/unit/${item.source.unit_id}`}
              onClick={(event) => {
                event.preventDefault()
                navigate(
                  `/listening/lesson/${item.source!.lesson_id}/unit/${item.source!.unit_id}`,
                )
              }}
            >
              {item.source.lesson_title} · Unit {item.source.unit_index}
            </a>
          )}
          {error && !confirming && (
            <p className="wordbook-row-error" role="alert" aria-live="assertive">
              {error}
            </p>
          )}
          <div className="wordbook-row-actions">
            <button
              type="button"
              aria-label={`Edit ${item.headword}`}
              onClick={() => {
                setError(null)
                setEditing(true)
              }}
              disabled={mutationsDisabled}
            >
              Edit
            </button>
            <button
              ref={deleteButtonRef}
              className="wordbook-delete"
              type="button"
              aria-label={`Delete ${item.headword}`}
              onClick={() => {
                setError(null)
                setConfirming(true)
              }}
              disabled={deleting || confirming || mutationsDisabled}
            >
              Delete
            </button>
          </div>
          {confirming && (
            <div
              className="wordbook-confirm"
              role="alertdialog"
              aria-labelledby={confirmTitleId}
              aria-describedby={confirmDescriptionId}
              onKeyDown={(event) => {
                if (event.key !== 'Escape' || deleting) return
                event.preventDefault()
                cancelDelete()
              }}
            >
              <h4 id={confirmTitleId}>Delete {item.headword}?</h4>
              <p id={confirmDescriptionId}>
                This permanently removes this word from My Words. This cannot be undone.
              </p>
              {error && (
                <p className="wordbook-row-error" role="alert" aria-live="assertive">
                  {error}
                </p>
              )}
              <div className="wordbook-row-actions wordbook-confirm-actions">
                <button
                  ref={confirmButtonRef}
                  className="wordbook-delete"
                  type="button"
                  onClick={() => void deleteItem()}
                  disabled={deleting || mutationsDisabled}
                >
                  {deleting ? 'Deleting' : 'Confirm delete'}
                </button>
                <button type="button" onClick={cancelDelete} disabled={deleting}>
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </article>
  )
}
