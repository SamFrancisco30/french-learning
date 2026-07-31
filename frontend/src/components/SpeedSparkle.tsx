import { useEffect, useRef } from 'react'

/**
 * The speed track's whole texture: flat grey blocks on a grid whose SEPARATORS are tinted blue at
 * the left and fade out past the knob, with blue sparkles travelling through the blocks.
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

/**
 * Block and pitch. The seam is the difference, and it stays at 1px because that is the thinnest line
 * that renders crisply at dpr 1 — going below it means half-pixel geometry and a blurred lattice.
 *
 * So making the blocks "stick together" is a matter of growing them AROUND that fixed 1px, not of
 * shrinking the gap: at 4/5 the seam was a fifth of the pitch, at 6/7 it is a seventh. The track
 * height follows, since a flush fit needs h = rows * PITCH - 1, and 7 admits exactly one sensible
 * height in range — 3 rows at 20px.
 */
const CELL = 6
const PITCH = 7
// Speeds must stay under PITCH * 60 = 420 px/s. Above that a block advances more than one cell per
// frame and the quantised hop turns into skipping, which reads as flicker rather than movement.

/** Tiles are a flat grey. The gradient belongs to the GRID LINES, not to the blocks. */
const BLOCK = '#cdd5dd' // --border-strong

/** The 1px separators between adjacent blocks — the grid itself — carry the gradient: the accent
 *  blue at the very left edge, fading out a little past the knob.
 *
 *  Painted by filling the whole canvas with the gradient and then covering it with the blocks, so
 *  the only thing left showing is the one-pixel lattice between them. Drawing the lines
 *  individually would be ~50 strokes a frame and would land on half-pixels; letting the blocks
 *  mask a single fill keeps the lines exactly where the block edges put them.
 *
 *  It fades to the same hue at zero alpha rather than to a grey, so past the fade the seams return
 *  to the track's own background with no colour boundary of their own. */
const SEAM_FROM = 'rgba(58, 110, 165, 0.95)' // --accent at the left edge
const SEAM_TO = 'rgba(58, 110, 165, 0)' //     the same blue, gone
/** The fade ends this far past the knob, as a fraction of the track. Ending exactly AT the knob put
 *  a hard stop under it; carrying on a little lets the colour die away behind the thumb instead. */
const SEAM_OVERSHOOT = 0.12

// Deliberately dense, past one sparkle per cell — the overlap reads as a denser blue rather than a
// lost sparkle.
//
// The rate is tied to BOTH the speed and the cell size, and both have moved. Steady-state population
// is rate times lifetime, and lifetime is distance over speed, so speeding the blocks up thins the
// field unless the rate rises with it: mean speed 38 -> 82 px/s took the rate 46 -> 100. Then the
// bigger 6/7 grid halved the cell count, 168 -> 87, which doubles coverage at the same rate — so the
// rate comes back down to 52 to land on the same ~160% it had before.
const SPAWN_PER_SEC = 52
const SPEED_MIN = 52 // css px/sec
const SPEED_MAX = 112
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
      ctx.clearRect(0, 0, w, h)

      // 1. THE GRID LINES. The gradient goes down first across the whole canvas; the blocks then
      //    cover it, so the only thing left visible is the 1px lattice between them. Blue at the
      //    left edge, faded to nothing a little past the knob.
      const knob = (pctRef.current / 100) * w
      const fadeEnd = Math.max(PITCH, knob + SEAM_OVERSHOOT * w)
      const seam = ctx.createLinearGradient(0, 0, fadeEnd, 0)
      seam.addColorStop(0, SEAM_FROM)
      seam.addColorStop(1, SEAM_TO)
      ctx.fillStyle = seam
      ctx.fillRect(0, 0, w, h)

      // 2. The blocks, flat grey, masking everything but the separators.
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
