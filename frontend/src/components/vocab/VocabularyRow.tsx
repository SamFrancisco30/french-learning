import { useEffect, useState, type FormEvent } from 'react'
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
}: {
  item: VocabItem
  navigate: (to: string) => void
  onEdit: (id: number, input: VocabEditInput) => Promise<void>
  onDelete: (item: VocabItem) => Promise<void>
}) {
  const [editing, setEditing] = useState(false)
  const [gloss, setGloss] = useState(item.gloss_en ?? '')
  const [example, setExample] = useState(item.example ?? '')
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (editing) return
    setGloss(item.gloss_en ?? '')
    setExample(item.example ?? '')
  }, [editing, item.example, item.gloss_en])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (saving) return
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
    if (deleting || !window.confirm(`Delete “${item.headword}” from My Words?`)) return
    setDeleting(true)
    setError(null)
    try {
      await onDelete(item)
    } catch (reason) {
      setError(message(reason))
      setDeleting(false)
    }
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
              disabled={saving}
            />
          </label>
          <label>
            <span>Example</span>
            <textarea
              aria-label="Example"
              value={example}
              onChange={(event) => setExample(event.target.value)}
              rows={2}
              disabled={saving}
            />
          </label>
          {error && (
            <p className="wordbook-row-error" role="alert">
              {error}
            </p>
          )}
          <div className="wordbook-row-actions">
            <button className="wordbook-primary" type="submit" disabled={saving}>
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
          {error && (
            <p className="wordbook-row-error" role="alert">
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
            >
              Edit
            </button>
            <button
              className="wordbook-delete"
              type="button"
              aria-label={`Delete ${item.headword}`}
              onClick={deleteItem}
              disabled={deleting}
            >
              {deleting ? 'Deleting' : 'Delete'}
            </button>
          </div>
        </div>
      )}
    </article>
  )
}
