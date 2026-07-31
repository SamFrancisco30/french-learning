"""ASR types and the Transcriber protocol.

Word-level timestamps are a hard requirement, not a nice-to-have: a cloze blank has
to map back to the exact moment in the audio so the learner can replay just that
phrase. Any backend added here must be able to produce them.
"""

from __future__ import annotations

import re
import unicodedata
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


# How many text tokens ahead we will look to resync when the token stream and the segment
# text disagree about a word. Small on purpose: a wide search would happily match a common
# word from a later sentence, which is the very mistake this is here to avoid.
TEXT_RESYNC_TOKENS = 8


def _distance_to(seg: ASRSegment, t: float) -> float:
    """How far `t` lies outside `seg`, or 0 if inside it."""
    if t < seg.start:
        return seg.start - t
    if t > seg.end:
        return t - seg.end
    return 0.0


def _fold_token(token: str) -> str:
    """Accent- and case-insensitive key for one word."""
    return "".join(
        c for c in unicodedata.normalize("NFD", token) if c.isalnum() and not unicodedata.combining(c)
    ).casefold()


def _segment_token_list(seg: ASRSegment) -> list[str]:
    """The words of a segment's own text, in order, keyed for comparison against ASR tokens.

    Splitting on non-word characters is what makes this comparable to the bare token stream:
    the text says "l'eau" and "toi-même" where the tokens say "l", "eau", "toi", "même", and
    splitting the text the same way puts both sides in the same vocabulary.
    """
    return [k for t in re.split(r"[^\w]+", seg.text) if (k := _fold_token(t))]


def attach_words_to_segments(
    segments: list[ASRSegment], words: list[Word]
) -> list[ASRSegment]:
    """Distribute a flat word list into segments by time overlap.

    The OpenAI verbose_json response returns `words` and `segments` as sibling arrays,
    so they have to be re-joined. Each word is assigned to the segment it overlaps
    most, falling back to the nearest segment when timings disagree at boundaries.

    Zero-duration words are the tricky part, and they are not rare — hosted whisper hands
    back 25-30 of them per minute of audio, with `start == end`. For such a word "overlaps
    most" is meaningless: the overlap with every segment is zero or negative, so the
    arithmetic alone cannot place it. Both the cursor and the tie-break below therefore have
    to treat a segment's end bound as still belonging to that segment, because getting this
    wrong does not merely misplace one timestamp — it puts a word in one segment's `words`
    while its text sits in the previous segment's `text`. Downstream, aligning that unit's
    word array onto its own punctuated text then fails (the stray leading token matches a
    later occurrence of the same letters and drags the forward-only cursor past everything
    before it), the cloze builder decides it cannot trust the alignment, and falls back to
    joining bare tokens — which is French with every apostrophe and hyphen stripped out:
    "de l eau", "toi même", "se passe t il".
    """
    if not segments or not words:
        return segments

    for seg in segments:
        seg.words = []

    # The segment texts, concatenated into one ordered token stream that remembers which segment
    # each token came from. Walking this in step with the ASR tokens is what lets a degenerate
    # word be placed by ORDER rather than by mere membership: "de", "la" and "Elle" occur in
    # several neighbouring segments at once, so asking "which neighbour's text contains this
    # word" cannot answer, while "which text token are we up to" can.
    text_tokens: list[tuple[int, str]] = [
        (i, tok) for i, seg in enumerate(segments) for tok in _segment_token_list(seg)
    ]
    ptr = 0

    cursor = 0
    for word in words:
        # Advance past segments that end STRICTLY before this word begins. The comparison must
        # be strict: an inclusive `<=` skipped the very segment a boundary word belongs to,
        # since a zero-duration word sitting exactly on a segment's end satisfies
        # `seg.end <= word.start`, leaving that segment out of the candidate window entirely.
        while cursor + 1 < len(segments) and segments[cursor].end < word.start - 1e-6:
            cursor += 1

        # Walk the text stream alongside the token stream, whether or not this particular word
        # needs it. Advancing only for the hard cases would let the pointer drift out of step,
        # and it is precisely the hard cases that then depend on it being right.
        text_owner: int | None = None
        key = _fold_token(word.text)
        if key:
            for j in range(ptr, min(len(text_tokens), ptr + TEXT_RESYNC_TOKENS)):
                if text_tokens[j][1] == key:
                    text_owner = text_tokens[j][0]
                    ptr = j + 1
                    break

        window = range(cursor, min(cursor + 3, len(segments)))
        best_i, best_overlap = cursor, -1.0
        for i in window:
            seg = segments[i]
            overlap = min(seg.end, word.end) - max(seg.start, word.start)
            if overlap > best_overlap:
                best_i, best_overlap = i, overlap

        if best_overlap <= 0.0:
            # A zero-width word, or one landing in a gap between segments: nothing overlaps, so
            # the timings alone cannot place it. The TEXT can — it is the ground truth this
            # function exists to stay consistent with, and `text_owner` has just read it.
            #
            # The neighbourhood reaches one segment BACK, because these words are as often the
            # tail of the phrase before as the head of the one after, and a forward-only search
            # can only ever guess the latter. Timing is the last resort, for words the text
            # stream could not place at all.
            near = range(max(0, cursor - 1), min(cursor + 3, len(segments)))
            if text_owner is not None and text_owner in near:
                best_i = text_owner
            else:
                mid = (word.start + word.end) / 2
                best_i = min(near, key=lambda i: _distance_to(segments[i], mid))

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
