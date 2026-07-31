/**
 * Small inline SVG marks, and the one bit of geometry two of them share.
 *
 * Inline rather than an icon font or sprite sheet: there are two of them, they inherit
 * `currentColor` so they follow the button state for free, and nothing has to load before the nav
 * can paint.
 */

/**
 * Points of a regular five-pointed star, with one vertex aimed `rotDeg` from straight up.
 *
 * Shared because both the star mark and China's flag need real star geometry, and the inner radius
 * ratio is the part worth getting right — 0.382 (1/φ²) is what makes the arms of a five-pointed star
 * meet at a straight line instead of bulging or pinching.
 */
export function starPoints(cx: number, cy: number, r: number, rotDeg = 0): string {
  const inner = r * 0.382
  const pts: string[] = []
  for (let i = 0; i < 5; i++) {
    const a = ((rotDeg - 90 + i * 72) * Math.PI) / 180
    const b = a + (36 * Math.PI) / 180
    pts.push(`${(cx + r * Math.cos(a)).toFixed(2)},${(cy + r * Math.sin(a)).toFixed(2)}`)
    pts.push(`${(cx + inner * Math.cos(b)).toFixed(2)},${(cy + inner * Math.sin(b)).toFixed(2)}`)
  }
  return pts.join(' ')
}

/**
 * The saved-words mark: a star with two sparkles that twinkle.
 *
 * Outlined normally and filled on the page it leads to, so the button carries its own selected
 * state without needing a label beside it. The animation lives in CSS so `prefers-reduced-motion`
 * can switch it off in one place rather than being negotiated here.
 */
export function StarMark() {
  return (
    <svg className="star-mark" viewBox="0 0 24 24" aria-hidden="true">
      <polygon className="star-body" points={starPoints(11, 12.5, 8)} />
      <polygon className="star-spark spark-a" points={starPoints(20, 5, 3.2)} />
      <polygon className="star-spark spark-b" points={starPoints(4.5, 4, 2.9)} />
    </svg>
  )
}
