"""Split a full transcript into listening units.

A 12-minute news bulletin is useless as a single exercise: learners can't hold it in
working memory and a wrong answer gives no diagnostic signal. So we cut the audio into
60-120s units that each stand on their own, preferring boundaries that fall on a
sentence end *and* a real pause — cutting mid-clause makes a unit unanswerable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ...asr.base import ASRSegment, Word

log = logging.getLogger(__name__)

TERMINAL_PUNCT = (".", "!", "?", "…", "。", "！", "？")

TARGET_S = 90.0
MIN_S = 45.0
MAX_S = 135.0
# A gap this long reads as a topic break, so it's a good place to cut.
GOOD_PAUSE_S = 0.55


@dataclass
class Unit:
    idx: int
    start_s: float
    end_s: float
    text: str
    words: list[Word] = field(default_factory=list)
    segment_idxs: list[int] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)


def _ends_sentence(text: str) -> bool:
    return text.rstrip().endswith(TERMINAL_PUNCT)


def _build(idx: int, group: list[ASRSegment]) -> Unit:
    return Unit(
        idx=idx,
        start_s=group[0].start,
        end_s=group[-1].end,
        text=" ".join(s.text.strip() for s in group if s.text.strip()),
        words=[w for s in group for w in s.words],
        segment_idxs=[s.idx for s in group],
    )


def segment_into_units(
    segments: list[ASRSegment],
    *,
    target_s: float = TARGET_S,
    min_s: float = MIN_S,
    max_s: float = MAX_S,
) -> list[Unit]:
    """Greedily pack ASR segments into units, closing on the best nearby boundary."""
    usable = [s for s in segments if s.text.strip()]
    if not usable:
        return []

    units: list[Unit] = []
    group: list[ASRSegment] = []

    for i, seg in enumerate(usable):
        group.append(seg)
        dur = group[-1].end - group[0].start
        if dur < min_s:
            continue

        nxt = usable[i + 1] if i + 1 < len(usable) else None
        pause = (nxt.start - seg.end) if nxt else float("inf")

        close = (
            dur >= max_s  # hard cap
            or nxt is None  # nothing left
            or (dur >= target_s and _ends_sentence(seg.text))
            or (dur >= target_s * 0.75 and _ends_sentence(seg.text) and pause >= GOOD_PAUSE_S)
        )
        if close:
            units.append(_build(len(units), group))
            group = []

    if group:
        units.append(_build(len(units), group))

    units = _absorb_runt(units, min_s, max_s)
    for i, u in enumerate(units):
        u.idx = i

    log.info(
        "segmented %d ASR segments into %d units (%.0fs-%.0fs)",
        len(usable),
        len(units),
        min((u.duration_s for u in units), default=0),
        max((u.duration_s for u in units), default=0),
    )
    return units


def _absorb_runt(units: list[Unit], min_s: float, max_s: float) -> list[Unit]:
    """Fold a too-short trailing unit into its predecessor when that stays sane."""
    if len(units) < 2:
        return units
    last, prev = units[-1], units[-2]
    if last.duration_s < min_s and (last.end_s - prev.start_s) <= max_s * 1.25:
        merged = Unit(
            idx=prev.idx,
            start_s=prev.start_s,
            end_s=last.end_s,
            text=f"{prev.text} {last.text}".strip(),
            words=prev.words + last.words,
            segment_idxs=prev.segment_idxs + last.segment_idxs,
        )
        return units[:-2] + [merged]
    return units
