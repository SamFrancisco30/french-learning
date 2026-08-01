import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api } from './api'
import { StarMark } from './components/icons'
import { LanguagePicker } from './components/LanguagePicker'
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
  const { path, segments, navigate } = useHashRoute()
  const mastheadRef = useRef<HTMLElement | null>(null)
  const [languages, setLanguages] = useState<Language[]>([])
  const [language, setLanguage] = useState('fr')

  useEffect(() => {
    api.languages().then(setLanguages).catch(() => undefined)
  }, [])

  // Every view opens at its own top. Without this the browser keeps the outgoing page's scroll
  // offset, so choosing a topic from half way down the grid dropped you half way down the lesson
  // list — past the heading, and on a short list past the content entirely.
  //
  // Instant, not smooth: the smooth scroll belongs to the OUTGOING page, where it runs alongside the
  // tiles lifting away and has something to animate over. By the time this fires the content has
  // already been replaced, so animating the scroll would just slide unfamiliar content around.
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: 'auto' })
  }, [path])

  // Publish the masthead's real height so the sticky audio player sits flush beneath it.
  // Measured every render via offsetHeight (border-box) rather than with a ResizeObserver:
  // the observer reports the content box and fired before the language picker had loaded its
  // languages, so the published value stayed stale at 56px for a 103px masthead and the player
  // tucked behind the nav. A layout-effect read is cheap and can't go stale.
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



  return (
    <div className="shell">
      <header className="masthead" ref={mastheadRef}>
        <div className="masthead-inner">
          <div className="brand">
            {/* Title is the current skill's name in the target language, so it tracks both
                the page and the chosen language rather than sitting on "Écoute" (which
                means "listening" and is wrong everywhere else). */}
            <h1 lang={isVocabulary ? 'en' : language}>
              {isVocabulary ? 'My Words' : skillTitle(skill, language)}
            </h1>
            {/* The flag replaces both the language name and the three code buttons that used to
                sit beside it. It says which language is loaded and is also how you change it, and
                between them those two jobs used to cost about 230px of a single-line bar. The
                skill is not repeated here either — the tab for it is right there, underlined. */}
            <LanguagePicker
              languages={languages}
              value={language}
              onChange={(code) => {
                setLanguage(code)
                navigate(isVocabulary ? '/vocabulary' : `/${skill.key}`)
              }}
            />
          </div>

          <nav className="skillnav" aria-label="Skills">
            {SKILLS.map((s) => (
              <button
                key={s.key}
                className={`skilltab ${!isVocabulary && s.key === skill.key ? 'on' : ''}`}
                onClick={() => navigate(s.route)}
                aria-current={!isVocabulary && s.key === skill.key ? 'page' : undefined}
              >
                <span className={`dot ${s.status}`} />
                {s.label}
                <span className="native">{s.native}</span>
                {/*
                  What the page is for, raised from the tab rather than printed at the top of the
                  page. The pages used to carry a heading repeating the tab's own label and a
                  paragraph under it — on the listening index that was 110px above the topics,
                  restating the word already underlined in the nav.

                  A styled panel rather than the `title` attribute it replaces: title has no styling,
                  a delay of about a second before it appears, and no way to hold more than one line
                  comfortably. The status still reaches a mouse user, appended for the skills that
                  are not finished, and the dot carries it at a glance.
                */}
                <span className="skilltab-tip" aria-hidden="true">
                  {s.tip ?? s.blurb}
                  {s.status !== 'live' && (
                    <em>{s.status === 'partial' ? 'Partly built.' : 'Not built yet.'}</em>
                  )}
                </span>
              </button>
            ))}
          </nav>

          {/* Last, and last in the DOM on purpose. Moving it here with CSS `order` would leave a
              keyboard user tabbing to the star before the skill tabs while seeing it after them. */}
          <nav className="utilitynav" aria-label="Utilities">
            {/* Icon-only, so the name has to come from aria-label — there is no text to read, and
                "My Words" is the whole meaning of the control. */}
            <button
              className={`utilitytab starbtn ${isVocabulary ? 'on' : ''}`}
              onClick={() => navigate('/vocabulary')}
              aria-current={isVocabulary ? 'page' : undefined}
              aria-label="My Words"
              title="My Words — the vocabulary you have saved"
            >
              <StarMark />
            </button>
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
