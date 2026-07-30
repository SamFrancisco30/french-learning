"""Difficulty estimation for a listening unit.

Listening difficulty is mostly *not* about vocabulary — it's about how fast the words
arrive and whether the learner gets recovery time. So speech rate carries the heaviest
weight, then lexical rarity, then syntactic load.

The output is a 0-100 score plus a CEFR band. It's a heuristic for ordering a library
and picking exercise aggressiveness, not a certified CEFR assessment — the component
breakdown is returned alongside so it can be inspected and recalibrated against real
learner data later.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any

from ...languages import LanguageProfile
from ...models import CEFR_LEVELS

# Calibration anchors.
#
# The lexical anchors were re-fitted after measuring 33 units of real French media
# (learner podcasts through fast financial debate to literary theory). The original
# 3.2-5.2 window was chosen a priori and turned out far too wide: observed mean content
# Zipf spans only 3.97-5.05, and observed rare-word share peaks at 0.15 against a 0.30
# saturation point. The lexical and rarity components could therefore reach only ~54% and
# ~52% of their range, stranding ~24 of the 100 points and making C1 (>66) unreachable no
# matter how hard the source.
#
# The new anchors sit just outside the observed extremes rather than exactly on them —
# fitting tightly to 33 samples would define "hardest French" as "hardest thing measured
# so far", which is circular and would clip genuinely harder material at 1.0.
#
# IMPORTANT: this is a better-spread heuristic, not a validated CEFR mapping. Real
# calibration requires learner accuracy per unit (the Attempt table exists for exactly
# this). Until then, treat C1/C2 labels as ordering hints, not assessments.
SLOW_WPM_RATIO = 0.65  # <= 0.65x native baseline reads as slow/deliberate
FAST_WPM_RATIO = 1.25  # >= 1.25x reads as fast
EASY_MEAN_ZIPF = 5.10  # observed max 5.05 — everyday-register vocabulary
HARD_MEAN_ZIPF = 3.70  # observed min 3.97, with headroom below for denser material
RARE_ZIPF_CUTOFF = 3.0  # below this a word is "rare" for a learner
RARE_SATURATION = 0.18  # observed max 0.15, with headroom
SHORT_SENTENCE = 8.0
LONG_SENTENCE = 28.0

# Delivery calibration.
#
# Added after measuring a real gap: three Easy French street-interview lessons scored A2
# at 144-154 wpm while RFI news scored B1 at the *same* rate. Unscripted speech is harder
# than its rate and vocabulary suggest — crosstalk, hesitation and self-repair all cost a
# learner processing time — and the four original components are blind to all of it.
#
# The signals are things ASR already produces, so this costs nothing extra:
#   fillers      hesitation markers per 100 words (density, not presence)
#   repeats      immediately repeated words — the surface trace of self-repair
#   confidence   mean ASR word probability; degrades on overlapping or slurred speech
#   pause spread  variability of inter-word gaps; scripted prosody is regular, spontaneous
#                 speech is bursty
REPEAT_RATE_SATURATION = 2.5  # immediate repeats per 100 words
CLEAN_CONFIDENCE = 0.93  # studio narration
MESSY_CONFIDENCE = 0.72  # overlapping or noisy speech
PAUSE_CV_CALM = 0.8  # coefficient of variation of inter-word gaps
PAUSE_CV_BURSTY = 2.2

DELIVERY_MIX = {"fillers": 0.35, "repeats": 0.20, "confidence": 0.25, "pauses": 0.20}

# Real filler rates are low even in genuinely unscripted speech — a couple per hundred
# words is already conversational — so saturation sits well below the synthetic extreme.
FILLER_RATE_SATURATION = 2.5

WEIGHTS = {"speech_rate": 0.35, "lexical": 0.30, "rarity": 0.20, "syntax": 0.15}

# Delivery is MEASURED but deliberately NOT SCORED. Set above 0 only with evidence.
#
# The problem it was meant to fix is real and measured: three street-interview lessons
# scored A2 at 144-154 wpm while RFI news scored B1 at the same rate, because unscripted
# speech costs a learner processing time that rate and vocabulary don't capture.
#
# But the signals aren't recoverable from these transcripts. whisper-1 normalizes what it
# hears: it removes hesitation markers, false starts and repetitions, so the observed
# filler rate is 0.00 per 100 words on genuinely unscripted street interviews — the exact
# evidence needed is destroyed upstream. The acoustic fallbacks (word confidence, pause
# variability) came out at 0.26-0.30 across the entire library, from slow learner podcasts
# to fast financial debate.
#
# A component with no variance is an offset, not a measurement. Enabling it added a flat
# +3.2 to every lesson and pushed four into C1 by inflation alone, which is worse than
# leaving the gap visible: it manufactures confident labels out of nothing.
#
# Doing this properly needs either a verbatim ASR pass (local faster-whisper without VAD
# retains more hesitation), speaker diarization for real turn-taking counts, or the
# scenario classification the ingest LLM pass could produce — it already reads the
# transcript and could judge scripted vs spontaneous from register. The raw signals stay
# in `delivery_detail` so any of those can be calibrated against them later.
DELIVERY_BONUS_MAX = 0.0

# Upper bound of each band; anything above the last is C2.
CEFR_THRESHOLDS = (22.0, 36.0, 50.0, 66.0, 80.0)

_SENT_SPLIT = re.compile(r"[.!?…。！？]+")


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


@dataclass
class DifficultyReport:
    score: float  # 0-100
    cefr: str
    wpm: float
    mean_zipf: float
    rare_ratio: float
    mean_sentence_words: float
    content_word_count: int
    components: dict[str, float]
    # Raw delivery signals, kept so the component can be recalibrated later without
    # re-running ASR.
    delivery_detail: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def cefr_for_score(score: float) -> str:
    for level, ceiling in zip(CEFR_LEVELS, CEFR_THRESHOLDS):
        if score < ceiling:
            return level
    return CEFR_LEVELS[-1]


def _delivery(
    tokens: list[str], words: list[Any] | None, lang: LanguageProfile
) -> tuple[float, dict[str, float]]:
    """0 (scripted, studio) .. 1 (unscripted, overlapping). Plus the raw signals."""
    detail: dict[str, float] = {}
    parts: dict[str, float] = {}
    n = max(1, len(tokens))

    fillers = sum(1 for t in tokens if lang.headword(t) in lang.filler_words)
    filler_rate = fillers * 100.0 / n
    detail["filler_rate"] = round(filler_rate, 2)
    parts["fillers"] = _clamp01(filler_rate / FILLER_RATE_SATURATION)

    repeats = sum(1 for a, b in zip(tokens, tokens[1:]) if a == b and len(a) > 1)
    repeat_rate = repeats * 100.0 / n
    detail["repeat_rate"] = round(repeat_rate, 2)
    parts["repeats"] = _clamp01(repeat_rate / REPEAT_RATE_SATURATION)

    # Acoustic signals need word timings; without them these two stay neutral rather than
    # guessing, and the mix is renormalized over what is available.
    probs = [w.get("probability") for w in (words or []) if isinstance(w, dict)]
    probs = [p for p in probs if isinstance(p, (int, float))]
    if probs:
        conf = statistics.fmean(probs)
        detail["mean_confidence"] = round(conf, 3)
        parts["confidence"] = _clamp01(
            (CLEAN_CONFIDENCE - conf) / (CLEAN_CONFIDENCE - MESSY_CONFIDENCE)
        )

    gaps = []
    seq = [w for w in (words or []) if isinstance(w, dict) and "start" in w and "end" in w]
    for a, b in zip(seq, seq[1:]):
        gap = float(b["start"]) - float(a["end"])
        if 0.0 <= gap < 3.0:  # ignore edit points and segment joins
            gaps.append(gap)
    if len(gaps) > 12:
        mean_gap = statistics.fmean(gaps)
        if mean_gap > 0.005:
            cv = statistics.pstdev(gaps) / mean_gap
            detail["pause_cv"] = round(cv, 2)
            parts["pauses"] = _clamp01((cv - PAUSE_CV_CALM) / (PAUSE_CV_BURSTY - PAUSE_CV_CALM))

    weight = sum(DELIVERY_MIX[k] for k in parts)
    score = sum(parts[k] * DELIVERY_MIX[k] for k in parts) / weight if weight else 0.0
    detail["signals_used"] = len(parts)
    return _clamp01(score), detail


def analyze(
    text: str,
    duration_s: float,
    lang: LanguageProfile,
    words: list[Any] | None = None,
) -> DifficultyReport:
    tokens = lang.tokenize(text)
    content = [t for t in tokens if lang.is_content_word(t)]

    minutes = max(duration_s, 1.0) / 60.0
    wpm = len(tokens) / minutes if tokens else 0.0

    zipfs = [z for z in (lang.zipf(t) for t in content) if z > 0]
    mean_zipf = statistics.fmean(zipfs) if zipfs else EASY_MEAN_ZIPF
    rare_ratio = (sum(1 for z in zipfs if z < RARE_ZIPF_CUTOFF) / len(zipfs)) if zipfs else 0.0

    sentences = [s for s in (p.strip() for p in _SENT_SPLIT.split(text)) if s]
    sent_lens = [len(lang.tokenize(s)) for s in sentences] or [len(tokens)]
    mean_sent = statistics.fmean(sent_lens)

    # Normalize each signal to 0 (easy) .. 1 (hard).
    ratio = wpm / lang.baseline_wpm if lang.baseline_wpm else 1.0
    delivery, delivery_detail = _delivery(tokens, words, lang)
    components = {
        "speech_rate": _clamp01((ratio - SLOW_WPM_RATIO) / (FAST_WPM_RATIO - SLOW_WPM_RATIO)),
        "lexical": _clamp01((EASY_MEAN_ZIPF - mean_zipf) / (EASY_MEAN_ZIPF - HARD_MEAN_ZIPF)),
        "rarity": _clamp01(rare_ratio / RARE_SATURATION),
        "syntax": _clamp01((mean_sent - SHORT_SENTENCE) / (LONG_SENTENCE - SHORT_SENTENCE)),
        "delivery": delivery,
    }
    base = 100.0 * sum(components[k] * w for k, w in WEIGHTS.items())
    # DELIVERY_BONUS_MAX is 0 — see the note above. The term stays so re-enabling it is a
    # one-constant change once a signal with real variance exists.
    score = min(100.0, base + DELIVERY_BONUS_MAX * delivery)

    return DifficultyReport(
        score=round(score, 1),
        cefr=cefr_for_score(score),
        wpm=round(wpm, 1),
        mean_zipf=round(mean_zipf, 2),
        rare_ratio=round(rare_ratio, 3),
        mean_sentence_words=round(mean_sent, 1),
        content_word_count=len(content),
        components={k: round(v, 3) for k, v in components.items()},
        delivery_detail=delivery_detail,
    )


def aggregate(reports: list[DifficultyReport]) -> tuple[float, str]:
    """Lesson-level difficulty. Uses the 75th percentile, not the mean: a lesson is
    as hard as its hard parts, and averaging hides a single brutal passage."""
    if not reports:
        return 0.0, CEFR_LEVELS[0]
    scores = sorted(r.score for r in reports)
    idx = min(len(scores) - 1, int(round(0.75 * (len(scores) - 1))))
    score = scores[idx]
    return round(score, 1), cefr_for_score(score)
