import { type ReactNode, useMemo, useRef, useState } from 'react'
import type { AttemptResult, Exercise } from '../types'
import { FollowToggle, usePassageFollow } from './Follow'
import { buildPieces, renderPieces, useLookupOn } from './Lookup'

/** Everything an exercise component needs from its parent. */
export interface ExerciseProps {
  ex: Exercise
  result: AttemptResult | null
  response: unknown
  setResponse: (r: unknown) => void
  /** Plays a window in ORIGINAL-video seconds; the player handles the clip offset. */
  play: (from: number, to: number, loop?: boolean) => void
  /**
   * Submit this exercise. Already guarded by the caller for "answered, not busy, not
   * already submitted", so components can call it on Enter without re-checking.
   */
  onSubmit?: () => void
}

const LETTERS = 'ABCDEFGH'

/* ------------------------------------------------------------------ cloze */

function Cloze({ ex, result, response, setResponse, play, onSubmit }: ExerciseProps) {
  const text = ex.payload.text ?? ''
  const blanks = useMemo(
    () => [...(ex.payload.blanks ?? [])].sort((a, b) => a.char_start - b.char_start),
    [ex.payload.blanks],
  )
  const values = (response as string[]) ?? blanks.map(() => '')
  const perBlank = result?.feedback.blanks ?? []

  // Blank spans are off limits to a selection: dragging across one would otherwise ask the
  // server to gloss a range containing the hidden answer, printing it into the popup.
  const blankRanges = useMemo(() => blanks.map((b) => [b.char_start, b.char_end]), [blanks])

  const textRef = useRef<HTMLDivElement | null>(null)
  useLookupOn(textRef, text, { blockedRanges: blankRanges })

  // Follow the voice through the passage, on by default. It gives nothing away: the answer's
  // characters are never rendered, and the per-blank replay button already hands over each
  // blank's exact audio window. Anyone who would rather work without it has the switch.
  const [follow, setFollow] = useState(true)
  const { available, words, activeWord, lineSpan } = usePassageFollow(text, follow)

  // Blank edges become piece boundaries, so no rendered piece can ever straddle a blank.
  const pieces = useMemo(
    () => buildPieces(text, { words, extraCuts: blanks.flatMap((b) => [b.char_start, b.char_end]) }),
    [text, words, blanks],
  )

  /** Which blank the spoken word falls inside, or -1. */
  const spokenBlank = useMemo(() => {
    const w = activeWord >= 0 ? words[activeWord] : null
    if (!w) return -1
    return blanks.findIndex((b) => w.char_start < b.char_end && b.char_start < w.char_end)
  }, [activeWord, words, blanks])

  const set = (i: number, v: string) => {
    const next = [...values]
    next[i] = v
    setResponse(next)
  }

  // Enter advances to the next blank; Enter on the last one submits. Tab still works
  // natively — this is the addition that makes the whole exercise keyboard-only, so a
  // learner can type through a passage without reaching for the mouse.
  const inputs = useRef<(HTMLInputElement | null)[]>([])
  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>, i: number) => {
    if (e.key !== 'Enter') return
    e.preventDefault() // never insert a newline or trigger implicit form submission
    const back = e.shiftKey
    if (!back && i === blanks.length - 1) {
      onSubmit?.()
      return
    }
    const target = inputs.current[i + (back ? -1 : 1)]
    if (target) {
      target.focus()
      target.select() // so revisiting a filled blank overwrites rather than appends
    }
  }

  /** Pieces of the passage inside [from, to), as `[data-off]` spans — see useTextSelection. */
  const chunk = (from: number, to: number) =>
    renderPieces(
      text,
      pieces.filter((p) => p.start >= from && p.end <= to),
      { activeWord, lineSpan },
    )

  const nodes: ReactNode[] = []
  let cursor = 0
  blanks.forEach((b, i) => {
    if (b.char_start > cursor) nodes.push(...chunk(cursor, b.char_start))
    const fb = perBlank.find((f) => f.index === i)
    const cls = !fb
      ? ''
      : fb.correct
        ? fb.tolerance
          ? 'tolerated'
          : 'correct'
        : 'wrong'

    nodes.push(
      // The spoken word landing inside a blank is the one moment the highlight cannot show
      // anything — the answer's characters are deliberately not rendered. Ringing the input
      // instead says "the missing word is being said right now", which is exactly the cue a
      // listening drill wants, and gives away nothing the replay button doesn't already.
      <span className={`blank-wrap ${spokenBlank === i ? 'now-blank' : ''}`} key={`b${i}`}>
        <span className="blank-num">{i + 1}</span>
        <input
          ref={(el) => {
            inputs.current[i] = el
          }}
          className={`cloze-input ${cls}`}
          style={{ width: `${Math.max(6, b.length + 2)}ch` }}
          value={values[i] ?? ''}
          onChange={(e) => set(i, e.target.value)}
          onKeyDown={(e) => onKeyDown(e, i)}
          disabled={!!result}
          aria-label={`Blank ${i + 1} of ${blanks.length}`}
          spellCheck={false}
          autoComplete="off"
        />
        <button
          type="button"
          className="mini-audio"
          title={`Replay blank ${i + 1} (loops)`}
          onClick={() => play(b.audio_start_s, b.audio_end_s, true)}
        >
          ⟳
        </button>
        {fb && !fb.correct && <span className="corrected">{fb.expected}</span>}
      </span>,
    )
    cursor = b.char_end
  })
  if (cursor < text.length) nodes.push(...chunk(cursor, text.length))

  return (
    <>
      {available && (
        <div className="follow-row">
          <FollowToggle on={follow} onChange={setFollow} />
        </div>
      )}
      {/* `selectable` is what marks a passage rendered as [data-off] pieces, and it is what the
          follow-along rules are scoped to. Without it the highlight class lands on the right
          span and styles nothing. */}
      <div className="cloze-text selectable" ref={textRef}>
        {nodes}
      </div>
      {ex.payload.word_bank && (
        <div className="word-bank">
          <span className="bar-label" style={{ marginRight: 4 }}>
            word bank:
          </span>
          {ex.payload.word_bank.map((w) => (
            <span className="chip" key={w}>
              {w}
            </span>
          ))}
        </div>
      )}
    </>
  )
}

/* ------------------------------------------------------------------ mcq */

function Mcq({ ex, result, response, setResponse }: ExerciseProps) {
  const options = ex.payload.options ?? []
  const selected = response as number | undefined
  const correctIdx = result?.feedback.correct_index

  return (
    <div className="options">
      {options.map((opt, i) => {
        let cls = ''
        if (result) {
          if (i === correctIdx) cls = 'correct'
          else if (i === selected) cls = 'wrong'
        } else if (i === selected) cls = 'selected'
        return (
          <button
            type="button"
            key={i}
            className={`option ${cls}`}
            disabled={!!result}
            onClick={() => setResponse(i)}
          >
            <span className="option-key">{LETTERS[i]}</span>
            <span>{opt}</span>
          </button>
        )
      })}
    </div>
  )
}

/* ------------------------------------------------------------------ true / false */

function TrueFalse({ ex, result, response, setResponse }: ExerciseProps) {
  const selected = response as boolean | undefined
  const correct = result?.feedback.correct_value as boolean | undefined
  const label = ['Vrai', 'Faux']

  return (
    <div className="tf-row">
      {[true, false].map((val, i) => {
        let cls = ''
        if (result) {
          if (val === correct) cls = 'correct'
          else if (val === selected) cls = 'wrong'
        } else if (val === selected) cls = 'selected'
        return (
          <button
            type="button"
            key={String(val)}
            className={`option ${cls}`}
            disabled={!!result}
            onClick={() => setResponse(val)}
          >
            {label[i]}
          </button>
        )
      })}
    </div>
  )
}

/* ------------------------------------------------------------------ vocab match */

function VocabMatch({ ex, result, response, setResponse }: ExerciseProps) {
  const words = ex.payload.words ?? []
  const glosses = ex.payload.glosses ?? []
  const pairs = (response as Record<string, string>) ?? {}
  const fb = result?.feedback.pairs

  return (
    <div className="vocab-grid">
      {words.map((w) => {
        const state = fb?.[w]
        const cls = state ? (state.correct ? 'correct' : 'wrong') : ''
        return (
          <div className={`vocab-row ${cls}`} key={w}>
            <span className="vocab-word">{w}</span>
            <select
              value={pairs[w] ?? ''}
              disabled={!!result}
              onChange={(e) => setResponse({ ...pairs, [w]: e.target.value })}
              aria-label={`Meaning of ${w}`}
            >
              <option value="">— choisir —</option>
              {glosses.map((g) => (
                <option value={g} key={g}>
                  {g}
                </option>
              ))}
            </select>
            {state && !state.correct && <span className="corrected">→ {state.expected}</span>}
          </div>
        )
      })}
    </div>
  )
}

/* ------------------------------------------------------------------ ordering */

function Ordering({ ex, result, response, setResponse }: ExerciseProps) {
  const items = (response as string[]) ?? ex.payload.items ?? []
  const correctOrder = result?.feedback.correct_order

  const move = (i: number, delta: number) => {
    const j = i + delta
    if (j < 0 || j >= items.length) return
    const next = [...items]
    ;[next[i], next[j]] = [next[j], next[i]]
    setResponse(next)
  }

  return (
    <div className="order-list">
      {items.map((item, i) => {
        const cls = correctOrder ? (correctOrder[i] === item ? 'correct' : 'wrong') : ''
        return (
          <div className={`order-item ${cls}`} key={item}>
            <span className="order-pos">{i + 1}</span>
            <span className="order-text">{item}</span>
            {!result && (
              <span className="order-btns">
                <button type="button" onClick={() => move(i, -1)} disabled={i === 0} title="Move up">
                  ▲
                </button>
                <button
                  type="button"
                  onClick={() => move(i, 1)}
                  disabled={i === items.length - 1}
                  title="Move down"
                >
                  ▼
                </button>
              </span>
            )}
          </div>
        )
      })}
      {correctOrder && (
        <div className="feedback partial" style={{ marginTop: 4 }}>
          <strong>Correct order:</strong>
          <ol style={{ margin: '6px 0 0', paddingLeft: 20 }}>
            {correctOrder.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ol>
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ dispatcher */

const BY_KIND = {
  cloze: Cloze,
  mcq: Mcq,
  true_false: TrueFalse,
  vocab_match: VocabMatch,
  ordering: Ordering,
} as const

export const KIND_LABEL: Record<string, string> = {
  cloze: 'Fill in the blanks',
  mcq: 'Multiple choice',
  true_false: 'True or false',
  vocab_match: 'Vocabulary',
  ordering: 'Order the events',
}

/** Initial response value for an exercise kind — shapes must match the API graders. */
export function emptyResponse(ex: Exercise): unknown {
  switch (ex.kind) {
    case 'cloze':
      return (ex.payload.blanks ?? []).map(() => '')
    case 'vocab_match':
      return {}
    case 'ordering':
      return [...(ex.payload.items ?? [])]
    default:
      return undefined
  }
}

/** Wraps a local response value into the payload the API expects. */
export function toApiResponse(ex: Exercise, response: unknown): Record<string, unknown> {
  switch (ex.kind) {
    case 'cloze':
      return { blanks: (response as string[]) ?? [] }
    case 'mcq':
      return { index: response }
    case 'true_false':
      return { value: response }
    case 'vocab_match':
      return { pairs: (response as Record<string, string>) ?? {} }
    case 'ordering':
      return { order: (response as string[]) ?? [] }
    default:
      return {}
  }
}

export function isAnswered(ex: Exercise, response: unknown): boolean {
  switch (ex.kind) {
    case 'cloze':
      return ((response as string[]) ?? []).some((v) => v.trim().length > 0)
    case 'mcq':
      return typeof response === 'number'
    case 'true_false':
      return typeof response === 'boolean'
    case 'vocab_match': {
      const pairs = (response as Record<string, string>) ?? {}
      return Object.values(pairs).filter(Boolean).length === (ex.payload.words ?? []).length
    }
    case 'ordering':
      return ((response as string[]) ?? []).length > 0
    default:
      return false
  }
}

export function ExerciseBody(props: ExerciseProps) {
  const Component = BY_KIND[props.ex.kind]
  if (!Component) return <div className="error">Unsupported exercise kind: {props.ex.kind}</div>
  return <Component {...props} />
}
