import { useEffect, useRef } from 'react'

/**
 * The speed track's whole texture: a grid of white blocks with blue sparkles travelling through it.
 *
 * ONE RENDERER DRAWS BOTH, and that is the entire reason this file looks the way it does. The grid
 * used to be a CSS repeating-gradient with the sparkles on a canvas above — two independent
 * implementations of the same grid, which disagreed for three different reasons in succession: the
 * gradient's phase started from a different edge than the canvas rows; the canvas coordinate space
 * was a rounded copy of a fractional element box; and then the browser resampled the bitmap by a
 * fraction of a percent, which does not look like a scale error, it looks like the sparkles are
 * smaller than the tiles and sitting between them. Each fix revealed the next variant of the same
 * bug. Drawing the blocks and the sparkles from one integer grid in one pass makes them incapable
 * of disagreeing about size, phase or position — there is no second grid to drift against.
 *
 * A sparkle always occupies exactly one whole block. Its position advances continuously but is
 * quantised to the pitch before drawing, so it jumps cell to cell and is never a different size
 * from its neighbours.
 *
 * Each picks a colour from a six-step blue ramp, a speed, and a despawn point at the knob plus a
 * random offset either side — so the lit region tracks the setting without anything computing that
 * it should.
 */

/** Six steps from the accent blue toward a blue-grey, deliberately never reaching the seam colour:
 *  a step that matches the background would be a wasted sixth of the palette. */
const PALETTE_FROM = [58, 110, 165] // --accent #3a6ea5
// Stops well short of the tile grey (#cdd5dd). The palest step used to be close enough to the tile
// that those sparkles read as blank cells rather than pale blue ones — there is no point spending a
// sixth of the palette on a colour indistinguishable from the background.
const PALETTE_TO = [124, 154, 189]
const STEPS = 6
/**
 * Skews the pick toward index 0. `Math.random() ** BLUE_BIAS` compresses the distribution toward 0,
 * and the exponent controls how hard: at 1.8 the ramp ran 37/18/14/12/11/9%, at 2.6 it runs
 * roughly 50/15/11/9/8/7 — half the sparkles are the strongest blue and the palest is rare.
 */
const BLUE_BIAS = 2.6

const CELL = 4 // a block
const PITCH = 5 // block plus its 1px seam

/** The original look: grey tiles, and the gaps left TRANSPARENT so the track's own pale gradient
 *  shows through as the seams. Painting the seams a dark colour gave the grid a black cast, which
 *  was not wanted — the seams should be lighter than the tiles, not darker. */
const BLOCK = '#cdd5dd' // --border-strong

// Deliberately dense. Past roughly 40/sec the field exceeds one sparkle per cell at Normal, so some
// get overwritten — that is a denser blue, not a lost sparkle, and density is the point here.
const SPAWN_PER_SEC = 46
const SPEED_MIN = 24 // css px/sec
const SPEED_MAX = 52
/** How far either side of the knob a sparkle may choose to die, as a fraction of the track. */
const VICINITY = 0.2
const FADE_IN = 10
const FADE_OUT = 14

function palette(): string[] {
  return Array.from({ length: STEPS }, (_, i) => {
    const t = i / (STEPS - 1)
    const c = PALETTE_FROM.map((from, k) => Math.round(from + (PALETTE_TO[k] - from) * t))
    return `rgb(${c[0]}, ${c[1]}, ${c[2]})`
  })
}

interface Particle {
  /** Continuous position; quantised to PITCH only when drawn. */
  x: number
  /** Row origin, always a multiple of PITCH. */
  y: number
  vx: number
  colour: string
  dieAt: number
}

export function SpeedSparkle({ pct, busy }: { pct: number; busy: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  // Through refs, so changing speed never restarts the animation — the sparkles keep flowing while
  // the knob moves under them.
  const pctRef = useRef(pct)
  const busyRef = useRef(busy)
  pctRef.current = pct
  busyRef.current = busy

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const colours = palette()
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    let particles: Particle[] = []
    let raf = 0
    let last = 0
    let spawnDebt = 0
    let cols = 0
    let rows = 0
    let w = 0
    let h = 0

    /**
     * Size the bitmap to a WHOLE number of css pixels and pin the element to that same integer.
     *
     * The canvas is inset:0 in the track, so its box can be fractional — 208.4px, say. Leaving the
     * css size at 100% while the bitmap is an integer number of device pixels makes the browser
     * rescale the drawing, and a 0.2% rescale is exactly what "the sparkles are a different size
     * from the tiles" looks like. Pinning both to the same integer removes the resample, at the
     * cost of up to one unpainted pixel at the right edge, which the rounded end hides.
     */
    const resize = () => {
      const dpr = window.devicePixelRatio || 1
      // Measure the PARENT, never the canvas itself. Now that the element's css size is pinned in
      // JS it has no size of its own to measure — an unsized canvas reports its intrinsic 300x150
      // default, so measuring itself made it lock to 300x150 forever, and there is no width at
      // which that is right.
      //
      // clientWidth/clientHeight, NOT getBoundingClientRect: the rect is the border box, but the
      // canvas is positioned at the PADDING box, so measuring the rect made the canvas 2px too tall
      // and shifted it a pixel down — which is where the grey strip above the tiles came from.
      const host = canvas.parentElement
      w = Math.max(PITCH, host ? host.clientWidth : Math.floor(canvas.getBoundingClientRect().width))
      h = Math.max(PITCH, host ? host.clientHeight : Math.floor(canvas.getBoundingClientRect().height))
      // Count whole blocks that FIT, rather than dividing the space up. n blocks with n-1 seams
      // between them occupy n*PITCH - 1, so dividing by PITCH always leaves a stripe over — the
      // remainder being the margin at the bottom edge.
      cols = Math.max(1, Math.floor((w - CELL) / PITCH) + 1)
      rows = Math.max(1, Math.floor((h - CELL) / PITCH) + 1)
      canvas.width = Math.round(w * dpr)
      canvas.height = Math.round(h * dpr)
      canvas.style.width = `${w}px`
      canvas.style.height = `${h}px`
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      // Paint at once. The first draw used to happen inside the frame loop, which meant the track
      // was blank until rAF fired — and completely blank wherever rAF never fires, such as a
      // background tab.
      draw()
    }
    // Observe the parent: the canvas's own box is now pinned, so it would never report a change.
    const spawn = (): Particle => {
      const knob = (pctRef.current / 100) * w
      return {
        x: -CELL,
        y: ((Math.random() * rows) | 0) * PITCH,
        vx: SPEED_MIN + Math.random() * (SPEED_MAX - SPEED_MIN),
        colour: colours[Math.min(STEPS - 1, (Math.random() ** BLUE_BIAS * STEPS) | 0)],
        dieAt: Math.max(CELL * 2, knob + (Math.random() * 2 - 1) * VICINITY * w),
      }
    }

    const draw = () => {
      // Clear rather than fill: the untouched gaps let the track's pale gradient through, which is
      // what makes the seams read as light. Then every cell as a grey tile.
      ctx.clearRect(0, 0, w, h)
      ctx.fillStyle = BLOCK
      for (let c = 0; c < cols; c++) {
        for (let r = 0; r < rows; r++) ctx.fillRect(c * PITCH, r * PITCH, CELL, CELL)
      }

      // Sparkles REPLACE whole blocks — same grid, same size, same origin, by construction.
      for (const p of particles) {
        const col = Math.round(p.x / PITCH)
        if (col < 0 || col >= cols) continue
        let alpha = 1
        if (p.x < FADE_IN) alpha = Math.max(0, p.x / FADE_IN)
        const left = p.dieAt - p.x
        if (left < FADE_OUT) alpha = Math.min(alpha, Math.max(0, left / FADE_OUT))
        if (alpha <= 0) continue
        ctx.globalAlpha = alpha
        ctx.fillStyle = p.colour
        ctx.fillRect(col * PITCH, p.y, CELL, CELL)
      }
      ctx.globalAlpha = 1
    }

    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(canvas.parentElement ?? canvas)

    if (reduced) {
      // No motion: one static field, so the track still reads as blue thinning to the right.
      for (let i = 0; i < cols * rows; i++) {
        const p = spawn()
        p.x = Math.random() * Math.max(1, p.dieAt)
        particles.push(p)
      }
      draw()
      return () => ro.disconnect()
    }

    const frame = (t: number) => {
      const dt = last ? Math.min(0.05, (t - last) / 1000) : 0
      last = t
      const rush = busyRef.current ? 2 : 1

      spawnDebt += dt * SPAWN_PER_SEC * rush
      while (spawnDebt >= 1) {
        particles.push(spawn())
        spawnDebt -= 1
      }
      for (const p of particles) p.x += p.vx * dt * rush
      particles = particles.filter((p) => p.x < p.dieAt && p.x < w + CELL)

      draw()
      raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)

    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
    }
  }, [])

  return <canvas ref={canvasRef} className="speed-canvas" aria-hidden="true" />
}
