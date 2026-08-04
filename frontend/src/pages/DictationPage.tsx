import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { LockedError, api, dictation } from '../api'
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
  navigate,
}: {
  language: string
  learnerKey: string
  navigate: (to: string) => void
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
  // Dictation items are cut from listening units, so a learner with nothing unlocked has nothing to
  // dictate. That is an entitlement state, not a failure, and it needs its own message rather than
  // the raw "LockedError: 402" that a generic catch produced.
  const [locked, setLocked] = useState(false)
  // Whichever control takes the first keystroke: the first word field, or the textarea when an
  // item arrives without word lengths. The parent only ever focuses it.
  const box = useRef<HTMLElement | null>(null)

  useEffect(() => {
    dictation.inventory(language).then(setInventory).catch(() => undefined)
  }, [language])

  const load = useCallback(
    async (m: DictationMode, lvl: string | null) => {
      setResult(null)
      setTyped('')
      setError(null)
      setLocked(false)
      setNext(null)
      try {
        const n = await dictation.next(m, learnerKey, language, lvl)
        setNext(n)
        dictation.levels(learnerKey).then(setLevels).catch(() => undefined)
        // Focus the box, not the play button: the learner will reach for audio themselves, and
        // landing in the field means the keyboard is already where the work happens.
        requestAnimationFrame(() => box.current?.focus())
      } catch (e) {
        if (e instanceof LockedError) setLocked(true)
        else setError(String(e))
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
      {/* Everything that frames the exercise, on one line: which length, what level, and the
          override. Each of the three used to bring its own explanatory text — two mode hints and a
          sentence of reasoning — which is what made it two full rows. The text is all still here,
          on hover, where it costs nothing until it is wanted. */}
      <div className="dict-bar">
        <div className="dict-modes" role="group" aria-label="Dictation length">
          {MODES.map((m) => (
            <button
              key={m.key}
              className={`dict-mode has-tip ${mode === m.key ? 'on' : ''}`}
              onClick={() => setMode(m.key)}
              aria-pressed={mode === m.key}
            >
              <span className="dict-mode-label">{m.label}</span>
              {inventory && (
                <span className="dict-mode-count">{inventory.totals[m.key] ?? 0}</span>
              )}
              <span className="tip" aria-hidden="true">{m.hint}</span>
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
      </div>

      {locked && (
        <div className="dict-locked">
          <h3>Unlock a recording first</h3>
          <p className="muted">
            Dictation practises the recordings you have opened, so there is nothing to dictate yet.
            Open one from the listening library and its sentences appear here.
          </p>
          <div className="actions">
            <button className="btn" onClick={() => navigate('/listening')}>
              Browse recordings →
            </button>
          </div>
        </div>
      )}

      {error && <div className="error">{error}</div>}

      {!item && !error && !locked && (
        <div className="empty">Finding something at your level…</div>
      )}

      {item && (
        <DictationDrill
          key={item.exercise_id}
          item={item}
          navigate={navigate}
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
  const reason = (
    <>
      {override
        ? `Pinned to ${override} — the ladder is paused.`
        : (level?.reason ?? 'Working out your level…')}
      {offLevel && !override && ` Nothing left at ${level?.level}, so this is the closest.`}
      {repeat && ' You have done this one before.'}
      {!repeat && remaining > 0 && ` ${remaining} unseen at this level.`}
    </>
  )

  return (
    <>
      {/* The level, with the reasoning behind it on hover. The reasoning is the part that changes —
          "no attempts yet", "nothing left at A2", "you have done this one before" — and it is worth
          reading when you wonder why, not on every item. */}
      <span className="dict-level-chip has-tip">
        <span className="chip level">{served ?? level?.level ?? '—'}</span>
        <span className="tip" aria-hidden="true">{reason}</span>
      </span>

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
    </>
  )
}

function DictationDrill({
  item,
  navigate,
  typed,
  setTyped,
  onSubmit,
  busy,
  result,
  onNext,
  boxRef,
}: {
  item: DictationItem
  navigate: (to: string) => void
  typed: string
  setTyped: (s: string) => void
  onSubmit: () => void
  busy: boolean
  result: AttemptResult | null
  onNext: () => void
  boxRef: React.RefObject<HTMLElement | null>
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
            {/*
              Counted from the hints, not from `word_count`.

              The two genuinely disagree: `word_count` is computed at curation time by a different
              tokenizer and feeds the difficulty score, so it counts things the learner does not type
              as separate words — a stray « counts, and an elision may split in two. Printing it
              beside the underscore runs put "22 words" above 21 runs, which reads as a bug in the
              hints. The runs are exactly what has to be typed, so they are the honest number.
            */}
            {item.word_lengths?.length
              ? ` · ${item.word_lengths.length} words`
              : item.word_count
                ? ` · ${item.word_count} words`
                : ''}
            {item.sentence_count && item.sentence_count > 1
              ? ` · ${item.sentence_count} sentences`
              : ''}
          </div>
          <div className="ex-prompt">{item.prompt}</div>
          {/* One word instead of a line. The full provenance — which lesson, at what moment — was a
              sentence longer than the sentence being dictated, and it is reference material: worth
              having, not worth reading every time. Hovering says where this came from; clicking opens
              that passage in the listening drill at the moment it was taken from. */}
          {item.lesson_title && (
            <div className="dict-source">
              {item.lesson_id != null ? (
                <button
                  className="has-tip dict-source-link"
                  onClick={() =>
                    navigate(
                      `/listening/lesson/${item.lesson_id}/unit/${item.unit_id}` +
                        (item.audio_start_s != null ? `/at/${item.audio_start_s}` : ''),
                    )
                  }
                >
                  source
                  <span className="tip" aria-hidden="true">
                    “{item.lesson_title}”
                    {item.audio_start_s != null && ` · at ${fmt(item.audio_start_s)}`}
                    <em>Open this passage in the listening drill.</em>
                  </span>
                </button>
              ) : (
                <span className="has-tip dict-source-link is-plain">
                  source
                  <span className="tip" aria-hidden="true">“{item.lesson_title}”</span>
                </span>
              )}
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

      {/*
        Write on the lines. The textarea remains only as a fallback for an item that arrives with no
        word lengths — an older cached payload, say — because without lengths there are no fields to
        lay out, and a dictation with nowhere to type would be worse than a plain box.
      */}
      {(item.word_lengths?.length ?? 0) > 0 ? (
        <WordFields
          lengths={item.word_lengths}
          typed={typed}
          setTyped={setTyped}
          disabled={!!result}
          firstRef={boxRef}
          onShortcut={onKeyDown}
        />
      ) : (
        <textarea
          ref={boxRef as React.RefObject<HTMLTextAreaElement | null>}
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
      )}

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
        <DictationFeedback
          result={result}
          onNext={onNext}
          onReplay={playItem}
          punctuationTypable={(item.word_lengths?.length ?? 0) === 0}
        />
      )}
    </div>
  )
}

function DictationFeedback({
  result,
  onNext,
  onReplay,
  punctuationTypable,
}: {
  result: AttemptResult
  onNext: () => void
  onReplay: () => void
  /** False when the word fields were used, since those accept letters only. */
  punctuationTypable: boolean
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

      {/* Only when there was somewhere to put it. With the word fields the learner types letters
          only, so listing the commas they "did not write" would be blaming them for a
          restriction the interface imposed. It is not scored either way. */}
      {punctuationTypable && Object.keys(punct).length > 0 && (
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

/**
 * The dictée itself: you write on the ruled lines, one field per word.
 *
 * There is no separate box any more. Each word is its own input, as wide as that word is long, with
 * a dash drawn under every character cell — so the underscores stay visible as you write over them
 * rather than being a legend above a box you type into somewhere else.
 *
 * The whole answer is still ONE string. Fields are a view of `typed` split on spaces, and every
 * edit rebuilds it with `join(' ')`; because a space can never be typed *into* a field, that
 * round-trip is exact. Grading, the reset on a new item, and the "nothing typed yet" check all keep
 * working on the same plain sentence they always did.
 *
 * Only lengths reach the client (see word_hint_lengths on the server), so the layout cannot leak
 * the answer. Punctuation is deliberately unrepresented — where the commas go is part of a dictée —
 * which is why a field accepts more characters than its width suggests: the comma after a word is
 * typed into that word's field, and the field grows to hold it.
 */
function WordFields({
  lengths,
  typed,
  setTyped,
  disabled,
  firstRef,
  onShortcut,
}: {
  lengths: number[]
  typed: string
  setTyped: (s: string) => void
  disabled: boolean
  firstRef: React.RefObject<HTMLElement | null>
  onShortcut: (e: React.KeyboardEvent) => void
}) {
  const parts = useMemo(() => {
    const split = typed.length > 0 ? typed.split(' ') : []
    return lengths.map((_, i) => split[i] ?? '')
  }, [typed, lengths])

  /**
   * Move focus `offset` fields along, walking the DOM rather than an array of refs.
   *
   * The refs version did not work. The `ref` callback is an inline arrow, so React gets a new
   * function every render and detaches the whole array (calling each ref with null) before
   * re-attaching — which left the slot empty at exactly the moment the auto-advance wanted to read
   * it, and focus silently stayed put. The fields are siblings in `.dict-write`, so the DOM already
   * holds the ordering reliably and needs nothing kept in sync.
   */
  const focusBy = (from: HTMLInputElement, offset: number) => {
    let el: Element | null = from
    for (let n = 0; n < Math.abs(offset) && el; n += 1) {
      el = offset > 0 ? el.nextElementSibling : el.previousElementSibling
    }
    if (!(el instanceof HTMLInputElement) || el.disabled) return
    el.focus()
    el.setSelectionRange(el.value.length, el.value.length)
  }

  /**
   * Write into field `i`, spilling any whitespace in the incoming value across the fields after it.
   *
   * The spill matters and is not theoretical. A keystroke space is caught by onKeyDown and moves the
   * cursor, but a space can reach a field without any keydown at all — pasting a phrase, an IME
   * committing a segment, or speech-to-text inserting a run of words. Stripping those spaces (the
   * first thing this did) silently concatenated the whole sentence into one field, so it read as the
   * field ignoring the space bar. Distributing them means every route to the same text lands the
   * same way, and pasting a sentence fills the exercise instead of destroying it.
   */
  const write = (el: HTMLInputElement, i: number, value: string) => {
    const next = [...parts]
    const chunks = value.split(/\s+/)
    if (chunks.length === 1) {
      next[i] = chunks[0]
    } else {
      chunks.forEach((chunk, offset) => {
        const at = i + offset
        if (at < next.length) next[at] = chunk
        // More words than fields: keep the overflow on the last field rather than dropping input
        // the learner can see they typed.
        else next[next.length - 1] += chunk
      })
      const landed = Math.min(i + chunks.length - 1, next.length - 1)
      // Follow the text, so typing continues where it left off rather than back at the start.
      focusBy(el, landed - i)
    }
    // Trailing empties are trimmed so an untouched exercise stays "" and the Check button stays off.
    setTyped(next.join(' ').replace(/\s+$/, ''))

    /*
      A finished word hands over to the next one on its own, so a whole sentence is typed without
      ever reaching for the space bar.

      Safe to key off the letter count because of what the grader does: words are scored,
      "PUNCTUATION AND CAPITALISATION ARE REPORTED, NOT SCORED" (see backend/app/skills/dictation/
      grading.py). So a field never needs to hold a comma, and being full really does mean done.

      Done here rather than in onKeyDown so it also fires for an IME commit and for speech-to-text,
      neither of which produces a keydown per character.

      Synchronous, NOT deferred to requestAnimationFrame. Deferring raced the keyboard: a fast typist
      got several more characters into the old field before focus moved, so "Les cinq mots" arrived as
      "Lescinqm" in a three-letter field. The next field already exists in the DOM, so there is
      nothing to wait for — React's following render only rewrites values, never focus.
    */
    if (chunks.length === 1 && next[i].length >= lengths[i] && i < lengths.length - 1) {
      focusBy(el, 1)
    }
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>, i: number) => {
    const el = e.currentTarget
    const atStart = el.selectionStart === 0 && el.selectionEnd === 0
    const atEnd = el.selectionStart === el.value.length && el.selectionEnd === el.value.length

    // Space and Enter both advance. Enter deliberately does NOT submit: ⌘+Enter is the documented
    // way to check, and a plain Enter that submitted would end the exercise on a stray keystroke
    // halfway through a sentence.
    if (e.key === ' ' || (e.key === 'Enter' && !e.metaKey && !e.ctrlKey)) {
      e.preventDefault()
      focusBy(el, 1)
      return
    }
    if (e.key === 'Backspace' && el.value === '' && i > 0) {
      e.preventDefault()
      focusBy(el, -1)
      return
    }
    if (e.key === 'ArrowLeft' && atStart && i > 0) {
      e.preventDefault()
      focusBy(el, -1)
      return
    }
    if (e.key === 'ArrowRight' && atEnd && i < lengths.length - 1) {
      e.preventDefault()
      focusBy(el, 1)
      return
    }
    onShortcut(e)
  }

  return (
    <div className="dict-write" lang="fr">
      {lengths.map((n, i) => (
        <input
          key={i}
          ref={(el) => {
            if (i === 0) firstRef.current = el
          }}
          className="dict-word"
          /*
            Two custom properties, and they do different jobs. `--len` sizes the field and draws one
            dash per character. `--typed` widens it only once what has been typed outruns the hint,
            so a word plus its comma still fits without every field being padded "just in case".
          */
          style={{
            ['--len' as string]: n,
            ['--typed' as string]: parts[i]?.length ?? 0,
          }}
          value={parts[i] ?? ''}
          onChange={(e) => write(e.currentTarget, i, e.currentTarget.value)}
          onKeyDown={(e) => onKeyDown(e, i)}
          disabled={disabled}
          aria-label={`Word ${i + 1} of ${lengths.length}, ${n} letters`}
          spellCheck={false}
          autoComplete="off"
          autoCapitalize="off"
          inputMode="text"
        />
      ))}
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
