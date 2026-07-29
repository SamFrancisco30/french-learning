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
} from '../types'
import { useTextSelection, type Picked } from '../useTextSelection'

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
  { allowCrossSegment = true }: { allowCrossSegment?: boolean } = {},
) {
  const ctx = useContext(LookupContext)
  const onPick = useCallback(
    (p: Picked) => ctx?.request({ text, start: p.start, end: p.end, rect: p.rect }),
    [ctx, text],
  )
  useTextSelection(ref, onPick, { allowCrossSegment, enabled: !!ctx })
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
  learnerKey,
  play,
}: {
  children: ReactNode
  language: string
  unitId?: number | null
  learnerKey: string
  /** Plays an ORIGINAL-video-timeline window, when a player is available. */
  play?: (from: number, to: number) => void
}) {
  const [anchor, setAnchor] = useState<DOMRect | null>(null)
  const [pendingText, setPendingText] = useState<string | null>(null)
  const [selection, setSelection] = useState<string>('')
  const [result, setResult] = useState<LookupResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const reqId = useRef(0)

  const close = useCallback(() => {
    setAnchor(null)
    setResult(null)
    setError(null)
    setPendingText(null)
    setLoading(false)
    window.getSelection()?.removeAllRanges()
  }, [])

  const request = useCallback(
    ({ text, start, end, rect }: LookupRequest) => {
      const id = ++reqId.current
      setAnchor(rect)
      setSelection(text.slice(start, end) || text)
      setPendingText(text)
      setResult(null)
      setError(null)
      setLoading(true)

      lexicon
        .lookup({ language, text, char_start: start, char_end: end, unit_id: unitId ?? null })
        .then((r) => {
          if (id !== reqId.current) return // a newer selection superseded this one
          setResult(r)
          setSelection(r.selection)
        })
        .catch((e) => {
          if (id !== reqId.current) return
          setError(String(e))
        })
        .finally(() => {
          if (id === reqId.current) setLoading(false)
        })
    },
    [language, unitId],
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
        createPortal(
          <Popup
            anchor={anchor}
            selection={selection}
            result={result}
            error={error}
            loading={loading}
            language={language}
            unitId={unitId}
            learnerKey={learnerKey}
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

function Popup({
  anchor,
  selection,
  result,
  error,
  loading,
  language,
  unitId,
  learnerKey,
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
  learnerKey: string
  play?: (from: number, to: number) => void
  onClose: () => void
}) {
  const [saved, setSaved] = useState<string | null>(null)
  const ref = useRef<HTMLDivElement | null>(null)
  const [flip, setFlip] = useState(false)
  // Measured height must live in state, not be read from the ref during render: on the
  // first pass the ref is null, and if the only state update here were `setFlip(false)`
  // React would bail out of the re-render, so the clamp below would never see a real
  // height and a tall popup would overflow the viewport.
  const [height, setHeight] = useState(0)

  useEffect(() => {
    const h = ref.current?.offsetHeight ?? 0
    setHeight(h)
    // Flip above the selection when there isn't room below and there is room above.
    setFlip(anchor.bottom + GAP + h > window.innerHeight && anchor.top - GAP - h > 0)
  }, [anchor, result, loading])

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

  const save = async (headword: string, gloss: string | null) => {
    try {
      await lexicon.saveVocab({
        language,
        headword,
        gloss_en: gloss,
        example: result?.context ?? null,
        unit_id: unitId ?? null,
        learner_key: learnerKey,
      })
      setSaved(headword)
    } catch {
      setSaved(null)
    }
  }

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
              <button
                className="tr-save"
                onClick={() => save(headline.canonical, headline.gloss_en)}
                disabled={saved === headline.canonical}
              >
                {saved === headline.canonical ? '✓ saved' : '+ save expression'}
              </button>
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
              <button
                className="tr-btn"
                onClick={() => save(word.lemma || selection, word.gloss_en)}
                disabled={saved === (word.lemma || selection)}
              >
                {saved === (word.lemma || selection) ? '✓ saved' : '+ save word'}
              </button>
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

/**
 * Renders a passage as `[data-off]` segments so selections resolve to exact offsets,
 * optionally marking known expression spans.
 */
export function SelectableText({
  text,
  spans = [],
  className = '',
  lang,
}: {
  text: string
  /** Expression component spans to mark, from GET /api/units/:id/expressions. */
  spans?: number[][]
  className?: string
  lang?: string
}) {
  const ref = useRef<HTMLDivElement | null>(null)
  const { activeSpans } = useLookupOn(ref, text)

  const marks = useMemo(() => normalizeSpans(spans, text.length), [spans, text.length])
  const active = useMemo(() => normalizeSpans(activeSpans, text.length), [activeSpans, text.length])

  const isActive = (s: number, e: number) => active.some(([as, ae]) => s < ae && as < e)

  const nodes: ReactNode[] = []
  let cursor = 0
  marks.forEach(([s, e], i) => {
    if (s > cursor) {
      nodes.push(
        <span data-off={cursor} key={`p${i}`}>
          {text.slice(cursor, s)}
        </span>,
      )
    }
    nodes.push(
      <span
        data-off={s}
        className={`mwe ${isActive(s, e) ? 'mwe-active' : ''}`}
        key={`m${i}`}
      >
        {text.slice(s, e)}
      </span>,
    )
    cursor = e
  })
  if (cursor < text.length) {
    nodes.push(
      <span data-off={cursor} key="tail">
        {text.slice(cursor)}
      </span>,
    )
  }

  return (
    <div ref={ref} className={`selectable ${className}`} lang={lang}>
      {nodes}
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
