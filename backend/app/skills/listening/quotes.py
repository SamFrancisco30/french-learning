"""Locate an LLM-supplied quote inside the word timeline.

The generator is asked to cite the transcript phrase that justifies each answer. Once
that phrase is matched back onto word timestamps, every question gains a "replay the
moment that answers this" button — which turns a wrong answer into a listening drill
instead of a dead end.

Matching is fuzzy on purpose: the model paraphrases punctuation, drops elisions, and
occasionally normalizes numerals.
"""

from __future__ import annotations

from ...asr.base import Word
from ...languages import LanguageProfile

MIN_OVERLAP_RATIO = 0.55
PAD_BEFORE_S = 0.8
PAD_AFTER_S = 0.6


def locate_quote(
    words: list[Word],
    quote: str,
    lang: LanguageProfile,
    *,
    clamp_start: float,
    clamp_end: float,
) -> tuple[float, float] | None:
    """Best-matching [start, end] window for `quote`, or None if nothing matches well."""
    if not quote or not words:
        return None

    q_tokens = [t for t in lang.tokenize(quote) if lang.is_content_word(t)]
    if not q_tokens:
        q_tokens = lang.tokenize(quote)
    if not q_tokens:
        return None

    # Token stream of the unit, keeping a link back to each source word.
    stream: list[tuple[str, int]] = []
    for wi, w in enumerate(words):
        for tok in lang.tokenize(w.text):
            stream.append((tok, wi))
    if not stream:
        return None

    q_set = set(q_tokens)
    win = max(2, len(q_tokens))
    best_score, best_range = 0.0, None

    # Try the natural width plus a little slack for paraphrase.
    for width in {win, int(win * 1.4) + 1}:
        if width > len(stream):
            width = len(stream)
        for start in range(0, len(stream) - width + 1):
            window = stream[start : start + width]
            hits = sum(1 for tok, _ in window if tok in q_set)
            score = hits / len(q_tokens)
            if score > best_score:
                best_score = score
                best_range = (window[0][1], window[-1][1])

    if not best_range or best_score < MIN_OVERLAP_RATIO:
        return None

    first, last = best_range
    start_s = max(clamp_start, words[first].start - PAD_BEFORE_S)
    end_s = min(clamp_end, words[last].end + PAD_AFTER_S)
    if end_s <= start_s:
        return None
    return round(start_s, 3), round(end_s, 3)
