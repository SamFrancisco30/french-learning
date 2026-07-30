import { useEffect, useRef, useState } from 'react'
import type { Transcript } from '../types'
import { FollowToggle, usePassageFollow } from './Follow'
import { SelectableText } from './Lookup'

/**
 * The transcript, following the voice.
 *
 * Highlighting the word being spoken is the point, but two smaller things do most of the
 * work of making it usable on fast material: the sentence being read is tinted, so the eye
 * can find its place again after glancing away, and clicking any word plays from there.
 *
 * Following is on by default here — a transcript exists to be read along with. The cloze
 * passage makes the opposite default, for the opposite reason.
 */
export function FollowTranscript({
  transcript,
  markSpans,
  expressionCount,
  language,
  playing,
}: {
  transcript: Transcript
  markSpans: number[][]
  expressionCount: number
  language: string
  playing: boolean
}) {
  const ref = useRef<HTMLDivElement | null>(null)
  const [follow, setFollow] = useState(true)

  const { available, words, activeWord, lineSpan, seekTo } = usePassageFollow(
    transcript.text,
    follow,
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
          {available && ' · click to play from there'}
        </span>
        {available && <FollowToggle on={follow} onChange={setFollow} />}
      </div>
      <SelectableText
        text={transcript.text}
        spans={markSpans}
        words={words}
        activeWord={activeWord}
        lineSpan={lineSpan}
        onWordClick={
          available && seekTo ? (i) => words[i] && seekTo(words[i].start) : undefined
        }
        lang={language}
      />
    </div>
  )
}
