"""Is this source clear enough to learn from?

A listening exercise is only as good as its audio. A source with music under the speech, a
distant microphone, or two people talking over each other produces a transcript that looks fine
and an exercise a learner cannot actually do — and it costs a full LLM generation pass to find
out. So the gate runs immediately after transcription and before anything is generated.

WHAT IS ACTUALLY BEING MEASURED, honestly stated. There is no direct measure of "are the words
clear to a human". What there is:

  * Whisper's own per-segment `avg_logprob` — how confident the recogniser was. A model that
    struggled to hear the words is good evidence a learner will too. It is a proxy, and it has a
    known blind spot: a clear but strongly accented or regional speaker can be perfectly
    intelligible to the model and hard for a B1 learner, and vice versa. It catches mud, not
    difficulty.
  * `no_speech_prob` — how much of the audio the recogniser thinks is not speech at all. This is
    what catches music beds, applause, long silences and jingles.
  * Integrated loudness and true peak from ffmpeg — catches recordings that are almost silent, and
    ones clipped so hard the consonants are destroyed.
  * Words per second — a sanity check at both ends. Near-zero means the video is mostly not
    talking; absurdly high means the transcript is not tracking the audio.

THRESHOLDS ARE CALIBRATED AGAINST THIS LIBRARY, not taken from folklore. Measured across all 18
transcripts in the corpus at the time of writing:

    avg_logprob mean   -0.138 .. -0.236     (worst single segment -0.918)
    frac of segments with avg_logprob < -1   0.000 for every source
    no_speech_prob mean 0.043 .. 0.466
    frac of segments with no_speech_prob>0.5 0.000 .. 0.543
    words per second   1.43 .. 4.24

So the confidence floor sits far below anything already in the library: the gate's job is to pass
material like this and catch what is genuinely worse. The non-speech ceilings are the ones that
bite, because that is the measurement that actually varies here — and the three sources that warn
are the ones with music under the speech.
"""

from __future__ import annotations

import logging
import statistics
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ...asr.base import ASRResult

log = logging.getLogger(__name__)

# --- intelligibility ---------------------------------------------------------------------
#
# Mean confidence below this is mud. The library's worst source averages -0.236, so -0.55 is more
# than twice as bad as anything that has ever been accepted — deliberately generous, because a
# false reject costs a usable source and a human can always override with --force.
LOGPROB_MEAN_FLOOR = -0.55
# One bad patch is normal: a cough, a crosstalk moment, a name the model has never seen. A source
# where a sixth of the segments are that bad is not one bad patch.
LOGPROB_SEGMENT_FLOOR = -1.0
MAX_FRACTION_LOW_CONFIDENCE = 0.15

# --- speech vs everything else -----------------------------------------------------------
#
# Above this the recogniser thinks most of the file is not speech: a music video, a montage, a
# trailer. Nothing in the library exceeds 0.543, which is why reject sits above that and warn
# below it — the existing music-bed sources should be flagged for a human, not thrown away.
MAX_FRACTION_NON_SPEECH = 0.60
WARN_FRACTION_NON_SPEECH = 0.35

# --- level and dynamics ------------------------------------------------------------------
#
# Integrated loudness, LUFS. Broadcast sits near -23; -40 is a recording so quiet that a learner
# will be riding the volume control, and consonants disappear into the noise floor.
MIN_LOUDNESS_LUFS = -40.0
# True peak above this means clipping, which destroys exactly the plosives and fricatives a
# learner needs to tell "petit" from "pédi".
MAX_TRUE_PEAK_DBTP = 0.5

# --- pace --------------------------------------------------------------------------------
#
# Words per second. The library spans 1.43-4.24, so these bounds only catch material that is not
# really speech at one end or not really tracking at the other.
MIN_WORDS_PER_SECOND = 0.8
MAX_WORDS_PER_SECOND = 5.5

ACCEPT = "accept"
WARN = "warn"
REJECT = "reject"


@dataclass(frozen=True)
class QualityReport:
    verdict: str
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, float | None] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Whether the pipeline should proceed. A warning proceeds — it is for a human to read."""
        return self.verdict != REJECT

    def summary(self) -> str:
        head = {ACCEPT: "audio OK", WARN: "audio usable, with reservations", REJECT: "audio rejected"}[
            self.verdict
        ]
        if not self.reasons:
            return head
        return head + ": " + "; ".join(self.reasons)

    def to_dict(self) -> dict:
        return {"verdict": self.verdict, "reasons": list(self.reasons), "metrics": dict(self.metrics)}


def measure_loudness(audio_path: Path) -> dict[str, float | None]:
    """Integrated loudness and true peak, via ffmpeg's EBU R128 meter.

    Returns None values rather than raising when ffmpeg cannot measure the file: an unmeasurable
    level is a reason to fall back on the transcript signals, not to reject a source outright.
    """
    try:
        proc = subprocess.run(
            ["ffmpeg", "-nostats", "-i", str(audio_path), "-filter_complex", "ebur128=peak=true",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=300, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("could not measure loudness: %s", exc)
        return {"loudness_lufs": None, "true_peak_dbtp": None}

    lufs = peak = None
    # The summary block is printed last, so the final occurrence of each label is the integrated
    # figure for the whole file rather than one of the running per-frame updates.
    for line in proc.stderr.splitlines():
        text = line.strip()
        if text.startswith("I:") and "LUFS" in text:
            lufs = _first_float(text)
        elif text.startswith("Peak:") and "dBFS" in text:
            peak = _first_float(text)
    return {"loudness_lufs": lufs, "true_peak_dbtp": peak}


def _first_float(text: str) -> float | None:
    for token in text.replace(":", " ").split():
        try:
            return float(token)
        except ValueError:
            continue
    return None


def assess_transcript(result: ASRResult, duration_s: float) -> QualityReport:
    """Everything measurable from the ASR output alone.

    Split out from the file measurement so existing lessons can be re-scored from their stored
    transcripts without re-downloading a single byte of audio — see the `audit` command.
    """
    reasons: list[str] = []
    metrics: dict[str, float | None] = {}

    logprobs = [s.avg_logprob for s in result.segments if s.avg_logprob is not None]
    non_speech = [s.no_speech_prob for s in result.segments if s.no_speech_prob is not None]
    # `word_count`, not `len(words)`: it falls back to splitting the text when a backend gave no
    # word-level timestamps, so a missing word array reads as "no timings" rather than "no speech"
    # — which would otherwise trip the words-per-second floor and reject a perfectly good source.
    words = result.word_count

    metrics["segments"] = float(len(result.segments))
    metrics["words"] = float(words)
    metrics["words_per_second"] = round(words / duration_s, 3) if duration_s > 0 else None

    verdict = ACCEPT

    if logprobs:
        mean_lp = statistics.mean(logprobs)
        low = sum(1 for x in logprobs if x < LOGPROB_SEGMENT_FLOOR) / len(logprobs)
        metrics["logprob_mean"] = round(mean_lp, 3)
        metrics["logprob_worst"] = round(min(logprobs), 3)
        metrics["fraction_low_confidence"] = round(low, 3)
        if mean_lp < LOGPROB_MEAN_FLOOR:
            verdict = REJECT
            reasons.append(
                f"the recogniser was unsure throughout (mean confidence {mean_lp:.2f}, "
                f"floor {LOGPROB_MEAN_FLOOR})"
            )
        if low > MAX_FRACTION_LOW_CONFIDENCE:
            verdict = REJECT
            reasons.append(
                f"{low:.0%} of segments are barely intelligible to the recogniser "
                f"(limit {MAX_FRACTION_LOW_CONFIDENCE:.0%})"
            )
    else:
        # No confidence data at all — a backend that does not report it. Say so rather than
        # silently treating the absence of evidence as evidence of quality.
        reasons.append("no per-segment confidence from this ASR backend; judged on other signals")

    if non_speech:
        loud_frac = sum(1 for x in non_speech if x > 0.5) / len(non_speech)
        metrics["non_speech_mean"] = round(statistics.mean(non_speech), 3)
        metrics["fraction_non_speech"] = round(loud_frac, 3)
        if loud_frac > MAX_FRACTION_NON_SPEECH:
            verdict = REJECT
            reasons.append(
                f"{loud_frac:.0%} of the audio is not speech — music, applause or silence "
                f"(limit {MAX_FRACTION_NON_SPEECH:.0%})"
            )
        elif loud_frac > WARN_FRACTION_NON_SPEECH:
            if verdict == ACCEPT:
                verdict = WARN
            reasons.append(
                f"{loud_frac:.0%} of segments read as non-speech; there is probably music under "
                "the voice"
            )

    wps = metrics.get("words_per_second")
    if wps is not None:
        if wps < MIN_WORDS_PER_SECOND:
            verdict = REJECT
            reasons.append(f"barely any speech ({wps:.2f} words/s)")
        elif wps > MAX_WORDS_PER_SECOND:
            verdict = REJECT
            reasons.append(f"transcript is not tracking the audio ({wps:.2f} words/s)")

    return QualityReport(verdict=verdict, reasons=reasons, metrics=metrics)


def assess(audio_path: Path | None, result: ASRResult, duration_s: float) -> QualityReport:
    """The full gate: transcript signals plus the file's own level and dynamics."""
    report = assess_transcript(result, duration_s)
    reasons = list(report.reasons)
    metrics = dict(report.metrics)
    verdict = report.verdict

    if audio_path is not None and audio_path.exists():
        level = measure_loudness(audio_path)
        metrics.update(level)
        lufs, peak = level["loudness_lufs"], level["true_peak_dbtp"]
        if lufs is not None and lufs < MIN_LOUDNESS_LUFS:
            verdict = REJECT
            reasons.append(f"recording is far too quiet ({lufs:.1f} LUFS)")
        if peak is not None and peak > MAX_TRUE_PEAK_DBTP:
            if verdict != REJECT:
                verdict = WARN
            reasons.append(f"clipped peaks ({peak:.1f} dBFS) — consonants may be damaged")

    return QualityReport(verdict=verdict, reasons=reasons, metrics=metrics)
