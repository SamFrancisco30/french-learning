/**
 * Photographs for the topic cards, keyed by topic slug.
 *
 * A glob rather than thirteen import lines: dropping `src/assets/topics/<slug>.webp` in is all
 * it takes for a new subject to get a picture, and a topic with no file falls back to the line
 * art in topicArt.tsx instead of rendering an empty box. Vite resolves each to a hashed URL at
 * build time, so these are ordinary cacheable static assets — nothing is fetched from a third
 * party at runtime.
 *
 * See src/assets/topics/CREDITS.md for provenance. All CC0 or public domain.
 */

const files = import.meta.glob('./assets/topics/*.webp', {
  eager: true,
  query: '?url',
  import: 'default',
}) as Record<string, string>

export const TOPIC_PHOTO: Record<string, string> = Object.fromEntries(
  Object.entries(files).map(([path, url]) => [
    path.replace(/^.*\/([^/]+)\.webp$/, '$1'),
    url,
  ]),
)
