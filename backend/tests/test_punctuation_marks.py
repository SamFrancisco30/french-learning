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


# --- one item must not inherit the sentence before it ----------------------------------------

# Two sentences in one unit. The first ends exactly where the second begins, which is the whole
# difficulty: a mark is timed from the END of the word it follows, so the first sentence's full stop
# is timed at the instant the second sentence starts.
TWO_SENTENCES = "Il pleut ici. Il fait chaud."
TWO_SENTENCE_WORDS = [
    {"word": "Il", "start": 0.0, "end": 0.3},
    {"word": "pleut", "start": 0.4, "end": 0.9},
    {"word": "ici", "start": 1.0, "end": 1.5},
    {"word": "Il", "start": 2.0, "end": 2.3},
    {"word": "fait", "start": 2.4, "end": 2.9},
    {"word": "chaud", "start": 3.0, "end": 3.6},
]


def test_an_item_does_not_announce_the_previous_sentences_full_stop() -> None:
    """The reported bug: a dictation of "Il fait chaud." opened by saying "point".

    The full stop of "Il pleut ici." is timed at 1.5s, and the second sentence's window starts at
    1.5s, so a time window that included its own start swept the mark in — and it was announced
    before the first word because it clamped to 0.
    """
    second = TWO_SENTENCES.index("Il fait")
    marks = find_marks(
        TWO_SENTENCES,
        TWO_SENTENCE_WORDS,
        FR,
        offset_s=1.5,
        until_s=3.6,
        char_range=(second, len(TWO_SENTENCES)),
    )

    assert [m.spoken for m in marks] == ["point"]
    # Its own full stop, at the end of its own audio — not one at 0.0 before it starts.
    assert marks[0].at_s == 3.6 - 1.5


def test_a_mark_timed_outside_the_item_is_dropped_rather_than_placed() -> None:
    """The same artefact by another route.

    When the word a mark follows fails to align, the fallback reaches back to an earlier word. That
    time lands before the item begins and clamps to 0.00 — "point" before the first word again, from
    a mark that genuinely belongs to this item. Dropping it loses one announcement; placing it puts
    the announcement in the one position that is definitely wrong.
    """
    # A window that starts after every word has already ended, so nothing can resolve inside it.
    marks = find_marks(
        TWO_SENTENCES,
        TWO_SENTENCE_WORDS,
        FR,
        offset_s=10.0,
        until_s=12.0,
        char_range=(0, len(TWO_SENTENCES)),
    )

    assert marks == []


def test_the_character_span_alone_is_not_enough() -> None:
    """Guards the combination: the span says which marks are the item's, the window says we know
    where they go. Either test alone lets the reported bug back in by one of its two routes."""
    first_stop = TWO_SENTENCES.index(".")

    # Textually the first sentence's, and timed at the second sentence's start.
    spans_first = find_marks(
        TWO_SENTENCES, TWO_SENTENCE_WORDS, FR,
        offset_s=1.5, until_s=3.6, char_range=(0, first_stop + 1),
    )

    assert spans_first == []
