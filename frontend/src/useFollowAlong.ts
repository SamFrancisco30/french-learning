import { useEffect, useRef, useState } from 'react'
import type { TimedWord } from './types'

/**
 * Which word is being spoken right now.
 *
 * Subscribes to the player's frame loop rather than reading a `position` prop, and puts
 * only the *index* in state. So a 60 Hz clock produces roughly four renders a second —
 * one per word — instead of sixty.
 *
 * Two decisions worth naming:
 *
 * Between words the previous word stays lit. Clearing the highlight in every gap makes it
 * flicker on and off through normal speech, and worse, it goes dark exactly during the
 * pauses that slow playback lengthens — the moments a learner most needs to see where
 * they are.
 *
 * Times arrive on the original-video timeline and the playhead is in playback seconds, so
 * `toOriginal` converts. That indirection is the whole reason this works unchanged at
 * 0.75×, where the two timelines diverge by seconds of inserted pause.
 */

/*
 * No lead time. Lighting each word slightly before its timestamp sounds like it should feel
 * more responsive, and a 60ms lead measured well on paper — but French word timings are
 * tight: 15-19% of words in this library are shorter than 60ms, and 64-86 words per unit sit
 * less than that from the next word's onset. A lead skips those words entirely, so they are
 * never highlighted at all. Given the whole point is to show the learner the word they are
 * hearing, never showing one is a far worse failure than showing it a frame late.
 *
 * With no lead, each word owns the interval from its own start to the next word's start, so
 * every word is lit, in order, for a visible span.
 */

/**
 * Two words are the same moment if their onsets are this close. Below the threshold there
 * is no information to tell them apart, and one frame at 60 Hz is 17ms.
 */
const SAME_MOMENT_S = 0.02

/** What actually gets highlighted: one or more ASR words spanning one moment of speech. */
export interface WordGroup {
  start: number
  end: number
  char_start: number
  char_end: number
}

/**
 * Turn ASR words into the units a reader sees, and give each one a moment of its own.
 *
 * Whisper's word array needs two repairs before it can drive a highlight.
 *
 * It tokenizes French elisions into fragments with degenerate timings — "aujourd'hui" arrives
 * as `aujourd` [0.30, 0.60] and `hui` [0.60, 0.60]. Highlighting those separately is wrong
 * twice over: it splits a word the learner reads as one, and a fragment whose successor
 * starts at the same instant can never be shown at all. So anything the text glues together
 * with an apostrophe or hyphen becomes one group. "aujourd'hui" then lights as one word.
 *
 * And it stamps consecutive *separate* words with the same onset — 7% of groups in this
 * library. Those cannot be ordered by timestamp, so one of them would never light. Merging
 * them would work, but two words lighting at once reads as a glitch. Instead the shared
 * interval is divided evenly among them: the words were certainly spoken in that order inside
 * that span, and an even split is the only guess available. It is approximate by construction
 * — an eye following along does not measure, and every word getting its turn matters more
 * than the split landing exactly.
 *
 * Measured before these repairs: 39 and 51 words per unit never highlighted at all.
 */
export function groupWords(text: string, words: TimedWord[]): WordGroup[] {
  const out: WordGroup[] = []
  for (const w of words) {
    const prev = out[out.length - 1]
    // Glued to the previous fragment by an apostrophe or hyphen: same word.
    if (prev && w.char_start >= prev.char_end && !/\s/.test(text.slice(prev.char_end, w.char_start))) {
      prev.end = Math.max(prev.end, w.end)
      prev.char_end = Math.max(prev.char_end, w.char_end)
      continue
    }
    out.push({ start: w.start, end: w.end, char_start: w.char_start, char_end: w.char_end })
  }

  // Spread any run of indistinguishable onsets across the time up to the next distinct one.
  for (let i = 0; i < out.length; ) {
    let j = i + 1
    while (j < out.length && out[j].start <= out[i].start + SAME_MOMENT_S) j++
    const n = j - i
    if (n > 1) {
      const from = out[i].start
      // The while loop guarantees out[j].start is more than SAME_MOMENT_S past `from`, so
      // this span is always positive. The tail has no successor, so fall back to its own end.
      const to = j < out.length ? out[j].start : Math.max(out[j - 1].end, from + 0.1)
      const step = (to - from) / n
      for (let k = 0; k < n; k++) {
        out[i + k].start = from + k * step
        out[i + k].end = from + (k + 1) * step
      }
    }
    i = j
  }
  return out
}

/** Index of the last word starting at or before `t`, or -1 if `t` precedes them all. */
function wordAt(words: WordGroup[], t: number): number {
  if (words.length === 0 || t < words[0].start) return -1
  let lo = 0
  let hi = words.length - 1
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (words[mid].start <= t) lo = mid
    else hi = mid - 1
  }
  return lo
}

export function useFollowAlong(
  words: WordGroup[],
  subscribe: (cb: (playbackSeconds: number) => void) => () => void,
  toOriginal: (playbackSeconds: number) => number,
  enabled: boolean,
): number {
  const [index, setIndex] = useState(-1)
  const current = useRef(-1)

  useEffect(() => {
    if (!enabled || words.length === 0) {
      current.current = -1
      setIndex(-1)
      return
    }
    return subscribe((playbackSeconds) => {
      const t = toOriginal(playbackSeconds)
      const i = current.current

      // Fast path: most frames land inside the word already lit, so there is nothing to
      // recompute and — crucially — no state to set.
      if (i >= 0 && t >= words[i].start && (i + 1 >= words.length || t < words[i + 1].start)) {
        return
      }
      const next = wordAt(words, t)
      if (next !== current.current) {
        current.current = next
        setIndex(next)
      }
    })
  }, [words, subscribe, toOriginal, enabled])

  return index
}

/** Sentence containing `at`, as a char span — used to tint the line being read. */
export function sentenceSpan(text: string, at: number): [number, number] {
  const isEnd = (ch: string) => ch === '.' || ch === '!' || ch === '?' || ch === '…'

  let start = 0
  for (let i = Math.min(at, text.length) - 1; i >= 0; i--) {
    if (isEnd(text[i])) {
      start = i + 1
      break
    }
  }
  let end = text.length
  for (let i = at; i < text.length; i++) {
    if (isEnd(text[i])) {
      end = i + 1
      break
    }
  }
  while (start < end && /\s/.test(text[start])) start++
  return [start, end]
}
