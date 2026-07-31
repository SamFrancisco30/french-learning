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
 * How often each step spawns, as a geometric ladder: each step is this fraction as likely as the one
 * before it. 0.62 gives roughly 40 / 25 / 15 / 10 / 6 / 4 percent — strong blue the most common,
 * every lighter step rarer than the last, the palest rarest of all.
 *
 * Explicit weights, rather than the `Math.random() ** exponent` trick this used to use. That skewed
 * toward index 0 well enough (50/15/11/9/8/7) but the tail came out nearly flat — 7, 8, 9 and 11
 * percent are the same thing to the eye, so the ramp read as "lots of blue, then a jumble" instead
 * of as a ladder. A cumulative table costs one array and says exactly what the distribution is.
 */
const RAMP_FALLOFF = 0.62

/**
 * ROWS is the fixed quantity now and the pitch is derived from it, which is the only way to get six
 * rows into a bar this short.
 *
 * In whole css pixels it is impossible. A flush fit needs `ROWS * pitch - seam = height`, and with a
 * seam of at least 1 nothing integer solves 6 * pitch - seam = 20; the nearest answers are a 17px
 * bar (2px blocks, the seam a third of the pitch) or a 23px bar — one throws away the tightness, the
 * other grows the control, and neither was what was asked for.
 *
 * The way out is to stop working in css pixels. The seam only ever had to be one DEVICE pixel — that
 * is what "the thinnest line that renders crisply" actually means — so the grid is computed and drawn
 * entirely in device space with integer coordinates, and the bar is then sized to whatever six flush
 * rows need. On a 2x display that is a 7-device pitch: 3px blocks with a 0.5px seam, still a seventh
 * of the pitch, in a 20.5px bar. Half a pixel taller than the three-row version, for twice the rows.
 *
 * The trade is that the geometry varies with display density — 2px blocks at 1x, 3px at 2x — which no
 * single viewer can ever see, and each is exactly crisp on its own screen.
 */
const ROWS = 6
/** The height to aim for. The real height is whatever six flush rows come to at this density. */
const TARGET_H = 20
const SEAM_DEV = 1
/** Floor on the pitch, so blocks stay visible on a hypothetical very low-density display. */
const MIN_PITCH_DEV = 3

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
// The rate is tied to BOTH the speed and the cell size, and both have moved repeatedly. Steady-state
// population is rate times lifetime, and lifetime is distance over speed, so speeding the blocks up
// thins the field unless the rate rises with it: mean speed 38 -> 82 px/s took the rate 46 -> 100.
// Then the bigger 6/7 grid halved the cell count, 168 -> 87, doubling coverage at the same rate, and
// the rate came back down to 52.
//
// Six rows quadrupled the cells again, 87 -> 354, so at 52 the field thinned to 9% lit: a grey bar
// with the occasional blue fleck. 210 put it back to the 36% measured before. Not a guess — this is
// simulated to steady state at the shipped constants, with the knob at its sparsest setting.
//
// 255 is the same exercise once sparkles started expiring before the knob (see DIE_BAND_FROM). Their
// runs are shorter now, and steady-state population is rate times lifetime, so the rate has to come
// up to hold the density: it puts Normal at the 66% asked for, and the other three stops follow from
// the same rate.
const SPAWN_PER_SEC = 255
// Css px/sec, so the motion looks the same on every display. The ceiling is one cell per frame —
// a 3.5px pitch at 60fps is 210 css px/s — above which the quantised hop skips cells and reads as
// flicker rather than movement.
const SPEED_MIN = 52
const SPEED_MAX = 112
/** How far either side of the knob a sparkle may choose to die, as a fraction of the track. */
const VICINITY = 0.2
const FADE_IN = 10
const FADE_OUT = 14

/**
 * Where sparkles give out: a band, as a fraction of the distance to the knob, so they expire in the
 * last third of the approach and the run-up to the thumb goes quiet.
 *
 * A BAND, and uniform across it, which is the whole trick. The obvious way to clear the end is to
 * skew the despawn point toward the near end, but that shortens every sparkle's run and drains the
 * density everywhere — measured 39% at Normal, the profile sagging from the second fifth onward.
 * Ending them in a band instead means every sparkle still crosses the early part of its run at full
 * strength and only the approach thins.
 *
 * Being a fraction of the KNOB rather than of the track is what makes one rule serve all four
 * settings: the clearing scales with the room available, so each stop reads the same way.
 *
 * DIE_THROUGH is why the far end is not bare. A clean cut reads as the texture hitting a wall rather
 * than fading, so a few percent run the ordinary knob-vicinity course and keep a thin scatter
 * arriving.
 *
 * DIE_MIN_REACH is for the bottom stop alone, where the knob sits ON the left edge and there is no
 * "before" to clear into — the fraction would collapse to zero and blank the bar. It keeps the
 * compact glow that setting has always had.
 */
const DIE_BAND_FROM = 0.55
const DIE_BAND_TO = 0.85
const DIE_THROUGH = 0.08
const DIE_MIN_REACH = 0.12

/** Cumulative weights for RAMP_FALLOFF, so a pick is one random number and a scan of six. */
function rampCdf(): number[] {
  const weights = Array.from({ length: STEPS }, (_, i) => RAMP_FALLOFF ** i)
  const total = weights.reduce((a, b) => a + b, 0)
  let run = 0
  return weights.map((wt) => (run += wt / total))
}

function palette(): string[] {
  return Array.from({ length: STEPS }, (_, i) => {
    const t = i / (STEPS - 1)
    const c = PALETTE_FROM.map((from, k) => Math.round(from + (PALETTE_TO[k] - from) * t))
    return `rgb(${c[0]}, ${c[1]}, ${c[2]})`
  })
}

interface Particle {
  /** Continuous position in CSS px. The physics stays in css space; only drawing is device space. */
  x: number
  /** Row INDEX, not a pixel offset — the pixel geometry is derived per display. */
  row: number
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
    const cdf = rampCdf()
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    let particles: Particle[] = []
    let raf = 0
    let last = 0
    let spawnDebt = 0
    let cols = 0
    // Device-pixel geometry, derived in resize() from the display density. Always integers.
    let pitchDev = 0
    let cellDev = 0
    let dpr = 1
    // Css-pixel mirror of the cell, for the physics.
    let cell = 0
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
      dpr = window.devicePixelRatio || 1
      // Derive the pitch from the ROW COUNT and the density, in device pixels. Six rows is the
      // requirement; the pitch is whatever satisfies it in about TARGET_H, and the seam is one device
      // pixel because that is the thinnest crisp line — half a css pixel at 2x, which is how six rows
      // fit where three used to without the seam growing as a fraction of the pitch.
      pitchDev = Math.max(MIN_PITCH_DEV, Math.round((TARGET_H * dpr) / ROWS))
      cellDev = pitchDev - SEAM_DEV
      cell = cellDev / dpr
      // ROWS blocks with ROWS-1 seams between them occupy exactly this, and the bar is sized to it
      // rather than the other way round — which is what makes the fit flush at any density, with no
      // leftover stripe at the bottom edge.
      const gridDev = ROWS * pitchDev - SEAM_DEV

      // Measure the PARENT, never the canvas itself. Now that the element's css size is pinned in JS
      // it has no size of its own to measure — an unsized canvas reports its intrinsic 300x150
      // default, so measuring itself made it lock to 300x150 forever, and there is no width at which
      // that is right.
      //
      // clientWidth, NOT getBoundingClientRect: the rect is the border box, but the canvas is
      // positioned at the PADDING box, so measuring the rect made the canvas 2px too tall and shifted
      // it a pixel down — which is where the grey strip above the tiles came from.
      const host = canvas.parentElement
      w = Math.max(4, host ? host.clientWidth : Math.floor(canvas.getBoundingClientRect().width))
      h = gridDev / dpr
      // The track takes its height FROM the grid. Writing to the element being observed is safe
      // because the value converges on the first pass: the notification that write triggers computes
      // the same height, sets nothing new, and the observer goes quiet.
      if (host && Math.abs(host.clientHeight - h) > 0.01) host.style.height = `${h}px`

      const wDev = Math.round(w * dpr)
      // Count whole blocks that FIT, rather than dividing the space up — dividing always leaves a
      // partial stripe over at the far edge.
      cols = Math.max(1, Math.floor((wDev - cellDev) / pitchDev) + 1)
      canvas.width = wDev
      canvas.height = gridDev
      canvas.style.width = `${w}px`
      canvas.style.height = `${h}px`
      // Identity transform: every drawing coordinate below is an integer number of device pixels, so
      // nothing lands on a fraction and nothing gets resampled.
      ctx.setTransform(1, 0, 0, 1, 0, 0)
      // Paint at once. The first draw used to happen inside the frame loop, which meant the track was
      // blank until rAF fired — and blank indefinitely wherever rAF never fires, such as a background
      // tab.
      draw()
    }
    // Observe the parent: the canvas's own box is now pinned, so it would never report a change.
    /** Weighted step index: strong blue most often, each lighter step rarer, the palest rarest. */
    const pick = (): number => {
      const r = Math.random()
      for (let i = 0; i < cdf.length; i++) if (r < cdf[i]) return i
      return cdf.length - 1
    }

    /**
     * Where a sparkle gives out — short of the knob, so the lit region ends before the thumb rather
     * than streaming through it.
     *
     * The old rule was the knob plus a random offset EITHER side, which meant half of them ran out
     * past the thumb. At the top setting that was worst: the knob is at the far right, so every
     * sparkle crossed the whole track, and the fullest the texture ever looked was the one setting
     * where nothing is done to the audio at all. Now the density falls away as the thumb approaches,
     * at every setting, which also pulls the four stops apart — they used to measure 7 / 31 / 57 / 61
     * percent, so Slow and Normal were all but indistinguishable.
     */
    const dieAtFor = (knob: number): number => {
      if (Math.random() < DIE_THROUGH) {
        return knob + (Math.random() * 2 - 1) * VICINITY * w
      }
      const span = DIE_BAND_TO - DIE_BAND_FROM
      return Math.max(knob * (DIE_BAND_FROM + span * Math.random()), DIE_MIN_REACH * w)
    }

    const spawn = (): Particle => {
      const knob = (pctRef.current / 100) * w
      return {
        x: -cell,
        row: (Math.random() * ROWS) | 0,
        vx: SPEED_MIN + Math.random() * (SPEED_MAX - SPEED_MIN),
        colour: colours[pick()],
        dieAt: Math.max(cell * 2, dieAtFor(knob)),
      }
    }

    const draw = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height)

      // 1. THE GRID LINES. The gradient goes down first across the whole canvas; the blocks then
      //    cover it, so the only thing left visible is the 1px lattice between them. Blue at the
      //    left edge, faded to nothing a little past the knob.
      const knob = (pctRef.current / 100) * w
      const fadeEnd = Math.max(pitchDev, (knob + SEAM_OVERSHOOT * w) * dpr)
      const seam = ctx.createLinearGradient(0, 0, fadeEnd, 0)
      seam.addColorStop(0, SEAM_FROM)
      seam.addColorStop(1, SEAM_TO)
      ctx.fillStyle = seam
      ctx.fillRect(0, 0, canvas.width, canvas.height)

      // 2. The blocks, flat grey, masking everything but the separators.
      ctx.fillStyle = BLOCK
      for (let c = 0; c < cols; c++) {
        for (let r = 0; r < ROWS; r++) ctx.fillRect(c * pitchDev, r * pitchDev, cellDev, cellDev)
      }

      // Sparkles REPLACE whole blocks — same grid, same size, same origin, by construction.
      for (const p of particles) {
        const col = Math.round((p.x * dpr) / pitchDev)
        if (col < 0 || col >= cols) continue
        let alpha = 1
        if (p.x < FADE_IN) alpha = Math.max(0, p.x / FADE_IN)
        const left = p.dieAt - p.x
        if (left < FADE_OUT) alpha = Math.min(alpha, Math.max(0, left / FADE_OUT))
        if (alpha <= 0) continue
        ctx.globalAlpha = alpha
        ctx.fillStyle = p.colour
        ctx.fillRect(col * pitchDev, p.row * pitchDev, cellDev, cellDev)
      }
      ctx.globalAlpha = 1
    }

    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(canvas.parentElement ?? canvas)

    if (reduced) {
      // No motion: one static field, so the track still reads as blue thinning to the right.
      for (let i = 0; i < cols * ROWS; i++) {
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
      particles = particles.filter((p) => p.x < p.dieAt && p.x < w + cell)

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
