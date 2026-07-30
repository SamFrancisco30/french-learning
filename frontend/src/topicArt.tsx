import type { ReactNode } from 'react'

/**
 * Background line art for the listening topic cards.
 *
 * Hand-authored inline SVG rather than photographs, for four reasons that all point the same
 * way: nothing is fetched over the network, there is no licence to honour or attribute, the
 * drawings inherit `currentColor` so they tint with the theme instead of fighting a committed
 * near-white palette, and the whole set costs a few kilobytes of source with no binary assets.
 *
 * House rules, because thirteen of these are inlined into one document:
 *   - viewBox="0 0 400 220", no width/height — the card sizes them
 *   - `currentColor` is the only colour; depth comes from per-element opacity
 *   - no id, defs, gradient, mask, clipPath, filter or url(...) reference anywhere, since ids
 *     would collide across the set
 *   - the left third is kept quiet: the card's title sits there
 *   - lines may run off the right and bottom edges, which is what makes them read as a
 *     cropped background rather than a logo floating in a box
 */

const STROKE = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.4,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
}

/** Sound radiating from the right edge, over a waveform. The fallback for any topic. */
function Other(): ReactNode {
  return (
    <svg viewBox="0 0 400 220" {...STROKE}>
      <path d="M 400 76 A 34 34 0 0 0 400 144" opacity="0.9" />
      <path d="M 400 52 A 58 58 0 0 0 400 168" opacity="0.6" />
      <path d="M 400 28 A 82 82 0 0 0 400 192" opacity="0.4" />
      <path d="M 400 4 A 106 106 0 0 0 400 216" opacity="0.26" />
      <path d="M 142 110 L 292 110" opacity="0.3" />
      <path d="M 152 96 L 152 124" opacity="0.5" />
      <path d="M 166 82 L 166 138" opacity="0.6" />
      <path d="M 180 60 L 180 160" opacity="0.75" />
      <path d="M 194 92 L 194 128" opacity="0.5" />
      <path d="M 208 48 L 208 172" opacity="0.85" />
      <path d="M 222 74 L 222 146" opacity="0.65" />
      <path d="M 236 100 L 236 120" opacity="0.45" />
      <path d="M 250 66 L 250 154" opacity="0.7" />
      <path d="M 264 88 L 264 132" opacity="0.55" />
      <path d="M 278 104 L 278 116" opacity="0.4" />
    </svg>
  )
}

/**
 * slug -> drawing. Topics with no entry fall back to `other` via TopicArt in topics.tsx, so a
 * newly ingested subject looks deliberate instead of blank while its drawing is made.
 */
export const TOPIC_ART: Record<string, ReactNode> = {
  other: <Other />,
}
