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


# --- punctuation on the hint line -----------------------------------------------------------


def test_slots_put_punctuation_where_it_belongs() -> None:
    """The hint line is the sentence: words as lengths, marks in place.

    This reverses the first version, which hid punctuation on the grounds that it was part of the
    challenge. It is not part of the *score* — the grader reports punctuation and does not mark it —
    so hiding it withheld nothing that was being assessed while leaving the hint line disagreeing
    with the sentence a learner could hear being read aloud.
    """
    from app.routers.dictation import word_hint_slots

    slots = word_hint_slots("Alors, ce sont des équations.")

    assert slots == [
        {"kind": "word", "length": 5},
        {"kind": "mark", "text": ","},
        {"kind": "word", "length": 2},
        {"kind": "word", "length": 4},
        {"kind": "word", "length": 3},
        {"kind": "word", "length": 9},
        {"kind": "mark", "text": "."},
    ]


def test_slots_keep_a_word_and_its_marks_distinguishable() -> None:
    """An opening mark comes before its word, a closing one after — so the UI can keep each mark
    against the word it touches rather than floating between two of them."""
    from app.routers.dictation import word_hint_slots

    assert word_hint_slots("«vraiment»?") == [
        {"kind": "mark", "text": "«"},
        {"kind": "word", "length": 8},
        {"kind": "mark", "text": "»?"},
    ]


def test_a_lone_mark_is_its_own_slot() -> None:
    from app.routers.dictation import word_hint_slots

    assert word_hint_slots("oui — non") == [
        {"kind": "word", "length": 3},
        {"kind": "mark", "text": "—"},
        {"kind": "word", "length": 3},
    ]


def test_lengths_are_derived_from_slots_so_they_cannot_disagree() -> None:
    """The word count printed beside the runs comes from the same tokenization as the runs."""
    from app.routers.dictation import word_hint_lengths, word_hint_slots

    for text in (
        "Alors, ce sont des équations.",
        "«vraiment»? oui — non, peut-être…",
        "C'est aujourd'hui.",
    ):
        slots = word_hint_slots(text)
        assert word_hint_lengths(text) == [s["length"] for s in slots if s["kind"] == "word"]


def test_slots_still_carry_no_letters_of_the_answer() -> None:
    """The safety property survives showing punctuation: words are lengths, never text."""
    from app.routers.dictation import word_hint_slots

    text = "Le secret absolu ne doit jamais fuiter."
    slots = word_hint_slots(text)

    # Word slots carry a length and nothing else — no key that could hold a letter of the answer.
    words = [s for s in slots if s["kind"] == "word"]
    assert all(set(s) == {"kind", "length"} for s in words)
    assert all(isinstance(s["length"], int) for s in words)

    # And the only text that ships is punctuation. Checked against the mark VALUES rather than the
    # serialised structure: `str(slots)` contains the key name "length", which contains "le", so a
    # naive substring search over it reports the word "Le" as leaked when nothing has.
    shipped = "".join(s["text"] for s in slots if s["kind"] == "mark")
    for word in text.replace(".", "").split():
        assert word.lower() not in shipped.lower()
