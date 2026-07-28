"""ASR types and the Transcriber protocol.

Word-level timestamps are a hard requirement, not a nice-to-have: a cloze blank has
to map back to the exact moment in the audio so the learner can replay just that
phrase. Any backend added here must be able to produce them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass
class Word:
    text: str
    start: float
    end: float
    probability: float | None = None

    def shifted(self, offset: float) -> "Word":
        return Word(self.text, self.start + offset, self.end + offset, self.probability)

    def to_dict(self) -> dict[str, Any]:
        return {
            "word": self.text,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "probability": round(self.probability, 4) if self.probability is not None else None,
        }


@dataclass
class ASRSegment:
    idx: int
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)
    avg_logprob: float | None = None
    no_speech_prob: float | None = None

    def shifted(self, offset: float, idx: int) -> "ASRSegment":
        return ASRSegment(
            idx=idx,
            start=self.start + offset,
            end=self.end + offset,
            text=self.text,
            words=[w.shifted(offset) for w in self.words],
            avg_logprob=self.avg_logprob,
            no_speech_prob=self.no_speech_prob,
        )


@dataclass
class ASRResult:
    text: str
    segments: list[ASRSegment]
    language: str
    duration_s: float
    backend: str
    model: str

    @property
    def words(self) -> list[Word]:
        return [w for s in self.segments for w in s.words]

    @property
    def word_count(self) -> int:
        return len(self.words) or len(self.text.split())

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "language": self.language,
            "duration_s": self.duration_s,
            "backend": self.backend,
            "model": self.model,
            "segments": [
                {
                    **{k: v for k, v in asdict(s).items() if k != "words"},
                    "words": [w.to_dict() for w in s.words],
                }
                for s in self.segments
            ],
        }


@runtime_checkable
class Transcriber(Protocol):
    name: str
    model: str

    def transcribe(self, audio_path: Path, *, language: str, prompt: str | None = None) -> ASRResult:
        ...


def attach_words_to_segments(
    segments: list[ASRSegment], words: list[Word]
) -> list[ASRSegment]:
    """Distribute a flat word list into segments by time overlap.

    The OpenAI verbose_json response returns `words` and `segments` as sibling arrays,
    so they have to be re-joined. Each word is assigned to the segment it overlaps
    most, falling back to the nearest segment when timings disagree at boundaries.
    """
    if not segments or not words:
        return segments

    for seg in segments:
        seg.words = []

    cursor = 0
    for word in words:
        # Advance past segments that clearly end before this word starts.
        while cursor + 1 < len(segments) and segments[cursor].end <= word.start + 1e-6:
            cursor += 1

        best_i, best_overlap = cursor, -1.0
        for i in range(cursor, min(cursor + 3, len(segments))):
            seg = segments[i]
            overlap = min(seg.end, word.end) - max(seg.start, word.start)
            if overlap > best_overlap:
                best_i, best_overlap = i, overlap
        segments[best_i].words.append(word)

    return segments


def stitch(results: list[tuple[ASRResult, float]]) -> ASRResult:
    """Merge per-chunk results into one timeline, shifting each by its offset."""
    if not results:
        raise ValueError("nothing to stitch")
    if len(results) == 1 and results[0][1] == 0.0:
        return results[0][0]

    merged: list[ASRSegment] = []
    texts: list[str] = []
    total = 0.0
    for res, offset in results:
        for seg in res.segments:
            merged.append(seg.shifted(offset, len(merged)))
        texts.append(res.text.strip())
        total = max(total, offset + res.duration_s)

    head = results[0][0]
    return ASRResult(
        text=" ".join(t for t in texts if t),
        segments=merged,
        language=head.language,
        duration_s=total,
        backend=head.backend,
        model=head.model,
    )
