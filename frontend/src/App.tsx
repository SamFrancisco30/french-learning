import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api } from './api'
import { DictationPage } from './pages/DictationPage'
import { ListeningPage } from './pages/ListeningPage'
import { ReadingPage } from './pages/ReadingPage'
import { SkillStatusPage } from './pages/SkillStatusPage'
import { VocabularyPage } from './pages/VocabularyPage'
import { useIdentity } from './identity/IdentityContext'
import { useHashRoute } from './router'
import { SKILLS, skillFromPath, skillTitle } from './skills'
import type { Language } from './types'

export default function App() {
  const { learnerKey } = useIdentity()
  const { segments, navigate } = useHashRoute()
  const mastheadRef = useRef<HTMLElement | null>(null)
  const [languages, setLanguages] = useState<Language[]>([])
  const [language, setLanguage] = useState('fr')

  useEffect(() => {
    api.languages().then(setLanguages).catch(() => undefined)
  }, [])

  // Publish the masthead's real height so the sticky audio player sits flush beneath it.
  // Measured every render via offsetHeight (border-box) rather than with a ResizeObserver:
  // the observer reports the content box and fired before the language buttons loaded, so
  // the published value stayed stale at 56px for a 103px masthead and the player tucked
  // behind the nav. A layout-effect read is cheap and can't go stale.
  useLayoutEffect(() => {
    const el = mastheadRef.current
    if (!el) return
    const publish = () =>
      document.documentElement.style.setProperty('--masthead-h', `${el.offsetHeight}px`)
    publish()
    window.addEventListener('resize', publish)
    return () => window.removeEventListener('resize', publish)
  })

  const skill = skillFromPath(segments)
  const isVocabulary = segments[0] === 'vocabulary'
  const active = languages.find((l) => l.code === language)

  // The listening index — the grid of topic photographs — is the one view that is pictures rather
  // than prose, so it gets the wide shell. Everything else keeps a reading column: the drills,
  // dictation and reading pages are all long French passages, and a line of French running the
  // width of a large monitor is a worse place to read it, not a better one.
  //
  // `segments.length <= 1` is the same test ListeningPage uses to decide it is showing the grid: no
  // /topic, /lesson or /unit segment. The empty hash also lands here, which is correct — it is the
  // page you get on first load.
  const isTopicIndex = !isVocabulary && skill.key === 'listening' && segments.length <= 1

  return (
    <div className={`shell ${isTopicIndex ? 'shell-wide' : ''}`}>
      <header className="masthead" ref={mastheadRef}>
        <div className="masthead-inner">
          <div className="brand">
            {/* Title is the current skill's name in the target language, so it tracks both
                the page and the chosen language rather than sitting on "Écoute" (which
                means "listening" and is wrong everywhere else). */}
            <h1 lang={isVocabulary ? 'en' : language}>
              {isVocabulary ? 'My Words' : skillTitle(skill, language)}
            </h1>
            <span className="tagline">
              {active ? active.name_native : language} ·{' '}
              {isVocabulary ? 'vocabulary' : skill.label.toLowerCase()}
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
                    navigate(isVocabulary ? '/vocabulary' : `/${skill.key}`)
                  }}
                  title={l.name_en}
                  aria-label={l.name_en}
                >
                  {l.code}
                </button>
              ))}
            </div>
          )}

          <nav className="utilitynav" aria-label="Utilities">
            <button
              className={`utilitytab ${isVocabulary ? 'on' : ''}`}
              onClick={() => navigate('/vocabulary')}
              aria-current={isVocabulary ? 'page' : undefined}
            >
              My Words
            </button>
          </nav>
          <nav className="skillnav" aria-label="Skills">
            {SKILLS.map((s) => (
              <button
                key={s.key}
                className={`skilltab ${!isVocabulary && s.key === skill.key ? 'on' : ''}`}
                onClick={() => navigate(s.route)}
                aria-current={!isVocabulary && s.key === skill.key ? 'page' : undefined}
                title={
                  s.status === 'live'
                    ? `${s.label}: ready`
                    : s.status === 'partial'
                      ? `${s.label}: partly built`
                      : `${s.label}: not built yet`
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

      {!isVocabulary && skill.key === 'listening' && (
        <ListeningPage
          segments={segments}
          navigate={navigate}
          language={language}
          learnerKey={learnerKey}
        />
      )}

      {!isVocabulary && skill.key === 'reading' && (
        <ReadingPage language={language} />
      )}

      {/* Dictation is built now, so it renders its own page rather than the "not built yet"
          placeholder this branch had it pointing at. The learner key comes from the shared
          identity context — the local useState(learnerKey) this used to read was replaced by
          IdentityContext, which is the one source of the key for every skill. */}
      {!isVocabulary && skill.key === 'dictation' && (
        <DictationPage language={language} learnerKey={learnerKey} />
      )}

      {!isVocabulary && (skill.key === 'writing' || skill.key === 'speaking') && (
        <SkillStatusPage skill={skill} onGoListening={() => navigate('/listening')} />
      )}

      {isVocabulary && <VocabularyPage language={language} navigate={navigate} />}
    </div>
  )
}
