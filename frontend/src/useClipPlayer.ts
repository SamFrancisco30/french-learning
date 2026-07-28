import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Audio control for one listening unit.
 *
 * Coordinate systems matter here: exercise and blank timings are stored in the
 * ORIGINAL video timeline, but each unit is served as its own clip where t=0 is
 * `unitStart`. Everything public on this hook takes original-timeline seconds and
 * converts internally, so callers never have to remember the offset.
 */
export function useClipPlayer(unitStart: number, unitEnd: number) {
  const ref = useRef<HTMLAudioElement | null>(null)
  const stopAt = useRef<number | null>(null)
  const loopRange = useRef<{ from: number; to: number } | null>(null)

  const [playing, setPlaying] = useState(false)
  const [rate, setRate] = useState(1)
  const [position, setPosition] = useState(0) // clip-relative seconds
  const [replays, setReplays] = useState(0)

  const duration = Math.max(0.001, unitEnd - unitStart)
  const toClip = useCallback((t: number) => Math.max(0, Math.min(duration, t - unitStart)), [duration, unitStart])

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
  }, [])

  useEffect(() => {
    if (ref.current) ref.current.playbackRate = rate
  }, [rate])

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

  /** Play a window given in ORIGINAL-video seconds. */
  const playWindow = useCallback(
    (from: number, to: number, loop = false) => {
      const el = ref.current
      if (!el) return
      const a = toClip(from)
      const b = Math.min(duration, Math.max(a + 0.2, toClip(to)))
      el.currentTime = a
      stopAt.current = b
      loopRange.current = loop ? { from: a, to: b } : null
      setReplays((n) => n + 1)
      void el.play()
    },
    [toClip, duration],
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
    playing,
    rate,
    setRate,
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
