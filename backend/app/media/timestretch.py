"""Natural slow playback: stretch the words continuously, lengthen silence that exists.

`audio.playbackRate = 0.75` stretches the inside of every phoneme, which is the underwater
drawl. A speaker talking deliberately instead keeps articulation near normal and takes
longer pauses. So this reconstructs the clip:

  1. one mild pitch-preserving stretch of the whole clip (default 0.9 — words 10% slower),
     continuous, so no cut is ever made through speech
  2. the remaining time is added *inside silences that are already there*, found from the
     waveform rather than from word timings

Point 2 is the whole design, and it is what a first attempt got wrong. Inserting silence
at every *word boundary* sounds broken, because French word boundaries are frequently not
acoustic boundaries at all. `l'emprise` is one continuous syllable, [lɑ̃pʁiz]; the ASR
reports `l'` and `emprise` as two word tokens, so a boundary-driven insert cuts a syllable
in half and is plainly audible. The same applies to every elision and liaison —
`les amis`, `il est`, `d'accord`.

Silence detected from the audio has none of that problem. If there is measurable silence
somewhere, the language permits a pause there, and lengthening it splices silence into
silence — inaudible by construction. There is no silence inside `l'emprise` to lengthen,
so the cut simply never happens.

The budget works out: a 95s unit holds ~154 silences of >=30 ms, and the ~22 s needed for
0.75x spreads to ~143 ms each, which is an ordinary phrase pause. When a clip is too dense
to absorb the time this way, the word stretch is deepened instead of cutting speech —
a continuous stretch degrades gracefully, a bad splice does not.

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

# The word stretch is deepened in these steps when the clip's own pauses cannot absorb the
# time budget. The step count is DERIVED from the range rather than picked, because the
# search must always end having actually decoded the factor it settled on — see the loop.
DEEPEN_STEP = 0.02

# Silence detection. The threshold adapts to each recording's own noise floor, so a quiet
# studio clip and a noisy street interview are both handled.
FRAME_MS = 5.0
NOISE_FLOOR_PERCENTILE = 10
NOISE_FLOOR_MULTIPLE = 2.5
MIN_SILENCE_FLOOR = 0.006
MAX_SILENCE_FLOOR = 0.030

# A silence must be at least this long to count as a place the language permits a pause.
# Below this it is more likely a stop consonant's closure than a real gap — and lengthening
# a plosive closure is exactly the artifact this design exists to avoid.
MIN_SILENCE_S = 0.030

# Hard per-pause ceiling used by the water-fill.
MAX_INSERT_S = 1.2

# The length a single added pause may reach before it stops sounding like speech and starts
# sounding like a gap. This — not MAX_INSERT_S — decides whether the clip's silence can
# carry the time budget.
#
# Measured: a sparse clip has ~155 usable pauses and absorbs 0.75x at ~136ms each, which is
# an ordinary phrase pause. A dense 226 wpm clip has only 37, so the same budget forced
# ~655ms each, and one blank's replay window came out 36% speech — the word was present but
# buried in room tone, which reads to a listener as the word never being said. Preferring a
# deeper continuous stretch over conspicuous gaps is the same trade this design makes
# everywhere else.
NATURAL_INSERT_S = 0.25

# Inserted pauses are filled with the clip's OWN room tone, not digital zero.
#
# Measured on this library: a recording's natural silences sit at RMS ~0.0068 — there is
# always breath, air and preamp noise. Padding with zeros drops the noise floor to nothing
# and brings it back, which is audible as a pump on every added pause.
#
# CRITICAL: the tone must come from genuinely silent audio, and it must be a GLOBAL
# reservoir rather than the run being extended. A mean-RMS threshold admits runs that
# contain quiet speech — measured, only 5 of 197 runs were true silence while 41 held
# speech peaking at 0.086 — and mirror-tiling one of those repeats the word, which is
# audible as the same word spoken twice. So runs are additionally gated on PEAK amplitude,
# and the reservoir is built only from the quietest stretches in the whole clip.
ROOM_TONE_MIN_SAMPLES = 256

# A run only counts as a place to insert if its peak stays under this. Mean RMS alone is
# not enough: a quiet syllable averages low while peaking well into speech territory.
SILENCE_PEAK_MULTIPLE = 2.0
SILENCE_PEAK_CEILING = 0.035

# Tone is sampled only from run cores at or below this peak — genuinely noise floor.
TONE_PEAK_CEILING = 0.020

# Both the insertion test and the tone sample use a run's CORE, not the whole run.
# A pause's edges hold the offset of the previous word and the onset of the next, so
# whole-run peaks read as speech even when the middle is pure noise floor. Judging by the
# core admits far more usable pauses (47 -> ~150 on the sample clip) and yields seconds of
# tone instead of 30 ms, which matters because tiling a very short sample pulses audibly.
RUN_CORE_FRACTION = 0.5
# Enough tone that tiling has no perceptible period.
TONE_TARGET_S = 1.0

# When the available silence can't absorb the required time, deepen the continuous word
# stretch instead of cutting speech. A stretch degrades gracefully; a bad splice does not.
MIN_WORD_FACTOR = 0.78

# How many deepening steps the search may take before it is standing on the floor. Derived
# so the loop can never run out of iterations with the factor still moving.
DEEPEN_STEPS = int(round((DEFAULT_WORD_FACTOR - MIN_WORD_FACTOR) / DEEPEN_STEP))

# Weighting: longer existing silences and silences after punctuation are stronger phrase
# boundaries, so they take more of the added time.
W_LENGTH_SCALE = 1.0
W_SOFT_PUNCT = 1.5
W_HARD_PUNCT = 3.0

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


def _find_silences(audio: np.ndarray) -> tuple[list[tuple[int, int]], float]:
    """Silence runs as (start_sample, end_sample), plus the threshold used.

    Detected from the waveform rather than from word timings, because a word boundary is
    frequently not an acoustic boundary in French. The threshold is relative to the clip's
    own noise floor so a studio recording and a street interview both work.
    """
    win = max(1, int(SAMPLE_RATE * FRAME_MS / 1000.0))
    n = len(audio) // win
    if n < 4:
        return [], 0.0
    rms = np.sqrt((audio[: n * win].reshape(n, win) ** 2).mean(axis=1))

    floor = float(np.percentile(rms, NOISE_FLOOR_PERCENTILE)) * NOISE_FLOOR_MULTIPLE
    threshold = float(np.clip(floor, MIN_SILENCE_FLOOR, MAX_SILENCE_FLOOR))

    quiet = rms < threshold
    peak_limit = min(SILENCE_PEAK_CEILING, threshold * SILENCE_PEAK_MULTIPLE)
    runs: list[tuple[int, int]] = []
    i = 0
    min_frames = max(1, int(MIN_SILENCE_S * 1000.0 / FRAME_MS))
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            if j - i >= min_frames:
                a, b = i * win, j * win
                # Peak gate, applied to the run's CORE: silence is inserted at the midpoint,
                # so what matters is whether the middle is clean, not whether the edges
                # carry the neighbouring words' onset and offset. Judging the whole run
                # rejected most genuine pauses.
                ca, cb = _run_core(a, b)
                if float(np.abs(audio[ca:cb]).max()) <= peak_limit:
                    runs.append((a, b))
            i = j
        else:
            i += 1
    return runs, threshold


def _run_core(a: int, b: int) -> tuple[int, int]:
    """The central portion of a silence run, away from the neighbouring words' edges."""
    span = b - a
    keep = max(ROOM_TONE_MIN_SAMPLES, int(span * RUN_CORE_FRACTION))
    mid = (a + b) // 2
    half = keep // 2
    return max(a, mid - half), min(b, mid + half)


def _tone_reservoir(audio: np.ndarray, runs: list[tuple[int, int]]) -> np.ndarray:
    """Genuinely-silent audio pooled from the whole clip, for filling added pauses.

    Deliberately global rather than per-run: sampling the run being extended is what let
    quiet speech get repeated. Only the very quietest runs qualify.
    """
    cores = [_run_core(a, b) for a, b in runs]
    quietest = [
        (a, b) for a, b in cores if float(np.abs(audio[a:b]).max()) <= TONE_PEAK_CEILING
    ]
    if not quietest:
        # Nothing clean enough. Take the single lowest-peak run and use its middle, which
        # is the least likely part to hold a transient.
        if not runs:
            return np.zeros(0, dtype=np.float32)
        a, b = min(runs, key=lambda r: float(np.abs(audio[r[0] : r[1]]).max()))
        mid = (a + b) // 2
        span = max(ROOM_TONE_MIN_SAMPLES, (b - a) // 4)
        return audio[max(a, mid - span) : min(b, mid + span)].copy()
    pooled = np.concatenate([audio[a:b] for a, b in quietest])
    if pooled.size >= int(TONE_TARGET_S * SAMPLE_RATE):
        return pooled
    # Short pool: mirror-extend it once so tiling has a longer, non-repeating period.
    return np.concatenate([pooled, pooled[::-1]])


def _room_tone(source: np.ndarray, n: int) -> np.ndarray:
    """`n` samples of room tone built from `source` by mirrored tiling.

    Mirroring keeps the waveform continuous across each seam, so the fill has the
    recording's own noise floor without introducing a periodic artifact.
    """
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    if source.size < ROOM_TONE_MIN_SAMPLES:
        # Nothing usable to sample — fall back to zeros rather than inventing noise.
        return np.zeros(n, dtype=np.float32)
    out = np.empty(n, dtype=np.float32)
    filled = 0
    flip = False
    while filled < n:
        chunk = source[::-1] if flip else source
        take = min(chunk.size, n - filled)
        out[filled : filled + take] = chunk[:take]
        filled += take
        flip = not flip
    return out


def _silence_weight(length_s: float, preceding_word: str) -> float:
    """Longer silences and silences after punctuation are stronger phrase boundaries."""
    w = 1.0 + W_LENGTH_SCALE * (length_s / MIN_SILENCE_S)
    t = preceding_word.rstrip()
    if t.endswith(_HARD_PUNCT):
        w += W_HARD_PUNCT
    elif t.endswith(_SOFT_PUNCT):
        w += W_SOFT_PUNCT
    return w


def _water_fill(budget_s: float, weights: list[float]) -> list[float]:
    """Distribute `budget_s` proportionally to `weights`, honouring MAX_INSERT_S per pause.

    Iterative because capping one pause frees its remainder for the others; without the
    redistribution the total falls short of the target duration.
    """
    n = len(weights)
    alloc = [0.0] * n
    remaining = budget_s
    open_idx = list(range(n))
    for _ in range(8):
        if remaining <= 1e-6 or not open_idx:
            break
        wsum = sum(weights[i] for i in open_idx) or 1.0
        newly_capped = []
        spent = 0.0
        for i in open_idx:
            want = alloc[i] + remaining * weights[i] / wsum
            if want >= MAX_INSERT_S:
                spent += MAX_INSERT_S - alloc[i]
                alloc[i] = MAX_INSERT_S
                newly_capped.append(i)
            else:
                spent += want - alloc[i]
                alloc[i] = want
        remaining -= spent
        if not newly_capped:
            break
        open_idx = [i for i in open_idx if i not in newly_capped]
    return alloc


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

    `words` are the unit's word timings in ORIGINAL-VIDEO seconds and are used only to
    label phrase boundaries and to build the time map — never to decide where to cut.
    """
    if not (0.4 <= speed <= 1.0):
        raise ValueError(f"speed {speed} outside the useful range 0.4-1.0")
    if speed >= 0.999:
        raise ValueError("speed 1.0 needs no processing — serve the original clip")

    # Word times, rebased onto clip time. Used for punctuation hints and the time map.
    ws: list[tuple[float, float, str]] = []
    for w in words:
        try:
            a = float(w["start"]) - clip_start_s
            b = float(w["end"]) - clip_start_s
        except (KeyError, TypeError, ValueError):
            continue
        if b > a >= 0:
            ws.append((a, b, str(w.get("word") or "")))
    ws.sort(key=lambda t: t[0])

    probe = _decode(src, 1.0)
    orig_duration = len(probe) / SAMPLE_RATE
    target_duration = orig_duration / speed

    # Choose the word stretch. Start at the requested factor and deepen it only if the
    # clip's own silence cannot absorb the remaining time within the per-pause cap —
    # deepening a continuous stretch is graceful, cutting speech is not.
    #
    # The deepening happens at the TOP of the loop, before the decode, so that every exit
    # leaves `audio` decoded at exactly the `factor` the rest of the function believes in.
    # Decrementing at the bottom looked equivalent and was not: when the loop ran out of
    # iterations it stepped the factor once more without re-decoding, so the time map was
    # built with scale=1/0.78 over audio actually stretched by 1/0.80. That is a 2.4% skew
    # accumulating from t=0 — measured at 3.1s by the end of a 98s clip, which silently
    # pointed every replay window at the wrong audio and put the clip's last three seconds
    # past the end of the file entirely.
    factor = word_factor
    for step in range(DEEPEN_STEPS + 1):
        if step:
            factor = max(MIN_WORD_FACTOR, factor - DEEPEN_STEP)
        audio = _decode(src, factor)
        runs, threshold = _find_silences(audio)
        # Capacity is measured against a NATURAL pause, not the hard cap, so a clip with
        # few gaps deepens the stretch instead of growing conspicuous holes.
        capacity = len(runs) * NATURAL_INSERT_S
        needed = target_duration - len(audio) / SAMPLE_RATE
        if needed <= 0 or needed <= capacity or factor <= MIN_WORD_FACTOR + 1e-6:
            break

    # MEASURED, not assumed. atempo does not hit its requested factor exactly, and deriving
    # the map's slope from the audio that actually exists makes the map correct by
    # construction rather than correct only while the factor bookkeeping is right.
    stretched_duration = len(audio) / SAMPLE_RATE
    scale = stretched_duration / orig_duration if orig_duration > 0 else 1.0 / factor
    needed = max(0.0, target_duration - stretched_duration)

    if not runs:
        # Nothing to lengthen — a wall of continuous speech. Serve the stretch alone rather
        # than forcing cuts, and say so in the result.
        _encode(audio, dst)
        log.warning("no usable silence in %s; served continuous stretch only", src.name)
        return StretchResult(
            path=dst, speed=speed, word_factor=round(factor, 3),
            original_duration_s=round(orig_duration, 3),
            new_duration_s=round(len(audio) / SAMPLE_RATE, 3),
            inserted_silence_s=0.0, boundaries=0,
            time_map=[[round(a, 3), round(a * scale, 3)] for a, _, _ in ws],
        )

    # Attribute each silence to the word that precedes it, for the punctuation hint.
    weights: list[float] = []
    for s0, s1 in runs:
        mid_orig = (s0 / SAMPLE_RATE) / scale
        prev = ""
        for a, b, t in ws:
            if b <= mid_orig + 0.02:
                prev = t
            else:
                break
        weights.append(_silence_weight((s1 - s0) / SAMPLE_RATE, prev))

    # Water-fill the time budget across pauses by weight. Simply capping each allocation
    # leaves the capped remainder unspent, so the clip comes out short of the requested
    # speed — measured 1.307x against a 1.333x target on a dense clip. Redistributing to
    # the uncapped pauses closes it.
    inserts = _water_fill(needed, weights)
    reservoir = _tone_reservoir(audio, runs)

    # Splice the extra silence into the middle of each existing silence. Both sides are
    # already near zero, so there is no discontinuity and no fade is required.
    pieces: list[np.ndarray] = []
    insert_points: list[tuple[int, int]] = []  # (sample position in stretched audio, added)
    cursor = 0
    for (s0, s1), pad in zip(runs, inserts):
        mid = (s0 + s1) // 2
        pieces.append(audio[cursor:mid])
        n_pad = int(round(pad * SAMPLE_RATE))
        if n_pad > 0:
            # Fill from the global quiet reservoir, never from this run — see the note on
            # ROOM_TONE_MIN_SAMPLES for why the local sample caused doubled words.
            pieces.append(_room_tone(reservoir, n_pad))
            insert_points.append((mid, n_pad))
        cursor = mid
    pieces.append(audio[cursor:])
    out = np.concatenate(pieces)

    _encode(out, dst)

    # Time map: playback time = stretched time + everything inserted before it.
    #
    # Breakpoints at word STARTS AND ENDS. With starts only, any time between two words —
    # including every word's own end, and therefore every replay window's edges — was
    # linearly interpolated across an interval that may contain an inserted pause. Since
    # the true mapping steps at the insertion point, that misplaced intermediate times by
    # up to the pause length, which is enough to clip a short word out of its own window.
    marks = sorted({round(a, 3) for a, _, _ in ws} | {round(b, 3) for _, b, _ in ws})
    prefix: list[tuple[int, int]] = []
    running = 0
    for at, n in sorted(insert_points):
        running += n
        prefix.append((at, running))

    def added_before(pos: int) -> int:
        # Binary search the cumulative insertion prefix rather than re-summing per mark.
        lo, hi = 0, len(prefix)
        while lo < hi:
            mid = (lo + hi) // 2
            if prefix[mid][0] <= pos:
                lo = mid + 1
            else:
                hi = mid
        return prefix[lo - 1][1] if lo else 0

    time_map: list[list[float]] = []
    for t in marks:
        pos = int(t * scale * SAMPLE_RATE)
        time_map.append([t, round((pos + added_before(pos)) / SAMPLE_RATE, 3)])

    result = StretchResult(
        path=dst,
        speed=speed,
        word_factor=round(factor, 3),
        original_duration_s=round(orig_duration, 3),
        new_duration_s=round(len(out) / SAMPLE_RATE, 3),
        inserted_silence_s=round(sum(inserts), 3),
        boundaries=len(inserts),
        time_map=time_map,
    )

    # The map must not describe audio that isn't there. A map running past the end of the
    # file means every mapped time is skewed, which is inaudible in review and shows up only
    # as a word that seems never to be spoken — so it is worth a loud line in the log.
    if time_map and time_map[-1][1] > result.new_duration_s + 0.05:
        log.warning(
            "time map overruns the audio by %.3fs (map ends %.3f, audio %.3f) for %s — "
            "replay windows will be misplaced",
            time_map[-1][1] - result.new_duration_s,
            time_map[-1][1],
            result.new_duration_s,
            src.name,
        )

    log.info("natural slow %s (silence threshold %.4f)", result.summary(), threshold)
    return result
