"""Natural slow playback: stretch the words a little, put the rest of the time in the gaps.

The problem with `audio.playbackRate = 0.75` is that it stretches everything uniformly,
including the inside of each phoneme, which is what produces the underwater drawl. Nobody
slows down that way. A speaker talking deliberately keeps their articulation close to
normal and inserts *pauses*.

So this reconstructs the clip instead:

  1. one mild pitch-preserving stretch of the whole clip (default 0.9, i.e. words 10%
     slower) — small enough to stay artifact-free
  2. the remaining time is inserted as silence at word boundaries, weighted so that
     boundaries which are already pauses, and boundaries after punctuation, absorb more
     than boundaries in the middle of a word group

Why weighting matters, measured on this library: speech fills 75-91% of a unit and the
*median word gap is 0 ms*. Reaching 0.75x on a 95s unit needs ~32s of extra time against
~8s of existing gap, so scaling gaps proportionally would put nothing at most boundaries
and absurd pauses at the handful of real ones. Distributing additively — about 73 ms per
boundary at 0.75x — is what makes it sound like careful speech rather than broken audio.

Rather than TTS: re-speaking the transcript would lose the authentic speaker's voice and
accent, which is the thing the app exists to train. This keeps the original audio.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 44_100
CHANNELS = 1

# How much the words themselves slow down. 0.9 is inaudible as an artifact; below ~0.8
# atempo starts to smear consonants, which defeats the purpose.
DEFAULT_WORD_FACTOR = 0.9

# Splice fade, to stop the discontinuity at each cut producing a click. 4 ms is under the
# threshold of audibility as a fade but long enough to kill the transient.
FADE_MS = 4.0

# No single inserted pause should exceed this, however the weights fall out.
MAX_INSERT_S = 1.2

# Boundary weights. A boundary that is already a pause, or follows punctuation, is where a
# speaker would naturally take more time.
W_BASE = 1.0
W_EXISTING_PAUSE = 2.0  # existing gap >= PAUSE_THRESHOLD_S
W_SOFT_PUNCT = 1.5  # , ; : )
W_HARD_PUNCT = 3.0  # . ! ? …
PAUSE_THRESHOLD_S = 0.08

_SOFT_PUNCT = tuple(",;:)»")
_HARD_PUNCT = tuple(".!?…")


class TimeStretchError(RuntimeError):
    pass


@dataclass
class StretchResult:
    path: Path
    speed: float
    word_factor: float
    original_duration_s: float
    new_duration_s: float
    inserted_silence_s: float
    boundaries: int
    # [[original_clip_s, new_clip_s], ...] at word starts — lets the client translate an
    # exercise's replay window into the stretched timeline.
    time_map: list[list[float]] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.speed}x: {self.original_duration_s:.1f}s -> {self.new_duration_s:.1f}s "
            f"(words {self.word_factor}x, +{self.inserted_silence_s:.1f}s over "
            f"{self.boundaries} boundaries)"
        )


def _decode(path: Path, tempo: float) -> np.ndarray:
    """Decode to mono float32, optionally applying a pitch-preserving tempo change.

    atempo is WSOLA-based, so a mild factor keeps pitch and formants intact — the point is
    slower articulation, not a lower voice.
    """
    cmd = ["ffmpeg", "-v", "error", "-i", str(path)]
    if abs(tempo - 1.0) > 1e-3:
        cmd += ["-filter:a", f"atempo={tempo:.4f}"]
    cmd += ["-f", "s16le", "-acodec", "pcm_s16le", "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "-"]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        raise TimeStretchError(f"decode failed: {proc.stderr.decode()[-400:]}")
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def _encode(samples: np.ndarray, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(samples, -1.0, 1.0)
    raw = (pcm * 32767.0).astype(np.int16).tobytes()
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "-i", "-",
        "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(dst),
    ]
    proc = subprocess.run(cmd, input=raw, capture_output=True)
    if proc.returncode != 0:
        raise TimeStretchError(f"encode failed: {proc.stderr.decode()[-400:]}")


def _boundary_weight(prev_word_text: str, existing_gap_s: float) -> float:
    w = W_BASE
    if existing_gap_s >= PAUSE_THRESHOLD_S:
        w += W_EXISTING_PAUSE
    t = prev_word_text.rstrip()
    if t.endswith(_HARD_PUNCT):
        w += W_HARD_PUNCT
    elif t.endswith(_SOFT_PUNCT):
        w += W_SOFT_PUNCT
    return w


def natural_slow(
    src: Path,
    words: list[dict[str, Any]],
    *,
    speed: float,
    clip_start_s: float = 0.0,
    word_factor: float = DEFAULT_WORD_FACTOR,
    dst: Path,
) -> StretchResult:
    """Write a naturally-slowed version of `src` at `speed` (e.g. 0.75).

    `words` are the unit's word timings in ORIGINAL-VIDEO seconds; `clip_start_s` is the
    unit's start, so they can be rebased onto clip time.
    """
    if not (0.4 <= speed <= 1.0):
        raise ValueError(f"speed {speed} outside the useful range 0.4-1.0")
    if speed >= 0.999:
        raise ValueError("speed 1.0 needs no processing — serve the original clip")

    # Rebase word times onto clip time and drop anything unusable.
    ws: list[tuple[float, float, str]] = []
    for w in words:
        try:
            s = float(w["start"]) - clip_start_s
            e = float(w["end"]) - clip_start_s
        except (KeyError, TypeError, ValueError):
            continue
        if e > s >= 0:
            ws.append((s, e, str(w.get("word") or "")))
    if len(ws) < 8:
        raise TimeStretchError("too few usable word timings to reshape this clip")
    ws.sort(key=lambda t: t[0])

    # A single mild stretch of the whole clip is equivalent to stretching every word by the
    # same factor, and one WSOLA pass sounds better than hundreds of tiny ones.
    audio = _decode(src, word_factor)
    total = len(audio) / SAMPLE_RATE
    scale = 1.0 / word_factor  # original clip time -> stretched audio time

    orig_duration = max(e for _, e, _ in ws)
    target_duration = orig_duration / speed

    speech = sum(e - s for s, e, _ in ws)
    gaps = [max(0.0, ws[i + 1][0] - ws[i][1]) for i in range(len(ws) - 1)]
    gap_total = sum(g for g in gaps if g < 3.0)

    # Time budget: words have already grown by `scale`; the shortfall goes into gaps.
    new_speech = speech * scale
    new_gap_total = max(0.0, target_duration - new_speech)
    extra = max(0.0, new_gap_total - gap_total * scale)

    weights = [_boundary_weight(ws[i][2], gaps[i]) for i in range(len(gaps))]
    wsum = sum(weights) or 1.0
    inserts = [min(MAX_INSERT_S, extra * w / wsum) for w in weights]

    fade = max(1, int(SAMPLE_RATE * FADE_MS / 1000.0))
    ramp_out = np.linspace(1.0, 0.0, fade, dtype=np.float32)
    ramp_in = np.linspace(0.0, 1.0, fade, dtype=np.float32)

    def frame(t: float) -> int:
        return max(0, min(len(audio), int(round(t * scale * SAMPLE_RATE))))

    pieces: list[np.ndarray] = []
    time_map: list[list[float]] = []
    out_len = 0

    # Keep whatever precedes the first word (a breath, room tone) so the clip doesn't
    # start abruptly.
    head = audio[: frame(ws[0][0])]
    if head.size:
        pieces.append(head)
        out_len += head.size

    for i, (s, e, _) in enumerate(ws):
        time_map.append([round(s, 3), round(out_len / SAMPLE_RATE, 3)])

        # Span runs to the next word's start so intra-word-group audio is never dropped;
        # the inserted silence is added on top of it.
        end_t = ws[i + 1][0] if i + 1 < len(ws) else e
        seg = audio[frame(s) : frame(end_t)].copy()
        if seg.size == 0:
            continue

        pad = inserts[i] if i < len(inserts) else 0.0
        if pad > 0.001 and seg.size > 2 * fade:
            # Fade the tail so the splice into silence has no transient.
            seg[-fade:] *= ramp_out
        pieces.append(seg)
        out_len += seg.size

        if pad > 0.001:
            n = int(round(pad * SAMPLE_RATE))
            pieces.append(np.zeros(n, dtype=np.float32))
            out_len += n
            # And fade the next span in, applied when it is appended below.
            if i + 1 < len(ws):
                nxt_start = frame(ws[i + 1][0])
                nxt_end = frame(ws[i + 2][0] if i + 2 < len(ws) else ws[i + 1][1])
                if nxt_end - nxt_start > 2 * fade:
                    audio[nxt_start : nxt_start + fade] *= ramp_in

    tail = audio[frame(ws[-1][1]) :]
    if tail.size:
        pieces.append(tail)
        out_len += tail.size

    out = np.concatenate(pieces) if pieces else audio
    _encode(out, dst)

    result = StretchResult(
        path=dst,
        speed=speed,
        word_factor=word_factor,
        original_duration_s=round(total / scale, 3),
        new_duration_s=round(len(out) / SAMPLE_RATE, 3),
        inserted_silence_s=round(sum(inserts), 3),
        boundaries=len(inserts),
        time_map=time_map,
    )
    log.info("natural slow %s", result.summary())
    return result
