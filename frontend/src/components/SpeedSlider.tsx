import { useEffect, useId, useRef, useState } from 'react'
import { SPEED_LEVELS, speedIndex } from '../useClipPlayer'
import { SpeedSparkle } from './SpeedSparkle'

/**
 * Playback speed as a four-stop slider: drag right to speed up, left to slow down.
 *
 * Built on a native `<input type="range">` rather than a custom pointer implementation, and that
 * is a deliberate choice rather than laziness. The native element brings dragging, click-to-jump,
 * arrow-key and Home/End support, touch handling, snapping to whole steps, and a real ARIA slider
 * role — all of which a hand-rolled div would have to reimplement, and most of which a hand-rolled
 * div quietly does not. What is left to do is styling, which is the part that was actually wanted.
 *
 * The track is a dithered ramp: a checkerboard of small squares over an accent gradient, masked so
 * the squares thin out toward the right. It reads as "more work is being done to the audio over
 * here", which is exactly what is happening — the left end stretches every word and inserts
 * seconds of pause, the right end is the untouched recording. The texture is on the LEFT for that
 * reason; putting it under "Normal" would colour the one setting where nothing happens at all.
 */
export function SpeedSlider({
  speed,
  onChange,
  disabled = false,
}: {
  speed: number
  onChange: (speed: number) => void
  disabled?: boolean
}) {
  const id = useId()
  const idx = speedIndex(speed)
  const level = SPEED_LEVELS[idx]
  const max = SPEED_LEVELS.length - 1
  // Where the thumb sits, so the fill and the glow can follow it.
  const pct = (idx / max) * 100

  // The level's name, its multiplier and what it does to the audio used to sit around the rail
  // permanently: a "SPEED" label, the current level, two end labels and a line of detail — five
  // pieces of text for one control, on a transport that now has to fit on a single line. They are
  // all still available, but only while the control is in use.
  //
  // `peek` is hover and keyboard focus, which last as long as the attention does. `flash` is a
  // change, which does not — so it times out. Both are needed: dragging the thumb with a mouse
  // never fires focus in some browsers, and a keyboard user never fires hover.
  const [peek, setPeek] = useState(false)
  const [flash, setFlash] = useState(false)
  const timer = useRef<number | undefined>(undefined)

  const flashOpen = () => {
    setFlash(true)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setFlash(false), 1600)
  }
  useEffect(() => () => window.clearTimeout(timer.current), [])

  // A speed that is still being prepared reports itself regardless of whether anyone is pointing at
  // it: the audio is about to change under the learner and that is worth saying unprompted.
  const open = peek || flash || disabled

  return (
    <div
      className={`speed ${disabled ? 'is-busy' : ''} ${open ? 'is-open' : ''}`}
      onPointerEnter={() => setPeek(true)}
      onPointerLeave={() => setPeek(false)}
    >
      {/* Two positional variables, deliberately. `--pos` is a percentage and drives the glow behind
          the thumb, which is a background-position and needs one. `--t` is the same thing unitless,
          because the knob's offset has to be arithmetic on a length — `calc(number * length)` is
          valid where `calc(percentage * length)` is not. */}
      <div
        className="speed-rail"
        style={{ ['--pos' as string]: `${pct}%`, ['--t' as string]: idx / max }}
      >
        <span className="speed-track" aria-hidden="true">
          {/* One canvas draws the block grid AND the sparkles. Two grids — a CSS gradient under a
              canvas — drifted apart on phase, on rounding, and on bitmap rescaling in turn, which
              is what made the sparkles look a different size from the tiles. See SpeedSparkle. */}
          <SpeedSparkle pct={pct} busy={disabled} />
        </span>
        <input
          id={id}
          className="speed-input"
          type="range"
          min={0}
          max={max}
          step={1}
          value={idx}
          disabled={disabled}
          onChange={(e) => {
            onChange(SPEED_LEVELS[Number(e.target.value)].speed)
            flashOpen()
          }}
          onFocus={() => setPeek(true)}
          onBlur={() => setPeek(false)}
          aria-label="Playback speed"
          // The thumb reads as "Slower", not "1" — the number would tell a screen-reader user
          // nothing, and the label is the whole point of the redesign.
          aria-valuetext={`${level.label}, ${level.speed}× speed`}
          title={level.hint}
        />
        {/*
          The visible handle, and the reason it is not the native thumb: a range thumb's position is
          derived from the input's value, so there is nothing to transition — it teleports between
          steps. This one is placed from `--t` and therefore glides.

          It comes AFTER the input so `:hover ~` can reach it, and it takes no pointer events, so the
          input underneath still receives every click, drag and key. The native thumb is still there
          at full size, just invisible: it remains the drag target and the accessibility object.
        */}
        <span className="speed-knob" aria-hidden="true" />
      </div>

      {/*
        Not conditionally rendered. It is always in the DOM and animated in and out on a class, so
        that it can fade rather than blink, and so a screen reader is not told a region appeared and
        vanished on every hover. `aria-live` is deliberately absent for the same reason: the input's
        own aria-valuetext already announces the level on change, and a live region would repeat it.

        The name alone. It used to carry the multiplier and a line describing what the setting does to
        the audio — "words at 84%, 0.23s added at each of 48 pauses" — which is a paragraph of
        explanation raised over the passage every time a hand passes the control. The name is what you
        need to know while reaching for it; the arithmetic behind it is not.
      */}
      <div className="speed-pop" role="note">
        {level.label}
      </div>
    </div>
  )
}
