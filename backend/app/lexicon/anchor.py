"""Locate substrings in text, tolerantly, and snap selections to word boundaries.

Two jobs:

1. **Re-anchoring.** The expression extractor asks the model for the *surface strings* of
   each component of an expression, never for character offsets — models miscount offsets
   constantly, and a wrong offset silently mis-highlights. We find those strings here.
   Matching is diacritic- and punctuation-tolerant because models normalize apostrophes
   (`l'` vs `l’`), casing, and occasionally accents.

2. **Word snapping.** A learner's drag rarely lands on clean boundaries. Naive `\\b`
   regexes are wrong for French: they split `l'eau` into `l` + `eau` and `est-ce` into
   `est` + `ce`, and they do nothing at all for Chinese, which has no whitespace
   boundaries. Snapping goes through the LanguageProfile instead.
"""

from __future__ import annotations

import re
import unicodedata

from ..languages import LanguageProfile

# How far past the cursor a later component may appear. French MWEs are discontinuous but
# not unboundedly so — PARSEME-FR tops out around eight intervening tokens.
MAX_COMPONENT_GAP_CHARS = 80

# Characters that may sit inside a word: French elision apostrophes and hyphens.
_WORDCHAR_EXTRA = "'’-"


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))


def _letters_index(text: str) -> tuple[str, list[int]]:
    """Alphanumeric-only projection plus each kept char's original index."""
    chars: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(text):
        if ch.isalnum():
            chars.append(ch.casefold())
            idx.append(i)
    return _fold("".join(chars)), idx


def _key(s: str) -> str:
    return _fold("".join(c.casefold() for c in s if c.isalnum()))


def find_span(text: str, needle: str, *, start_at: int = 0) -> tuple[int, int] | None:
    """Character span of `needle` in `text`, ignoring punctuation/case/diacritics.

    `start_at` is a *character* offset in `text`; searching resumes at or after it.
    """
    stream, positions = _letters_index(text)
    key = _key(needle)
    if not key or not stream:
        return None

    # Translate the character cursor into a letter-stream cursor.
    lo = 0
    while lo < len(positions) and positions[lo] < start_at:
        lo += 1

    at = stream.find(key, lo)
    if at == -1:
        return None
    return positions[at], positions[at + len(key) - 1] + 1


def find_component_spans(
    text: str, components: list[str], *, start_at: int = 0
) -> list[tuple[int, int]] | None:
    """Spans for each component of a (possibly discontinuous) expression, in order.

    Returns None if any component can't be placed, or if a later component lands
    implausibly far from the previous one — which usually means the model quoted a
    component that isn't really in this passage.
    """
    spans: list[tuple[int, int]] = []
    cursor = start_at
    for i, comp in enumerate(components):
        span = find_span(text, comp, start_at=cursor)
        if span is None:
            return None
        if i > 0 and span[0] - spans[-1][1] > MAX_COMPONENT_GAP_CHARS:
            return None
        spans.append(span)
        cursor = span[1]
    return spans or None


# ---------------------------------------------------------------- word snapping


def snap_to_words(text: str, start: int, end: int, lang: LanguageProfile) -> tuple[int, int]:
    """Expand [start, end) outward to whole-word boundaries."""
    n = len(text)
    start = max(0, min(start, n))
    end = max(start, min(end, n))

    if lang.needs_segmentation:
        # No whitespace boundaries; a character-level selection is already meaningful.
        # Just trim surrounding punctuation and whitespace.
        while start < end and not text[start].isalnum():
            start += 1
        while end > start and not text[end - 1].isalnum():
            end -= 1
        return start, end

    def is_word_char(ch: str) -> bool:
        return ch.isalnum() or ch in _WORDCHAR_EXTRA

    # Walk left/right while still inside a word.
    while start > 0 and is_word_char(text[start - 1]) and is_word_char(text[start]):
        start -= 1
    while end < n and is_word_char(text[end]) and is_word_char(text[end - 1]):
        end += 1

    # Trim boundary punctuation that got swept in (a trailing hyphen or apostrophe).
    while start < end and not text[start].isalnum():
        start += 1
    while end > start and not text[end - 1].isalnum():
        end -= 1
    return start, end


_SENT_BOUNDARY = re.compile(r"[.!?…]+[\s ]+|[。！？]+")


def sentence_bounds(text: str, start: int, end: int, *, max_chars: int = 400) -> tuple[int, int]:
    """Character bounds of the sentence containing [start, end).

    Falls back to a character window when no boundary is nearby, so a transcript with
    missing punctuation still yields usable context.
    """
    left = 0
    for m in _SENT_BOUNDARY.finditer(text, 0, start):
        left = m.end()
    right_m = _SENT_BOUNDARY.search(text, end)
    right = right_m.end() if right_m else len(text)

    if right - left > max_chars:
        pad = max_chars // 2
        left = max(left, start - pad)
        right = min(right, end + pad)
    return left, right


def sentence_around(text: str, start: int, end: int, *, max_chars: int = 400) -> str:
    """The sentence containing [start, end), for sense disambiguation."""
    lo, hi = sentence_bounds(text, start, end, max_chars=max_chars)
    return text[lo:hi].strip()
