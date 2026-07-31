import { useEffect, useRef } from 'react'

/**
 * The speed track's sparkle: individual squares drifting left to right, each a random colour from a
 * six-step blue-to-grey ramp, each dying somewhere around the knob.
 *
 * A canvas rather than CSS, because the brief is a particle system and CSS cannot express it: every
 * sparkle needs its own colour drawn from a set, its own speed, and its own despawn point. Earlier
 * CSS attempts could only move a mask over a fixed pattern, which gives a travelling *wave* — every
 * square lighting in lockstep — and no amount of gradient work turns that into independent
 * particles.
 *
 * Two details that make it read as tiles lighting up rather than as dots flying past:
 *
 * Sparkles are SNAPPED to the block grid. The grey base is a full field of 3px blocks on a 4px
 * pitch, so a particle's drawn x is quantised to that pitch and it hops cell to cell along a row
 * instead of sliding between them. Every cell is a real block, so any row and any column will do.
 *
 * They die around the KNOB, not at the right edge. Each picks a despawn point at the thumb plus a
 * random offset either side, so the lit region tracks the setting: at Slowest the knob is far left
 * and sparkles wink out early, at Normal they run the whole bar. The animation ends up describing
 * the control's state instead of merely decorating it.
 */

/** 6 steps from the accent blue to the tile grey, as the brief asks. */
const PALETTE_FROM = [58, 110, 165] //  --accent      #3a6ea5
const PALETTE_TO = [205, 213, 221] // --border-strong #cdd5dd
const STEPS = 6

const CELL = 3 // one block
const PITCH = 4 // block plus its 1px gutter — matches .speed-dither

const SPAWN_PER_SEC = 26
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
  x: number
  y: number
  vx: number
  colour: string
  dieAt: number
}

export function SpeedSparkle({ pct, busy }: { pct: number; busy: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  // Read through refs so a speed change never restarts the animation — the sparkles should keep
  // flowing while the knob moves under them.
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
    let w = 0
    let h = 0

    const resize = () => {
      const dpr = window.devicePixelRatio || 1
      const r = canvas.getBoundingClientRect()
      w = Math.max(1, Math.round(r.width))
      h = Math.max(1, Math.round(r.height))
      canvas.width = Math.round(w * dpr)
      canvas.height = Math.round(h * dpr)
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(canvas)

    /** Every row of the block grid is available, since no cell is blank. */
    const rows = () => {
      const out: number[] = []
      for (let y = 0; y + CELL <= h; y += PITCH) out.push(y)
      return out
    }

    const spawn = (): Particle => {
      const r = rows()
      const knob = (pctRef.current / 100) * w
      return {
        x: -CELL,
        y: r.length ? r[(Math.random() * r.length) | 0] : 0,
        vx: SPEED_MIN + Math.random() * (SPEED_MAX - SPEED_MIN),
        colour: colours[(Math.random() * colours.length) | 0],
        // A random region to the left OR right of the knob's vicinity.
        dieAt: Math.max(CELL * 2, knob + (Math.random() * 2 - 1) * VICINITY * w),
      }
    }

    const draw = () => {
      ctx.clearRect(0, 0, w, h)
      for (const p of particles) {
        // Quantise to the block pitch so it lands on a block, not across two.
        const gx = Math.round(p.x / PITCH) * PITCH
        let alpha = 1
        if (p.x < FADE_IN) alpha = Math.max(0, p.x / FADE_IN)
        const left = p.dieAt - p.x
        if (left < FADE_OUT) alpha = Math.min(alpha, Math.max(0, left / FADE_OUT))
        if (alpha <= 0) continue
        ctx.globalAlpha = alpha
        ctx.fillStyle = p.colour
        ctx.fillRect(gx, p.y, CELL, CELL)
      }
      ctx.globalAlpha = 1
    }

    if (reduced) {
      // No motion: seed a static field so the track still reads as a blue-to-grey ramp.
      for (let i = 0; i < 140; i++) {
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

      // Busy doubles the flow, so waiting for a slower variant to render is visible.
      spawnDebt += dt * SPAWN_PER_SEC * (busyRef.current ? 2.2 : 1)
      while (spawnDebt >= 1) {
        particles.push(spawn())
        spawnDebt -= 1
      }

      for (const p of particles) p.x += p.vx * dt * (busyRef.current ? 1.8 : 1)
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
