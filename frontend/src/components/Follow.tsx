import { createContext, useContext, useMemo, type ReactNode } from 'react'
import type { TimedWord } from '../types'
import { groupWords, sentenceSpan, useFollowAlong, type WordGroup } from '../useFollowAlong'

/**
 * Follow-along state, shared by every passage on the page.
 *
 * The unit renders the same words twice — once as a cloze passage with blanks, once as the
 * revealed transcript — and both want to show the word being spoken. Computing "which word is
 * it" once here and letting each passage decide whether to *display* it keeps the two in exact
 * agreement and means the frame loop has a single subscriber.
 *
 * Each passage keeps its own on/off switch, so a learner can follow along in one and not the
 * other. Both start on: the highlight is the reason the feature exists, and in the cloze it
 * gives nothing away — the blanked characters are never rendered.
 */

interface FollowValue {
  /**
   * The exact string the word spans are aligned to. Consumers compare their own passage against
   * it before using any offset — if the two ever diverge, the right behaviour is to show no
   * highlight rather than a confidently misplaced one.
   */
  text: string
  words: WordGroup[]
  /** -1 when nothing is being spoken yet. */
  activeWord: number
  /** Jump to a moment on the ORIGINAL-video timeline. */
  seekTo: (originalSeconds: number) => void
}

const FollowContext = createContext<FollowValue | null>(null)

export function FollowProvider({
  children,
  text,
  words,
  subscribe,
  toOriginal,
  seekTo,
}: {
  children: ReactNode
  /** The transcript text the word timings are aligned to. */
  text: string
  words: TimedWord[]
  subscribe: (cb: (playbackSeconds: number) => void) => () => void
  toOriginal: (playbackSeconds: number) => number
  seekTo: (originalSeconds: number) => void
}) {
  // Highlight whole words, not ASR fragments — see groupWords.
  const groups = useMemo(() => groupWords(text, words), [text, words])
  const activeWord = useFollowAlong(groups, subscribe, toOriginal, groups.length > 0)

  const value = useMemo<FollowValue>(
    () => ({ text, words: groups, activeWord, seekTo }),
    [text, groups, activeWord, seekTo],
  )
  return <FollowContext.Provider value={value}>{children}</FollowContext.Provider>
}

export interface PassageFollow {
  /** True when this passage can be followed at all — words loaded and offsets applicable. */
  available: boolean
  words: WordGroup[]
  /** -1 when following is off for this passage, or nothing is being spoken. */
  activeWord: number
  lineSpan: [number, number] | null
  seekTo?: (originalSeconds: number) => void
}

/**
 * Follow-along data for one passage. `passageText` is checked against the aligned text, so a
 * passage that is not the same string simply cannot be highlighted.
 */
export function usePassageFollow(passageText: string, enabled: boolean): PassageFollow {
  const ctx = useContext(FollowContext)
  const available = !!ctx && ctx.words.length > 0 && ctx.text === passageText
  const words = available ? ctx!.words : []
  const activeWord = available && enabled ? ctx!.activeWord : -1

  const lineSpan = useMemo<[number, number] | null>(
    () =>
      activeWord >= 0 && words[activeWord]
        ? sentenceSpan(passageText, words[activeWord].char_start)
        : null,
    [activeWord, words, passageText],
  )

  return { available, words, activeWord, lineSpan, seekTo: ctx?.seekTo }
}

export function FollowToggle({
  on,
  onChange,
  className = '',
}: {
  on: boolean
  onChange: (next: boolean) => void
  className?: string
}) {
  return (
    <button
      type="button"
      className={`follow-toggle ${on ? 'on' : ''} ${className}`}
      onClick={() => onChange(!on)}
      title={
        on ? 'Stop highlighting the word being spoken' : 'Highlight the word being spoken'
      }
    >
      {on ? '◉ following' : '○ follow the voice'}
    </button>
  )
}
