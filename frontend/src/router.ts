import { useCallback, useEffect, useState } from 'react'

/**
 * Minimal hash router.
 *
 * Hash-based rather than History API so the Vite dev server and any static host serve it
 * without rewrite rules, and hand-rolled rather than react-router because the whole route
 * table is five skills plus two nested listening views — a dependency would cost more than
 * it saves. The point of having routes at all is that browser back/forward work and a unit
 * is a shareable URL.
 *
 *   #/listening
 *   #/listening/topic/geography      (or /topic/all)
 *   #/listening/lesson/2
 *   #/listening/lesson/2/unit/5
 *   #/reading  #/writing  #/speaking  #/dictation
 */

export function useHashRoute() {
  const read = () => window.location.hash.replace(/^#/, '') || '/listening'
  const [path, setPath] = useState(read)

  useEffect(() => {
    const onChange = () => setPath(read())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  const navigate = useCallback((to: string) => {
    // Assigning the hash pushes a history entry, so Back returns to the previous view.
    window.location.hash = to
  }, [])

  const segments = path.split('/').filter(Boolean)
  return { path, segments, navigate }
}

/** Numeric route param, or null when absent/malformed. */
export function paramAfter(segments: string[], key: string): number | null {
  const i = segments.indexOf(key)
  if (i === -1 || i + 1 >= segments.length) return null
  const n = Number(segments[i + 1])
  return Number.isFinite(n) ? n : null
}

/** String route param, or null when absent. Decoded, since topics arrive in the URL. */
export function slugAfter(segments: string[], key: string): string | null {
  const i = segments.indexOf(key)
  if (i === -1 || i + 1 >= segments.length) return null
  return decodeURIComponent(segments[i + 1]) || null
}
