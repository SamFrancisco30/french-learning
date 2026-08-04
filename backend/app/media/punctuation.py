"""Read a dictation passage the way a teacher reads a dictée: with the punctuation spoken.

A comma and a clause break sound identical, so without this the learner is guessing where the
punctuation goes — which is why a real dictée is read "virgule", "point". The grader deliberately
does not score punctuation for exactly that reason; announcing it is the other half of the fix, and
turns punctuation from a guess into something you can actually get right.

Method: the original audio is never re-synthesised or altered. The spoken name is spliced in at the
punctuation's own position, found by aligning the word timings onto the text, with a short pause on
each side so it reads as an aside rather than as part of the sentence. The speaker's voice stays
exactly as recorded, which is the whole point of using authentic media.
"""

from __future__ import annotations

import logging
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..languages import LanguageProfile
from ..skills.listening.align import align_words_to_text
from ..asr.base import Word

log = logging.getLogger(__name__)

ASSET_ROOT = Path(__file__).resolve().parent / "assets" / "punctuation"
SAMPLE_RATE = 44_100

# Silence around a spoken mark. Enough that it detaches from the sentence, short enough that the
# passage keeps its rhythm — a long gap makes the reader sound uncertain.
GAP_BEFORE_S = 0.16
GAP_AFTER_S = 0.20

# The announcement sits slightly below the speaker so it reads as an instruction rather than as
# part of the text. Not so quiet that it is easy to miss.
ANNOUNCE_GAIN = 0.72


def asset_name(spoken: str) -> str:
    """Filesystem-safe stem for a spoken name: "point d'interrogation" -> point_d_interrogation."""
    folded = "".join(
        c for c in unicodedata.normalize("NFD", spoken) if not unicodedata.combining(c)
    )
    return re.sub(r"[^a-z0-9]+", "_", folded.lower()).strip("_")


@dataclass(frozen=True)
class Mark:
    """A punctuation mark located in both the text and the audio."""

    symbol: str
    spoken: str
    char_pos: int
    # Seconds into the passage where the announcement belongs.
    at_s: float


# Below this share of words placed onto the text, the alignment is not trustworthy enough to
# position anything. Announcing at a guessed time is worse than not announcing: every mark whose
# preceding word cannot be found collapses onto the same fallback instant, which is heard as
# "virgule virgule virgule" before the sentence has started.
MIN_ALIGNED_FRACTION = 0.5


def find_marks(
    text: str,
    words_json: list[dict[str, Any]],
    lang: LanguageProfile,
    *,
    offset_s: float = 0.0,
    until_s: float | None = None,
) -> list[Mark]:
    """Locate each punctuation mark and the moment it should be announced.

    A mark is announced after the word it follows, so the learner hears "…dans la vie, virgule".

    `text` MUST be the text these `words_json` were transcribed from — the whole unit's text, not a
    sentence cut out of it. The words are aligned onto the text to find where each mark falls in
    time, and a sentence-sized text with a unit's worth of words aligns almost nowhere: 6 of 303, in
    the case that produced this note. Every mark then failed to find a preceding word and fell back
    to the same instant, so a four-comma sentence opened with four announcements stacked before the
    first word.

    Use `offset_s` and `until_s` to narrow to one item's window instead. Times come back relative to
    `offset_s`, so they index the item's own audio.
    """
    if not text:
        return []
    words = [
        Word(
            text=w.get("word", ""),
            start=float(w.get("start") or 0.0),
            end=float(w.get("end") or 0.0),
        )
        for w in words_json
        if (w.get("word") or "").strip()
    ]
    if not words:
        return []

    spans = align_words_to_text(words, text)
    placed = [(s, words[i]) for i, s in enumerate(spans) if s is not None]
    if not placed:
        return []

    # Refuse rather than guess. See MIN_ALIGNED_FRACTION: a weak alignment does not produce slightly
    # wrong positions, it produces every mark at one position.
    if len(placed) / len(words) < MIN_ALIGNED_FRACTION:
        log.warning(
            "not announcing punctuation: only %d/%d words aligned onto the text",
            len(placed),
            len(words),
        )
        return []

    # Longest symbols first so "..." is matched before ".".
    pairs = sorted(lang.punctuation_names, key=lambda p: -len(p[0]))

    # One pass over the placed words instead of re-scanning them for every mark. `cursor` is the
    # index of the last word known to end at or before the mark, and marks are found left to right,
    # so it only ever moves forward.
    ends = [(end, word) for (_start, end), word in placed]

    marks: list[Mark] = []
    cursor = -1
    i = 0
    while i < len(text):
        for symbol, spoken in pairs:
            if not text.startswith(symbol, i):
                continue
            while cursor + 1 < len(ends) and ends[cursor + 1][0] <= i:
                cursor += 1
            # A mark before any word can only be an opening bracket or quote, and belongs just
            # before the word it opens.
            absolute = ends[cursor][1].end if cursor >= 0 else ends[0][1].start
            if until_s is None or offset_s <= absolute <= until_s:
                marks.append(
                    Mark(
                        symbol=symbol,
                        spoken=spoken,
                        char_pos=i,
                        at_s=max(0.0, absolute - offset_s),
                    )
                )
            i += len(symbol)
            break
        else:
            i += 1
    return marks


def _decode(path: Path) -> np.ndarray:
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-acodec", "pcm_s16le",
         "-ac", "1", "-ar", str(SAMPLE_RATE), "-"],
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(f"decode failed for {path.name}: {proc.stderr.decode()[-300:]}")
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def _encode(samples: np.ndarray, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    raw = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
    proc = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1",
         "-i", "-", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(dst)],
        input=raw, capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"encode failed: {proc.stderr.decode()[-300:]}")


class MissingAssets(RuntimeError):
    """The spoken-punctuation assets have not been built for this language."""


def _load_assets(lang: LanguageProfile) -> dict[str, np.ndarray]:
    root = ASSET_ROOT / lang.code
    needed = {name for _, name in lang.punctuation_names}
    out: dict[str, np.ndarray] = {}
    missing: list[str] = []
    for name in needed:
        path = root / f"{asset_name(name)}.wav"
        if not path.exists():
            missing.append(name)
            continue
        out[name] = _decode(path) * ANNOUNCE_GAIN
    if missing:
        raise MissingAssets(
            f"no spoken-punctuation audio for {lang.code}: missing {', '.join(sorted(missing))}. "
            f"Build it with: python scripts/build_punctuation_audio.py --language {lang.code}"
        )
    return out


@dataclass
class SpokenResult:
    path: Path
    duration_s: float
    marks: int
    # original passage seconds -> seconds in the produced audio, at each insertion.
    time_map: list[list[float]]


def splice_announcements(
    src: Path,
    spoken_at: list[tuple[str, float]],
    lang: LanguageProfile,
    *,
    dst: Path,
) -> SpokenResult:
    """Splice (spoken name, seconds-into-src) announcements into `src`.

    Takes positions rather than deriving them, because a caller that has already reshaped the audio
    — slowed it, say — knows where the marks moved to and this module does not.
    """
    assets = _load_assets(lang)
    audio = _decode(src)
    if not spoken_at:
        _encode(audio, dst)
        return SpokenResult(dst, len(audio) / SAMPLE_RATE, 0, [])

    gap_before = np.zeros(int(GAP_BEFORE_S * SAMPLE_RATE), dtype=np.float32)
    gap_after = np.zeros(int(GAP_AFTER_S * SAMPLE_RATE), dtype=np.float32)

    pieces: list[np.ndarray] = []
    time_map: list[list[float]] = []
    cursor = 0
    added = 0

    for spoken, at_s in sorted(spoken_at, key=lambda x: x[1]):
        clip = assets.get(spoken)
        if clip is None:
            continue
        pos = min(len(audio), max(cursor, int(at_s * SAMPLE_RATE)))
        pieces.append(audio[cursor:pos])
        insert = np.concatenate([gap_before, clip, gap_after])
        pieces.append(insert)
        added += len(insert)
        time_map.append([round(pos / SAMPLE_RATE, 3), round((pos + added) / SAMPLE_RATE, 3)])
        cursor = pos

    pieces.append(audio[cursor:])
    out = np.concatenate(pieces)
    _encode(out, dst)
    return SpokenResult(dst, round(len(out) / SAMPLE_RATE, 3), len(time_map), time_map)


def with_spoken_punctuation(
    src: Path,
    text: str,
    words_json: list[dict[str, Any]],
    lang: LanguageProfile,
    *,
    offset_s: float,
    dst: Path,
) -> SpokenResult:
    """Convenience wrapper: locate the marks in `src` itself, then splice them.

    `offset_s` is where the passage starts on the timeline the word timings use, so the two line up.
    """
    marks = find_marks(text, words_json, lang, offset_s=offset_s)
    return splice_announcements(src, [(m.spoken, m.at_s) for m in marks], lang, dst=dst)
