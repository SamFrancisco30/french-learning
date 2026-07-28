"""Answer grading.

Grading philosophy: this module tests *listening*, not spelling. So a learner who
clearly heard the word but wrote `ecologie` for `écologie`, or `resource` for
`ressource`, gets the credit — with a note telling them what was off. Being pedantic
here trains the wrong thing and makes the app feel hostile.

Tolerances, in order of severity:
  exact          -> full credit, silent
  diacritic-only -> full credit + accent note (only for languages where it matters)
  1-char typo    -> full credit + spelling note (words >= 5 chars only)
  elision slip   -> full credit ("l'eau" accepted for "eau")
  otherwise      -> no credit, correct answer shown
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...languages import LanguageProfile
from ...models import EX_CLOZE, EX_MCQ, EX_ORDERING, EX_TRUE_FALSE, EX_VOCAB_MATCH

TYPO_MIN_LEN = 5
PASS_THRESHOLD = 0.999  # what counts as "correct" for the is_correct flag


@dataclass
class GradeResult:
    is_correct: bool
    score: float  # 0.0 - 1.0
    feedback: dict[str, Any] = field(default_factory=dict)


def _levenshtein(a: str, b: str, cap: int = 2) -> int:
    """Edit distance, short-circuited once it exceeds `cap`."""
    if a == b:
        return 0
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def _match_word(given: str, expected: str, lang: LanguageProfile) -> tuple[bool, str | None]:
    """(credited, note). Note is a machine-readable tolerance tag."""
    g = lang.normalize_answer(given)
    e = lang.normalize_answer(expected)
    if not g:
        return False, "blank"
    if g == e:
        return True, None

    # Learner kept the elision: "l'eau" for "eau", or dropped it the other way.
    if lang.headword(g) == lang.headword(e):
        return True, "elision"

    gf = lang.normalize_answer(given, fold_diacritics=True)
    ef = lang.normalize_answer(expected, fold_diacritics=True)
    if gf == ef:
        return True, "diacritics" if lang.diacritics_significant else None

    if len(ef) >= TYPO_MIN_LEN and _levenshtein(gf, ef, cap=1) <= 1:
        return True, "typo"

    return False, None


def grade_cloze(response: dict[str, Any], answer: dict[str, Any], lang: LanguageProfile) -> GradeResult:
    expected: list[str] = list(answer.get("blanks", []))
    given_raw = response.get("blanks", [])
    given: list[str] = [str(g or "") for g in given_raw] + [""] * max(
        0, len(expected) - len(given_raw)
    )

    per_blank: list[dict[str, Any]] = []
    credited = 0
    for i, exp in enumerate(expected):
        ok, note = _match_word(given[i], exp, lang)
        credited += int(ok)
        per_blank.append(
            {
                "index": i,
                "given": given[i],
                "expected": exp,
                "correct": ok,
                "tolerance": note,
                "message": _MESSAGES.get(note) if ok and note else None,
            }
        )

    score = credited / len(expected) if expected else 0.0
    return GradeResult(
        is_correct=score >= PASS_THRESHOLD,
        score=round(score, 4),
        feedback={"blanks": per_blank, "correct_count": credited, "total": len(expected)},
    )


_MESSAGES = {
    "diacritics": "Correct — but check your accents.",
    "typo": "Correct — small spelling slip.",
    "elision": "Correct — the elided article isn't needed here.",
}


def grade_mcq(response: dict[str, Any], answer: dict[str, Any], lang: LanguageProfile) -> GradeResult:
    expected_idx = answer.get("index")
    expected_val = answer.get("value")
    given_idx = response.get("index")
    given_val = response.get("value")

    ok = False
    if given_idx is not None and expected_idx is not None:
        ok = int(given_idx) == int(expected_idx)
    elif given_val is not None and expected_val is not None:
        ok = lang.normalize_answer(str(given_val)) == lang.normalize_answer(str(expected_val))

    return GradeResult(
        is_correct=ok,
        score=1.0 if ok else 0.0,
        feedback={"correct_index": expected_idx, "correct_value": expected_val},
    )


def grade_true_false(
    response: dict[str, Any], answer: dict[str, Any], lang: LanguageProfile
) -> GradeResult:
    given = response.get("value")
    if isinstance(given, str):
        given = given.strip().lower() in {"true", "vrai", "1", "yes", "да", "对"}
    ok = bool(given) == bool(answer.get("value"))
    return GradeResult(
        is_correct=ok, score=1.0 if ok else 0.0, feedback={"correct_value": answer.get("value")}
    )


def grade_vocab_match(
    response: dict[str, Any], answer: dict[str, Any], lang: LanguageProfile
) -> GradeResult:
    expected: dict[str, str] = dict(answer.get("pairs", {}))
    given: dict[str, str] = dict(response.get("pairs", {}))
    if not expected:
        return GradeResult(False, 0.0, {})

    detail = {}
    hits = 0
    for word, gloss in expected.items():
        got = given.get(word, "")
        ok = lang.normalize_answer(str(got), fold_diacritics=True) == lang.normalize_answer(
            gloss, fold_diacritics=True
        )
        hits += int(ok)
        detail[word] = {"given": got, "expected": gloss, "correct": ok}

    score = hits / len(expected)
    return GradeResult(
        is_correct=score >= PASS_THRESHOLD,
        score=round(score, 4),
        feedback={"pairs": detail, "correct_count": hits, "total": len(expected)},
    )


def _lcs_len(a: list[str], b: list[str]) -> int:
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b, 1):
            cur.append(prev[j - 1] + 1 if x == y else max(prev[j], cur[j - 1]))
        prev = cur
    return prev[-1]


def grade_ordering(
    response: dict[str, Any], answer: dict[str, Any], lang: LanguageProfile
) -> GradeResult:
    expected: list[str] = list(answer.get("order", []))
    given: list[str] = [str(g) for g in response.get("order", [])]
    if not expected:
        return GradeResult(False, 0.0, {})

    norm = {lang.normalize_answer(e): e for e in expected}
    given_n = [lang.normalize_answer(g) for g in given]
    expected_n = [lang.normalize_answer(e) for e in expected]

    # Partial credit from the longest common subsequence — rewards getting most of
    # the sequence right instead of all-or-nothing.
    score = _lcs_len(given_n, expected_n) / len(expected_n)
    exact = given_n == expected_n
    return GradeResult(
        is_correct=exact,
        score=1.0 if exact else round(score, 4),
        feedback={"correct_order": expected, "unknown_items": [g for g in given_n if g not in norm]},
    )


_GRADERS = {
    EX_CLOZE: grade_cloze,
    EX_MCQ: grade_mcq,
    EX_TRUE_FALSE: grade_true_false,
    EX_VOCAB_MATCH: grade_vocab_match,
    EX_ORDERING: grade_ordering,
}


def grade(
    kind: str, response: dict[str, Any], answer: dict[str, Any], lang: LanguageProfile
) -> GradeResult:
    try:
        grader = _GRADERS[kind]
    except KeyError:
        raise ValueError(f"No grader for exercise kind {kind!r}") from None
    return grader(response or {}, answer or {}, lang)
