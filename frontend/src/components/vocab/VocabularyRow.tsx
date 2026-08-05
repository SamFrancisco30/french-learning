import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import type { VocabEditInput, VocabItem } from '../../types'
import type { VocabularyMode } from './VocabularyToolbar'

function message(reason: unknown): string {
  return reason instanceof Error ? reason.message : 'Something went wrong'
}

/**
 * One saved word: the word, and what it means.
 *
 * Deliberately just those two. The row used to carry the save date, the example sentence, the lesson
 * it came from and two buttons — five things around one word, which made a list of ten words a page
 * of scrolling. The date in particular answered a question nobody asks: "recently saved" is already
 * a sort option, so the ordering says what the date said, without a line per row to say it.
 *
 * Neither the example nor the source is lost. The source moves onto the headword, which is now the
 * link back to its lesson, so the way back costs no extra line. The example appears when the row is
 * opened for editing, which is where a learner is looking at one word rather than scanning many.
 */
export function VocabularyRow({
  item,
  navigate,
  mode,
  onEdit,
  onDelete,
  mutationsDisabled,
}: {
  item: VocabItem
  navigate: (to: string) => void
  /** What a click on this row does. Null leaves the row as plain text. */
  mode: VocabularyMode
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
  const confirmButtonRef = useRef<HTMLButtonElement | null>(null)
  const pickRef = useRef<HTMLButtonElement | null>(null)
  // Set when a confirmation was dismissed rather than acted on, so focus can go back where the
  // learner left it. Without this, cancelling drops focus to the document and a keyboard user has
  // to tab in from the top of the list again.
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
      // The pick button is re-created when the row re-arms, so this runs after that render.
      pickRef.current?.focus()
    }
  }, [confirming])

  // Leaving a mode closes whatever it opened, so switching from Delete to Edit cannot leave a
  // confirmation hanging over a row the learner is no longer pointing at.
  useEffect(() => {
    if (mode !== 'delete' && confirming) {
      setConfirming(false)
      setError(null)
    }
    if (mode !== 'edit' && editing) {
      setEditing(false)
      setError(null)
    }
  }, [mode, confirming, editing])

  useEffect(() => {
    if (!mutationsDisabled || !confirming) return
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

  const pick = () => {
    if (mutationsDisabled) return
    setError(null)
    if (mode === 'edit') setEditing(true)
    else if (mode === 'delete') setConfirming(true)
  }

  const armed = mode !== null && !editing && !confirming && !mutationsDisabled
  // The list is two columns wide, and an open row does not fit in half of it: editing adds two
  // fields and a pair of buttons, confirming adds a dialog. Both span the full width instead — see
  // .wordbook-row.expanded.
  const expanded = editing || confirming

  return (
    <article
      className={`wordbook-row ${armed ? `armed ${mode}` : ''} ${expanded ? 'expanded' : ''}`}
      aria-label={item.headword}
    >
      {/*
        The click target is a real button covering the row, not an onClick on the article.

        It has to be a button: a div that responds to a click is invisible to a keyboard and to a
        screen reader, and this is the only way to reach either action now that the per-row buttons
        are gone. Its label carries the verb, so "Edit en effet" is what gets announced rather than
        the bare headword — the same row means two different things depending on the armed mode, and
        the label is the only place that difference can be stated.
      */}
      {armed && (
        <button
          ref={pickRef}
          type="button"
          className="wordbook-pick"
          aria-label={`${mode === 'edit' ? 'Edit' : 'Delete'} ${item.headword}`}
          onClick={pick}
        />
      )}

      <div className="wordbook-word">
        {/*
          The headword is the link to where it came from, so the row can be two columns — word and
          meaning — without losing the way back to the lesson. A separate source line under the gloss
          is what made the right column three things deep; putting it on the word costs no space.

          Only when nothing is armed: with a mode active the pick button covers the row, and a link
          underneath it would be both unreachable and a second meaning for the same click.
        */}
        {item.source && !armed ? (
          <a
            className="wordbook-headlink"
            lang={item.language}
            href={`#/listening/lesson/${item.source.lesson_id}/unit/${item.source.unit_id}`}
            /*
              Both, explicitly. Left to the default computation the name came out as the `title` —
              the whole lesson title — so a screen reader announced a sentence of provenance where
              the word should have been. Naming it here puts the word first and the destination
              second, which is the order they matter in, and `title` still serves the hover.
            */
            aria-label={`${item.headword} — open ${item.source.lesson_title}, Unit ${item.source.unit_index}`}
            title={`${item.source.lesson_title} · Unit ${item.source.unit_index}`}
            onClick={(event) => {
              event.preventDefault()
              navigate(
                `/listening/lesson/${item.source!.lesson_id}/unit/${item.source!.unit_id}`,
              )
            }}
          >
            <h3 lang={item.language}>{item.headword}</h3>
          </a>
        ) : (
          <h3 lang={item.language}>{item.headword}</h3>
        )}
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

          {/* Where the word came from. Kept here rather than on every row: it is reference material,
              wanted when you are looking at one word, not when scanning a list. */}
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

          {error && !confirming && (
            <p className="wordbook-row-error" role="alert" aria-live="assertive">
              {error}
            </p>
          )}

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
