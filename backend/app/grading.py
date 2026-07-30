"""Grading dispatch across skills.

Lives at app level rather than inside skills/listening/ because there is now more than one skill
that grades. The listening graders stay where they are; this only decides who handles a kind, so
adding a skill means adding a branch here rather than reaching into another skill's module.
"""

from __future__ import annotations

from typing import Any

from .languages import LanguageProfile
from .models import DICTATION_KINDS
from .skills.dictation.grading import grade_dictation
from .skills.listening.grading import GradeResult
from .skills.listening.grading import grade as grade_listening


def _dictation(response: dict[str, Any], answer: dict[str, Any], lang: LanguageProfile) -> GradeResult:
    result = grade_dictation(
        str(response.get("text") or ""), str(answer.get("text") or ""), lang
    )
    return GradeResult(
        is_correct=result.is_correct,
        score=result.score,
        feedback={
            **result.feedback,
            # The per-word verdicts are the useful part of a dictation result: the score says how
            # you did, this says what to fix. Serialised flat so the client can render it directly.
            "words": [
                {
                    "expected": v.expected,
                    "given": v.given,
                    "verdict": v.verdict,
                    "credit": v.credit,
                    "note": v.note,
                }
                for v in result.verdicts
            ],
        },
    )


def grade(
    kind: str, response: dict[str, Any], answer: dict[str, Any], lang: LanguageProfile
) -> GradeResult:
    if kind in DICTATION_KINDS:
        return _dictation(response, answer, lang)
    return grade_listening(kind, response, answer, lang)
