#!/usr/bin/env node
/**
 * Topic card photography: shortlist -> contact sheet -> shipped WebP.
 *
 * Photographs are chosen by eye, so this is deliberately two steps with a human in the middle:
 *
 *   node scripts/topic-photos.mjs sheets    downloads every candidate in candidates.json and
 *                                           tiles them into one contact sheet per topic, so a
 *                                           person can actually look before choosing
 *   node scripts/topic-photos.mjs build     downloads the picks in topic-photos.json, crops to
 *                                           16:9, resizes and writes src/assets/topics/<slug>.webp
 *                                           plus CREDITS.md
 *
 * Only CC0 and public-domain-mark images are permitted. `build` refuses anything else rather
 * than trusting the manifest, because a licence mistake is not the kind of thing that should be
 * caught after launch. Sources are recorded in CREDITS.md even though neither licence requires
 * attribution — knowing where an asset came from is worth having regardless.
 *
 * Needs only Node and ffmpeg. No image libraries, no API keys.
 */

import { execFile } from 'node:child_process'
import { mkdir, readFile, rm, writeFile, stat, readdir } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'

const run = promisify(execFile)
const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const ASSETS = join(ROOT, 'src/assets/topics')
const SCRATCH = join(ROOT, '.topic-photos')

/** Card art is at most ~900px wide on a wide screen; 2x that covers every display. */
const OUT_W = 1280
const OUT_H = 720
const QUALITY = 78

const ALLOWED = new Set(['cc0', 'pdm'])
const UA = 'french-learning-app/0.1 (topic card art; local dev)'

async function fetchImage(url, dest) {
  const res = await fetch(url, { headers: { 'User-Agent': UA }, redirect: 'follow' })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const type = res.headers.get('content-type') || ''
  if (!type.startsWith('image/')) throw new Error(`content-type ${type}`)
  await writeFile(dest, Buffer.from(await res.arrayBuffer()))
  return type
}

/** Scale-to-cover then centre-crop, so every card gets the same shape whatever came down. */
function coverFilter(w, h) {
  return `scale=${w}:${h}:force_original_aspect_ratio=increase,crop=${w}:${h}`
}

async function sheets() {
  const manifest = JSON.parse(await readFile(join(HERE, 'candidates.json'), 'utf8'))
  await rm(SCRATCH, { recursive: true, force: true })
  await mkdir(SCRATCH, { recursive: true })

  for (const topic of manifest) {
    const dir = join(SCRATCH, topic.slug)
    await mkdir(dir, { recursive: true })
    let n = 0
    for (const c of topic.candidates) {
      const raw = join(dir, `raw_${String(n).padStart(2, '0')}`)
      try {
        await fetchImage(c.url, raw)
        // Numbered PNGs so ffmpeg's tile filter can read them as one sequence.
        await run('ffmpeg', ['-y', '-v', 'error', '-i', raw,
          '-vf', coverFilter(560, 315), join(dir, `cand_${String(n).padStart(2, '0')}.png`)])
        console.log(`  ${topic.slug} [${n}] ok  ${c.title.slice(0, 54)}`)
        n++
      } catch (e) {
        console.log(`  ${topic.slug} [--] SKIP ${e.message}  ${c.url.slice(0, 70)}`)
      }
    }
    if (n === 0) {
      console.log(`  ${topic.slug}: no usable candidates`)
      continue
    }
    const cols = Math.min(3, n)
    const rows = Math.ceil(n / cols)
    await run('ffmpeg', ['-y', '-v', 'error', '-i', join(dir, 'cand_%02d.png'),
      '-vf', `tile=${cols}x${rows}:padding=6:color=0xdddddd`, join(SCRATCH, `sheet_${topic.slug}.png`)])
    console.log(`${topic.slug}: sheet with ${n} candidates (${cols}x${rows})`)
  }
  console.log(`\ncontact sheets in ${SCRATCH}`)
}

async function build() {
  const picks = JSON.parse(await readFile(join(HERE, 'topic-photos.json'), 'utf8'))
  await mkdir(ASSETS, { recursive: true })
  await mkdir(SCRATCH, { recursive: true })

  const bad = picks.filter((p) => !ALLOWED.has(p.license))
  if (bad.length) {
    console.error('Refusing to build — these are not cc0/pdm:')
    for (const p of bad) console.error(`  ${p.slug}: ${p.license}  ${p.url}`)
    process.exit(1)
  }

  const credits = []
  for (const p of picks) {
    const raw = join(SCRATCH, `pick_${p.slug}`)
    const out = join(ASSETS, `${p.slug}.webp`)
    await fetchImage(p.url, raw)
    await run('ffmpeg', ['-y', '-v', 'error', '-i', raw,
      '-vf', coverFilter(OUT_W, OUT_H),
      '-c:v', 'libwebp', '-quality', String(QUALITY), '-compression_level', '6', out])
    const { size } = await stat(out)
    console.log(`${p.slug.padEnd(12)} ${(size / 1024).toFixed(0).padStart(4)} KB  ${p.title.slice(0, 50)}`)
    credits.push({ ...p, bytes: size })
  }

  const total = credits.reduce((n, c) => n + c.bytes, 0)
  const lines = [
    '# Topic card photography',
    '',
    'Every image here is Creative Commons Zero (CC0) or public domain, sourced through the',
    'Openverse API. Neither licence requires attribution or imposes share-alike terms; these',
    'credits exist so the provenance of each asset is knowable, not because it is owed.',
    '',
    `${credits.length} images, ${(total / 1024).toFixed(0)} KB total, ${OUT_W}x${OUT_H} WebP q${QUALITY}.`,
    '',
    '| topic | title | creator | licence | provider | source |',
    '| --- | --- | --- | --- | --- | --- |',
    ...credits.map(
      (c) =>
        `| \`${c.slug}\` | ${c.title.replace(/\|/g, '/')} | ${c.creator || '—'} | ${c.license.toUpperCase()} | ${c.provider} | ${c.foreign_landing_url ? `[link](${c.foreign_landing_url})` : '—'} |`,
    ),
    '',
    '_Regenerate with `node scripts/topic-photos.mjs build` from `frontend/`._',
    '',
  ]
  await writeFile(join(ASSETS, 'CREDITS.md'), lines.join('\n'))
  console.log(`\n${credits.length} images, ${(total / 1024).toFixed(0)} KB total -> src/assets/topics/`)

  const have = (await readdir(ASSETS)).filter((f) => f.endsWith('.webp')).length
  console.log(`${have} webp files present`)
}

const cmd = process.argv[2]
if (cmd === 'sheets') await sheets()
else if (cmd === 'build') await build()
else {
  console.error('usage: topic-photos.mjs sheets | build')
  process.exit(2)
}
