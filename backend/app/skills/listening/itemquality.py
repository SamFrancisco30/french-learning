"""Keeping a multiple-choice item honest.

A comprehension question is only a comprehension question if the audio is the only way to answer
it. The classic failure is not a wrong answer — it is a *guessable* one: some surface property of
the options gives the game away, and a test-wise learner scores well without listening at all.

This was measured on the 207 MCQ items in the library before any of this existed:

    longest option is correct (uniquely)   41.5%   (chance is 25%)
    correct option is tied-longest          9.2%
    correct option's length advantage      +4.1 characters over the mean distractor
    answer echoes the question (uniquely)   6.3%   (chance is ~25%)
    duplicate options                       0.0%
    true/false items that are true         50.7%

So exactly one give-away was real, and it was large: pick the longest option, never listen, score
about 41%. Echoes, duplicates and true/false imbalance were at or below chance and needed no fix —
they are audited here only so they cannot regress silently.

The fix for the length cue is deliberately not "ask the model nicely". The generator requests more
distractors than it needs and this module *selects* the subset that flattens the length profile,
which is deterministic, costs no extra tokens, and cannot drift the way an instruction can.
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from itertools import combinations

# A length difference under this fraction of the answer's length is noise, not a cue. At 0.15 a
# 40-character answer may exceed its distractors by 6 characters before it counts as a tell —
# roughly one word, which is below what a learner can eyeball across four options.
LENGTH_CUE_TOLERANCE = 0.15

# Content words shorter than this are function words in every language here ("dans", "avec"), and
# counting them makes every option look like an echo of every question.
MIN_CONTENT_WORD_LEN = 4

# How many content words the correct option must uniquely share with the question before word
# matching becomes a viable strategy for skipping the audio.
MIN_ECHO_OVERLAP = 2

# The trap that needs no understanding of the passage to write, and the one a learner can most often
# eliminate on gist alone. Allowed, but not more than once in a set.
WEAK_TRAP = "plausible_unstated"


def needs_longer_distractors(answer: str, distractors: list[str]) -> bool:
    """Is the key still conspicuous after the best available selection?

    True when no rearrangement of what the model offered can hide the key, which is the signal that
    another candidate slate is needed rather than another attempt at choosing from this one.
    """
    if not distractors:
        return False
    return all(len(answer) > len(d) for d in distractors) and (
        length_cue(answer, distractors) > LENGTH_CUE_TOLERANCE
    )


# Give-aways that make an item not worth serving at all, as opposed to merely flawed.
#
# `proper_noun_recognition` is here by explicit instruction: an item whose options are one sentence
# with a different name or number dropped in tests whether a label was caught, not whether anything
# was understood. Losing one question of three is a better outcome than asking a learner how old
# someone was. A length cue, by contrast, still leaves a real comprehension question, so it is
# recorded and kept.
DISQUALIFYING = frozenset(
    {"no_correct_option", "duplicate_options", "too_few_options", "proper_noun_recognition"}
)


def _fold(text: str) -> str:
    """Casefold, strip accents and punctuation — for comparing options as *content*."""
    decomposed = unicodedata.normalize("NFD", text.casefold())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^\w]+", "", stripped)


def _content_words(text: str) -> set[str]:
    decomposed = unicodedata.normalize("NFD", text.casefold())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return {w for w in re.split(r"[^\w]+", stripped) if len(w) >= MIN_CONTENT_WORD_LEN}


def length_cue(answer: str, distractors: list[str]) -> float:
    """How much longer the answer is than its distractors, as a fraction of its own length.

    Positive means the answer stands out as long, negative as short. Both are cues; a learner who
    notices that the odd-length option is always right does not need the audio either.
    """
    if not distractors or not answer:
        return 0.0
    mean_other = statistics.mean(len(d) for d in distractors)
    return (len(answer) - mean_other) / max(len(answer), 1)


def _penalty(answer: str, chosen: tuple[str, ...]) -> tuple[float, float]:
    """Lower is better. Sorts by "is the key the longest", then by how wide the option set is.

    Being the uniquely longest option is its own category rather than a magnitude, because it is
    the specific pattern a learner can act on: "longest wins" is a strategy, whereas "3 characters
    above the mean" is not.

    The second term is the SPREAD of all four options, not the key's distance from the mean. Using
    the mean looked equivalent and was not: it rewards balancing one very long distractor against
    one very short one, which leaves the key sitting at a comfortable average while a nine-character
    option next to two fifty-five-character ones is transparently filler. Optimising the band keeps
    every option in play, which is what makes the choice a real four-way one.
    """
    lengths = [len(c) for c in chosen]
    uniquely_longest = 1.0 if all(len(answer) > n for n in lengths) else 0.0
    together = [len(answer), *lengths]
    spread = (max(together) - min(together)) / max(max(together), 1)
    return (uniquely_longest, spread)


def choose_distractors(
    answer: str,
    candidates: list[str],
    n: int = 3,
    traps: list[str | None] | None = None,
) -> list[str]:
    """Pick `n` distractors that do not betray which option is correct.

    Duplicates go first — of the answer, and of each other — because an item with two identical
    options is a three-way choice wearing a four-way costume, and one that repeats the answer has
    no defensible key at all. Whatever survives is searched for the subset that is flattest in
    length and widest in trap mechanism.

    Trap variety is part of the objective because the model does not supply it unprompted: on the
    first live run with the trap taxonomy, 8 of the 9 distractors came back labelled
    `plausible_unstated` — the weakest of the seven, and the one that produces "wrong topic
    entirely" options a learner can eliminate on gist alone. Given five candidates, preferring
    three distinct mechanisms costs nothing and makes the item harder in the way that matters.

    Falls back to the best it can do when there are too few usable candidates: a short slate is a
    weaker item, but a missing item teaches nothing.
    """
    seen = {_fold(answer)}
    usable: list[str] = []
    usable_traps: list[str | None] = []
    for i, cand in enumerate(candidates):
        if not isinstance(cand, str):
            continue
        text = cand.strip()
        key = _fold(text)
        if not key or key in seen:
            continue
        seen.add(key)
        usable.append(text)
        usable_traps.append(traps[i] if traps and i < len(traps) else None)

    if len(usable) <= n:
        return usable

    def score(idx: tuple[int, ...]) -> tuple:
        chosen = tuple(usable[i] for i in idx)
        labels = [usable_traps[i] for i in idx if usable_traps[i]]
        # Distinct mechanisms, negated so that more variety sorts earlier. Ranked below the
        # length cue: a guessable item is a worse failure than a monotonous one.
        variety = -len(set(labels))
        # Surplus uses of the weakest trap. The instruction to use it at most once per question is
        # only partly obeyed: on a 10-unit live sample it was 45.6% of the distractors actually
        # shown, where "once per question" means at most 33%. Capped here rather than asked for.
        weak = max(0, labels.count(WEAK_TRAP) - 1)
        return (*_penalty(answer, chosen), weak, variety, sum(idx))

    # C(5,3) is ten subsets, so this is exhaustive rather than greedy. Combinations are taken over
    # positions, not strings: the index sum breaks ties toward the model's own ordering — which
    # reflects how good it thought each distractor was — and positions stay unambiguous where equal
    # strings would not.
    best = min(combinations(range(len(usable)), n), key=score)
    return [usable[i] for i in best]


def _proper_nouns(text: str) -> set[str]:
    """Capitalised words that are not merely sentence-initial, plus bare numerals.

    A heuristic, and only sound for languages that do not capitalise common nouns — French, Russian
    and Chinese all qualify, German would not. It exists to catch the item whose options differ
    only in which name is plugged into them, which tests whether the learner caught a proper noun
    rather than whether they understood anything.
    """
    tokens = re.findall(r"\S+", text)
    found: set[str] = set()
    for i, token in enumerate(tokens):
        bare = re.sub(r"[^\w]", "", token)
        if not bare:
            continue
        if bare.isdigit():
            found.add(bare)
        elif i > 0 and bare[:1].isupper():
            found.add(bare.casefold())
    return found


def differs_only_by_proper_noun(options: list[str]) -> bool:
    """Do the options say the same thing about different names?

    "Le sommet a lieu à Berlin" against "...à Madrid" is not a comprehension question — it asks
    whether a city name was caught. Detected by deleting the proper nouns and numbers and seeing
    whether the remaining frames collapse into one.
    """
    if len(options) < 2:
        return False
    frames = set()
    for option in options:
        tokens = re.findall(r"\S+", option)
        kept = [
            t
            for i, t in enumerate(tokens)
            if not (
                (i > 0 and re.sub(r"[^\w]", "", t)[:1].isupper())
                or re.sub(r"[^\w]", "", t).isdigit()
            )
        ]
        frames.add(_fold(" ".join(kept)))
    # Every option reduces to the same sentence, and the proper nouns really did differ.
    return len(frames) == 1 and len({frozenset(_proper_nouns(o)) for o in options}) > 1


def audit_mcq(question: str, options: list[str], correct_index: int) -> list[str]:
    """Name every give-away in a finished item. Empty means nothing detectable leaks.

    Reported rather than raised: these are quality signals for a human or a test, and an item with
    a length cue is still a usable item, unlike one with no correct answer.
    """
    problems: list[str] = []
    if len(options) < 3:
        problems.append("too_few_options")
    if not (0 <= correct_index < len(options)):
        return [*problems, "no_correct_option"]

    folded = [_fold(o) for o in options]
    if len(set(folded)) < len(folded):
        problems.append("duplicate_options")

    answer = options[correct_index]
    others = [o for i, o in enumerate(options) if i != correct_index]

    if others:
        cue = length_cue(answer, others)
        uniquely_longest = all(len(answer) > len(o) for o in others)
        uniquely_shortest = all(len(answer) < len(o) for o in others)
        if abs(cue) > LENGTH_CUE_TOLERANCE and (uniquely_longest or uniquely_shortest):
            problems.append("length_cue")

    if differs_only_by_proper_noun(options):
        problems.append("proper_noun_recognition")

    q_words = _content_words(question)
    if q_words:
        overlaps = [len(q_words & _content_words(o)) for o in options]
        best = overlaps[correct_index]
        if (
            best >= MIN_ECHO_OVERLAP
            and best == max(overlaps)
            and overlaps.count(best) == 1
        ):
            problems.append("answer_echoes_question")

    return problems


def is_worth_learning(word: str) -> bool:
    """Is this a word a learner should be taught, or just a name they heard?

    Proper nouns, numerals and single letters make terrible vocabulary entries: "Guterres" and
    "2024" are not French, and putting them in a matching exercise spends a learner's attention on
    recognition rather than on language.
    """
    text = word.strip()
    if len(text) < 2:
        return False
    if any(c.isdigit() for c in text):
        return False
    # A capitalised headword in these languages is a name. Multiword expressions are judged on
    # their first word, since that is what carries the sentence-initial exemption elsewhere.
    return not text[:1].isupper()


__all__ = [
    "LENGTH_CUE_TOLERANCE",
    "WEAK_TRAP",
    "audit_mcq",
    "choose_distractors",
    "differs_only_by_proper_noun",
    "is_worth_learning",
    "length_cue",
    "needs_longer_distractors",
]
