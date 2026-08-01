import { useEffect, useMemo, useRef, useState } from 'react'
import type { LessonSummary } from '../types'
import { ALL, TOPICS, TopicArt, hasPhoto, topicMeta } from '../topics'

/**
 * The listening landing page: pick a subject, then a lesson.
 *
 * Grouping happens here rather than on the server. The library is a few dozen lessons and the
 * page already has all of them, so a topic endpoint would be a round trip to compute a
 * `groupBy` — and counts stay correct the moment a lesson is ingested, with nothing to
 * invalidate.
 *
 * Topics with no lessons are not shown. A grid of empty subjects looks like a broken app, and
 * the taxonomy in topics.tsx deliberately runs ahead of what has been ingested.
 */

const CEFR_ORDER = ['A1', 'A2', 'B1', 'B2', 'C1', 'C2']

function levelRange(lessons: LessonSummary[]): string | null {
  const levels = lessons
    .map((l) => l.cefr)
    .filter((c): c is string => !!c && CEFR_ORDER.includes(c))
    .sort((a, b) => CEFR_ORDER.indexOf(a) - CEFR_ORDER.indexOf(b))
  if (levels.length === 0) return null
  const lo = levels[0]
  const hi = levels[levels.length - 1]
  return lo === hi ? lo : `${lo}–${hi}`
}

export function TopicGrid({
  lessons,
  language,
  onOpenTopic,
}: {
  lessons: LessonSummary[]
  language: string
  onOpenTopic: (slug: string) => void
}) {
  const groups = useMemo(() => {
    const by = new Map<string, LessonSummary[]>()
    for (const l of lessons) {
      const slug = l.topic || 'other'
      const list = by.get(slug)
      if (list) list.push(l)
      else by.set(slug, [l])
    }

    // Editorial order first, then anything ingested under a slug the taxonomy has not caught
    // up with, so a new topic appears immediately instead of vanishing.
    const known = TOPICS.map((t) => t.slug).filter((s) => by.has(s))
    const extra = [...by.keys()].filter((s) => !known.includes(s)).sort()
    return [...known, ...extra].map((slug) => ({ slug, lessons: by.get(slug)! }))
  }, [lessons])

  const totalUnits = lessons.reduce((n, l) => n + l.unit_count, 0)

  // Opening a topic plays the tiles out before the route changes: they lift and fade one after the
  // other, and the page scrolls back to the top while they go, so the lesson list is not entered
  // half way down. `leaving` holds the slug that has been asked for but not yet navigated to.
  const [leaving, setLeaving] = useState<string | null>(null)
  const grid = useRef<HTMLDivElement | null>(null)

  const open = (slug: string) => {
    if (leaving) return // a second click during the exit would queue a second navigation
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      onOpenTopic(slug)
      return
    }
    setLeaving(slug)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  // Navigate when the animation is actually over, rather than after a duration copied from the
  // stylesheet. The last tile carries the largest stagger delay, so its `animationend` is the end
  // of the sequence — and reading it from the DOM means the timing cannot drift out of step with
  // the CSS. The timeout is only a backstop for the case where no animation runs at all and the
  // event therefore never fires.
  useEffect(() => {
    if (!leaving) return
    const tiles = grid.current?.querySelectorAll<HTMLElement>('.topic-card')
    const last = tiles?.[tiles.length - 1]
    let done = false
    const go = () => {
      if (done) return
      done = true
      onOpenTopic(leaving)
    }
    last?.addEventListener('animationend', go, { once: true })

    // The backstop covers the case where no animation runs and `animationend` never arrives — a
    // background tab suspends them, and some environments disable them outright. Its budget is read
    // off the last tile's own computed style rather than hardcoded, so it stays just past the real
    // end of the sequence however many topics there are. A fixed number would either cut a longer
    // grid's exit short or make a shorter one sit waiting.
    const secs = (v: string) => (v.endsWith('ms') ? parseFloat(v) : parseFloat(v) * 1000) || 0
    const style = last && getComputedStyle(last)
    const budget = style ? secs(style.animationDelay) + secs(style.animationDuration) + 120 : 500
    const backstop = window.setTimeout(go, budget)
    return () => {
      last?.removeEventListener('animationend', go)
      window.clearTimeout(backstop)
    }
  }, [leaving, onOpenTopic])

  return (
    <div className={`topic-grid ${leaving ? 'is-leaving' : ''}`} ref={grid}>
      {groups.map(({ slug, lessons: group }, i) => {
        const meta = topicMeta(slug)
        const units = group.reduce((n, l) => n + l.unit_count, 0)
        const range = levelRange(group)
        return (
          <button
            className={`topic-card enters ${hasPhoto(slug) ? 'has-photo' : ''}`}
            style={{ ['--i' as string]: i }}
            key={slug}
            onClick={() => open(slug)}
            aria-label={`${meta.label} — ${group.length} lessons`}
          >
            <span className="topic-art" aria-hidden="true">
              <TopicArt slug={slug} />
            </span>
            <span className="topic-body">
              <span className="topic-head">
                <span className="topic-label">{meta.label}</span>
                <span className="topic-native" lang={language}>
                  {meta.native}
                </span>
              </span>
              <span className="topic-blurb">{meta.blurb}</span>
              <span className="topic-stats">
                <span className="chip">{group.length} lesson{group.length === 1 ? '' : 's'}</span>
                <span className="chip">{units} units</span>
                {range && <span className="chip level">{range}</span>}
              </span>
            </span>
          </button>
        )
      })}

      {groups.length > 1 && (
        <button
          className={`topic-card wide enters ${hasPhoto(ALL.slug) ? 'has-photo' : ''}`}
          style={{ ['--i' as string]: groups.length }}
          onClick={() => open(ALL.slug)}
          aria-label={`${ALL.label} — ${lessons.length} lessons`}
        >
            <span className="topic-art" aria-hidden="true">
            <TopicArt slug={ALL.slug} />
          </span>
          <span className="topic-body">
            <span className="topic-head">
              <span className="topic-label">{ALL.label}</span>
              <span className="topic-native" lang={language}>
                {ALL.native}
              </span>
            </span>
            <span className="topic-blurb">{ALL.blurb}</span>
            <span className="topic-stats">
              <span className="chip">{lessons.length} lessons</span>
              <span className="chip">{totalUnits} units</span>
            </span>
          </span>
        </button>
      )}
    </div>
  )
}
