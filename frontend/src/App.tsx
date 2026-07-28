import { useEffect, useState } from 'react'
import { api } from './api'
import { LessonLibrary, LessonView } from './components/Library'
import { UnitDrill } from './components/UnitDrill'
import type { Language, LessonSummary } from './types'

type View =
  | { name: 'library' }
  | { name: 'lesson'; lesson: LessonSummary }
  | { name: 'unit'; lesson: LessonSummary; unitId: number }

// Anonymous, device-local identity. Replace with real auth when accounts land.
function learnerKey(): string {
  const existing = localStorage.getItem('learner_key')
  if (existing) return existing
  const key = `learner_${Math.random().toString(36).slice(2, 10)}`
  localStorage.setItem('learner_key', key)
  return key
}

export default function App() {
  const [view, setView] = useState<View>({ name: 'library' })
  const [languages, setLanguages] = useState<Language[]>([])
  const [language, setLanguage] = useState('fr')
  const [key] = useState(learnerKey)

  useEffect(() => {
    api.languages().then(setLanguages).catch(() => undefined)
  }, [])

  const active = languages.find((l) => l.code === language)

  return (
    <div className="shell">
      <div className="topbar">
        <h1>Écoute</h1>
        <span className="tagline">
          listening comprehension from authentic media
          {active ? ` · ${active.name_native}` : ''}
        </span>
        {languages.length > 1 && (
          <div className="rates" style={{ marginLeft: 'auto' }}>
            {languages.map((l) => (
              <button
                key={l.code}
                className={`rate-btn ${l.code === language ? 'on' : ''}`}
                onClick={() => {
                  setLanguage(l.code)
                  setView({ name: 'library' })
                }}
                title={l.name_en}
              >
                {l.code}
              </button>
            ))}
          </div>
        )}
      </div>

      {view.name === 'library' && (
        <LessonLibrary
          language={language}
          learnerKey={key}
          onOpen={(lesson) => setView({ name: 'lesson', lesson })}
        />
      )}

      {view.name === 'lesson' && (
        <LessonView
          lessonId={view.lesson.id}
          onBack={() => setView({ name: 'library' })}
          onOpenUnit={(unitId) => setView({ name: 'unit', lesson: view.lesson, unitId })}
        />
      )}

      {view.name === 'unit' && (
        <UnitDrill
          unitId={view.unitId}
          lessonTitle={view.lesson.title}
          language={view.lesson.language}
          learnerKey={key}
          onExit={() => setView({ name: 'lesson', lesson: view.lesson })}
        />
      )}
    </div>
  )
}
