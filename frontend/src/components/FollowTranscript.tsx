import { useEffect, useMemo, useRef, useState } from 'react'
import type { Transcript } from '../types'
import { groupWords, sentenceSpan, useFollowAlong } from '../useFollowAlong'
import { SelectableText } from './Lookup'

/**
 * The transcript, following the voice.
 *
 * Highlighting the word being spoken is the point, but two smaller things do most of the
 * work of making it usable on fast material: the sentence being read is tinted, so the eye
 * can find its place again after glancing away, and clicking any word plays from there.
 *
 * The follow toggle exists because this is not always wanted. Reading ahead of the audio is
 * a legitimate way to use a transcript, and a highlight that keeps yanking the page is
 * hostile to it.
 */
export function FollowTranscript({
  transcript,
  markSpans,
  expressionCount,
  language,
  playing,
  subscribe,
  toOriginal,
  seekTo,
}: {
  transcript: Transcript
  markSpans: number[][]
  expressionCount: number
  language: string
  playing: boolean
  subscribe: (cb: (playbackSeconds: number) => void) => () => void
  toOriginal: (playbackSeconds: number) => number
  /** Seeks to a moment on the ORIGINAL-video timeline. */
  seekTo: (originalSeconds: number) => void
}) {
  const ref = useRef<HTMLDivElement | null>(null)
  const [follow, setFollow] = useState(true)

  // Highlight whole words, not ASR fragments — see groupWords.
  const words = useMemo(
    () => groupWords(transcript.text, transcript.words),
    [transcript.text, transcript.words],
  )
  const canFollow = words.length > 0
  const activeWord = useFollowAlong(words, subscribe, toOriginal, follow && canFollow)

  const lineSpan = useMemo<[number, number] | null>(
    () =>
      activeWord >= 0 && words[activeWord]
        ? sentenceSpan(transcript.text, words[activeWord].char_start)
        : null,
    [activeWord, words, transcript.text],
  )

  // Keep the lit word visible, but only nudge: `block: 'nearest'` is a no-op while the word
  // is already on screen, so this never fights a learner who is scrolling. And it stays out
  // of the way entirely once the transcript itself has been scrolled off.
  useEffect(() => {
    if (!follow || !playing || activeWord < 0) return
    const root = ref.current
    const el = root?.querySelector('.now')
    if (!root || !el) return
    const box = root.getBoundingClientRect()
    if (box.bottom < 0 || box.top > window.innerHeight) return
    el.scrollIntoView({
      block: 'nearest',
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches
        ? 'auto'
        : 'smooth',
    })
  }, [activeWord, follow, playing])

  return (
    <div className="transcript" ref={ref}>
      <div className="label">
        <span>
          Transcript · {transcript.asr_backend}/{transcript.asr_model}
          {markSpans.length > 0 && ` · ${expressionCount} expressions marked`}
          {' · select any word to translate'}
          {canFollow && ' · click to play from there'}
        </span>
        {canFollow && (
          <button
            className={`follow-toggle ${follow ? 'on' : ''}`}
            onClick={() => setFollow((f) => !f)}
            title={
              follow
                ? 'Stop highlighting the word being spoken'
                : 'Highlight the word being spoken'
            }
          >
            {follow ? '◉ following' : '○ follow the voice'}
          </button>
        )}
      </div>
      <SelectableText
        text={transcript.text}
        spans={markSpans}
        words={canFollow ? words : []}
        activeWord={follow ? activeWord : -1}
        lineSpan={follow ? lineSpan : null}
        onWordClick={
          canFollow ? (i) => words[i] && seekTo(words[i].start) : undefined
        }
        lang={language}
      />
    </div>
  )
}
