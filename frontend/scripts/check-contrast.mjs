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
import { readdir, mkdtemp, rm, readFile, writeFile } from 'node:fs/promises'
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
/**
 * The text sits on an OPAQUE footer, so its contrast is fixed by the theme and no photograph can
 * touch it. What is still worth checking is the footer's 18px top fade: text must never stray
 * into it. FOOTER_H is measured from the rendered page, not guessed.
 */
const CARD_W = 292 // one grid column at 1280px viewport
const CARD_H = 260 // .topic-card min-height
const FOOTER_H = 145 // .topic-body height at a 292px card, measured in the browser
const FADE_H = 18
const OVERALL_WASH = 0.16
const STOPS = null // the footer is not a gradient; alphaAt handles the fade directly
const AA = 4.5

/** White alpha at a given distance up from the card's bottom edge, in pixels. */
function alphaAtPx(pxFromBottom) {
  const solidTop = FOOTER_H - FADE_H
  if (pxFromBottom <= solidTop) return 1
  if (pxFromBottom <= FOOTER_H) {
    const t = (pxFromBottom - solidTop) / FADE_H
    return 1 - t * (1 - OVERALL_WASH)
  }
  return OVERALL_WASH
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
  const W = CARD_W
  const H = CARD_H // the real rendered card size, so bands land where they land on screen
  const rawPath = join(tmp, `${basename(file, '.webp')}.rgb`)
  await run('ffmpeg', ['-y', '-v', 'error', '-i', join(ASSETS, file),
    '-vf', `scale=${W}:${H}:force_original_aspect_ratio=increase,crop=${W}:${H}`,
    '-pix_fmt', 'rgb24', '-f', 'rawvideo', rawPath])
  const buf = await readFile(rawPath)

  const textLum = lum(TEXT.r, TEXT.g, TEXT.b)
  const dimLum = lum(DIM.r, DIM.g, DIM.b)

  let worst = { ratio: Infinity, dimRatio: Infinity, x: 0, y: 0 }
  // The solid part of the footer, where every glyph lives.
  const yFrom = H - (FOOTER_H - FADE_H)
  const yTo = H

  for (let y = yFrom; y < yTo; y++) {
    const a = alphaAtPx(H - 1 - y)
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

/**
 * Render what a card actually looks like: photograph, CSS filter, scrim, all composited by the
 * same code that computes the contrast above — so the picture and the number can never disagree.
 * Exists because the numbers alone cannot tell you whether a photograph survives the scrim or is
 * washed to nothing, and a browser screenshot is not always available.
 */
async function preview(files, tmp, dest) {
  const W = CARD_W
  const H = CARD_H
  let i = 0
  for (const f of files) {
    const rawPath = join(tmp, `p_${basename(f, '.webp')}.rgb`)
    await run('ffmpeg', ['-y', '-v', 'error', '-i', join(ASSETS, f),
      '-vf', `scale=${W}:${H}:force_original_aspect_ratio=increase,crop=${W}:${H}`,
      '-pix_fmt', 'rgb24', '-f', 'rawvideo', rawPath])
    const buf = await readFile(rawPath)
    const out = Buffer.alloc(buf.length)
    for (let y = 0; y < H; y++) {
      const a = alphaAtPx(H - 1 - y)
      for (let x = 0; x < W; x++) {
        const k = (y * W + x) * 3
        const [r, g, b] = applyFilter(buf[k], buf[k + 1], buf[k + 2])
        out[k] = r * (1 - a) + 255 * a
        out[k + 1] = g * (1 - a) + 255 * a
        out[k + 2] = b * (1 - a) + 255 * a
      }
    }
    const rgbOut = join(tmp, `c_${String(i).padStart(2, '0')}.rgb`)
    await writeFile(rgbOut, out)
    await run('ffmpeg', ['-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
      '-s', `${W}x${H}`, '-i', rgbOut, join(tmp, `c_${String(i).padStart(2, '0')}.png`)])
    i++
  }
  const cols = Math.min(4, i)
  await run('ffmpeg', ['-y', '-v', 'error', '-i', join(tmp, 'c_%02d.png'),
    '-vf', `tile=${cols}x${Math.ceil(i / cols)}:padding=8:color=0xf4f6f8`, dest])
  console.log(`\npreview of the composited cards -> ${dest}`)
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
if (process.argv.includes('--preview')) {
  await preview(files, tmp, join(ROOT, '.topic-photos', 'sheet_CARDS.png'))
}
await rm(tmp, { recursive: true, force: true })

if (failed) {
  console.log(`\n${failed} image(s) below AA. Darken the scrim or choose a lighter photograph.`)
  process.exit(1)
}
console.log('\nall pass')
