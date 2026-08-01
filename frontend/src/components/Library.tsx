import { useEffect, useState } from 'react'
import { api } from '../api'
import { useAuth } from '../auth/AuthContext'
import { ALL, TopicArt, hasPhoto, topicMeta } from '../topics'
import type { LessonDetail, LessonSummary, Progress } from '../types'
import { fmt } from '../useClipPlayer'
import { LockChip, QuotaBar, UnlockGate } from './Entitlement'

export function ProgressSummary({ progress }: { progress: Progress | null }) {
  if (!progress || progress.attempts === 0) return null
  return (
    <div className="summary">
      <div className="stat">
        <div className="n">{progress.attempts}</div>
        <div className="l">attempts</div>
      </div>
      <div className="stat">
        <div className="n">{Math.round(progress.accuracy * 100)}%</div>
        <div className="l">accuracy</div>
      </div>
      <div className="stat">
        <div className="n">{Math.round(progress.mean_score * 100)}%</div>
        <div className="l">mean score</div>
      </div>
      <div className="stat">
        <div className="n">{progress.units_touched}</div>
        <div className="l">units studied</div>
      </div>
      <div style={{ flex: 1, minWidth: 220 }}>
        <div className="bars">
          {Object.entries(progress.by_kind).map(([kind, s]) => (
            <div className="bar-row" key={kind}>
              <span className="bar-label">{kind}</span>
              <span className="bar-track">
                <span className="bar-fill" style={{ width: `${s.mean_score * 100}%` }} />
              </span>
              <span className="bar-val">{Math.round(s.mean_score * 100)}%</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

/**
 * The lessons inside one topic. `topic` is a slug, or ALL.slug for the whole library.
 *
 * Lessons arrive as a prop rather than being fetched here: the topic grid needs the same list
 * to count by subject, so fetching once in the page keeps the two views consistent and makes
 * going back to the grid instant.
 */
export function LessonLibrary({
  lessons,
  topic,
  language,
  onOpen,
  onBack,
}: {
  lessons: LessonSummary[]
  topic: string
  language: string
  onOpen: (lesson: LessonSummary) => void
  onBack: () => void
}) {
  const meta = topicMeta(topic)
  const shown =
    topic === ALL.slug ? lessons : lessons.filter((l) => (l.topic || 'other') === topic)

  return (
    <>
      <div className="crumbs">
        <button onClick={onBack}>← Topics</button>
        <span>/</span>
        <span>{meta.label}</span>
      </div>

      <div className={`topic-banner ${hasPhoto(topic) ? 'has-photo' : ''}`}>
            <span className="topic-art" aria-hidden="true">
          <TopicArt slug={topic} />
        </span>
        <span className="topic-body">
          <span className="topic-head">
            <span className="topic-label">{meta.label}</span>
            <span className="topic-native" lang={language}>
              {meta.native}
            </span>
          </span>
          <span className="topic-blurb">{meta.blurb}</span>
        </span>
      </div>

      {shown.length === 0 && (
        <div className="empty">
          Nothing ingested under this topic yet. Add one with:
          <br />
          <code>python scripts/ingest.py add "&lt;youtube-url&gt;" --topic {topic}</code>
        </div>
      )}

      {shown.map((l, i) => (
        <div
          className="card clickable enters"
          style={{ ['--i' as string]: i }}
          key={l.id}
          onClick={() => onOpen(l)}
        >
          <h3>{l.title}</h3>
          <div className="sub">
            {l.source.channel ?? 'unknown channel'}
            {l.duration_s ? ` · ${fmt(l.duration_s)}` : ''}
          </div>
          <div className="meta-row">
            {l.cefr && (
              <span className="chip level">
                {l.cefr}
                {l.difficulty_score !== null && ` · ${l.difficulty_score.toFixed(0)}`}
              </span>
            )}
            {l.topic && <span className="chip topic">{topicMeta(l.topic).label}</span>}
            <span className="chip">{l.unit_count} units</span>
            <span className="chip">{l.exercise_count} exercises</span>
          </div>
        </div>
      ))}
    </>
  )
}

export function LessonView({
  lessonId,
  onOpenUnit,
  onBack,
  onSignIn,
  backLabel = 'Library',
}: {
  lessonId: number
  onOpenUnit: (unitId: number) => void
  onBack: () => void
  onSignIn: () => void
  backLabel?: string
}) {
  const auth = useAuth()
  const [lesson, setLesson] = useState<LessonDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  // The unit whose lock the learner just clicked, if any. Held here rather than per row so only
  // one decision can be open at a time.
  const [gateFor, setGateFor] = useState<number | null>(null)

  useEffect(() => {
    api.lesson(lessonId).then(setLesson).catch((e) => setError(String(e)))
  }, [lessonId])

  if (error) return <div className="error">{error}</div>
  if (!lesson) return <div className="empty">Loading lesson…</div>

  return (
    <>
      <div className="crumbs">
        <button onClick={onBack}>← {backLabel}</button>
        <span>/</span>
        <span>{lesson.title}</span>
      </div>

      <div className="card">
        <h3>{lesson.title}</h3>
        <div className="sub">
          {lesson.source.channel} ·{' '}
          <a href={lesson.source.url} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>
            original on YouTube ↗
          </a>
        </div>
        <div className="meta-row">
          {lesson.cefr && <span className="chip level">{lesson.cefr}</span>}
          {lesson.topic && <span className="chip topic">{topicMeta(lesson.topic).label}</span>}
          <span className="chip">{lesson.unit_count} units</span>
          <span className="chip">{lesson.exercise_count} exercises</span>
          <span className="chip">licence: {lesson.source.license_name ?? 'standard YouTube'}</span>
        </div>
      </div>

      <QuotaBar onSignIn={onSignIn} />

      {lesson.units.map((u, i) => (
        <div
          className={`card clickable enters ${auth.isUnlocked(u.id) ? '' : 'is-locked'}`}
          style={{ ['--i' as string]: i }}
          key={u.id}
          // A locked unit opens the decision instead of the drill. Navigating first and letting the
          // 402 land would mean the learner watches a page load and then get taken away again.
          onClick={() => (auth.isUnlocked(u.id) ? onOpenUnit(u.id) : setGateFor(u.id))}
        >
          <h3>
            Unit {u.idx + 1} · {fmt(u.start_s)}–{fmt(u.end_s)}
          </h3>
          {u.gist && <div className="gist">{u.gist}</div>}
          <div className="meta-row">
            {u.cefr && (
              <span className="chip level">
                {u.cefr}
                {u.difficulty_score !== null && ` · ${u.difficulty_score.toFixed(0)}`}
              </span>
            )}
            {u.wpm !== null && <span className="chip">{u.wpm.toFixed(0)} wpm</span>}
            <span className="chip">{Math.round(u.duration_s)}s</span>
            <span className="chip">{u.exercise_count} exercises</span>
            <LockChip unitId={u.id} />
          </div>
        </div>
      ))}

      {gateFor !== null && (
        <UnlockGate
          unitId={gateFor}
          unitLabel={(() => {
            const unit = lesson.units.find((u) => u.id === gateFor)
            return unit
              ? `${lesson.title} · Unit ${unit.idx + 1} (${fmt(unit.start_s)}–${fmt(unit.end_s)})`
              : lesson.title
          })()}
          onUnlocked={() => {
            const opened = gateFor
            setGateFor(null)
            if (opened !== null) onOpenUnit(opened)
          }}
          onClose={() => setGateFor(null)}
          onSignIn={onSignIn}
        />
      )}
    </>
  )
}
