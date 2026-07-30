import { useCallback, useEffect, useRef, useState } from 'react'
import { clips } from './api'
import type { ClipVariant } from './types'

/**
 * Audio control for one listening unit, with natural slow playback.
 *
 * Two coordinate systems, and a third once a slow variant is in play:
 *
 *   original-video seconds   what exercises and blanks store
 *   clip seconds             original minus `unitStart` — each unit is its own clip
 *   playback seconds         where that moment sits in the *reshaped* audio
 *
 * At 1× the last two coincide. Below 1× they don't: the variant keeps articulation close
 * to normal and inserts pauses between words, so time accumulates unevenly. `time_map`
 * carries original→playback breakpoints at each word start, and everything public on this
 * hook takes original-video seconds and converts, so callers never deal with any of it.
 *
 * Slow speeds change the audio SOURCE rather than `playbackRate`. Uniform rate change
 * stretches the inside of every phoneme, which is the underwater drawl this exists to
 * avoid.
 */

/** Speeds offered in the UI. 1 is the untouched original. */
export const SPEEDS = [0.75, 0.9, 1] as const

function interpolate(t: number, map: number[][]): number {
  if (map.length === 0) return t
  if (t <= map[0][0]) return map[0][1]
  const last = map[map.length - 1]
  if (t >= last[0]) {
    // Past the final breakpoint the two timelines advance together again.
    return last[1] + (t - last[0])
  }
  let lo = 0
  let hi = map.length - 1
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1
    if (map[mid][0] <= t) lo = mid
    else hi = mid
  }
  const [ax, ay] = map[lo]
  const [bx, by] = map[hi]
  const span = bx - ax
  return span <= 0 ? ay : ay + ((t - ax) / span) * (by - ay)
}

export function useClipPlayer(
  unitId: number | null,
  unitStart: number,
  unitEnd: number,
  fallbackUrl: string | null,
) {
  const ref = useRef<HTMLAudioElement | null>(null)
  const stopAt = useRef<number | null>(null)
  const loopRange = useRef<{ from: number; to: number } | null>(null)

  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState<number>(1)
  const [variant, setVariant] = useState<ClipVariant | null>(null)
  const [loadingSpeed, setLoadingSpeed] = useState(false)
  const [position, setPosition] = useState(0)
  const [replays, setReplays] = useState(0)

  const clipDuration = Math.max(0.001, unitEnd - unitStart)
  const duration = variant?.duration_s ?? clipDuration
  const src = variant?.url ?? fallbackUrl ?? undefined
  const timeMap = variant?.time_map ?? []

  /** original-video seconds -> playback seconds in whatever variant is loaded */
  const toPlayback = useCallback(
    (t: number) => {
      const clipT = Math.max(0, Math.min(clipDuration, t - unitStart))
      return Math.max(0, Math.min(duration, interpolate(clipT, timeMap)))
    },
    [clipDuration, unitStart, duration, timeMap],
  )

  // Fetch the variant whenever the chosen speed changes. Position is preserved as a
  // fraction of the clip: the two timelines are not linearly related, and re-deriving an
  // exact offset would need the inverse map for a benefit nobody would notice.
  useEffect(() => {
    if (unitId == null) return
    let live = true
    const el = ref.current
    const fraction = el && el.duration > 0 ? el.currentTime / el.duration : 0
    const wasPlaying = el ? !el.paused : false

    setLoadingSpeed(true)
    clips
      .variant(unitId, speed)
      .then((v) => {
        if (!live) return
        setVariant(v)
        // Restore roughly where the learner was, then resume if they were listening.
        requestAnimationFrame(() => {
          const a = ref.current
          if (!a) return
          const restore = () => {
            if (a.duration > 0) a.currentTime = fraction * a.duration
            if (wasPlaying) void a.play()
            a.removeEventListener('loadedmetadata', restore)
          }
          if (a.readyState >= 1) restore()
          else a.addEventListener('loadedmetadata', restore)
        })
      })
      .catch(() => {
        // Fall back to the original clip rather than leaving the player dead.
        if (live) setVariant(null)
      })
      .finally(() => live && setLoadingSpeed(false))
    return () => {
      live = false
    }
  }, [unitId, speed])

  useEffect(() => {
    const el = ref.current
    if (!el) return

    const onTime = () => {
      setPosition(el.currentTime)
      const limit = stopAt.current
      if (limit !== null && el.currentTime >= limit) {
        const loop = loopRange.current
        if (loop) {
          el.currentTime = loop.from
          setReplays((n) => n + 1)
        } else {
          el.pause()
          stopAt.current = null
        }
      }
    }
    const onPlay = () => setPlaying(true)
    const onPause = () => setPlaying(false)
    const onEnded = () => {
      setPlaying(false)
      stopAt.current = null
      loopRange.current = null
    }

    el.addEventListener('timeupdate', onTime)
    el.addEventListener('play', onPlay)
    el.addEventListener('pause', onPause)
    el.addEventListener('ended', onEnded)
    return () => {
      el.removeEventListener('timeupdate', onTime)
      el.removeEventListener('play', onPlay)
      el.removeEventListener('pause', onPause)
      el.removeEventListener('ended', onEnded)
    }
  }, [src])

  const toggle = useCallback(() => {
    const el = ref.current
    if (!el) return
    if (el.paused) {
      stopAt.current = null
      loopRange.current = null
      void el.play()
    } else {
      el.pause()
    }
  }, [])

  /** Play a window given in ORIGINAL-video seconds, wherever it lands in this variant. */
  const playWindow = useCallback(
    (from: number, to: number, loop = false) => {
      const el = ref.current
      if (!el) return
      const a = toPlayback(from)
      const b = Math.min(duration, Math.max(a + 0.2, toPlayback(to)))
      el.currentTime = a
      stopAt.current = b
      loopRange.current = loop ? { from: a, to: b } : null
      setReplays((n) => n + 1)
      void el.play()
    },
    [toPlayback, duration],
  )

  const stopLoop = useCallback(() => {
    loopRange.current = null
    stopAt.current = null
    ref.current?.pause()
  }, [])

  const seekFraction = useCallback(
    (f: number) => {
      const el = ref.current
      if (!el) return
      stopAt.current = null
      loopRange.current = null
      el.currentTime = Math.max(0, Math.min(duration, f * duration))
      setPosition(el.currentTime)
    },
    [duration],
  )

  const restart = useCallback(() => {
    const el = ref.current
    if (!el) return
    stopAt.current = null
    loopRange.current = null
    el.currentTime = 0
    void el.play()
  }, [])

  return {
    ref,
    src,
    playing,
    speed,
    setSpeed,
    variant,
    loadingSpeed,
    position,
    duration,
    replays,
    toggle,
    playWindow,
    stopLoop,
    seekFraction,
    restart,
    isLooping: () => loopRange.current !== null,
  }
}

export function fmt(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}
