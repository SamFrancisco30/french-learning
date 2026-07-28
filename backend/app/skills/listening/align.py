"""Align timestamped word tokens onto punctuated transcript text.

Why this exists: Whisper's *segment* text is properly punctuated and accented
("On l'a appris dans la matinée."), but its *word* array is bare tokens
("On", "l", "a", "appris", ...). Building cloze display text by joining the word
array therefore produces "On l a appris" — visibly wrong French, and it strips the
elisions that matter most to a learner.

So we keep the punctuated text as the display string and align the word array onto
it, giving each word a character span in the *real* text.

Method: compare letter streams. Strip everything but alphanumerics from both the text
and each token; because both derive from the same audio pass, the token letters appear
in order in the text's letter stream. That makes the alignment robust to punctuation,
apostrophes and whitespace differences, and to whether the model emitted "l'a" as one
token or as "l" + "a". A bounded forward resync handles the occasional true mismatch.
"""

from __future__ import annotations

import logging
import unicodedata

from ...asr.base import Word

log = logging.getLogger(__name__)

# How far ahead (in letters) we'll look to resync after a mismatch.
RESYNC_WINDOW = 60


def _letters_index(text: str) -> tuple[str, list[int]]:
    """Alphanumeric-only projection of `text`, plus each kept char's original index."""
    chars: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(text):
        if ch.isalnum():
            chars.append(ch.casefold())
            idx.append(i)
    return "".join(chars), idx


def _key(token: str) -> str:
    return "".join(c.casefold() for c in token if c.isalnum())


def _fold(s: str) -> str:
    """Drop diacritics so an accent disagreement doesn't derail the whole alignment."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c)
    )


def align_words_to_text(words: list[Word], text: str) -> list[tuple[int, int] | None]:
    """Character span in `text` for each word, or None where no match was found."""
    stream, positions = _letters_index(text)
    stream_f = _fold(stream)
    spans: list[tuple[int, int] | None] = []
    cursor = 0

    for w in words:
        key = _key(w.text)
        if not key:
            spans.append(None)
            continue
        key_f = _fold(key)

        at = -1
        if stream_f.startswith(key_f, cursor):
            at = cursor
        else:
            # Tolerate a small drift (an inserted filler, a dropped token).
            found = stream_f.find(key_f, cursor, cursor + RESYNC_WINDOW + len(key_f))
            if found != -1:
                at = found

        if at == -1:
            spans.append(None)
            continue

        start_char = positions[at]
        end_char = positions[at + len(key_f) - 1] + 1
        spans.append((start_char, end_char))
        cursor = at + len(key_f)

    matched = sum(1 for s in spans if s is not None)
    if words and matched / len(words) < 0.8:
        log.warning("weak word/text alignment: %d/%d matched", matched, len(words))
    return spans
