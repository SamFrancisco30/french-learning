import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, dictation } from '../api'
import { SpeedSlider } from '../components/SpeedSlider'
import { fmt } from '../useClipPlayer'
import type {
  AttemptResult,
  DictationAudio,
  DictationInventory,
  DictationItem,
  DictationLevel,
  DictationMode,
  DictationNext,
  DictationWord,
} from '../types'

/**
 * Dictation: hear it, type it, see exactly what you got wrong.
 *
 * Two modes, because they train different things. A SENTENCE is a memory-and-orthography task —
 * hold one clause, write it correctly. A PARAGRAPH adds the thing sentence mode cannot: keeping
 * structure across sentence boundaries while your hands are busy. They are separate ladders for
 * the same reason, since being comfortable at B2 sentences says little about B2 paragraphs.
 *
 * The level is derived from the learner's own history and always shown with its reason. An
 * adaptive system that silently changes what it gives you feels broken rather than clever, so the
 * level, the reason and a manual override are all on screen.
 */

const MODES: { key: DictationMode; label: string; hint: string }[] = [
  {
    key: 'sentence',
    label: 'Sentence',
    hint: 'One sentence at a time — spelling is the point.',
  },
  {
    key: 'paragraph',
    label: 'Paragraph',
    hint: 'Several in one go — harder, and builds stamina.',
  },
]

/** Verdicts that are wholly correct, so the report can lead with what needs work. */
const CLEAN = new Set(['exact', 'case'])

const VERDICT_LABEL: Record<string, string> = {
  exact: 'correct',
  case: 'capitalisation',
  accent: 'accent',
  typo: 'spelling',
  elision: 'elision',
  ending: 'verb ending',
  homophone: 'homophone',
  wrong: 'wrong word',
  missing: 'missed',
  added: 'not in the audio',
}

export function DictationPage({
  language,
  learnerKey,
}: {
  language: string
  learnerKey: string
}) {
  const [mode, setMode] = useState<DictationMode>('sentence')
  const [override, setOverride] = useState<string | null>(null)
  const [next, setNext] = useState<DictationNext | null>(null)
  const [inventory, setInventory] = useState<DictationInventory | null>(null)
  const [levels, setLevels] = useState<DictationLevel[]>([])
  const [typed, setTyped] = useState('')
  const [result, setResult] = useState<AttemptResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const box = useRef<HTMLTextAreaElement | null>(null)

  useEffect(() => {
    dictation.inventory(language).then(setInventory).catch(() => undefined)
  }, [language])

  const load = useCallback(
    async (m: DictationMode, lvl: string | null) => {
      setResult(null)
      setTyped('')
      setError(null)
      setNext(null)
      try {
        const n = await dictation.next(m, learnerKey, language, lvl)
        setNext(n)
        dictation.levels(learnerKey).then(setLevels).catch(() => undefined)
        // Focus the box, not the play button: the learner will reach for audio themselves, and
        // landing in the field means the keyboard is already where the work happens.
        requestAnimationFrame(() => box.current?.focus())
      } catch (e) {
        setError(String(e))
      }
    },
    [learnerKey, language],
  )

  useEffect(() => {
    void load(mode, override)
  }, [mode, override, load])

  const item = next?.item ?? null

  const submit = useCallback(async () => {
    if (!item || busy || !typed.trim()) return
    setBusy(true)
    try {
      const res = await api.submit(item.exercise_id, { text: typed }, 0, learnerKey)
      setResult(res)
      dictation.levels(learnerKey).then(setLevels).catch(() => undefined)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }, [item, busy, typed, learnerKey])

  const modeLevel = levels.find((l) => l.mode === mode)

  return (
    <>
      <div className="pagehead">
        <h2>Dictation</h2>
        <p>
          Listen and type what you hear. Spelling counts here — unlike the listening drills, this
          is where accents, homophones and verb endings are the point. Every miss is named, so you
          can see whether you misheard or just misspelled.
        </p>
      </div>

      <div className="dict-modes">
        {MODES.map((m) => (
          <button
            key={m.key}
            className={`dict-mode ${mode === m.key ? 'on' : ''}`}
            onClick={() => setMode(m.key)}
          >
            {/* The count sits with the name rather than on a line of its own — it is a property of
                the mode, not a separate fact about it, and it was costing a third of the card. */}
            <span className="dict-mode-head">
              <span className="dict-mode-label">{m.label}</span>
              {inventory && (
                <span className="dict-mode-count">{inventory.totals[m.key] ?? 0} items</span>
              )}
            </span>
            <span className="dict-mode-hint">{m.hint}</span>
          </button>
        ))}
      </div>

      <LevelBar
        level={modeLevel}
        served={next?.served_level ?? null}
        offLevel={!!next?.off_level}
        remaining={next?.remaining_at_level ?? 0}
        repeat={!!next?.repeat}
        override={override}
        levels={inventory?.levels ?? []}
        counts={inventory?.by_mode?.[mode] ?? {}}
        onOverride={setOverride}
      />

      {error && <div className="error">{error}</div>}

      {!item && !error && <div className="empty">Finding something at your level…</div>}

      {item && (
        <DictationDrill
          key={item.exercise_id}
          item={item}
          typed={typed}
          setTyped={setTyped}
          onSubmit={submit}
          busy={busy}
          result={result}
          onNext={() => load(mode, override)}
          boxRef={box}
        />
      )}
    </>
  )
}

function LevelBar({
  level,
  served,
  offLevel,
  remaining,
  repeat,
  override,
  levels,
  counts,
  onOverride,
}: {
  level: DictationLevel | undefined
  served: string | null
  offLevel: boolean
  remaining: number
  repeat: boolean
  override: string | null
  levels: string[]
  counts: Record<string, number>
  onOverride: (l: string | null) => void
}) {
  return (
    <div className="dict-level">
      <div className="dict-level-main">
        <span className="chip level">{served ?? level?.level ?? '—'}</span>
        <span className="dict-level-reason">
          {override
            ? `pinned to ${override} — the ladder is paused`
            : (level?.reason ?? 'working out your level…')}
          {offLevel && !override && ` · nothing left at ${level?.level}, so this is the closest`}
          {repeat && ' · you have done this one before'}
          {!repeat && remaining > 0 && ` · ${remaining} unseen at this level`}
        </span>
      </div>
      <div className="dict-level-pick">
        <span className="bar-label">level</span>
        <button
          className={`rate-btn ${override === null ? 'on' : ''}`}
          onClick={() => onOverride(null)}
          title="Let the app follow your scores"
        >
          auto
        </button>
        {levels.map((l) => (
          <button
            key={l}
            className={`rate-btn ${override === l ? 'on' : ''}`}
            disabled={!counts[l]}
            onClick={() => onOverride(l)}
            title={counts[l] ? `${counts[l]} items` : 'nothing at this level yet'}
          >
            {l}
          </button>
        ))}
      </div>
    </div>
  )
}

function DictationDrill({
  item,
  typed,
  setTyped,
  onSubmit,
  busy,
  result,
  onNext,
  boxRef,
}: {
  item: DictationItem
  typed: string
  setTyped: (s: string) => void
  onSubmit: () => void
  busy: boolean
  result: AttemptResult | null
  onNext: () => void
  boxRef: React.RefObject<HTMLTextAreaElement | null>
}) {
  // The keyboard shortcut announces itself when an item opens and then gets out of the way. The
  // component is keyed on the exercise id in the parent, so this remounts per item and the hint
  // reappears for each new one — which is what makes it a reminder rather than a permanent label.
  const [hintOn, setHintOn] = useState(true)
  useEffect(() => {
    const t = window.setTimeout(() => setHintOn(false), 3800)
    return () => window.clearTimeout(t)
  }, [])

  // Dictation plays the item's OWN audio file rather than a window inside the unit clip. It has to:
  // the punctuation announcements are spliced into that file, so there is no window of the original
  // that contains them, and once the server owns the cutting the client no longer needs a time map.
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const [speed, setSpeed] = useState(1)
  const [punctuation, setPunctuation] = useState(true)
  const [audio, setAudio] = useState<DictationAudio | null>(null)
  const [loadingAudio, setLoadingAudio] = useState(false)
  const [audioError, setAudioError] = useState<string | null>(null)
  const [plays, setPlays] = useState(0)
  // Read from the element, not the API: only the browser knows the delivered file's length.
  const [heard, setHeard] = useState<number | null>(null)

  useEffect(() => {
    let live = true
    setLoadingAudio(true)
    setAudioError(null)
    dictation
      .audio(item.exercise_id, speed, punctuation)
      .then((a) => live && setAudio(a))
      .catch((e) => live && setAudioError(String(e)))
      .finally(() => live && setLoadingAudio(false))
    return () => {
      live = false
    }
  }, [item.exercise_id, speed, punctuation])

  const playItem = useCallback(() => {
    const el = audioRef.current
    if (!el) return
    el.currentTime = 0
    setPlays((n) => n + 1)
    void el.play()
  }, [])

  // Ctrl/Cmd+Enter submits, so the learner never has to leave the keyboard, and plain Enter
  // stays available for line breaks inside a paragraph.
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      if (!result) onSubmit()
      else onNext()
    }
  }

  return (
    <div className="card">
      <div className="ex-head">
        <div>
          <div className="ex-kind">
            {item.mode === 'sentence' ? 'Sentence dictation' : 'Paragraph dictation'}
            {item.word_count ? ` · ${item.word_count} words` : ''}
            {item.sentence_count && item.sentence_count > 1
              ? ` · ${item.sentence_count} sentences`
              : ''}
          </div>
          <div className="ex-prompt">{item.prompt}</div>
          {item.lesson_title && (
            <div className="sub">
              from “{item.lesson_title}”
              {heard ? ` · ${fmt(heard)} of audio` : ''}
            </div>
          )}
        </div>
      </div>

      <div className="dict-player">
        <button
          className="play-btn"
          onClick={playItem}
          disabled={loadingAudio || !audio?.url}
          title="Play the passage"
        >
          ▶
        </button>
        {/* Glyph only, the same compact control the listening transport uses — the word "replay" was
            costing 60px and making this button the tallest thing in the row, which set the row's
            height on its own. The accessible name carries what the glyph cannot say. */}
        <button
          className="replay icon"
          onClick={playItem}
          disabled={loadingAudio || !audio?.url}
          title="Play from the start"
          aria-label="Play from the start"
        >
          ↺
        </button>
        <button
          className={`glass-btn ${punctuation ? 'on' : ''}`}
          onClick={() => setPunctuation((p) => !p)}
          disabled={loadingAudio}
          title={
            punctuation
              ? 'The punctuation is read aloud, as in a dictée — turn it off to work it out yourself'
              : 'Have the punctuation read aloud: “virgule”, “point”'
          }
        >
          , point
        </button>
        {/* The speed control belongs on this line, not on one of its own — same as the listening
            transport, which this now matches. */}
        <SpeedSlider speed={speed} onChange={setSpeed} disabled={loadingAudio} />
        <span className="dict-meta">
          {loadingAudio
            ? 'preparing the audio…'
            : `${plays} plays${heard ? ` · ${fmt(heard)}` : ''}`}
        </span>
        {audioError && <span className="dict-meta">audio unavailable — {audioError}</span>}
        {audio?.url && (
          <audio
            ref={audioRef}
            src={audio.url}
            preload="auto"
            onLoadedMetadata={(e) => setHeard(e.currentTarget.duration)}
          />
        )}
      </div>

      <textarea
        ref={boxRef}
        className="dict-input"
        value={typed}
        onChange={(e) => setTyped(e.target.value)}
        onKeyDown={onKeyDown}
        disabled={!!result}
        rows={item.mode === 'paragraph' ? 7 : 3}
        placeholder="Type what you hear, with accents and punctuation…"
        spellCheck={false}
        autoComplete="off"
        lang="fr"
      />

      {!result ? (
        <div className="actions">
          <button className="btn" onClick={onSubmit} disabled={busy || !typed.trim()}>
            {busy ? 'Checking…' : 'Check'}
          </button>
          {/*
            A shortcut worth knowing once, not worth reading every time. It shows itself when the
            item opens and then fades, rather than sitting beside the button for the whole exercise.

            Always rendered and hidden on a class, never unmounted: an element that leaves the DOM
            cannot fade, and it would also take its space with it and shift the button sideways
            mid-exercise. Absolutely positioned for the same reason — it occupies no layout at all,
            so its arrival and departure move nothing.
          */}
          <span className={`kbd-hint ${hintOn ? 'on' : ''}`} aria-hidden="true">
            <kbd>{navigator.platform.includes('Mac') ? '⌘' : 'Ctrl'}</kbd>+<kbd>Enter</kbd> to
            check
          </span>
        </div>
      ) : (
        <DictationFeedback result={result} onNext={onNext} onReplay={playItem} />
      )}
    </div>
  )
}

function DictationFeedback({
  result,
  onNext,
  onReplay,
}: {
  result: AttemptResult
  onNext: () => void
  onReplay: () => void
}) {
  const words = result.feedback.words ?? []
  const counts = result.feedback.counts ?? {}
  const missed = useMemo(() => words.filter((w) => !CLEAN.has(w.verdict)), [words])
  const punct = result.feedback.punctuation_missing ?? {}

  const cls = result.is_correct ? 'correct' : result.score >= 0.6 ? 'partial' : 'wrong'

  return (
    <div className={`feedback ${cls}`}>
      <strong>
        {result.is_correct
          ? '✓ Clean'
          : `${Math.round(result.score * 100)}% — ${missed.length} to look at`}
      </strong>

      {/* The whole reference, marked up. Reading your own errors in place is how the correction
          sticks; a list of wrong words out of context does not. */}
      <div className="dict-diff" lang="fr">
        {words.map((w, i) => (
          <WordMark key={i} w={w} />
        ))}
      </div>

      {missed.length > 0 && (
        <ul className="dict-notes">
          {missed.map((w, i) => (
            <li key={i}>
              <span className={`dict-tag ${w.verdict}`}>{VERDICT_LABEL[w.verdict] ?? w.verdict}</span>{' '}
              {w.verdict === 'added' ? (
                <>
                  you wrote <b>{w.given}</b> — {w.note}
                </>
              ) : w.verdict === 'missing' ? (
                <>
                  <b>{w.expected}</b> was said and is not in your answer
                </>
              ) : (
                <>
                  <b>{w.expected}</b>, you wrote <i>{w.given}</i>
                  {w.note ? ` — ${w.note}` : ''}
                </>
              )}
            </li>
          ))}
        </ul>
      )}

      {Object.keys(punct).length > 0 && (
        <div className="why">
          Punctuation you did not write: {Object.entries(punct).map(([c, n]) => `${n}× ${c}`).join(', ')}
          {' — '}not counted in the score, since a comma and a clause break sound the same.
        </div>
      )}

      <div className="dict-counts">
        {Object.entries(counts)
          .sort((a, b) => b[1] - a[1])
          .map(([k, n]) => (
            <span className="chip" key={k}>
              {n} {VERDICT_LABEL[k] ?? k}
            </span>
          ))}
      </div>

      <div className="actions">
        <button className="btn" onClick={onNext}>
          Next →
        </button>
        <button className="btn ghost" onClick={onReplay}>
          ↺ hear it again
        </button>
      </div>
    </div>
  )
}

function WordMark({ w }: { w: DictationWord }) {
  if (w.verdict === 'added') {
    return (
      <span className="dw added" title={`you added "${w.given}"`}>
        {w.given}
      </span>
    )
  }
  if (CLEAN.has(w.verdict)) return <span className="dw ok">{w.expected}</span>
  return (
    <span className={`dw ${w.verdict}`} title={w.note ?? VERDICT_LABEL[w.verdict]}>
      {w.expected}
      {w.given && w.verdict !== 'missing' && <i className="dw-given">{w.given}</i>}
    </span>
  )
}
