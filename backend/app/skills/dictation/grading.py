"""Dictation grading: align, classify, then score.

The listening grader in ../listening/grading.py opens by saying it "tests listening, not
spelling", and gives full credit for a missing accent or a one-character typo. That philosophy is
right there and wrong here. French dictée is substantially ABOUT orthography — the whole exercise
is discriminating a/à, ses/ces/c'est, and -é/-er/-ez, which sound identical. A grader that shrugs
at "les chat noir" is not teaching dictation, it is hiding the lesson.

So this module scores spelling. What it refuses to do is be merely punitive: every miss is
CLASSIFIED, because "homophone — you heard it correctly and chose the wrong spelling" teaches
something that "wrong" does not, and a learner who sees three accent slips and no real errors
knows exactly what to work on.

Three design decisions worth stating:

WORDS ARE SCORED; PUNCTUATION AND CAPITALISATION ARE REPORTED, NOT SCORED. A traditional dictée
counts commas, but in a listening app the score has to mean "did you hear this correctly", and
punctuation is largely unhearable — a comma and a clause break sound the same. They are counted
and shown so the learner can see them, without letting them dominate a number that should be
about the language.

ALIGNMENT IS GLOBAL, NOT POSITIONAL. Comparing word i to word i breaks catastrophically on a
single dropped word: everything after it shifts and scores zero. A Needleman-Wunsch alignment over
tokens finds the real correspondence, so one missed word costs one word.

INSERTIONS COST. Otherwise padding the answer with extra words is free, and a learner who types
the same phrase twice out of uncertainty scores better than one who commits.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from ...languages import LanguageProfile

# Credit awarded per reference word, by verdict.
#
# The accent and typo tiers are deliberately 0.5 rather than 0 or 1: they are real errors in a
# dictation, so full credit would be a lie, but a learner who heard the whole sentence and dropped
# one circumflex has done something categorically better than one who wrote a different word.
CREDIT = {
    "exact": 1.0,
    "case": 1.0,       # capitalisation alone — reported separately, not punished twice
    "accent": 0.5,
    "typo": 0.5,
    "elision": 0.5,    # "l'eau" / "leau", "j'ai" / "jai" — heard right, written wrong
    "ending": 0.0,     # -é / -er / -ez: the classic French dictation error, and the point
    "homophone": 0.0,  # heard right, chose the wrong spelling — also the point
    "wrong": 0.0,
    "missing": 0.0,
}
INSERTION_COST = 0.5

# Verb-ending set for the -é/-er/-ez confusion. All of these are the same sound in French, which is
# why the error is universal and why it earns its own category rather than landing in "wrong".
VERB_ENDINGS = ("é", "ée", "és", "ées", "er", "ez", "ai", "ais", "ait", "aient", "aie")

TYPO_MIN_LEN = 5
PASS_THRESHOLD = 0.95  # a dictation is "correct" at 95% of available credit, not 99.9%

_WORD_RE = re.compile(r"[^\W\d_]+(?:[''’\-][^\W\d_]+)*|\d+(?:[.,]\d+)*", re.UNICODE)
_PUNCT_RE = re.compile(r"[.,;:!?…«»\"()\[\]—–]")


@dataclass
class WordVerdict:
    """One aligned pair. `expected` empty means the learner added a word."""

    expected: str
    given: str
    verdict: str
    credit: float
    note: str | None = None


@dataclass
class DictationResult:
    is_correct: bool
    score: float
    verdicts: list[WordVerdict] = field(default_factory=list)
    feedback: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------- normalisation


def _fold_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))


def _bare(s: str) -> str:
    """Lowercased, accent-stripped, apostrophes and hyphens removed — the loosest comparison."""
    return _fold_accents(s.casefold()).replace("'", "").replace("’", "").replace("-", "")


def tokenize_surface(text: str) -> list[str]:
    """Words with their original case, accents and apostrophes intact.

    Deliberately not LanguageProfile.tokenize, which lowercases and splits elisions — both of
    which are precisely the distinctions being graded here.
    """
    return _WORD_RE.findall(text)


def count_punctuation(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for ch in _PUNCT_RE.findall(text):
        out[ch] = out.get(ch, 0) + 1
    return out


# ---------------------------------------------------------------- classification


def _homophone_group(word: str, lang: LanguageProfile) -> tuple[str, ...] | None:
    w = word.casefold()
    for group in lang.homophone_groups:
        if w in group:
            return group
    return None


def _shares_verb_ending(a: str, b: str) -> bool:
    """Same stem, both ending in the -é/-er/-ez family."""
    for ea in VERB_ENDINGS:
        if not a.endswith(ea):
            continue
        for eb in VERB_ENDINGS:
            if ea == eb or not b.endswith(eb):
                continue
            if _fold_accents(a[: -len(ea)]).casefold() == _fold_accents(b[: -len(eb)]).casefold():
                return len(a) - len(ea) >= 2  # a real stem, not a two-letter coincidence
    return False


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def classify(expected: str, given: str, lang: LanguageProfile) -> tuple[str, str | None]:
    """(verdict, human note). Order matters: the most specific diagnosis wins."""
    if expected == given:
        return "exact", None
    if expected.casefold() == given.casefold():
        return "case", "capitalisation"

    # Homophones are checked BEFORE accents, and that ordering is the whole point. "a" for "à"
    # differs only by a diacritic, so an accent-first grader calls French's single most common
    # dictation error a stray accent and hands over half credit. It is not a slip of the pen; it
    # is the distinction being tested. This only fires when both words are declared members of
    # the same set, so an ordinary accent miss still lands in "accent".
    group = _homophone_group(expected, lang)
    if group and given.casefold() in group:
        others = " / ".join(g for g in group if g != expected.casefold())
        return "homophone", f"sounds the same as {others} — the spelling here is {expected}"

    if _fold_accents(expected).casefold() == _fold_accents(given).casefold():
        return "accent", f"accents: {expected}"

    # Elision written open or closed: "l'eau" vs "leau", "j'ai" vs "jai".
    if _bare(expected) == _bare(given):
        return "elision", f"elision: {expected}"

    if _shares_verb_ending(expected, given):
        return "ending", f"verb ending: {expected}, not {given}"

    if len(expected) >= TYPO_MIN_LEN and _levenshtein(expected.casefold(), given.casefold()) == 1:
        return "typo", f"one letter out: {expected}"

    return "wrong", None


# ---------------------------------------------------------------- alignment


def _similarity(a: str, b: str) -> float:
    """0..1, used only to decide whether two tokens should align at all."""
    ba, bb = _bare(a), _bare(b)
    if ba == bb:
        return 1.0
    if not ba or not bb:
        return 0.0
    d = _levenshtein(ba, bb)
    return max(0.0, 1.0 - d / max(len(ba), len(bb)))

# Below this, two tokens are treated as unrelated, so the aligner prefers a deletion plus an
# insertion over pairing them. Without a floor, every missing word gets "matched" to whatever
# happened to be typed there and the report blames the wrong thing.
ALIGN_FLOOR = 0.34


def align_tokens(ref: list[str], got: list[str]) -> list[tuple[int | None, int | None]]:
    """Needleman-Wunsch over tokens. Returns (ref_idx, got_idx) pairs; None marks a gap."""
    n, m = len(ref), len(got)
    GAP = -1.0
    # score[i][j] = best score aligning ref[:i] with got[:j]
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0] = i * GAP
    for j in range(1, m + 1):
        score[0][j] = j * GAP
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            sim = _similarity(ref[i - 1], got[j - 1])
            diag = score[i - 1][j - 1] + (sim if sim >= ALIGN_FLOOR else 2 * GAP)
            score[i][j] = max(diag, score[i - 1][j] + GAP, score[i][j - 1] + GAP)

    out: list[tuple[int | None, int | None]] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0:
            sim = _similarity(ref[i - 1], got[j - 1])
            diag = score[i - 1][j - 1] + (sim if sim >= ALIGN_FLOOR else 2 * GAP)
            if score[i][j] == diag:
                out.append((i - 1, j - 1))
                i, j = i - 1, j - 1
                continue
        if i > 0 and score[i][j] == score[i - 1][j] + GAP:
            out.append((i - 1, None))
            i -= 1
            continue
        out.append((None, j - 1))
        j -= 1
    out.reverse()
    return out


# ---------------------------------------------------------------- public


def grade_dictation(
    given_text: str, reference: str, lang: LanguageProfile
) -> DictationResult:
    ref = tokenize_surface(reference)
    got = tokenize_surface(given_text)

    if not ref:
        return DictationResult(is_correct=False, score=0.0, feedback={"error": "no reference text"})
    if not got:
        return DictationResult(
            is_correct=False,
            score=0.0,
            verdicts=[WordVerdict(w, "", "missing", 0.0) for w in ref],
            feedback={"total": len(ref), "typed_nothing": True},
        )

    verdicts: list[WordVerdict] = []
    counts: dict[str, int] = {}
    earned = 0.0
    insertions = 0

    for ri, gi in align_tokens(ref, got):
        if ri is None:
            insertions += 1
            verdicts.append(WordVerdict("", got[gi], "added", 0.0, "not in the audio"))
            counts["added"] = counts.get("added", 0) + 1
            continue
        if gi is None:
            verdicts.append(WordVerdict(ref[ri], "", "missing", 0.0, "missed"))
            counts["missing"] = counts.get("missing", 0) + 1
            continue
        verdict, note = classify(ref[ri], got[gi], lang)
        credit = CREDIT.get(verdict, 0.0)
        earned += credit
        verdicts.append(WordVerdict(ref[ri], got[gi], verdict, credit, note))
        counts[verdict] = counts.get(verdict, 0) + 1

    earned = max(0.0, earned - insertions * INSERTION_COST)
    score = min(1.0, earned / len(ref))

    # Punctuation and capitalisation: counted, shown, not scored.
    ref_punct = count_punctuation(reference)
    got_punct = count_punctuation(given_text)
    punct_missing = {k: v - got_punct.get(k, 0) for k, v in ref_punct.items() if v > got_punct.get(k, 0)}

    return DictationResult(
        is_correct=score >= PASS_THRESHOLD,
        score=round(score, 4),
        verdicts=verdicts,
        feedback={
            "total": len(ref),
            "typed": len(got),
            "counts": counts,
            "exact": counts.get("exact", 0) + counts.get("case", 0),
            "punctuation_missing": punct_missing,
            "punctuation_scored": False,
            "reference": reference,
        },
    )
