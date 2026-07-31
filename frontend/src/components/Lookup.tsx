import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { createPortal } from 'react-dom'
import { grammar, lexicon } from '../api'
import type {
  ConstructionHit,
  ExpressionHit,
  LookupResult,
  Practice,
  PracticeCheck,
  SentenceAnalysis,
  VocabSaveInput,
} from '../types'
import { useTextSelection, type Picked } from '../useTextSelection'
import { useVocab, type SavedStatus } from '../vocab/VocabContext'

/* ------------------------------------------------------------------ context */

interface LookupRequest {
  text: string
  start: number
  end: number
  rect: DOMRect
}

interface LookupContextValue {
  request: (r: LookupRequest) => void
  /** Passage whose expression is currently highlighted, and the spans to highlight. */
  activeFor: string | null
  activeSpans: number[][]
}

const LookupContext = createContext<LookupContextValue | null>(null)

/** How far the page may scroll before the popup's viewport anchor is considered stale. */
const SCROLL_DISMISS_PX = 48

/**
 * Attach selection-driven lookup to a container that renders `text` as
 * `[data-off]` segments.
 */
export function useLookupOn(
  ref: React.RefObject<HTMLElement | null>,
  text: string,
  { blockedRanges }: { blockedRanges?: number[][] } = {},
) {
  const ctx = useContext(LookupContext)
  const onPick = useCallback(
    (p: Picked) => ctx?.request({ text, start: p.start, end: p.end, rect: p.rect }),
    [ctx, text],
  )
  useTextSelection(ref, onPick, { blockedRanges, enabled: !!ctx })
  const active = ctx?.activeFor === text ? (ctx?.activeSpans ?? []) : []
  return { activeSpans: active }
}

/* ------------------------------------------------------------------ provider */

const KIND_LABEL: Record<string, string> = {
  idiom: 'idiom',
  collocation: 'collocation',
  phrasal_verb: 'phrasal verb',
  fixed_phrase: 'fixed phrase',
  compound: 'compound',
  proper_noun: 'name',
}

const SOURCE_NOTE: Record<string, string> = {
  precomputed: 'annotated in this passage',
  inferred: 'recognised from another lesson — less certain',
  live: 'looked up just now',
}

export function LookupProvider({
  children,
  language,
  unitId,
  play,
}: {
  children: ReactNode
  language: string
  unitId?: number | null
  /** Plays an ORIGINAL-video-timeline window, when a player is available. */
  play?: (from: number, to: number) => void
}) {
  const { ensureKeys } = useVocab()
  const [anchor, setAnchor] = useState<DOMRect | null>(null)
  const [pendingText, setPendingText] = useState<string | null>(null)
  const [selection, setSelection] = useState<string>('')
  const [result, setResult] = useState<LookupResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [requestScope, setRequestScope] = useState<string | null>(null)
  const reqId = useRef(0)
  const mounted = useRef(true)
  const scopeKey = `${language}\u0000${unitId ?? ''}`
  const previousScope = useRef(scopeKey)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      reqId.current += 1
    }
  }, [])

  useEffect(() => {
    // VocabProvider records failures for visible retry UI. Consuming the rejection keeps
    // mount and language-change loads safe in StrictMode.
    void ensureKeys(language).catch(() => undefined)
  }, [ensureKeys, language])

  const close = useCallback(() => {
    reqId.current += 1
    setAnchor(null)
    setResult(null)
    setError(null)
    setPendingText(null)
    setSelection('')
    setLoading(false)
    setRequestScope(null)
    window.getSelection()?.removeAllRanges()
  }, [])

  useEffect(() => {
    if (previousScope.current === scopeKey) return
    previousScope.current = scopeKey
    close()
  }, [close, scopeKey])

  const request = useCallback(
    ({ text, start, end, rect }: LookupRequest) => {
      const id = ++reqId.current
      setAnchor(rect)
      setSelection(text.slice(start, end) || text)
      setPendingText(text)
      setRequestScope(scopeKey)
      setResult(null)
      setError(null)
      setLoading(true)

      lexicon
        .lookup({ language, text, char_start: start, char_end: end, unit_id: unitId ?? null })
        .then((r) => {
          if (!mounted.current || id !== reqId.current) return
          setResult(r)
          setSelection(r.selection)
        })
        .catch((e) => {
          if (!mounted.current || id !== reqId.current) return
          setError(String(e))
        })
        .finally(() => {
          if (mounted.current && id === reqId.current) setLoading(false)
        })
    },
    [language, scopeKey, unitId],
  )

  // Dismiss on Escape, on a click outside, and on a *substantial* scroll.
  //
  // The popup is fixed-positioned against a viewport rect, so once the page moves far
  // enough the anchor is stale and it would point at the wrong words. But closing on any
  // scroll at all makes it fragile — a learner nudging the page with a trackpad while
  // reading the popup would lose it. So tolerate small movements and only close past a
  // threshold.
  useEffect(() => {
    if (!anchor) return
    const openedAt = window.scrollY
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && close()
    const onScroll = () => {
      if (Math.abs(window.scrollY - openedAt) > SCROLL_DISMISS_PX) close()
    }
    const onDown = (e: MouseEvent) => {
      const el = e.target as HTMLElement
      if (!el.closest?.('.tr-popup')) close()
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('scroll', onScroll, { passive: true, capture: true })
    document.addEventListener('mousedown', onDown)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('scroll', onScroll, { capture: true })
      document.removeEventListener('mousedown', onDown)
    }
  }, [anchor, close])

  const headline: ExpressionHit | null = result?.expressions?.[0] ?? null

  const value = useMemo<LookupContextValue>(
    () => ({
      request,
      activeFor: headline ? pendingText : null,
      activeSpans: headline?.component_spans ?? [],
    }),
    [request, headline, pendingText],
  )

  return (
    <LookupContext.Provider value={value}>
      {children}
      {anchor &&
        requestScope === scopeKey &&
        createPortal(
          <Popup
            anchor={anchor}
            selection={selection}
            result={result}
            error={error}
            loading={loading}
            language={language}
            unitId={unitId}
            play={play}
            onClose={close}
          />,
          // Portal to body deliberately: the lesson cards apply a hover transform, which
          // creates a containing block for fixed-position descendants and would anchor the
          // popup to the card instead of the viewport.
          document.body,
        )}
    </LookupContext.Provider>
  )
}

/* ------------------------------------------------------------------ popup */

const POPUP_W = 340
const GAP = 10
type CandidateKind = 'expression' | 'word'
type SaveAttempt = {
  input: VocabSaveInput
  busy: boolean
  error: string | null
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason)
}

function SaveControl({
  kind,
  status,
  attempt,
  onSave,
}: {
  kind: CandidateKind
  status: SavedStatus
  attempt?: SaveAttempt
  onSave: () => void
}) {
  const className = kind === 'expression' ? 'tr-save' : 'tr-btn'

  // The shared normalized-key cache is authoritative. A stale local failure or an older
  // pending request must never mask a save confirmed through another candidate.
  if (status === 'saved') {
    return (
      <button className={className} aria-label={`${kind} saved`} disabled>
        ✓ saved
      </button>
    )
  }

  if (attempt?.busy) {
    return (
      <button className={className} aria-label={`saving ${kind}`} disabled>
        Saving…
      </button>
    )
  }

  if (attempt?.error) {
    return (
      <div className="tr-error" role="alert">
        Save failed. {attempt.error}{' '}
        <button className="tr-btn" onClick={onSave} aria-label={`retry save ${kind}`}>
          Retry
        </button>
      </div>
    )
  }

  if (status === 'unknown') {
    return (
      <button
        className={className}
        aria-label={`checking saved status for ${kind}`}
        disabled
      >
        checking saved words…
      </button>
    )
  }

  return (
    <button className={className} onClick={onSave} aria-label={`save ${kind}`}>
      + save {kind}
    </button>
  )
}

function Popup({
  anchor,
  selection,
  result,
  error,
  loading,
  language,
  unitId,
  play,
  onClose,
}: {
  anchor: DOMRect
  selection: string
  result: LookupResult | null
  error: string | null
  loading: boolean
  language: string
  unitId?: number | null
  play?: (from: number, to: number) => void
  onClose: () => void
}) {
  const vocab = useVocab()
  const [attempts, setAttempts] = useState<Partial<Record<CandidateKind, SaveAttempt>>>({})
  const mounted = useRef(true)
  const activeResult = useRef(result)
  activeResult.current = result
  const pendingSaves = useRef<
    Partial<Record<CandidateKind, { token: object; result: LookupResult }>>
  >({})
  const ref = useRef<HTMLDivElement | null>(null)
  const [flip, setFlip] = useState(false)
  // Measured height must live in state, not be read from the ref during render: on the
  // first pass the ref is null, and if the only state update here were `setFlip(false)`
  // React would bail out of the re-render, so the clamp below would never see a real
  // height and a tall popup would overflow the viewport.
  const [height, setHeight] = useState(0)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  useEffect(() => {
    const h = ref.current?.offsetHeight ?? 0
    setHeight(h)
    // Flip above the selection when there isn't room below and there is room above.
    setFlip(anchor.bottom + GAP + h > window.innerHeight && anchor.top - GAP - h > 0)
  }, [anchor, result, loading])

  useEffect(() => {
    setAttempts({})
  }, [result])

  const left = Math.min(
    Math.max(GAP, anchor.left + anchor.width / 2 - POPUP_W / 2),
    window.innerWidth - POPUP_W - GAP,
  )
  // Clamp vertically too. In normal use the anchor is on screen (the learner just selected
  // it), but a stale anchor or a popup taller than the viewport would otherwise render
  // partly or wholly out of view.
  const h = height
  const style: React.CSSProperties = flip
    ? {
        left,
        bottom: Math.min(
          Math.max(GAP, window.innerHeight - anchor.top + GAP),
          Math.max(GAP, window.innerHeight - h - GAP),
        ),
        width: POPUP_W,
      }
    : {
        left,
        top: Math.min(Math.max(GAP, anchor.bottom + GAP), Math.max(GAP, window.innerHeight - h - GAP)),
        width: POPUP_W,
      }

  const headline = result?.expressions?.[0] ?? null
  const others = result?.expressions?.slice(1) ?? []
  const word = result?.word

  const save = async (
    kind: CandidateKind,
    normalizedHeadword: string,
    input: VocabSaveInput,
  ) => {
    const candidateResult = result
    if (!candidateResult) return
    if (pendingSaves.current[kind]?.result === candidateResult) return

    const token = {}
    pendingSaves.current[kind] = { token, result: candidateResult }
    setAttempts((current) => ({
      ...current,
      [kind]: { input, busy: true, error: null },
    }))
    try {
      await vocab.save(input)
      if (!mounted.current || activeResult.current !== candidateResult) return
      setAttempts((current) => {
        const active = current[kind]
        if (!active || active.input !== input) return current
        const next = { ...current }
        delete next[kind]
        return next
      })
    } catch (reason: unknown) {
      if (!mounted.current || activeResult.current !== candidateResult) return
      setAttempts((current) => {
        const active = current[kind]
        if (!active || active.input !== input) return current
        if (vocab.savedStatus(language, normalizedHeadword) === 'saved') {
          const next = { ...current }
          delete next[kind]
          return next
        }
        return {
          ...current,
          [kind]: { input, busy: false, error: errorMessage(reason) },
        }
      })
    } finally {
      if (pendingSaves.current[kind]?.token === token) {
        delete pendingSaves.current[kind]
      }
    }
  }

  const retryKeys = () => {
    void vocab.ensureKeys(language).catch(() => undefined)
  }
  const keyState = vocab.keyState(language)
  const expressionStatus = headline
    ? vocab.savedStatus(language, headline.normalized_headword)
    : 'unknown'
  const wordStatus = word
    ? vocab.savedStatus(language, word.normalized_headword)
    : 'unknown'
  const expressionInput: VocabSaveInput | null =
    headline && result
      ? {
          language,
          headword: headline.canonical,
          gloss_en: headline.gloss_en,
          example: result.context,
          unit_id: unitId ?? null,
        }
      : null
  const wordInput: VocabSaveInput | null =
    word && result
      ? {
          language,
          headword: word.lemma || selection,
          gloss_en: word.gloss_en,
          example: result.context,
          unit_id: unitId ?? null,
        }
      : null

  const canPlay =
    !!play && result?.audio_start_s != null && result?.audio_end_s != null

  return (
    <div className="tr-popup" style={style} ref={ref} role="dialog" aria-label="Translation">
      <div className="tr-head">
        <span className="tr-sel" lang={language}>
          {selection}
        </span>
        <button className="tr-close" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </div>

      {loading && <div className="tr-loading">Looking up…</div>}

      {error && <div className="tr-error">Lookup failed. {error}</div>}

      {keyState.status === 'error' && (
        <div className="tr-error" role="alert">
          Saved words unavailable. {keyState.error?.message ?? 'Please try again.'}{' '}
          <button className="tr-btn" onClick={retryKeys} aria-label="retry saved words">
            Retry
          </button>
        </div>
      )}

      {result && (
        <>
          {/* The expression comes first — it is the reason this feature exists. */}
          {headline && (
            <div className={`tr-expr ${headline.source === 'inferred' ? 'is-inferred' : ''}`}>
              <div className="tr-expr-top">
                <span className="tr-kind">{KIND_LABEL[headline.kind] ?? headline.kind}</span>
                {headline.component_spans.length > 1 && (
                  <span className="tr-kind tr-split" title="Split by other words in the sentence">
                    split
                  </span>
                )}
              </div>
              <div className="tr-expr-canonical" lang={language}>
                {headline.canonical}
              </div>
              <div className="tr-expr-gloss">{headline.gloss_en}</div>
              {headline.literal_en && (
                <div className="tr-literal">
                  literally: <em>{headline.literal_en}</em>
                </div>
              )}
              {headline.note && <div className="tr-note">{headline.note}</div>}
              <div className="tr-provenance">{SOURCE_NOTE[headline.source]}</div>
              <SaveControl
                kind="expression"
                status={expressionStatus}
                attempt={attempts.expression}
                onSave={() =>
                  void save(
                    'expression',
                    headline.normalized_headword,
                    attempts.expression?.input ?? expressionInput!,
                  )
                }
              />
            </div>
          )}

          {word && (
            <div className="tr-word">
              <div className="tr-word-line">
                <span className="tr-word-gloss">
                  {word.gloss_en || <span className="tr-muted">no gloss available</span>}
                </span>
                {word.pos && <span className="tr-pos">{word.pos}</span>}
              </div>
              {word.lemma && word.lemma.toLowerCase() !== selection.toLowerCase() && (
                <div className="tr-lemma">
                  dictionary form: <b lang={language}>{word.lemma}</b>
                </div>
              )}
              {word.note && <div className="tr-note">{word.note}</div>}
              {word.other_senses.length > 0 && (
                <div className="tr-senses">
                  <span className="tr-senses-label">elsewhere</span>
                  {word.other_senses.map((s) => (
                    <div className="tr-sense" key={s.gloss_en}>
                      {s.gloss_en}
                      {s.when && <span className="tr-sense-when"> — {s.when}</span>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {result.is_sentence && (
            <SentenceSection
              sentence={result.selection}
              language={language}
              constructions={result.constructions}
            />
          )}

          {others.length > 0 && (
            <div className="tr-others">
              <span className="tr-senses-label">also part of</span>
              {others.map((e) => (
                <div className="tr-other" key={e.canonical}>
                  <b lang={language}>{e.canonical}</b> — {e.gloss_en}
                </div>
              ))}
            </div>
          )}

          <div className="tr-actions">
            {canPlay && (
              <button
                className="tr-btn"
                onClick={() => play!(result.audio_start_s!, result.audio_end_s!)}
              >
                ▶ hear it
              </button>
            )}
            {word && (
              <SaveControl
                kind="word"
                status={wordStatus}
                attempt={attempts.word}
                onSave={() =>
                  void save(
                    'word',
                    word.normalized_headword,
                    attempts.word?.input ?? wordInput!,
                  )
                }
              />
            )}
            {result.source === 'offline' && <span className="tr-badge">offline</span>}
            {result.source === 'cache' && <span className="tr-badge">cached</span>}
          </div>
        </>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ selectable text */

/** Merge and sort spans so segment boundaries never overlap. */
function normalizeSpans(spans: number[][], len: number): number[][] {
  const clean = spans
    .map(([s, e]) => [Math.max(0, s), Math.min(len, e)] as [number, number])
    .filter(([s, e]) => e > s)
    .sort((a, b) => a[0] - b[0])
  const out: number[][] = []
  for (const [s, e] of clean) {
    const last = out[out.length - 1]
    if (last && s <= last[1]) last[1] = Math.max(last[1], e)
    else out.push([s, e])
  }
  return out
}

/** A run of text with one set of decorations. Geometry only — classes are applied later. */
export interface TextPiece {
  start: number
  end: number
  mwe: boolean
  /** Index into `words`, or -1 for the punctuation and whitespace between them. */
  word: number
}

/** Index of the word span containing `pos`, or -1. Spans are sorted and disjoint. */
function wordSpanAt(spans: [number, number, number][], pos: number): number {
  let lo = 0
  let hi = spans.length - 1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    const [s, e, i] = spans[mid]
    if (pos < s) hi = mid - 1
    else if (pos >= e) lo = mid + 1
    else return i
  }
  return -1
}

/**
 * Cut a passage at every boundary any decoration could ever need.
 *
 * Shared by the transcript and the cloze passage so both produce identical markup and identical
 * `data-off` semantics. `extraCuts` lets a caller force boundaries of its own — the cloze passes
 * its blank edges, which guarantees no piece straddles a blank and therefore that rendering a
 * range of pieces can never emit part of a hidden answer.
 *
 * Geometry does not depend on which word is currently spoken, so the result is stable across
 * playback and only `className` changes frame to frame.
 */
export function buildPieces(
  text: string,
  {
    spans = [],
    words = [],
    extraCuts = [],
  }: {
    spans?: number[][]
    words?: { char_start: number; char_end: number }[]
    extraCuts?: number[]
  } = {},
): TextPiece[] {
  const marks = normalizeSpans(spans, text.length)

  // Drop any span that runs backwards into its predecessor. Alignment is per-word and
  // ascending, so an overlap means one of the two is wrong; keeping the earlier one at least
  // keeps the sequence sane.
  const wordSpans: [number, number, number][] = []
  words.forEach((w, i) => {
    const s = Math.max(0, w.char_start)
    const e = Math.min(text.length, w.char_end)
    const prev = wordSpans[wordSpans.length - 1]
    if (e <= s || (prev && s < prev[1])) return
    wordSpans.push([s, e, i])
  })

  const cuts = new Set<number>([0, text.length])
  for (const [s, e] of marks) {
    cuts.add(s)
    cuts.add(e)
  }
  for (const [s, e] of wordSpans) {
    cuts.add(s)
    cuts.add(e)
  }
  for (const c of extraCuts) {
    if (c > 0 && c < text.length) cuts.add(c)
  }
  const bounds = [...cuts].sort((a, b) => a - b)

  const out: TextPiece[] = []
  for (let i = 0; i < bounds.length - 1; i++) {
    const s = bounds[i]
    const e = bounds[i + 1]
    if (e <= s) continue
    out.push({
      start: s,
      end: e,
      mwe: marks.some(([ms, me]) => s >= ms && e <= me),
      word: wordSpanAt(wordSpans, s),
    })
  }
  return out
}

/** Class list for one piece, given what is currently highlighted. */
function pieceClass(
  p: TextPiece,
  {
    activeWord = -1,
    lineSpan = null,
    isActive,
  }: {
    activeWord?: number
    lineSpan?: [number, number] | null
    isActive?: (s: number, e: number) => boolean
  },
): string | undefined {
  const mid = p.start + (p.end - p.start) / 2
  const cls = [
    p.mwe ? 'mwe' : '',
    p.mwe && isActive?.(p.start, p.end) ? 'mwe-active' : '',
    p.word >= 0 && p.word === activeWord ? 'now' : '',
    // Midpoint test: a sentence boundary falls on punctuation, not on a word edge, so a piece
    // can straddle it. Half a comma of imprecision is invisible.
    lineSpan && mid >= lineSpan[0] && mid < lineSpan[1] ? 'now-line' : '',
  ]
    .filter(Boolean)
    .join(' ')
  return cls || undefined
}

/**
 * Render pieces as `[data-off]` spans. Both passages go through this, so a selection resolves
 * the same way and the follow-along highlight looks the same wherever it appears.
 */
export function renderPieces(
  text: string,
  pieces: TextPiece[],
  opts: {
    activeWord?: number
    lineSpan?: [number, number] | null
    isActive?: (s: number, e: number) => boolean
  } = {},
): ReactNode[] {
  return pieces.map((p) => (
    <span
      key={p.start}
      data-off={p.start}
      data-w={p.word >= 0 ? p.word : undefined}
      className={pieceClass(p, opts)}
    >
      {text.slice(p.start, p.end)}
    </span>
  ))
}

/**
 * Renders a passage as `[data-off]` segments so selections resolve to exact offsets,
 * marking known expression spans and — when word timings are supplied — the word being
 * spoken.
 *
 * The passage is cut once, at every boundary any decoration could ever need, and the
 * resulting pieces are memoized. Only `className` changes as the audio advances, so
 * following the voice re-renders spans without rebuilding them.
 */
export function SelectableText({
  text,
  spans = [],
  words = [],
  activeWord = -1,
  lineSpan = null,
  onWordClick,
  className = '',
  lang,
}: {
  text: string
  /** Expression component spans to mark, from GET /api/units/:id/expressions. */
  spans?: number[][]
  /** Word char spans, for follow-along highlighting and click-to-seek. */
  words?: { char_start: number; char_end: number }[]
  activeWord?: number
  /** Sentence to tint, so the eye can find the line again after looking away. */
  lineSpan?: [number, number] | null
  onWordClick?: (index: number) => void
  className?: string
  lang?: string
}) {
  const ref = useRef<HTMLDivElement | null>(null)
  const { activeSpans } = useLookupOn(ref, text)

  const active = useMemo(() => normalizeSpans(activeSpans, text.length), [activeSpans, text.length])
  const isActive = (s: number, e: number) => active.some(([as, ae]) => s < ae && as < e)

  const pieces = useMemo(() => buildPieces(text, { spans, words }), [text, spans, words])

  // A click seeks; a drag looks a word up. Both end in `mouseup`, so the two are told
  // apart by whether anything is actually selected.
  const onClick = useCallback(
    (e: React.MouseEvent) => {
      if (!onWordClick) return
      const sel = window.getSelection()
      if (sel && !sel.isCollapsed) return
      const el = (e.target as HTMLElement | null)?.closest?.('[data-w]')
      const i = el ? Number(el.getAttribute('data-w')) : NaN
      if (Number.isInteger(i) && i >= 0) onWordClick(i)
    },
    [onWordClick],
  )

  return (
    <div
      ref={ref}
      className={`selectable ${onWordClick ? 'seekable ' : ''}${className}`}
      lang={lang}
      onClick={onClick}
    >
      {renderPieces(text, pieces, { activeWord, lineSpan, isActive })}
    </div>
  )
}


/* ------------------------------------------------------------------ sentence grammar */

/**
 * Sentence-level grammar, in two waves.
 *
 * The constructions arrive with the lookup itself — they come from the deterministic
 * matcher, so naming "il n'y a pas que X" costs nothing and appears immediately. The
 * translation, the sentence-specific reading and the practice item need a model call, so
 * they fill in after. That ordering is deliberate: the learner sees *what* the structure is
 * without waiting, and the explanation catches up.
 */
function SentenceSection({
  sentence,
  language,
  constructions,
}: {
  sentence: string
  language: string
  constructions: ConstructionHit[]
}) {
  const [analysis, setAnalysis] = useState<SentenceAnalysis | null>(null)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let live = true
    setAnalysis(null)
    setLoading(true)
    setFailed(false)
    grammar
      .sentence(language, sentence)
      .then((a) => live && setAnalysis(a))
      .catch(() => live && setFailed(true))
      .finally(() => live && setLoading(false))
    return () => {
      live = false
    }
  }, [sentence, language])

  // Prefer the analysed structures (they carry the sentence-specific reading); fall back to
  // the raw matcher hits so something useful shows even if the model call fails.
  const structures = analysis?.structures ?? []
  const showing = structures.length
    ? structures
    : constructions.map((c) => ({ ...c, in_this_sentence: '', source: 'pattern' as const }))

  return (
    <div className="tr-sentence">
      <div className="tr-senses-label">Sentence</div>

      {analysis?.translation_en ? (
        <div className="tr-translation">{analysis.translation_en}</div>
      ) : loading ? (
        <div className="tr-loading">Translating…</div>
      ) : failed ? (
        <div className="tr-error">Couldn't analyse this sentence.</div>
      ) : null}

      {showing.length === 0 && !loading && (
        <div className="tr-note">No fixed structures detected — this sentence is built plainly.</div>
      )}

      {showing.map((st) => (
        <div className={`tr-struct ${st.source === 'llm' ? 'is-proposed' : ''}`} key={st.key}>
          <div className="tr-struct-top">
            <span className="tr-schema">{st.schema_form}</span>
            <span className="tr-kind">{st.cefr}</span>
            {st.source === 'llm' && (
              <span className="tr-kind tr-split" title="Proposed by the model, not pattern-matched">
                unverified
              </span>
            )}
          </div>
          <div className="tr-struct-name">{st.name_en}</div>
          <div className="tr-struct-meaning">{st.meaning_en}</div>
          {st.literal_trap && (
            <div className="tr-trap">
              <span className="tr-trap-label">word-by-word you'd read</span> {st.literal_trap}
            </div>
          )}
          {st.why_opaque && <div className="tr-why">{st.why_opaque}</div>}
          {'in_this_sentence' in st && st.in_this_sentence && (
            <div className="tr-here">
              <span className="tr-trap-label">here</span> {st.in_this_sentence}
            </div>
          )}
        </div>
      ))}

      {(analysis?.practices ?? []).map((p, i) => (
        <PracticeBox
          key={`${p.construction_key}-${i}`}
          practice={p}
          index={i}
          sentence={sentence}
          language={language}
        />
      ))}
    </div>
  )
}

/** Produce the construction, don't just read about it. */
function PracticeBox({
  practice,
  index,
  sentence,
  language,
}: {
  practice: Practice
  index: number
  sentence: string
  language: string
}) {
  const [answer, setAnswer] = useState('')
  const [result, setResult] = useState<PracticeCheck | null>(null)
  const [busy, setBusy] = useState(false)
  const [showHint, setShowHint] = useState(false)

  const submit = async () => {
    if (!answer.trim() || busy) return
    setBusy(true)
    try {
      setResult(
        await grammar.checkPractice({
          language,
          sentence,
          practice_index: index,
          answer,
        }),
      )
    } catch {
      setResult(null)
    } finally {
      setBusy(false)
    }
  }

  const tone = !result ? '' : result.correct ? 'correct' : result.score > 0 ? 'partial' : 'wrong'

  return (
    <div className={`tr-practice ${tone}`}>
      <div className="tr-practice-head">
        <span className="tr-senses-label">Your turn</span>
        {practice.schema_form && <span className="tr-schema sm">{practice.schema_form}</span>}
      </div>
      <div className="tr-practice-prompt">{practice.prompt_en}</div>

      <textarea
        className="tr-practice-input"
        value={answer}
        onChange={(e) => setAnswer(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            void submit()
          }
        }}
        placeholder="Écrivez-le en français…"
        disabled={!!result}
        rows={2}
        spellCheck={false}
        lang={language}
      />

      {!result ? (
        <div className="tr-practice-actions">
          <button className="tr-btn" onClick={submit} disabled={busy || !answer.trim()}>
            {busy ? 'Checking…' : 'Check'}
          </button>
          {practice.hint_en && (
            <button className="tr-btn" onClick={() => setShowHint((h) => !h)}>
              {showHint ? 'hide hint' : 'hint'}
            </button>
          )}
          {showHint && practice.hint_en && <div className="tr-hint">{practice.hint_en}</div>}
        </div>
      ) : (
        <div className="tr-practice-result">
          <div className="tr-practice-headline">{result.headline}</div>

          {/* The two signals stay separate — "correct French that dodges the pattern" is the
              most useful thing this can say, and a single score could not express it. */}
          {result.structure.checked && (
            <div className={`tr-signal ${result.structure.used ? 'ok' : 'no'}`}>
              {result.structure.used
                ? `✓ used ${result.structure.schema_form ?? 'the structure'}`
                : `✗ missing ${result.structure.missing_markers.join(', ') || 'the structure'}`}
            </div>
          )}
          {result.meaning_ok !== null && (
            <div className={`tr-signal ${result.meaning_ok && result.grammar_ok ? 'ok' : 'no'}`}>
              {result.meaning_ok && result.grammar_ok
                ? '✓ meaning and grammar fine'
                : result.meaning_ok
                  ? '~ right idea, grammar off'
                  : '✗ meaning is off'}
            </div>
          )}

          {result.issues.map((iss) => (
            <div className="tr-issue" key={iss.fragment + iss.problem}>
              <s>{iss.fragment}</s> → <b lang={language}>{iss.fix}</b>
              <div className="tr-why">{iss.problem}</div>
            </div>
          ))}

          {result.corrected_fr && (
            <div className="tr-corrected">
              <span className="tr-trap-label">corrected</span>{' '}
              <span lang={language}>{result.corrected_fr}</span>
            </div>
          )}
          {result.note_en && <div className="tr-why">{result.note_en}</div>}
          <div className="tr-model-answer">
            <span className="tr-trap-label">
              {result.better_than_reference ? 'another way' : 'model answer'}
            </span>{' '}
            <span lang={language}>{result.reference_fr}</span>
          </div>
          <button
            className="tr-btn"
            onClick={() => {
              setResult(null)
              setAnswer('')
            }}
          >
            try again
          </button>
        </div>
      )}
    </div>
  )
}
