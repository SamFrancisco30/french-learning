#!/usr/bin/env node
/**
 * Prove the card text stays readable over every topic photograph.
 *
 * The scrim is a fixed gradient but the photographs are not: one is a pale sky, the next a dark
 * server room. Eyeballing a screenshot cannot tell you whether the worst pixel under the blurb
 * still clears WCAG AA, and the failure is exactly the kind that ships. So this measures it.
 *
 * Method: decode each WebP to raw RGB, apply the same brightness/saturation/contrast filter the
 * CSS applies, composite the same white gradient the CSS composites, then compute the WCAG
 * contrast ratio between the resulting background and the text colour — for the DARKEST row band
 * the text occupies, which is the worst case, and per column so a bright-left/dark-right image
 * cannot hide behind an average.
 *
 * Run from frontend/:  node scripts/check-contrast.mjs
 */

import { execFile } from 'node:child_process'
import { readdir, mkdtemp, rm, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join, resolve, basename } from 'node:path'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'

const run = promisify(execFile)
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const ASSETS = join(ROOT, 'src/assets/topics')

// Must mirror index.css.
const TEXT = { r: 0x19, g: 0x1d, b: 0x23 } //  --text
const DIM = { r: 0x5b, g: 0x66, b: 0x74 } //   --text-dim, used by the blurb
const FILTER = { saturate: 0.5, contrast: 0.94, brightness: 1.05 }
const STOPS = [
  // [fraction from the BOTTOM, white alpha]
  [0.0, 0.99],
  [0.46, 0.97],
  [0.66, 0.7],
  [0.85, 0.26],
  [1.0, 0.06],
]
/** The text block occupies roughly the bottom third of the card, plus padding. */
const TEXT_BAND_FROM_BOTTOM = [0.04, 0.42]
const AA = 4.5

function alphaAt(fracFromBottom) {
  for (let i = 1; i < STOPS.length; i++) {
    const [x0, a0] = STOPS[i - 1]
    const [x1, a1] = STOPS[i]
    if (fracFromBottom <= x1) {
      const t = (fracFromBottom - x0) / (x1 - x0 || 1)
      return a0 + t * (a1 - a0)
    }
  }
  return STOPS[STOPS.length - 1][1]
}

const clamp = (v) => Math.max(0, Math.min(255, v))

/** saturate -> contrast -> brightness, in the order CSS applies them. */
function applyFilter(r, g, b) {
  const l = 0.2126 * r + 0.7152 * g + 0.0722 * b
  const s = FILTER.saturate
  let R = l + (r - l) * s
  let G = l + (g - l) * s
  let B = l + (b - l) * s
  const c = FILTER.contrast
  R = (R - 127.5) * c + 127.5
  G = (G - 127.5) * c + 127.5
  B = (B - 127.5) * c + 127.5
  return [clamp(R * FILTER.brightness), clamp(G * FILTER.brightness), clamp(B * FILTER.brightness)]
}

const srgb = (c) => {
  const v = c / 255
  return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4
}
const lum = (r, g, b) => 0.2126 * srgb(r) + 0.7152 * srgb(g) + 0.0722 * srgb(b)
const ratio = (a, b) => {
  const [hi, lo] = a > b ? [a, b] : [b, a]
  return (hi + 0.05) / (lo + 0.05)
}

async function check(file, tmp) {
  const W = 292
  const H = 196 // the real rendered card size, so bands land where they land on screen
  const rawPath = join(tmp, `${basename(file, '.webp')}.rgb`)
  await run('ffmpeg', ['-y', '-v', 'error', '-i', join(ASSETS, file),
    '-vf', `scale=${W}:${H}:force_original_aspect_ratio=increase,crop=${W}:${H}`,
    '-pix_fmt', 'rgb24', '-f', 'rawvideo', rawPath])
  const buf = await readFile(rawPath)

  const textLum = lum(TEXT.r, TEXT.g, TEXT.b)
  const dimLum = lum(DIM.r, DIM.g, DIM.b)

  let worst = { ratio: Infinity, dimRatio: Infinity, x: 0, y: 0 }
  const yFrom = Math.round(H * (1 - TEXT_BAND_FROM_BOTTOM[1]))
  const yTo = Math.round(H * (1 - TEXT_BAND_FROM_BOTTOM[0]))

  for (let y = yFrom; y < yTo; y++) {
    const fracFromBottom = (H - 1 - y) / (H - 1)
    const a = alphaAt(fracFromBottom)
    for (let x = 0; x < W; x++) {
      const i = (y * W + x) * 3
      const [r, g, b] = applyFilter(buf[i], buf[i + 1], buf[i + 2])
      // Composite white at alpha a over the filtered photo.
      const R = r * (1 - a) + 255 * a
      const G = g * (1 - a) + 255 * a
      const B = b * (1 - a) + 255 * a
      const bg = lum(R, G, B)
      const cr = ratio(bg, textLum)
      if (cr < worst.ratio) worst = { ratio: cr, dimRatio: ratio(bg, dimLum), x, y }
    }
  }
  return worst
}

const files = (await readdir(ASSETS)).filter((f) => f.endsWith('.webp')).sort()
if (files.length === 0) {
  console.log('no topic photos yet')
  process.exit(0)
}

const tmp = await mkdtemp(join(tmpdir(), 'contrast-'))
let failed = 0
console.log(`worst-case contrast under the text block (AA needs ${AA}:1)\n`)
console.log('topic          title text   blurb text   verdict')
for (const f of files) {
  const w = await check(f, tmp)
  const ok = w.ratio >= AA && w.dimRatio >= AA
  if (!ok) failed++
  console.log(
    `${basename(f, '.webp').padEnd(14)} ${w.ratio.toFixed(2).padStart(9)}:1 ${w.dimRatio
      .toFixed(2)
      .padStart(11)}:1   ${ok ? 'pass' : `FAIL at (${w.x},${w.y})`}`,
  )
}
await rm(tmp, { recursive: true, force: true })

if (failed) {
  console.log(`\n${failed} image(s) below AA. Darken the scrim or choose a lighter photograph.`)
  process.exit(1)
}
console.log('\nall pass')
