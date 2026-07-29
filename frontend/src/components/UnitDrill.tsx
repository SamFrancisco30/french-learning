import { useCallback, useEffect, useMemo, useState } from 'react'
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
import { LookupProvider, SelectableText } from './Lookup'

interface Props {
  unitId: number
  lessonTitle: string
  language: string
  learnerKey: string
  onExit: () => void
}

export function UnitDrill({ unitId, lessonTitle, language, learnerKey, onExit }: Props) {
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
  const player = useClipPlayer(unit.start_s, unit.end_s)

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

  const revealTranscript = useCallback(async () => {
    setShowTranscript(true)
    if (!transcript) {
      try {
        setTranscript(await api.transcript(unit.id))
      } catch (e) {
        setError(String(e))
      }
    }
  }, [transcript, unit.id, setShowTranscript, setTranscript, setError])

  const done = unit.exercises.filter((e) => results[e.id])
  const totalScore = done.reduce((s, e) => s + results[e.id].score, 0)

  const answeredAll = done.length === unit.exercises.length && unit.exercises.length > 0

  return (
    <LookupProvider
      language={language}
      unitId={unit.id}
      learnerKey={learnerKey}
      play={player.playWindow}
    >
      {error && <div className="error">{error}</div>}

      <div className="crumbs">
        <button onClick={onExit}>← {lessonTitle}</button>
        <span>/</span>
        <span>
          Unit {unit.idx + 1} · {fmt(unit.start_s)}–{fmt(unit.end_s)}
        </span>
      </div>

      {/* ---- player ---- */}
      <div className="player">
        <div className="player-row">
          <button className="play-btn" onClick={player.toggle} title="Play / pause">
            {player.playing ? '❚❚' : '▶'}
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
          <div className="rates">
            {[0.75, 0.9, 1].map((r) => (
              <button
                key={r}
                className={`rate-btn ${player.rate === r ? 'on' : ''}`}
                onClick={() => player.setRate(r)}
                title={`${r}× speed`}
              >
                {r}×
              </button>
            ))}
          </div>
          <button className="replay" onClick={player.restart} title="Play from the start">
            ↺ restart
          </button>
        </div>
        <div className="player-hint">
          {unit.cefr} · {unit.wpm?.toFixed(0)} words/min · {player.replays} replays
          {' · '}slow it to 0.75× first, then confirm at 1×
        </div>
        {unit.clip_url && (
          <audio ref={player.ref} src={unit.clip_url} preload="auto" />
        )}
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

      {showTranscript && (
        <div className="transcript">
          <div className="label">
            Transcript {transcript ? `· ${transcript.asr_backend}/${transcript.asr_model}` : ''}
            {markSpans.length > 0 && ` · ${exprSpans.length} expressions marked`}
            {' · select any word to translate'}
          </div>
          {transcript ? (
            <SelectableText text={transcript.text} spans={markSpans} lang={language} />
          ) : (
            'Loading…'
          )}
        </div>
      )}
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
