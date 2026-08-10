import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { drill } from '../api'
import { LookupProvider, SelectableText } from '../components/Lookup'
import type { DrillProgress, DrillQuestion, DrillResult } from '../types'

/**
 * Drill mode: answer real TCF items, one at a time.
 *
 * Reading passages render as text, not as the image they were scraped from. The bank ships
 * every reading item as a PNG with the passage typeset into it — 2576 of them — and OCR
 * recovered the French. Serving the text is what makes the passage selectable, so the same
 * word-lookup and expression detection the reading page already has works here; an image
 * would be a dead rectangle. The image is still offered as "see the original", because the
 * layout carries meaning for the low-level items (a notice, a receipt, an SMS).
 *
 * Listening hides the transcript until the attempt is submitted. The transcript IS the
 * recording's content, so showing it alongside the audio answers the question — the server
 * flags this per item with `document_is_spoiler` rather than the client inferring it from
 * the skill.
 */

const SKILLS = [
  { key: 'reading', label: 'Reading', native: '阅读' },
  { key: 'listening', label: 'Listening', native: '听力' },
  { key: 'writing', label: 'Writing', native: '写作' },
  { key: 'speaking', label: 'Speaking', native: '口语' },
] as const

const LEVELS = ['A1', 'A2', 'B1', 'B2', 'C1', 'C2'] as const

type Phase = 'answering' | 'answered'

export function DrillPage({
  language,
  onAbout,
}: {
  language: string
  /** Writing and speaking have no grading yet; this opens the page that says so. */
  onAbout?: () => void
}) {
  const [skill, setSkill] = useState<string>('reading')
  const [level, setLevel] = useState<string | null>(null)
  const [question, setQuestion] = useState<DrillQuestion | null>(null)
  const [result, setResult] = useState<DrillResult | null>(null)
  const [phase, setPhase] = useState<Phase>('answering')
  const [selected, setSelected] = useState<string | null>(null)
  const [progress, setProgress] = useState<DrillProgress[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showOriginal, setShowOriginal] = useState(false)
  const [revealDocument, setRevealDocument] = useState(false)

  // Wall-clock on the item, sent with the attempt. A ref, not state: it is read once on
  // submit and re-rendering on every tick would restart the audio element.
  const startedAt = useRef<number>(Date.now())

  const isProduction = question?.kind === 'production'

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    setResult(null)
    setSelected(null)
    setPhase('answering')
    setShowOriginal(false)
    setRevealDocument(false)
    try {
      const next = await drill.next({ skill, level })
      setQuestion(next)
      startedAt.current = Date.now()
    } catch (e) {
      setQuestion(null)
      setError(
        e instanceof Error && e.message.includes('404')
          ? 'No items match that filter yet.'
          : e instanceof Error
            ? e.message
            : 'Could not load an item.',
      )
    } finally {
      setLoading(false)
    }
  }, [skill, level])

  useEffect(() => {
    void load()
  }, [load])

  const refreshProgress = useCallback(() => {
    drill
      .progress()
      .then(setProgress)
      .catch(() => {
        /* progress is a nicety; a failure here must not break the drill */
      })
  }, [])

  useEffect(() => {
    refreshProgress()
  }, [refreshProgress])

  async function submit(letter: string | null, response?: Record<string, unknown>) {
    if (!question || phase === 'answered') return
    setSelected(letter)
    try {
      const res = await drill.submit({
        question_id: question.id,
        selected: letter,
        elapsed_ms: Date.now() - startedAt.current,
        response,
      })
      setResult(res)
      setPhase('answered')
      setRevealDocument(true)
      refreshProgress()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not submit that answer.')
    }
  }

  const mine = useMemo(
    () => progress.filter((p) => p.skill === skill && (!level || p.level === level)),
    [progress, skill, level],
  )
  const totals = useMemo(
    () =>
      mine.reduce(
        (acc, p) => ({
          attempted: acc.attempted + p.attempted,
          correct: acc.correct + p.correct,
          graded: acc.graded + p.graded,
        }),
        { attempted: 0, correct: 0, graded: 0 },
      ),
    [mine],
  )

  // The transcript stays hidden while answering a listening item; reading shows it always.
  const documentHidden = Boolean(question?.document_is_spoiler) && !revealDocument

  return (
    <main className="drill">
      <header className="drill-head">
        <div className="drill-skills" role="tablist" aria-label="Skill">
          {SKILLS.map((s) => (
            <button
              key={s.key}
              role="tab"
              aria-selected={skill === s.key}
              className={`chip ${skill === s.key ? 'is-active' : ''}`}
              onClick={() => {
                setSkill(s.key)
                // Production tasks carry no level, so a level filter would return nothing.
                if (s.key === 'writing' || s.key === 'speaking') setLevel(null)
              }}
            >
              {s.label} <span className="chip-native">{s.native}</span>
            </button>
          ))}
        </div>

        {(skill === 'reading' || skill === 'listening') && (
          <div className="drill-levels" role="group" aria-label="Level">
            <button
              className={`chip chip-sm ${level === null ? 'is-active' : ''}`}
              onClick={() => setLevel(null)}
            >
              All
            </button>
            {LEVELS.map((l) => (
              <button
                key={l}
                className={`chip chip-sm ${level === l ? 'is-active' : ''}`}
                onClick={() => setLevel(l)}
              >
                {l}
              </button>
            ))}
          </div>
        )}

        {totals.attempted > 0 && (
          <p className="drill-progress">
            {totals.graded > 0
              ? `${totals.correct}/${totals.graded} correct`
              : `${totals.attempted} attempted`}
            {totals.graded > 0 && (
              <span className="drill-pct">
                {' '}
                · {Math.round((totals.correct / totals.graded) * 100)}%
              </span>
            )}
          </p>
        )}
      </header>

      {loading && <p className="drill-status">Loading…</p>}
      {error && !loading && <p className="drill-status drill-error">{error}</p>}

      {question && !loading && (
        <LookupProvider language={language}>
          <article className="drill-card">
            <p className="drill-meta">
              {question.collection}
              {question.level && <span className="drill-badge">{question.level}</span>}
              {question.seq !== null && <span className="drill-seq">#{question.seq}</span>}
            </p>

            {question.audio_url && (
              // Native controls on purpose: a listening item is played, paused and
              // scrubbed, and the browser's own control does all three accessibly.
              <audio className="drill-audio" src={question.audio_url} controls preload="none" />
            )}

            {documentHidden ? (
              <p className="drill-hidden">
                Transcript hidden until you answer.
                <button className="linkbtn" onClick={() => setRevealDocument(true)}>
                  Show it anyway
                </button>
              </p>
            ) : (
              question.document && (
                <div className="drill-doc">
                  <SelectableText text={question.document} lang="fr" />
                </div>
              )
            )}

            {question.image_url && (
              <p className="drill-original">
                <button className="linkbtn" onClick={() => setShowOriginal((v) => !v)}>
                  {showOriginal ? 'Hide the original' : 'See the original'}
                </button>
              </p>
            )}
            {showOriginal && question.image_url && (
              <img
                className="drill-image"
                src={question.image_url}
                alt="The item as the question bank renders it"
              />
            )}

            {question.question && (
              <h2 className="drill-question" lang="fr">
                {question.question}
              </h2>
            )}

            {isProduction ? (
              <ProductionAnswer
                key={question.id}
                onSubmit={(text) => submit(null, { text })}
                submitted={phase === 'answered'}
              />
            ) : (
              <ol className="drill-options">
                {question.options.map((o) => {
                  const isChosen = selected === o.label
                  const isKey = result?.answer === o.label
                  const state =
                    phase !== 'answered'
                      ? ''
                      : isKey
                        ? 'is-correct'
                        : isChosen
                          ? 'is-wrong'
                          : ''
                  return (
                    <li key={o.label}>
                      <button
                        className={`drill-option ${state} ${isChosen ? 'is-chosen' : ''}`}
                        disabled={phase === 'answered'}
                        // The letter and the text are separate spans so they can be
                        // styled apart. Named explicitly so the control announces as one
                        // choice — "B. Dʼune catastrophe climatique" — instead of leaving
                        // the letter to be read as a stray character beside it.
                        aria-label={`${o.label}. ${o.text}`}
                        onClick={() => void submit(o.label)}
                      >
                        <span className="drill-letter" aria-hidden="true">
                          {o.label}
                        </span>
                        <span lang="fr">{o.text}</span>
                      </button>
                    </li>
                  )
                })}
              </ol>
            )}

            {phase === 'answered' && result && (
              <section className="drill-result">
                {result.correct === null ? (
                  <p className="drill-verdict is-neutral">
                    Recorded. There is no answer key for a production task — compare yours
                    with the model answer below.
                  </p>
                ) : (
                  <p className={`drill-verdict ${result.correct ? 'is-correct' : 'is-wrong'}`}>
                    {result.correct ? 'Correct' : `Answer: ${result.answer}`}
                  </p>
                )}

                {result.answer_source && (
                  // An inferred key is a different kind of fact from one the bank shipped,
                  // and a learner deciding whether to trust a surprising answer should know
                  // which they are looking at.
                  <p className="drill-note">
                    This item shipped without an answer key; this one was worked out from the
                    passage and the vendor's notes ({result.answer_source}).
                  </p>
                )}

                {result.model_answer && (
                  <div className="drill-model">
                    <h3>Model answer</h3>
                    <div lang="fr">
                      <SelectableText text={result.model_answer} lang="fr" />
                    </div>
                  </div>
                )}

                {result.explanation && (
                  <details className="drill-explanation" open>
                    <summary>Explanation</summary>
                    <div>{result.explanation}</div>
                  </details>
                )}

                {result.document_zh && (
                  <details className="drill-translation">
                    <summary>Translation</summary>
                    <div>{result.document_zh}</div>
                  </details>
                )}
              </section>
            )}

            <footer className="drill-actions">
              {phase === 'answering' && !isProduction && (
                <button className="btn ghost" onClick={() => void submit(null)}>
                  Skip
                </button>
              )}
              <button className="btn" onClick={() => void load()}>
                {phase === 'answered' ? 'Next item' : 'Another item'}
              </button>
              {isProduction && onAbout && (
                <button className="linkbtn drill-about" onClick={onAbout}>
                  What is still missing here
                </button>
              )}
            </footer>
          </article>
        </LookupProvider>
      )}
    </main>
  )
}

/** Free-text box for writing tasks. Speaking needs a microphone, which does not exist yet. */
function ProductionAnswer({
  onSubmit,
  submitted,
}: {
  onSubmit: (text: string) => void
  submitted: boolean
}) {
  const [text, setText] = useState('')
  return (
    <div className="drill-production">
      <textarea
        className="drill-textarea"
        lang="fr"
        rows={8}
        value={text}
        disabled={submitted}
        placeholder="Écrivez votre réponse ici…"
        onChange={(e) => setText(e.target.value)}
      />
      <div className="drill-count">{text.trim() ? text.trim().split(/\s+/).length : 0} words</div>
      {!submitted && (
        <button className="btn" disabled={!text.trim()} onClick={() => onSubmit(text)}>
          Submit and see the model answer
        </button>
      )}
    </div>
  )
}
