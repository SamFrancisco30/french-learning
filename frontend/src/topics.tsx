import type { ReactNode } from 'react'
import { TOPIC_ART } from './topicArt'
import { TOPIC_PHOTO } from './topicPhotos'

/**
 * The listening library's topic taxonomy.
 *
 * Topics are chosen at ingest (`ingest.py add --topic ...`) and stored on the lesson, so this
 * file is the display side only: a label, the same word in the language being learned, and a
 * line about what the listening is actually like. That last part is the useful one — "history"
 * tells you the subject, but "the past recounted, and the tenses it needs" tells you what you
 * are about to practise.
 *
 * ORDER IS EDITORIAL, not alphabetical: concrete and news-like topics first, where a learner
 * can lean on context, and the abstract, fast, vocabulary-dense ones last. Only topics that
 * actually have lessons are rendered, so the list can safely run ahead of the library.
 */

export interface TopicMeta {
  slug: string
  /** English name, used in the UI chrome. */
  label: string
  /** The same topic in the target language, shown beneath the label. */
  native: string
  /** What the listening is like, not just what it is about. */
  blurb: string
}

export const TOPICS: TopicMeta[] = [
  {
    slug: 'world_news',
    label: 'World news',
    native: 'actualité internationale',
    blurb: 'Bulletins and correspondents — formal register, but the context carries you.',
  },
  {
    slug: 'geography',
    label: 'Geography',
    native: 'géographie',
    blurb: 'Terrain, borders and how places get described.',
  },
  {
    slug: 'environment',
    label: 'Environment',
    native: 'environnement',
    blurb: 'Climate and ecology, and the pressure on natural systems.',
  },
  {
    slug: 'biology',
    label: 'Biology',
    native: 'biologie',
    blurb: 'Cells, bodies and living systems. Technical nouns, plain syntax.',
  },
  {
    slug: 'science',
    label: 'Science',
    native: 'sciences',
    blurb: 'Measurement and explanation — often slow, deliberate delivery.',
  },
  {
    slug: 'technology',
    label: 'Technology',
    native: 'technologie',
    blurb: 'Computation and networks, with a lot of borrowed English.',
  },
  {
    slug: 'economics',
    label: 'Economics',
    native: 'économie',
    blurb: 'Markets and value, and arguing about numbers out loud.',
  },
  {
    slug: 'politics',
    label: 'Politics',
    native: 'politique',
    blurb: 'Institutions and law. Long sentences, subjunctive everywhere.',
  },
  {
    slug: 'history',
    label: 'History',
    native: 'histoire',
    blurb: 'The past recounted, and the tenses it needs.',
  },
  {
    slug: 'society',
    label: 'Society',
    native: 'société',
    blurb: 'Everyday life and unscripted speech — hesitations, elisions, real pace.',
  },
  {
    slug: 'culture',
    label: 'Culture',
    native: 'culture',
    blurb: 'Art, literature and philosophy. The densest vocabulary in the library.',
  },
  {
    slug: 'sport',
    label: 'Sport',
    native: 'sport',
    blurb: 'Commentary: the fastest delivery here, and thick with idiom.',
  },
]

const BY_SLUG = new Map(TOPICS.map((t) => [t.slug, t]))

/** Shown for the "everything" card and for any topic the taxonomy doesn't know. */
export const OTHER: TopicMeta = {
  slug: 'other',
  label: 'Other',
  native: 'divers',
  blurb: "Everything that doesn't sit in one box yet.",
}

export const ALL: TopicMeta = {
  slug: 'all',
  label: 'Everything',
  native: 'tout',
  blurb: 'The whole library in one list, newest first.',
}

/**
 * Metadata for a slug. Unknown slugs fall back to OTHER but keep their own name, so a topic
 * added at ingest before it is added here still reads correctly instead of showing "Other".
 */
export function topicMeta(slug: string | null | undefined): TopicMeta {
  if (!slug) return OTHER
  if (slug === ALL.slug) return ALL
  const known = BY_SLUG.get(slug)
  if (known) return known
  return { ...OTHER, slug, label: slug.replace(/_/g, ' ') }
}

/**
 * Background for a topic card, in two stacked layers.
 *
 * The line-art motif is the base and the photograph sits on top. Two reasons that is better
 * than swapping one for the other: a lazily-loaded photo renders nothing until it arrives, so a
 * photo-only card is briefly — or, if the asset is missing, permanently — an empty rectangle;
 * and the base layer costs nothing, since it is inline SVG that is already parsed.
 *
 * The photo is decorative, so its alt is empty: a screen reader announcing "photograph of
 * shipping containers" before the word "Economics" is noise, and the card's own aria-label
 * already says what it is. Lazy, so the subjects below the fold wait their turn.
 */
export function TopicArt({ slug }: { slug: string }): ReactNode {
  const photo = TOPIC_PHOTO[slug]
  return (
    <>
      <span className="art-base">{TOPIC_ART[slug] ?? TOPIC_ART.other}</span>
      {photo && (
        <img
          className="art-photo"
          src={photo}
          alt=""
          loading="lazy"
          decoding="async"
          draggable={false}
        />
      )}
    </>
  )
}

/** True when this topic has a photograph, so the card can pick the right scrim weight. */
export function hasPhoto(slug: string): boolean {
  return !!TOPIC_PHOTO[slug]
}
