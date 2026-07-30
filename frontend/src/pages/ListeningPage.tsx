import { useEffect, useState } from 'react'
import { api } from '../api'
import { LessonLibrary, LessonView, ProgressSummary } from '../components/Library'
import { TopicGrid } from '../components/TopicGrid'
import { UnitDrill } from '../components/UnitDrill'
import { paramAfter, slugAfter } from '../router'
import { topicMeta } from '../topics'
import type { LessonDetail, LessonSummary, Progress } from '../types'

/**
 * Listening: topics → lessons → units → drill, driven off the route so Back works and a unit
 * is a shareable URL.
 *
 * The lesson list is fetched once here and handed to both the topic grid (which counts by
 * subject) and the lesson list (which filters by it). One request serves both, they can never
 * disagree about what exists, and stepping back up from a topic is instant.
 *
 * The unit view needs its lesson's title and language, which the route only carries as an id,
 * so the lesson is fetched here rather than threaded through navigation state — state would be
 * lost on reload or on a pasted link.
 */
export function ListeningPage({
  segments,
  navigate,
  language,
  learnerKey,
}: {
  segments: string[]
  navigate: (to: string) => void
  language: string
  learnerKey: string
}) {
  const lessonId = paramAfter(segments, 'lesson')
  const unitId = paramAfter(segments, 'unit')
  const topic = slugAfter(segments, 'topic')

  const [lessons, setLessons] = useState<LessonSummary[] | null>(null)
  const [progress, setProgress] = useState<Progress | null>(null)
  const [lesson, setLesson] = useState<LessonDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    setLessons(null)
    api
      .lessons(language)
      .then((l) => live && setLessons(l))
      .catch((e) => live && setError(String(e)))
    api
      .progress(learnerKey)
      .then((p) => live && setProgress(p))
      .catch(() => undefined) // progress is decoration; never block the library on it
    return () => {
      live = false
    }
  }, [language, learnerKey])

  useEffect(() => {
    if (lessonId == null) {
      setLesson(null)
      return
    }
    let live = true
    api
      .lesson(lessonId)
      .then((l) => live && setLesson(l))
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [lessonId])

  if (unitId != null && lessonId != null) {
    return (
      <UnitDrill
        unitId={unitId}
        lessonTitle={lesson?.title ?? 'Lesson'}
        language={lesson?.language ?? language}
        learnerKey={learnerKey}
        onExit={() => navigate(`/listening/lesson/${lessonId}`)}
      />
    )
  }

  if (lessonId != null) {
    return (
      <LessonView
        lessonId={lessonId}
        backLabel={lesson ? topicMeta(lesson.topic).label : 'Topics'}
        // Back goes to the subject this lesson belongs to, not all the way to the grid — the
        // route carries no topic, so it is read off the lesson itself.
        onBack={() => navigate(`/listening/topic/${lesson?.topic || 'all'}`)}
        onOpenUnit={(uid) => navigate(`/listening/lesson/${lessonId}/unit/${uid}`)}
      />
    )
  }

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

  if (topic) {
    return (
      <LessonLibrary
        lessons={lessons}
        topic={topic}
        language={language}
        onOpen={(l) => navigate(`/listening/lesson/${l.id}`)}
        onBack={() => navigate('/listening')}
      />
    )
  }

  return (
    <>
      <div className="pagehead">
        <h2>Listening</h2>
        <p>
          Pick a subject, then a lesson, then a unit. Each unit is a 60–120 second passage with
          its own difficulty estimate — slow it to 0.75× first, then confirm at full speed.
        </p>
      </div>
      <ProgressSummary progress={progress} />
      <TopicGrid
        lessons={lessons}
        language={language}
        onOpenTopic={(slug) => navigate(`/listening/topic/${slug}`)}
      />
    </>
  )
}
