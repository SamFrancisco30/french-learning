import { useEffect, useState } from 'react'
import { api } from '../api'
import { LessonLibrary, LessonView } from '../components/Library'
import { UnitDrill } from '../components/UnitDrill'
import { paramAfter } from '../router'
import type { LessonDetail } from '../types'

/**
 * Listening: library → lesson → unit, driven off the route so Back works and a unit is a
 * shareable URL. The unit view needs the lesson's title and language, which the route only
 * carries as an id, so the lesson is fetched here rather than threaded through navigation
 * state (state would be lost on reload or on a pasted link).
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

  const [lesson, setLesson] = useState<LessonDetail | null>(null)

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
        onBack={() => navigate('/listening')}
        onOpenUnit={(uid) => navigate(`/listening/lesson/${lessonId}/unit/${uid}`)}
      />
    )
  }

  return (
    <>
      <div className="pagehead">
        <h2>Listening</h2>
        <p>
          Pick a lesson, then a unit. Each unit is a 60–120 second passage with its own
          difficulty estimate — slow it to 0.75× first, then confirm at full speed.
        </p>
      </div>
      <LessonLibrary
        language={language}
        learnerKey={learnerKey}
        onOpen={(l) => navigate(`/listening/lesson/${l.id}`)}
      />
    </>
  )
}
