import { useEffect, useState } from 'react'
import { api } from './api'
import { ListeningPage } from './pages/ListeningPage'
import { ReadingPage } from './pages/ReadingPage'
import { SkillStatusPage } from './pages/SkillStatusPage'
import { useHashRoute } from './router'
import { SKILLS, skillFromPath } from './skills'
import type { Language } from './types'

// Anonymous, device-local identity. Replace with real auth when accounts land.
function learnerKey(): string {
  const existing = localStorage.getItem('learner_key')
  if (existing) return existing
  const key = `learner_${Math.random().toString(36).slice(2, 10)}`
  localStorage.setItem('learner_key', key)
  return key
}

export default function App() {
  const { segments, navigate } = useHashRoute()
  const [languages, setLanguages] = useState<Language[]>([])
  const [language, setLanguage] = useState('fr')
  const [key] = useState(learnerKey)

  useEffect(() => {
    api.languages().then(setLanguages).catch(() => undefined)
  }, [])

  const skill = skillFromPath(segments)
  const active = languages.find((l) => l.code === language)

  return (
    <div className="shell">
      <header className="masthead">
        <div className="masthead-inner">
          <div className="brand">
            <h1>Écoute</h1>
            <span className="tagline">
              {active ? active.name_native : 'Français'} · from authentic media
            </span>
          </div>

          {languages.length > 1 && (
            <div className="langnav">
              {languages.map((l) => (
                <button
                  key={l.code}
                  className={`rate-btn ${l.code === language ? 'on' : ''}`}
                  onClick={() => {
                    setLanguage(l.code)
                    navigate(`/${skill.key}`)
                  }}
                  title={l.name_en}
                >
                  {l.code}
                </button>
              ))}
            </div>
          )}

          <nav className="skillnav" aria-label="Skills">
            {SKILLS.map((s) => (
              <button
                key={s.key}
                className={`skilltab ${s.key === skill.key ? 'on' : ''}`}
                onClick={() => navigate(s.route)}
                aria-current={s.key === skill.key ? 'page' : undefined}
                title={
                  s.status === 'live'
                    ? `${s.label} — ready`
                    : s.status === 'partial'
                      ? `${s.label} — partly built`
                      : `${s.label} — not built yet`
                }
              >
                <span className={`dot ${s.status}`} />
                {s.label}
                <span className="native">{s.native}</span>
              </button>
            ))}
          </nav>
        </div>
      </header>

      {skill.key === 'listening' && (
        <ListeningPage
          segments={segments}
          navigate={navigate}
          language={language}
          learnerKey={key}
        />
      )}

      {skill.key === 'reading' && <ReadingPage language={language} learnerKey={key} />}

      {(skill.key === 'writing' || skill.key === 'speaking' || skill.key === 'dictation') && (
        <SkillStatusPage skill={skill} onGoListening={() => navigate('/listening')} />
      )}
    </div>
  )
}
