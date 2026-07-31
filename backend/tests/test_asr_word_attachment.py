"""Word-to-segment attachment, and the boundary case that stripped a cloze of its punctuation.

Hosted whisper returns `words` and `segments` as sibling arrays, so they have to be re-joined
by time. It also returns a lot of zero-duration words (start == end). When one of those sits
exactly on a segment boundary, an inclusive cursor test dropped the segment that owns it out
of the candidate window, and the word was filed under the NEXT segment — while its text stayed
with the previous one.

That desynchronises the unit: its word array begins with a word that its own text does not
contain. `align_words_to_text` then matches that stray token against a later occurrence of the
same letters, drags its forward-only cursor past everything before it, and reports a hopeless
match ratio. `build_cloze` sees the bad ratio, stops trusting the transcript text, and renders
the passage by joining bare tokens instead — French with the apostrophes and hyphens gone:
"de l eau", "toi même", "se passe t il".

One exercise in the library was in that state, from exactly the data below.
"""

from __future__ import annotations

from app.asr.base import ASRSegment, Word, attach_words_to_segments
from app.skills.listening.align import align_words_to_text


def _texts(seg: ASRSegment) -> list[str]:
    return [w.text for w in seg.words]


def test_zero_duration_word_on_a_boundary_stays_with_the_segment_holding_its_text() -> None:
    # Verbatim from unit 4 of "COMMENT SE FORMENT LES RIVIÈRES ?": segment A's text ends
    # "dans le sol.", and whisper timestamped "sol" at 76.10-76.10 — precisely A's end.
    a = ASRSegment(idx=0, start=70.0, end=76.10, text="peut s'infiltrer dans le sol.")
    b = ASRSegment(idx=1, start=77.94, end=90.0, text="Si tu as déjà arrosé une plante,")
    words = [
        Word("infiltrer", 75.18, 75.48),
        Word("dans", 75.48, 75.66),
        Word("le", 75.66, 76.10),
        Word("sol", 76.10, 76.10),
        Word("Si", 77.94, 78.46),
        Word("tu", 78.46, 78.56),
    ]

    attach_words_to_segments([a, b], words)

    assert "sol" in _texts(a), "the word must follow the segment whose text contains it"
    assert "sol" not in _texts(b)
    assert _texts(b) == ["Si", "tu"]


def test_the_desync_this_prevents_is_total_not_marginal() -> None:
    """One misfiled leading token is enough to destroy the whole alignment, not just its own."""
    text = (
        "Si tu as déjà arrosé une plante, tu as pu voir toi-même qu'en effet, "
        "le sol peut absorber de l'eau."
    )
    real = [
        Word("Si", 77.94, 78.46), Word("tu", 78.46, 78.56), Word("as", 78.56, 78.68),
        Word("déjà", 78.68, 79.0), Word("arrosé", 79.0, 79.4), Word("une", 79.4, 79.6),
        Word("plante", 79.6, 80.0), Word("tu", 80.0, 80.2), Word("as", 80.2, 80.4),
        Word("pu", 80.4, 80.6), Word("voir", 80.6, 80.9), Word("toi", 80.9, 81.1),
        Word("même", 81.3, 81.3), Word("qu", 81.4, 81.5), Word("en", 81.54, 81.54),
        Word("effet", 81.6, 82.0), Word("le", 82.0, 82.2), Word("sol", 82.2, 82.5),
        Word("peut", 82.5, 82.8), Word("absorber", 82.8, 83.3), Word("de", 83.3, 83.5),
        Word("l", 83.5, 83.7), Word("eau", 83.86, 83.86),
    ]

    clean = align_words_to_text(real, text)
    clean_ratio = sum(1 for s in clean if s is not None) / len(clean)

    # Same words, but with the stray "sol" from the previous segment on the front.
    contaminated = align_words_to_text([Word("sol", 76.10, 76.10)] + real, text)
    bad_ratio = sum(1 for s in contaminated if s is not None) / len(contaminated)

    assert clean_ratio > 0.9, f"a correct word array should align cleanly, got {clean_ratio:.2f}"
    # The damage is not confined to the one bad token: the stray "sol" matches the real
    # "le sol" further along, and everything before that point becomes unreachable.
    assert bad_ratio < 0.5, (
        "expected the stray leading token to desync the whole alignment, "
        f"got {bad_ratio:.2f} — if this now passes, the aligner itself became resilient"
    )


def test_ordinary_words_are_unaffected() -> None:
    """A word that legitimately starts where a segment ends still goes to the next segment."""
    a = ASRSegment(idx=0, start=0.0, end=10.0, text="Première phrase.")
    b = ASRSegment(idx=1, start=10.0, end=20.0, text="Deuxième phrase.")
    words = [
        Word("Première", 0.5, 1.2),
        Word("phrase", 1.2, 2.0),
        Word("Deuxième", 10.0, 10.8),  # starts exactly on the shared boundary
        Word("phrase", 10.8, 11.4),
    ]

    attach_words_to_segments([a, b], words)

    assert _texts(a) == ["Première", "phrase"]
    assert _texts(b) == ["Deuxième", "phrase"]


def test_word_in_a_gap_goes_to_the_nearer_segment() -> None:
    """With no overlap to compare, fall back to nearest-in-time as the contract promises."""
    a = ASRSegment(idx=0, start=0.0, end=10.0, text="Avant.")
    b = ASRSegment(idx=1, start=15.0, end=25.0, text="Après.")
    attach_words_to_segments([a, b], [Word("hein", 10.4, 10.4), Word("bon", 14.6, 14.6)])

    assert _texts(a) == ["hein"], "10.4 is 0.4s from A and 4.6s from B"
    assert _texts(b) == ["bon"], "14.6 is 4.6s from A and 0.4s from B"


def test_every_word_is_placed_exactly_once() -> None:
    """No word may be dropped or duplicated, whatever the timings look like."""
    segs = [
        ASRSegment(idx=0, start=0.0, end=5.0, text="un"),
        ASRSegment(idx=1, start=5.0, end=10.0, text="deux"),
        ASRSegment(idx=2, start=10.0, end=15.0, text="trois"),
    ]
    words = [
        Word("a", 0.0, 0.0), Word("b", 4.9, 5.0), Word("c", 5.0, 5.0),
        Word("d", 7.0, 7.5), Word("e", 10.0, 10.0), Word("f", 12.0, 12.4),
        Word("g", 15.0, 15.0), Word("h", 99.0, 99.0),
    ]

    attach_words_to_segments(segs, words)

    placed = [w.text for s in segs for w in s.words]
    assert sorted(placed) == sorted(w.text for w in words)
    assert len(placed) == len(words)
