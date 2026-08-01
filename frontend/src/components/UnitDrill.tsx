import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, lexicon } from '../api'
import type {
  AttemptResult,
  Exercise,
  Transcript,
  UnitDetail,
  UnitExpressionSpan,
} from '../types'
import { fmt, useClipPlayer } from '../useClipPlayer'
import {
  ExerciseBody,
  KIND_LABEL,
  emptyResponse,
  isAnswered,
  toApiResponse,
} from './Exercises'
import { FollowProvider } from './Follow'
import { FollowTranscript } from './FollowTranscript'
import { LookupProvider, SelectableText } from './Lookup'
import { SpeedSlider } from './SpeedSlider'

interface Props {
  unitId: number
  lessonTitle: string
  /**
   * Original-video seconds to open at, when arriving from somewhere that already knows the moment —
   * the dictation source link. Absent for a normal visit, which starts at the top of the unit.
   */
  startAt?: number | null
  language: string
  learnerKey: string
  onExit: () => void
}

export function UnitDrill({ unitId, lessonTitle, language, learnerKey, onExit, startAt }: Props) {
  const [unit, setUnit] = useState<UnitDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [responses, setResponses] = useState<Record<number, unknown>>({})
  const [results, setResults] = useState<Record<number, AttemptResult>>({})
  const [busy, setBusy] = useState<number | null>(null)
  const [transcript, setTranscript] = useState<Transcript | null>(null)
  const [showTranscript, setShowTranscript] = useState(false)

  useEffect(() => {
    let live = true
    setUnit(null)
    setResponses({})
    setResults({})
    setShowTranscript(false)
    api
      .unit(unitId)
      .then((u) => {
        if (!live) return
        setUnit(u)
        setResponses(Object.fromEntries(u.exercises.map((e) => [e.id, emptyResponse(e)])))
      })
      .catch((e) => live && setError(String(e)))
    return () => {
      live = false
    }
  }, [unitId])

  return unit ? (
    <Drill
      unit={unit}
      lessonTitle={lessonTitle}
      startAt={startAt}
      language={language}
      learnerKey={learnerKey}
      onExit={onExit}
      responses={responses}
      setResponses={setResponses}
      results={results}
      setResults={setResults}
      busy={busy}
      setBusy={setBusy}
      transcript={transcript}
      setTranscript={setTranscript}
      showTranscript={showTranscript}
      setShowTranscript={setShowTranscript}
      error={error}
      setError={setError}
    />
  ) : (
    <>
      {error && <div className="error">{error}</div>}
      <div className="empty">Loading unit…</div>
    </>
  )
}

/** Split out so the audio hook can depend on the unit's real bounds. */
function Drill({
  unit,
  lessonTitle,
  startAt,
  language,
  learnerKey,
  onExit,
  responses,
  setResponses,
  results,
  setResults,
  busy,
  setBusy,
  transcript,
  setTranscript,
  showTranscript,
  setShowTranscript,
  error,
  setError,
}: {
  unit: UnitDetail
  lessonTitle: string
  startAt?: number | null
  language: string
  learnerKey: string
  onExit: () => void
  responses: Record<number, unknown>
  setResponses: React.Dispatch<React.SetStateAction<Record<number, unknown>>>
  results: Record<number, AttemptResult>
  setResults: React.Dispatch<React.SetStateAction<Record<number, AttemptResult>>>
  busy: number | null
  setBusy: (n: number | null) => void
  transcript: Transcript | null
  setTranscript: (t: Transcript | null) => void
  showTranscript: boolean
  setShowTranscript: (b: boolean) => void
  error: string | null
  setError: (e: string | null) => void
}) {
  // The player owns its own source now: slow speeds load a reshaped variant rather
  // than changing playbackRate.
  const player = useClipPlayer(unit.id, unit.start_s, unit.end_s, unit.clip_url)

  // Arriving from a dictation source link, open at the moment that sentence was taken from rather
  // than at the top of the unit.
  //
  // Re-applied on EVERY media load, not once, because the element loads more than one source on the
  // way in: the unit arrives with a fallback clip_url and the speed variant replaces it a moment
  // later, and a new src resets currentTime to 0 and pauses. A one-shot seek landed on the fallback
  // and was wiped when the variant took over, which is why this looked like "it just starts from the
  // beginning".
  //
  // It stops re-applying once the seek has actually stuck — once playback has carried past the
  // requested moment — so a later source change, such as choosing a slower speed, does not drag the
  // learner back here. That is a state about the audio, so it is checked against the audio rather
  // than against a timer.
  const pendingSeek = useRef(false)
  useEffect(() => {
    pendingSeek.current = startAt != null
  }, [unit.id, startAt])
  useEffect(() => {
    const el = player.ref.current
    if (el == null || startAt == null) return
    const apply = () => {
      if (pendingSeek.current) player.seekTo(startAt)
    }
    const settle = () => {
      // toOriginal maps playback seconds back onto the video's timeline, which is what startAt is in.
      if (player.toOriginal(el.currentTime) > startAt + 0.75) pendingSeek.current = false
    }
    if (el.readyState >= 1) apply()
    el.addEventListener('loadedmetadata', apply)
    el.addEventListener('timeupdate', settle)
    return () => {
      el.removeEventListener('loadedmetadata', apply)
      el.removeEventListener('timeupdate', settle)
    }
  }, [startAt, unit.id, player.src, player.ref, player.seekTo, player.toOriginal])

  // Known expression spans, so the transcript can mark them before anything is clicked.
  const [exprSpans, setExprSpans] = useState<UnitExpressionSpan[]>([])
  useEffect(() => {
    let live = true
    lexicon
      .unitExpressions(unit.id)
      .then((r) => live && setExprSpans(r.expressions))
      .catch(() => undefined) // decoration only; never block the drill on it
    return () => {
      live = false
    }
  }, [unit.id])

  const markSpans = useMemo(
    () => exprSpans.flatMap((e) => e.component_spans),
    [exprSpans],
  )

  const submit = useCallback(
    async (ex: Exercise) => {
      setBusy(ex.id)
      try {
        const res = await api.submit(
          ex.id,
          toApiResponse(ex, responses[ex.id]),
          player.replays,
          learnerKey,
        )
        setResults((prev) => ({ ...prev, [ex.id]: res }))
      } catch (e) {
        setError(String(e))
      } finally {
        setBusy(null)
      }
    },
    [responses, player.replays, learnerKey, setBusy, setResults, setError],
  )

  // Word timings are fetched up front, not on reveal, because the cloze passage wants to
  // follow the voice too and it is visible from the start. This is not a new spoiler: the
  // cloze exercise's own payload already carries the same text.
  useEffect(() => {
    let live = true
    setTranscript(null)
    api
      .transcript(unit.id)
      .then((t) => live && setTranscript(t))
      .catch(() => undefined) // the drill works without it; only following is lost
    return () => {
      live = false
    }
  }, [unit.id, setTranscript])

  const revealTranscript = useCallback(() => setShowTranscript(true), [setShowTranscript])

  const done = unit.exercises.filter((e) => results[e.id])
  const totalScore = done.reduce((s, e) => s + results[e.id].score, 0)

  const answeredAll = done.length === unit.exercises.length && unit.exercises.length > 0

  return (
    <LookupProvider
      language={language}
      unitId={unit.id}
      play={player.playWindow}
    >
      <FollowProvider
        text={transcript?.text ?? ''}
        words={transcript?.words ?? []}
        subscribe={player.subscribe}
        toOriginal={player.toOriginal}
        seekTo={player.seekTo}
      >
      {/* Fades the drill in when the unit's data lands, so the audio page arrives rather than
          replacing "Loading unit…" in a single frame. Opacity only — see the note on `route-in`:
          this element is an ancestor of both the sticky player and the fixed lookup popup, and a
          transform here would re-anchor that popup to this div instead of the viewport. */}
      <div className="route">
      {error && <div className="error">{error}</div>}

      <div className="crumbs">
        <button onClick={onExit}>← {lessonTitle}</button>
        <span>/</span>
        <span>
          Unit {unit.idx + 1} · {fmt(unit.start_s)}–{fmt(unit.end_s)}
        </span>
      </div>

      {/* ---- player ---- */}
      {/* One line. The transport, the speed control and the unit's own numbers used to occupy three
          stacked rows — scrubber, then a labelled slider with two end labels and a line of detail,
          then a meta line. On a 1208px frame that was mostly empty space held open by text that is
          only worth reading while you are changing something, so the slider's labels moved into a
          popup it raises on use and everything else fits across. */}
      <div className="player">
        <div className="player-row">
          <button className="play-btn" onClick={player.toggle} title="Play / pause">
            {/* Wrapped so it can be pushed under the glass: the beams are drawn by a pseudo-element
                ABOVE this, which is what makes the mark read as sitting inside the body rather than
                printed on the front of it. */}
            <span className="play-glyph">{player.playing ? '❚❚' : '▶'}</span>
          </button>
          <div
            className="scrub"
            onClick={(e) => {
              const r = e.currentTarget.getBoundingClientRect()
              player.seekFraction((e.clientX - r.left) / r.width)
            }}
          >
            <div
              className="scrub-fill"
              style={{ width: `${(player.position / player.duration) * 100}%` }}
            />
          </div>
          <span className="time">
            {fmt(player.position)} / {fmt(player.duration)}
          </span>
          {/* Glyph only. The word "restart" cost about 55px of a row that now has to hold the speed
              control too, and the accessible name carries what the glyph cannot say. */}
          <button
            className="replay icon"
            onClick={player.restart}
            title="Play from the start"
            aria-label="Play from the start"
          >
            ↺
          </button>

          <SpeedSlider
            speed={player.speed}
            onChange={player.setSpeed}
            disabled={player.loadingSpeed}
          />

          <div className="player-meta">
            {unit.cefr} · {unit.wpm?.toFixed(0)} wpm · {player.replays} replays
          </div>
        </div>
        {player.src && <audio ref={player.ref} src={player.src} preload="auto" />}
      </div>

      {unit.gist && (
        <div className="card">
          <div className="ex-kind">Before you listen</div>
          <SelectableText text={unit.gist} className="gist" lang="en" />
        </div>
      )}

      {/* ---- exercises ---- */}
      {unit.exercises.map((ex) => {
        const res = results[ex.id] ?? null
        const cls = !res ? '' : res.is_correct ? 'correct' : res.score > 0 ? 'partial' : 'wrong'
        return (
          <div className={`card ex ${cls}`} key={ex.id}>
            <div className="ex-head">
              <div>
                <div className="ex-kind">
                  {KIND_LABEL[ex.kind] ?? ex.kind}
                  {ex.generator === 'deterministic' && ' · timestamp-anchored'}
                </div>
                <div className="ex-prompt">{ex.prompt}</div>
              </div>
              {ex.audio_start_s !== null && ex.audio_end_s !== null && (
                <button
                  className="replay"
                  onClick={() => player.playWindow(ex.audio_start_s!, ex.audio_end_s!)}
                  title="Play just the part this question is about"
                >
                  ▶ {fmt(ex.audio_start_s - unit.start_s)}
                </button>
              )}
            </div>

            <ExerciseBody
              ex={ex}
              result={res}
              response={responses[ex.id]}
              setResponse={(r) => setResponses((prev) => ({ ...prev, [ex.id]: r }))}
              play={player.playWindow}
              // Guarded here so keyboard submission can never do what the disabled Check
              // button wouldn't — no empty attempts, no double submits.
              onSubmit={() => {
                if (!res && busy !== ex.id && isAnswered(ex, responses[ex.id])) submit(ex)
              }}
            />

            {!res ? (
              <div className="actions">
                <button
                  className="btn"
                  disabled={busy === ex.id || !isAnswered(ex, responses[ex.id])}
                  onClick={() => submit(ex)}
                >
                  {busy === ex.id ? 'Checking…' : 'Check'}
                </button>
                {!isAnswered(ex, responses[ex.id]) ? (
                  <span className="bar-label">answer to enable</span>
                ) : (
                  ex.kind === 'cloze' && (
                    <span className="bar-label">
                      <kbd>Enter</kbd> next blank · <kbd>Enter</kbd> on the last one checks
                    </span>
                  )
                )}
              </div>
            ) : (
              <Feedback result={res} />
            )}
          </div>
        )
      })}

      {/* ---- wrap-up ---- */}
      {done.length > 0 && (
        <div className="summary">
          <div className="stat">
            <div className="n">
              {done.length}/{unit.exercises.length}
            </div>
            <div className="l">answered</div>
          </div>
          <div className="stat">
            <div className="n">{Math.round((totalScore / Math.max(1, done.length)) * 100)}%</div>
            <div className="l">mean score</div>
          </div>
          <div className="stat">
            <div className="n">{done.filter((e) => results[e.id].is_correct).length}</div>
            <div className="l">fully correct</div>
          </div>
          <div className="stat">
            <div className="n">{player.replays}</div>
            <div className="l">replays</div>
          </div>
        </div>
      )}

      <div className="actions">
        {!showTranscript ? (
          <button className="btn ghost" onClick={revealTranscript}>
            {answeredAll ? 'Reveal transcript' : 'Reveal transcript (spoiler)'}
          </button>
        ) : (
          <button className="btn ghost" onClick={() => setShowTranscript(false)}>
            Hide transcript
          </button>
        )}
        <button className="btn ghost" onClick={onExit}>
          Back to units
        </button>
      </div>

      {showTranscript &&
        (transcript ? (
          <FollowTranscript
            transcript={transcript}
            markSpans={markSpans}
            expressionCount={exprSpans.length}
            language={language}
            playing={player.playing}
          />
        ) : (
          <div className="transcript">
            <div className="label">Transcript</div>
            Loading…
          </div>
        ))}
      </div>
      </FollowProvider>
    </LookupProvider>
  )
}

function Feedback({ result }: { result: AttemptResult }) {
  const cls = result.is_correct ? 'correct' : result.score > 0 ? 'partial' : 'wrong'
  const notes = useMemo(
    () =>
      (result.feedback.blanks ?? [])
        .filter((b) => b.correct && b.message)
        .map((b) => `#${b.index + 1}: ${b.message}`),
    [result.feedback.blanks],
  )

  return (
    <div className={`feedback ${cls}`}>
      <strong>
        {result.is_correct
          ? '✓ Correct'
          : result.score > 0
            ? `Partly right — ${Math.round(result.score * 100)}%`
            : '✗ Not quite'}
      </strong>
      {result.feedback.total !== undefined && (
        <> ({result.feedback.correct_count}/{result.feedback.total})</>
      )}
      {notes.length > 0 && (
        <ul>
          {notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
      {result.explanation && <div className="why">{result.explanation}</div>}
    </div>
  )
}
