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

/**
 * Speeds offered in the UI. 1 is the untouched original.
 *
 * 0.5 is qualitatively different from the others: the word stretch tops out around 1.28x, so
 * most of the extra time has to come from the gaps between words, which run to a second or more
 * each. That is dictation pace — a phrase, then room to think — rather than slowed speech.
 */
export const SPEEDS = [0.5, 0.75, 0.9, 1] as const

/** Piecewise-linear lookup over `map`, reading column `from` and returning column `to`. */
function interpolate(t: number, map: number[][], from: 0 | 1 = 0, to: 0 | 1 = 1): number {
  if (map.length === 0) return t
  if (t <= map[0][from]) return map[0][to]
  const last = map[map.length - 1]
  if (t >= last[from]) {
    // Past the final breakpoint the two timelines advance together again.
    return last[to] + (t - last[from])
  }
  let lo = 0
  let hi = map.length - 1
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1
    if (map[mid][from] <= t) lo = mid
    else hi = mid
  }
  const ax = map[lo][from]
  const ay = map[lo][to]
  const bx = map[hi][from]
  const by = map[hi][to]
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

  /** playback seconds -> original-video seconds. The inverse, read off the same map. */
  const toOriginal = useCallback(
    (t: number) => unitStart + interpolate(Math.max(0, t), timeMap, 1, 0),
    [unitStart, timeMap],
  )

  // Follow-along highlighting needs the playhead far more often than the ~4 Hz that
  // `timeupdate` fires: at 220 words/min a word lasts ~270ms, so a quarter-second update
  // lands the highlight a whole word behind. But putting a 60 Hz value in state would
  // re-render the entire drill on every frame. So the frame loop pushes the time to
  // subscribers instead, and a subscriber re-renders only when its own derived value —
  // which word is being spoken — actually changes.
  const listeners = useRef(new Set<(t: number) => void>())
  const emit = useCallback(() => {
    const t = ref.current?.currentTime ?? 0
    listeners.current.forEach((fn) => fn(t))
  }, [])

  const subscribe = useCallback((cb: (t: number) => void) => {
    listeners.current.add(cb)
    cb(ref.current?.currentTime ?? 0)
    return () => {
      listeners.current.delete(cb)
    }
  }, [])

  // Two clocks, deliberately. The frame loop is smooth but browsers suspend
  // requestAnimationFrame entirely in a hidden tab — measured: 0 frames in 500ms — while the
  // audio keeps playing. On its own that leaves a learner who switches windows mid-clip
  // returning to a highlight frozen on a word from minutes ago. `timeupdate` keeps firing
  // there, so it drives the same emit at ~4 Hz as a floor. Subscribers are idempotent, so
  // both clocks running at once costs nothing.
  useEffect(() => {
    if (!playing) {
      emit() // settle on the final position when playback stops
      return
    }
    let raf = 0
    const tick = () => {
      emit()
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [playing, emit])

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
            else emit() // paused: nothing else will move the highlight to the new position
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
  }, [unitId, speed, emit])

  useEffect(() => {
    const el = ref.current
    if (!el) return

    const onTime = () => {
      setPosition(el.currentTime)
      emit()
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
  }, [src, emit])

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
      emit()
      void el.play()
    },
    [toPlayback, duration, emit],
  )

  /** Jump to a moment given in ORIGINAL-video seconds and keep playing from there. */
  const seekTo = useCallback(
    (t: number) => {
      const el = ref.current
      if (!el) return
      stopAt.current = null
      loopRange.current = null
      el.currentTime = toPlayback(t)
      setPosition(el.currentTime)
      emit()
      void el.play()
    },
    [toPlayback, emit],
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
      emit()
    },
    [duration, emit],
  )

  const restart = useCallback(() => {
    const el = ref.current
    if (!el) return
    stopAt.current = null
    loopRange.current = null
    el.currentTime = 0
    emit()
    void el.play()
  }, [emit])

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
    seekTo,
    stopLoop,
    seekFraction,
    restart,
    subscribe,
    toOriginal,
    isLooping: () => loopRange.current !== null,
  }
}

export function fmt(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}
