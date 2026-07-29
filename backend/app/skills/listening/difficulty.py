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
from dataclasses import asdict, dataclass
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

WEIGHTS = {"speech_rate": 0.35, "lexical": 0.30, "rarity": 0.20, "syntax": 0.15}

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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def cefr_for_score(score: float) -> str:
    for level, ceiling in zip(CEFR_LEVELS, CEFR_THRESHOLDS):
        if score < ceiling:
            return level
    return CEFR_LEVELS[-1]


def analyze(text: str, duration_s: float, lang: LanguageProfile) -> DifficultyReport:
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
    components = {
        "speech_rate": _clamp01((ratio - SLOW_WPM_RATIO) / (FAST_WPM_RATIO - SLOW_WPM_RATIO)),
        "lexical": _clamp01((EASY_MEAN_ZIPF - mean_zipf) / (EASY_MEAN_ZIPF - HARD_MEAN_ZIPF)),
        "rarity": _clamp01(rare_ratio / RARE_SATURATION),
        "syntax": _clamp01((mean_sent - SHORT_SENTENCE) / (LONG_SENTENCE - SHORT_SENTENCE)),
    }
    score = 100.0 * sum(components[k] * w for k, w in WEIGHTS.items())

    return DifficultyReport(
        score=round(score, 1),
        cefr=cefr_for_score(score),
        wpm=round(wpm, 1),
        mean_zipf=round(mean_zipf, 2),
        rare_ratio=round(rare_ratio, 3),
        mean_sentence_words=round(mean_sent, 1),
        content_word_count=len(content),
        components={k: round(v, 3) for k, v in components.items()},
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
