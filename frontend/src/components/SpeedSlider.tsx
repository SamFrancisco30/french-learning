import { useId } from 'react'
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
  detail,
}: {
  speed: number
  onChange: (speed: number) => void
  disabled?: boolean
  /** Optional line under the label — what the reshaping actually did to this clip. */
  detail?: string
}) {
  const id = useId()
  const idx = speedIndex(speed)
  const level = SPEED_LEVELS[idx]
  const max = SPEED_LEVELS.length - 1
  // Where the thumb sits, so the fill and the glow can follow it.
  const pct = (idx / max) * 100

  return (
    <div className={`speed ${disabled ? 'is-busy' : ''}`}>
      <div className="speed-head">
        <label className="speed-title" htmlFor={id}>
          Speed
        </label>
        <span className="speed-now">
          <b>{level.label}</b>
          <span className="speed-x">{level.speed}×</span>
        </span>
      </div>

      <div className="speed-rail" style={{ ['--pos' as string]: `${pct}%` }}>
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
          onChange={(e) => onChange(SPEED_LEVELS[Number(e.target.value)].speed)}
          aria-label="Playback speed"
          // The thumb reads as "Slower", not "1" — the number would tell a screen-reader user
          // nothing, and the label is the whole point of the redesign.
          aria-valuetext={`${level.label}, ${level.speed}× speed`}
          title={level.hint}
        />
      </div>

      <div className="speed-ends" aria-hidden="true">
        <span>{SPEED_LEVELS[0].label}</span>
        <span>{SPEED_LEVELS[max].label}</span>
      </div>

      <div className="speed-hint">{disabled ? 'preparing the audio…' : (detail ?? level.hint)}</div>
    </div>
  )
}
