"""Cut a unit's transcript into dictation-sized pieces, each with an audio window.

Two jobs, and the second is the one that makes or breaks the feature.

SPLITTING. The input is punctuated ASR text, so a naive split on [.!?] is wrong in ways that
matter: "M. Dupont" would become a one-word item and orphan the rest of the sentence, "3.5" would
split mid-number, and an ellipsis would end a sentence that continues. Each guard here is for a
pattern that actually occurs in this corpus.

WINDOWING. A sentence's audio window is derived from the word timings, not from dividing the
clip's duration — but a window that starts exactly at the first word's onset clips the attack of
the consonant, and one that ends exactly at the last word's offset swallows the final syllable.
So each window is padded into the silence on either side, taking at most half of the available
gap so two adjacent sentences never overlap. A dictation item whose audio starts mid-breath is
useless however good the text is.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from ...asr.base import Word
from ...languages import LanguageProfile
from ..listening.align import align_words_to_text

log = logging.getLogger(__name__)

# Sentence-ending punctuation. The ellipsis is included but guarded: it ends a sentence only when
# what follows looks like a fresh start.
TERMINATORS = ".!?…"

# Punctuation that belongs to the sentence it follows, so a closing quote or bracket is not
# stranded at the head of the next item.
TRAILING = "\"'»”’)]…"

# Padding around a window, in seconds. Enough to catch a plosive onset without bleeding a
# neighbouring word in.
PAD_S = 0.18
# Never take more than this share of the available silence, so adjacent windows cannot overlap.
PAD_SHARE = 0.5

# A unit whose words mostly fail to align onto its text is not fit for dictation at any length:
# the audio windows would be guesses. Unit 4 is the live example — 39 of 232 words align, because
# its stored text is an unpunctuated token-join rather than real prose — and it is exactly the unit
# that would otherwise produce a single unsplittable 165-word "sentence".
MIN_ALIGN_RATIO = 0.75


@dataclass(frozen=True)
class Sentence:
    """One candidate dictation item."""

    idx: int
    text: str
    char_start: int
    char_end: int
    # ORIGINAL-VIDEO seconds, matching Exercise.audio_start_s and the player's timeline.
    start_s: float
    end_s: float
    word_count: int
    # Silence found before the first word and after the last. Small values mean the sentence
    # boundary is not a real pause, which makes for jarring dictation audio.
    lead_gap_s: float
    trail_gap_s: float

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


def _is_abbreviation(text: str, dot: int, lang: LanguageProfile) -> bool:
    """True when the full stop at `dot` belongs to an abbreviation rather than a sentence end.

    Terminal-capable abbreviations ("etc.") return False so the usual capital-letter test decides;
    only titles and initials, which always precede more of the same sentence, suppress the split.
    """
    j = dot - 1
    while j >= 0 and (text[j].isalpha() or text[j] in ".-'’"):
        j -= 1
    token = text[j + 1 : dot].lower().strip(".")
    if not token:
        return False
    if token in lang.terminal_abbreviations:
        return False
    # A lone letter is an initial: "J. Dupont", "A. Camus". Occurs 5 times in this corpus.
    if len(token) == 1 and text[j + 1].isupper():
        return True
    return token in lang.sentence_abbreviations


def _is_decimal(text: str, dot: int) -> bool:
    """"3.5" is one number, not two sentences.

    Does not fire on the current corpus — measured zero digit-dot-digit occurrences across all 69
    units — but ASR of any numeric material produces them, and the guard is one comparison.
    """
    return dot > 0 and dot + 1 < len(text) and text[dot - 1].isdigit() and text[dot + 1].isdigit()


def _starts_new_sentence(text: str, pos: int) -> bool:
    """Does a fresh sentence begin at or after `pos`? Skips whitespace and opening quotes."""
    k = pos
    while k < len(text) and (text[k].isspace() or text[k] in "\"'«“‘-–—"):
        k += 1
    if k >= len(text):
        return True
    ch = text[k]
    return ch.isupper() or ch.isdigit()


def split_sentences(text: str, lang: LanguageProfile) -> list[tuple[int, int]]:
    """Character spans of the sentences in `text`, in order, covering all non-space content."""
    spans: list[tuple[int, int]] = []
    start = 0
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]
        if ch not in TERMINATORS:
            i += 1
            continue

        # Consume a run of terminators so "?!" and "..." are treated as one boundary.
        end = i + 1
        while end < n and text[end] in TERMINATORS:
            end += 1

        if ch == "." and end == i + 1:
            if _is_decimal(text, i) or _is_abbreviation(text, i, lang):
                i = end
                continue

        # Pull in trailing punctuation that closes this sentence.
        while end < n and text[end] in TRAILING:
            end += 1

        if not _starts_new_sentence(text, end):
            i = end
            continue

        piece = text[start:end].strip()
        if piece:
            lead = len(text[start:end]) - len(text[start:end].lstrip())
            spans.append((start + lead, end))
        start = end
        i = end

    tail = text[start:].strip()
    if tail:
        lead = len(text[start:]) - len(text[start:].lstrip())
        spans.append((start + lead, n))
    return spans


@dataclass(frozen=True)
class Passage:
    """A run of consecutive sentences: one paragraph-mode dictation item."""

    idx: int
    text: str
    char_start: int
    char_end: int
    start_s: float
    end_s: float
    word_count: int
    sentence_count: int

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


# Paragraph mode is NOT "the whole unit". Measured on this corpus, a unit runs 66-427 words with a
# median of 226 — ten minutes of typing, and a single mistake near the end is demoralising rather
# than instructive. A paragraph is instead a run of whole sentences inside a word budget: long
# enough that the learner has to hold structure across sentence boundaries, which is the thing
# paragraph dictation trains and sentence mode cannot.
MIN_PASSAGE_WORDS = 35
MAX_PASSAGE_WORDS = 80


def group_passages(
    sentences: list[Sentence],
    *,
    min_words: int = MIN_PASSAGE_WORDS,
    max_words: int = MAX_PASSAGE_WORDS,
) -> list[Passage]:
    """Group consecutive sentences into passages within a word budget.

    Only ever breaks at a sentence boundary, so a passage is always something a learner could be
    read aloud. A trailing group below `min_words` is merged back into the previous passage rather
    than shipped as a runt or thrown away — the audio is continuous either way.
    """
    groups: list[list[Sentence]] = []
    current: list[Sentence] = []
    words = 0
    for s in sentences:
        if current and words + s.word_count > max_words:
            groups.append(current)
            current, words = [], 0
        current.append(s)
        words += s.word_count
    if current:
        groups.append(current)

    # A trailing group below the minimum is merged back — but only when the result still fits the
    # budget. Merging unconditionally overshoots by up to min_words: measured, it produced a
    # 104-word passage against an 80-word cap, and the card promises "about 104 words" to a
    # learner who chose paragraph mode expecting the advertised size. An unmergeable runt is left
    # alone and then dropped by the min_words filter; sentence mode still covers that material.
    if len(groups) > 1 and sum(s.word_count for s in groups[-1]) < min_words:
        merged = sum(s.word_count for s in groups[-2]) + sum(s.word_count for s in groups[-1])
        if merged <= max_words:
            groups[-2].extend(groups.pop())

    out: list[Passage] = []
    for idx, g in enumerate(groups):
        total = sum(s.word_count for s in g)
        if total < min_words:
            continue  # a lone short unit; sentence mode still covers it
        if len(g) == 1 and total > max_words:
            # One sentence that blew the budget on its own, so grouping had nothing to work with.
            # In practice this is a punctuation failure upstream, and the item would be a wall of
            # text with no internal structure — the opposite of what paragraph mode is for.
            continue
        out.append(
            Passage(
                idx=idx,
                # Rejoined from the source spans rather than " ".join(texts), so the original
                # spacing and punctuation between sentences survive verbatim.
                text="",
                char_start=g[0].char_start,
                char_end=g[-1].char_end,
                start_s=g[0].start_s,
                end_s=g[-1].end_s,
                word_count=total,
                sentence_count=len(g),
            )
        )
    return out


def passages_for_unit(
    text: str,
    words_json: list[dict[str, Any]],
    lang: LanguageProfile,
    *,
    unit_start_s: float,
    unit_end_s: float,
    min_words: int = MIN_PASSAGE_WORDS,
    max_words: int = MAX_PASSAGE_WORDS,
) -> list[Passage]:
    sentences = sentences_for_unit(
        text, words_json, lang, unit_start_s=unit_start_s, unit_end_s=unit_end_s
    )
    return [
        Passage(**{**p.__dict__, "text": text[p.char_start : p.char_end].strip()})
        for p in group_passages(sentences, min_words=min_words, max_words=max_words)
    ]


def _words_from_json(raw: list[dict[str, Any]]) -> list[Word]:
    return [
        Word(
            text=w.get("word", ""),
            start=float(w.get("start") or 0.0),
            end=float(w.get("end") or 0.0),
            probability=w.get("probability"),
        )
        for w in raw
        if (w.get("word") or "").strip()
    ]


def sentences_for_unit(
    text: str,
    words_json: list[dict[str, Any]],
    lang: LanguageProfile,
    *,
    unit_start_s: float,
    unit_end_s: float,
) -> list[Sentence]:
    """Split `text` and give each sentence an audio window on the original-video timeline.

    Sentences the aligner cannot place are dropped rather than guessed at: a dictation item whose
    audio plays the wrong words is worse than one fewer item.
    """
    if not text.strip():
        return []
    words = _words_from_json(words_json)
    if not words:
        return []

    spans = align_words_to_text(words, text)
    placed = [(s, words[i]) for i, s in enumerate(spans) if s is not None]
    ratio = len(placed) / len(words)
    if ratio < MIN_ALIGN_RATIO:
        log.warning(
            "only %d/%d words align onto the text (%.0f%%); skipping this unit for dictation",
            len(placed), len(words), 100 * ratio,
        )
        return []

    out: list[Sentence] = []
    for idx, (cs, ce) in enumerate(split_sentences(text, lang)):
        inside = [w for (s, e), w in placed if s >= cs and e <= ce]
        if not inside:
            continue

        first, last = inside[0], inside[-1]
        # Silence available on each side, bounded by the unit and by the neighbouring words.
        prev_end = max(
            (w.end for (_, e), w in placed if e <= cs),
            default=unit_start_s,
        )
        next_start = min(
            (w.start for (s, _), w in placed if s >= ce),
            default=unit_end_s,
        )
        lead_gap = max(0.0, first.start - prev_end)
        trail_gap = max(0.0, next_start - last.end)

        start_s = max(unit_start_s, first.start - min(PAD_S, lead_gap * PAD_SHARE))
        end_s = min(unit_end_s, last.end + min(PAD_S, trail_gap * PAD_SHARE))

        out.append(
            Sentence(
                idx=idx,
                text=text[cs:ce].strip(),
                char_start=cs,
                char_end=ce,
                start_s=round(start_s, 3),
                end_s=round(end_s, 3),
                word_count=len(lang.tokenize(text[cs:ce])),
                lead_gap_s=round(lead_gap, 3),
                trail_gap_s=round(trail_gap, 3),
            )
        )
    return out
