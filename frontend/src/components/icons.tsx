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

/**
 * Google's "G", in its four brand colours.
 *
 * The one mark here that deliberately does NOT inherit `currentColor`. Google's brand terms require
 * the logo be reproduced in its own colours on a white or neutral button, so hover and focus states
 * are carried by the button around it rather than by the mark. Inline like the flags, so no request
 * has to complete before the sign-in page can paint — and so the button cannot end up as a broken
 * image if a CDN is blocked.
 */
export function GoogleMark() {
  return (
    <svg className="googlemark" viewBox="0 0 48 48" aria-hidden="true" focusable="false">
      <path
        fill="#4285F4"
        d="M45.12 24.5c0-1.56-.14-3.06-.4-4.5H24v8.51h11.84c-.51 2.75-2.06 5.08-4.39 6.64v5.52h7.11c4.16-3.83 6.56-9.47 6.56-16.17z"
      />
      <path
        fill="#34A853"
        d="M24 46c5.94 0 10.92-1.97 14.56-5.33l-7.11-5.52c-1.97 1.32-4.49 2.1-7.45 2.1-5.73 0-10.58-3.87-12.31-9.07H4.34v5.7C7.96 41.07 15.4 46 24 46z"
      />
      <path
        fill="#FBBC05"
        d="M11.69 28.18C11.25 26.86 11 25.45 11 24s.25-2.86.69-4.18v-5.7H4.34C2.85 17.09 2 20.45 2 24s.85 6.91 2.34 9.88l7.35-5.7z"
      />
      <path
        fill="#EA4335"
        d="M24 10.75c3.23 0 6.13 1.11 8.41 3.29l6.31-6.31C34.91 4.18 29.93 2 24 2 15.4 2 7.96 6.93 4.34 14.12l7.35 5.7c1.73-5.2 6.58-9.07 12.31-9.07z"
      />
    </svg>
  )
}
