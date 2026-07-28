"""Fill-in-the-blank (cloze) generation, anchored to word-level audio timestamps.

Design decisions worth knowing:

* The display text is rebuilt **from the word array**, not from the ASR segment text,
  so every blank's character span is guaranteed to line up with the audio timing. If
  you build from segment text you have to fuzzy-match words back onto it and the
  spans silently drift.

* Blanks are only placed on words the ASR was *confident* about. Blanking a word the
  model guessed at means grading the learner against a possibly-wrong answer — the
  single worst failure mode for this exercise type.

* Target words sit in a "teachable band" of frequency: common enough to be worth
  learning, rare enough not to be free. Zipf ~3.0-4.7 is the sweet spot; `le`/`de`
  teach nothing and a hapax is just cruel.

* Blanks are spread out in time so the learner isn't asked to catch two words in one
  breath group.
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass
from typing import Any

from ...asr.base import Word
from ...languages import LanguageProfile
from .align import align_words_to_text

log = logging.getLogger(__name__)

# Below this share of aligned words we don't trust the punctuated text and fall back
# to joining the raw tokens.
MIN_ALIGN_RATIO = 0.8

# Frequency band that makes a good target.
IDEAL_ZIPF = 3.9
MIN_ZIPF = 2.2
MAX_ZIPF = 5.4
ZIPF_SPREAD = 1.1  # gaussian width around IDEAL_ZIPF

MIN_WORD_LEN = 3
MIN_ASR_CONFIDENCE = 0.55

# Spacing between blanks.
MIN_WORD_GAP = 4
MIN_TIME_GAP_S = 1.8

# One blank per this many seconds of audio, within [MIN, MAX].
SECONDS_PER_BLANK = 11.0
MIN_BLANKS = 3
MAX_BLANKS = 9

# Replay window padding around a blank.
PAD_BEFORE_S = 1.6
PAD_AFTER_S = 0.9

# Levels that get a word bank instead of free typing.
WORD_BANK_LEVELS = {"A1", "A2"}
WORD_BANK_DECOYS = 3

_ATTACH_LEFT = set(",.;:!?…)»%]}")
_ATTACH_RIGHT = set("(«[{")


@dataclass
class RenderedText:
    text: str
    spans: list[tuple[int, int]]  # char span of each word, parallel to the word list


def render_from_words(words: list[Word]) -> RenderedText:
    """Join word tokens into display text, recording each token's character span."""
    parts: list[str] = []
    spans: list[tuple[int, int]] = []
    pos = 0
    prev_attaches_right = False

    for i, w in enumerate(words):
        tok = w.text.strip()
        if not tok:
            spans.append((pos, pos))
            continue

        needs_space = (
            i > 0
            and not prev_attaches_right
            and tok[0] not in _ATTACH_LEFT
            and not tok.startswith("'")
            and not tok.startswith("’")
            and not (parts and parts[-1].endswith(("'", "’")))
        )
        if needs_space:
            parts.append(" ")
            pos += 1

        parts.append(tok)
        spans.append((pos, pos + len(tok)))
        pos += len(tok)
        prev_attaches_right = tok[-1] in _ATTACH_RIGHT

    return RenderedText(text="".join(parts), spans=spans)


def _core(token: str) -> str:
    """Strip surrounding punctuation, keeping internal apostrophes/hyphens."""
    return token.strip().strip(".,;:!?…«»\"'()[]{}—–-").strip()


def _teachability(zipf: float) -> float:
    return math.exp(-((zipf - IDEAL_ZIPF) ** 2) / (2 * ZIPF_SPREAD**2))


@dataclass
class Candidate:
    word_index: int
    answer: str
    span: tuple[int, int]
    start: float
    end: float
    zipf: float
    score: float


def _candidates(words: list[Word], rendered: RenderedText, lang: LanguageProfile) -> list[Candidate]:
    out: list[Candidate] = []
    for i, w in enumerate(words):
        span = rendered.spans[i]
        # Prefer the surface form as it appears in the display text: it carries the
        # correct accents and casing, which the bare ASR token often lacks.
        surface = _core(rendered.text[span[0] : span[1]]) or _core(w.text)
        if len(surface) < MIN_WORD_LEN or any(ch.isdigit() for ch in surface):
            continue
        if w.probability is not None and w.probability < MIN_ASR_CONFIDENCE:
            continue
        if not lang.is_content_word(surface):
            continue

        zipf = lang.zipf(surface)
        if not (MIN_ZIPF <= zipf <= MAX_ZIPF):
            continue

        score = _teachability(zipf)
        score += min(len(surface), 12) / 60.0  # mild preference for meatier words
        if w.probability is not None:
            score *= 0.6 + 0.4 * w.probability

        out.append(
            Candidate(
                word_index=i,
                answer=surface,
                span=span,
                start=w.start,
                end=w.end,
                zipf=zipf,
                score=score,
            )
        )
    return out


def _fits(c: Candidate, chosen: list[Candidate]) -> bool:
    return not any(
        abs(c.word_index - o.word_index) < MIN_WORD_GAP
        or abs(c.start - o.start) < MIN_TIME_GAP_S
        for o in chosen
    )


def _select(
    cands: list[Candidate], n: int, *, start_s: float, end_s: float
) -> list[Candidate]:
    """Pick `n` candidates spread across the clip, best-scoring within each region.

    Purely greedy top-N selection clusters blanks wherever the rare vocabulary happens
    to sit, which can leave a third of the audio untested. Splitting the clip into `n`
    equal time buckets and taking the best candidate from each guarantees the learner
    has to follow the whole passage; leftover slots are then filled greedily.
    """
    chosen: list[Candidate] = []
    seen: set[str] = set()
    span = max(0.001, end_s - start_s)

    def take(c: Candidate) -> bool:
        key = c.answer.casefold()
        if key in seen or not _fits(c, chosen):
            return False
        chosen.append(c)
        seen.add(key)
        return True

    # Pass 1: one blank per time bucket.
    buckets: list[list[Candidate]] = [[] for _ in range(n)]
    for c in cands:
        b = min(n - 1, max(0, int((c.start - start_s) / span * n)))
        buckets[b].append(c)
    for bucket in buckets:
        for c in sorted(bucket, key=lambda c: c.score, reverse=True):
            if take(c):
                break

    # Pass 2: fill any slots the bucket pass couldn't (sparse or crowded regions).
    if len(chosen) < n:
        for c in sorted(cands, key=lambda c: c.score, reverse=True):
            if len(chosen) >= n:
                break
            take(c)

    return sorted(chosen, key=lambda c: c.word_index)


def _rendered_for(words: list[Word], display_text: str | None) -> RenderedText:
    """Use the punctuated transcript as the display string when we can align to it.

    Falls back to joining raw tokens, which is always safe but yields text without
    punctuation or elisions.
    """
    if display_text and display_text.strip():
        spans = align_words_to_text(words, display_text)
        matched = sum(1 for s in spans if s is not None)
        if words and matched / len(words) >= MIN_ALIGN_RATIO:
            # Unmatched words collapse to a zero-width span and are skipped as candidates.
            return RenderedText(
                text=display_text,
                spans=[s if s is not None else (0, 0) for s in spans],
            )
        log.warning(
            "falling back to token-joined cloze text (%d/%d words aligned)", matched, len(words)
        )
    return render_from_words(words)


def build_cloze(
    words: list[Word],
    lang: LanguageProfile,
    *,
    unit_start_s: float,
    unit_end_s: float,
    display_text: str | None = None,
    cefr: str | None = None,
    max_blanks: int | None = None,
    rng: random.Random | None = None,
) -> dict[str, Any] | None:
    """Build one cloze exercise for a unit, or None if the audio yields no good targets."""
    rng = rng or random.Random(1234)
    words = [w for w in words if w.text.strip()]
    if len(words) < 12:
        return None

    rendered = _rendered_for(words, display_text)
    cands = _candidates(words, rendered, lang)
    if not cands:
        log.warning("no cloze candidates in unit starting at %.1fs", unit_start_s)
        return None

    duration = max(1.0, unit_end_s - unit_start_s)
    n = max_blanks or int(round(duration / SECONDS_PER_BLANK))
    n = max(MIN_BLANKS, min(MAX_BLANKS, n, len(cands)))

    picked = _select(cands, n, start_s=unit_start_s, end_s=unit_end_s)
    if not picked:
        return None

    blanks: list[dict[str, Any]] = []
    for bi, c in enumerate(picked):
        blanks.append(
            {
                "index": bi,
                "char_start": c.span[0],
                "char_end": c.span[1],
                "length": len(c.answer),
                "audio_start_s": round(max(unit_start_s, c.start - PAD_BEFORE_S), 3),
                "audio_end_s": round(min(unit_end_s, c.end + PAD_AFTER_S), 3),
                "word_start_s": round(c.start, 3),
                "word_end_s": round(c.end, 3),
                "zipf": round(c.zipf, 2),
                # First letter is a cheap, optional scaffold the UI can reveal on request.
                "hint_initial": c.answer[0],
            }
        )

    answers = [c.answer for c in picked]
    payload: dict[str, Any] = {
        "text": rendered.text,
        "blanks": blanks,
        # Text with blanks already substituted, for clients that don't want to slice spans.
        "masked_text": _mask(rendered.text, blanks),
    }

    if (cefr or "") in WORD_BANK_LEVELS:
        bank = list(answers)
        pool = [
            c.answer
            for c in sorted(cands, key=lambda c: c.score, reverse=True)
            if c.answer.lower() not in {a.lower() for a in answers}
        ]
        bank.extend(pool[:WORD_BANK_DECOYS])
        rng.shuffle(bank)
        payload["word_bank"] = bank

    return {
        "kind": "cloze",
        "prompt": (
            "Écoutez l'extrait et complétez les blancs avec les mots manquants."
            if lang.code == "fr"
            else "Listen and fill in the missing words."
        ),
        "payload": payload,
        "answer": {"blanks": answers},
        "audio_start_s": round(unit_start_s, 3),
        "audio_end_s": round(unit_end_s, 3),
        "generator": "deterministic",
    }


def _mask(text: str, blanks: list[dict[str, Any]]) -> str:
    out = []
    cursor = 0
    for b in blanks:
        out.append(text[cursor : b["char_start"]])
        out.append(f"[{b['index'] + 1}:{'_' * max(3, b['length'])}]")
        cursor = b["char_end"]
    out.append(text[cursor:])
    return "".join(out)
