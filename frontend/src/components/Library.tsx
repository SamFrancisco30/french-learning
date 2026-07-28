import { useEffect, useState } from 'react'
import { api } from '../api'
import type { LessonDetail, LessonSummary, Progress } from '../types'
import { fmt } from '../useClipPlayer'

const TOPIC_LABEL: Record<string, string> = {
  world_news: 'world news',
  geography: 'geography',
  biology: 'biology',
  science: 'science',
  environment: 'environment',
  economics: 'economics',
  politics: 'politics',
  technology: 'technology',
  history: 'history',
  culture: 'culture',
  society: 'society',
  sport: 'sport',
}

export function LessonLibrary({
  language,
  learnerKey,
  onOpen,
}: {
  language: string
  learnerKey: string
  onOpen: (lesson: LessonSummary) => void
}) {
  const [lessons, setLessons] = useState<LessonSummary[] | null>(null)
  const [progress, setProgress] = useState<Progress | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.lessons(language).then(setLessons).catch((e) => setError(String(e)))
    api.progress(learnerKey).then(setProgress).catch(() => undefined)
  }, [language, learnerKey])

  if (error) return <div className="error">{error}</div>
  if (!lessons) return <div className="empty">Loading library…</div>
  if (lessons.length === 0) {
    return (
      <div className="empty">
        No lessons yet. Add one with:
        <br />
        <code>python scripts/ingest.py add "&lt;youtube-url&gt;" --topic world_news</code>
      </div>
    )
  }

  return (
    <>
      {progress && progress.attempts > 0 && (
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
      )}

      {lessons.map((l) => (
        <div className="card clickable" key={l.id} onClick={() => onOpen(l)}>
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
            {l.topic && <span className="chip topic">{TOPIC_LABEL[l.topic] ?? l.topic}</span>}
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
}: {
  lessonId: number
  onOpenUnit: (unitId: number) => void
  onBack: () => void
}) {
  const [lesson, setLesson] = useState<LessonDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.lesson(lessonId).then(setLesson).catch((e) => setError(String(e)))
  }, [lessonId])

  if (error) return <div className="error">{error}</div>
  if (!lesson) return <div className="empty">Loading lesson…</div>

  return (
    <>
      <div className="crumbs">
        <button onClick={onBack}>← Library</button>
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
          {lesson.topic && <span className="chip topic">{TOPIC_LABEL[lesson.topic] ?? lesson.topic}</span>}
          <span className="chip">{lesson.unit_count} units</span>
          <span className="chip">{lesson.exercise_count} exercises</span>
          <span className="chip">licence: {lesson.source.license_name ?? 'standard YouTube'}</span>
        </div>
      </div>

      {lesson.units.map((u) => (
        <div className="card clickable" key={u.id} onClick={() => onOpenUnit(u.id)}>
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
          </div>
        </div>
      ))}
    </>
  )
}
