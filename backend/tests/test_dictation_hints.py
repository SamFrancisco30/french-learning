"""Word-length hints for the dictée.

The property that matters most is negative: the hint must carry lengths and nothing else. The words
are the answer, and the item payload is served *before* the learner attempts it.
"""

from __future__ import annotations

import pytest

from app.routers.dictation import word_hint_lengths


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("David Delos, on va plus loin avec vous.", [5, 5, 2, 2, 4, 4, 4, 4]),
        # Trailing punctuation is measured off, so a comma does not inflate the word before it.
        ("Bonjour, monde!", [7, 5]),
        ("", []),
        ("   ", []),
        # Punctuation alone contributes no run at all rather than a zero-length one, which would
        # render as an empty gap the learner would try to fill.
        ("oui — non", [3, 3]),
        ("«Bonjour»", [7]),
    ],
)
def test_counts_letters_per_word(text: str, expected: list[int]) -> None:
    assert word_hint_lengths(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # An elision is one thing the learner types, so it is one run including the apostrophe.
        ("l'a", [3]),
        ("C'est", [5]),
        ("peut-être", [9]),
        ("aujourd'hui", [11]),
    ],
)
def test_elisions_and_hyphens_stay_one_word(text: str, expected: list[int]) -> None:
    """Splitting "peut-être" into two runs would tell the learner it is two words, which is both
    wrong and a worse hint than saying nothing."""
    assert word_hint_lengths(text) == expected


def test_accented_letters_count_once() -> None:
    # "matinée" is 7 characters, not 7-plus-combining-marks. A hint that over-counted accented words
    # would be actively misleading in the one language this app is built for.
    assert word_hint_lengths("matinée") == [7]
    assert word_hint_lengths("ça où être") == [2, 2, 4]


def test_hint_reveals_no_letters() -> None:
    """The whole safety property in one assertion: the output is numbers, and nothing but."""
    text = "Le secret absolu ne doit jamais fuiter."
    hints = word_hint_lengths(text)

    assert all(isinstance(n, int) for n in hints)
    # No word of the source survives anywhere in the serialised hint.
    serialised = str(hints)
    for word in text.replace(".", "").split():
        assert word.lower() not in serialised.lower()


def test_word_count_agrees_with_the_payloads_own_count() -> None:
    """`word_count` is already published beside these lengths; a hint with a different number of
    runs than the stated word count would look like a bug to the learner."""
    text = "David Delos, on va plus loin avec vous."
    assert len(word_hint_lengths(text)) == 8
