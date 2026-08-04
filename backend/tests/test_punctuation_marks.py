"""Where a spoken punctuation mark lands in time.

The bug this pins: every mark in a sentence was announced at the same instant, before the first
word, so a four-comma sentence opened with "virgule virgule virgule point" and then read the
sentence. It was silent — the audio spliced perfectly, just all in one place — and it happened for
every item with more than one mark.

The cause was a mismatch of inputs: the ITEM's one sentence was aligned against the UNIT's word
timings. Almost nothing matched, so no mark could find the word it follows and all of them took the
same fallback.
"""

from __future__ import annotations

from app.languages import get_language
from app.media.punctuation import find_marks

FR = get_language("fr")

# Four words, one per second, and a text they align to exactly.
WORDS = [
    {"word": "Alors", "start": 0.0, "end": 0.8},
    {"word": "ce", "start": 1.0, "end": 1.4},
    {"word": "sont", "start": 2.0, "end": 2.6},
    {"word": "des", "start": 3.0, "end": 3.4},
    {"word": "équations", "start": 4.0, "end": 4.9},
]
TEXT = "Alors, ce sont des équations."


def test_each_mark_lands_after_the_word_it_follows() -> None:
    marks = find_marks(TEXT, WORDS, FR)

    assert [m.spoken for m in marks] == ["virgule", "point"]
    # The comma follows "Alors" (ends 0.8); the full stop follows "équations" (ends 4.9).
    assert marks[0].at_s == 0.8
    assert marks[1].at_s == 4.9


def test_marks_do_not_collapse_onto_one_instant() -> None:
    """The actual regression, stated directly: distinct marks get distinct times."""
    text = "Alors, ce sont, des équations."
    words = WORDS
    marks = find_marks(text, words, FR)

    times = [m.at_s for m in marks]
    assert len(times) == len(set(times)), f"marks share an instant: {times}"
    assert times == sorted(times), "marks must be announced in the order they are written"


def test_window_narrows_to_one_item_and_rebases_its_times() -> None:
    """A dictation item is a window into its unit, so it gets the unit's text and its own bounds."""
    marks = find_marks(TEXT, WORDS, FR, offset_s=2.0, until_s=5.0)

    # The comma at 0.8s is before the window and is dropped; the full stop is inside it.
    assert [m.spoken for m in marks] == ["point"]
    # Rebased, so it indexes the item's own audio rather than the unit's.
    assert marks[0].at_s == 4.9 - 2.0


def test_refuses_to_place_anything_when_the_alignment_is_weak() -> None:
    """The failure mode that produced the bug, now caught.

    Passing an item's sentence with a unit's worth of words aligns almost nothing. Announcing at a
    guessed time is worse than staying silent, because every unplaceable mark collapses onto the
    same instant — which is what a learner heard.
    """
    unit_words = WORDS + [
        {"word": f"mot{i}", "start": 10.0 + i, "end": 10.5 + i} for i in range(40)
    ]

    marks = find_marks("Alors, ce sont des équations.", unit_words, FR)

    assert marks == []


def test_no_words_means_no_announcements() -> None:
    assert find_marks(TEXT, [], FR) == []
    assert find_marks("", WORDS, FR) == []


def test_longest_symbol_wins_so_an_ellipsis_is_not_three_full_stops() -> None:
    words = [{"word": "Attends", "start": 0.0, "end": 0.9}]
    marks = find_marks("Attends...", words, FR)

    assert [m.spoken for m in marks] == ["points de suspension"]
