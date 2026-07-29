"""Grade a learner's free-form translation practice.

Free translation has many correct answers, so exact matching is useless and an
unconstrained judge is either a pushover or a pedant. But one thing *can* be checked
exactly — whether the learner actually used the target construction, which is the entire
point of the exercise. So grading is two independent signals:

  1. **Structure** — deterministic, from the construction's `required_markers`. Free, exact,
     and not subject to a model's opinion.
  2. **Meaning and grammar** — a judge, instructed to accept legitimate variation and to be
     specific about what is actually wrong.

Keeping them separate is what makes the feedback teach. "Your French is correct but it
sidesteps the pattern you were practising" is the single most useful thing this can say, and
a single blended score could never express it.

Tolerances match the cloze grader: diacritic slips and single-character typos are credited
with a note rather than penalised, because the skill under test is grammar, not spelling.
"""

from __future__ import annotations

import logging
from typing import Any

from ..languages import LanguageProfile
from ..llm.openai_client import LLMError, StructuredLLM
from ..skills.listening.grading import _levenshtein
from .constructions import by_key, uses_markers

log = logging.getLogger(__name__)

MAX_ANSWER_CHARS = 400
TYPO_MIN_LEN = 5

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["meaning_ok", "grammar_ok", "issues", "corrected_fr", "note_en", "better_than_reference"],
    "properties": {
        "meaning_ok": {
            "type": "boolean",
            "description": "Does the answer convey the meaning of the English prompt?",
        },
        "grammar_ok": {
            "type": "boolean",
            "description": "Is it grammatical French? Ignore accents and obvious typos.",
        },
        "issues": {
            "type": "array",
            "description": "Specific problems. Empty if none. Do not invent issues to seem thorough.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["fragment", "problem", "fix"],
                "properties": {
                    "fragment": {
                        "type": "string",
                        "description": "The learner's own words that are wrong, quoted.",
                    },
                    "problem": {"type": "string", "description": "What is wrong, in English."},
                    "fix": {"type": "string", "description": "The corrected French fragment."},
                },
            },
        },
        "corrected_fr": {
            "type": "string",
            "description": "The learner's sentence minimally corrected — keep their wording and choices wherever they work. Empty string if nothing needed fixing.",
        },
        "note_en": {
            "type": "string",
            "description": "One or two sentences of teaching feedback. Encouraging and specific.",
        },
        "better_than_reference": {
            "type": "boolean",
            "description": "True if the answer is a valid alternative at least as natural as the reference — say so rather than nudging them toward the reference.",
        },
    },
}

SYSTEM_PROMPT = """You judge a learner's {language_name} translation.

Be fair, not pedantic. Specifically:
- MANY renderings are correct. Accept different word choices, synonyms, and either tense \
where both work. The reference answer is one valid answer, not the only one.
- IGNORE accents and obvious typos entirely — they are handled elsewhere and are not what \
is being tested here.
- Do NOT invent problems to appear rigorous. If the answer is fine, say it is fine and \
return an empty issues list.
- Do NOT mark an answer wrong for being simpler or more elaborate than the reference.
- If their version is as good as or better than the reference, set \
better_than_reference and say so.
- `corrected_fr` must preserve the learner's own choices and change only what is actually \
broken. Never rewrite their sentence into the reference.
- Be concrete in `issues`: quote their own fragment, name the problem, give the fix.

You are NOT judging whether they used a particular grammatical construction — that is \
checked separately and precisely. Judge only meaning and grammaticality."""

USER_PROMPT = """The learner was asked to translate this into {language_name}:
  "{prompt_en}"

A model answer is:
  "{reference_fr}"
{alternatives}
The learner wrote:
  "{answer}"

Judge their answer on meaning and grammar."""


def _normalized_equal(a: str, b: str, lang: LanguageProfile) -> tuple[bool, str | None]:
    """(equivalent, tolerance note) under the same rules the cloze grader uses."""
    na, nb = lang.normalize_answer(a), lang.normalize_answer(b)
    if na == nb:
        return True, None
    fa = lang.normalize_answer(a, fold_diacritics=True)
    fb = lang.normalize_answer(b, fold_diacritics=True)
    if fa == fb:
        return True, "diacritics" if lang.diacritics_significant else None
    # Whole-sentence typo tolerance scales with length but stays tight.
    cap = 1 if len(fb) < 24 else 2
    if len(fb) >= TYPO_MIN_LEN and _levenshtein(fa, fb, cap=cap) <= cap:
        return True, "typo"
    return False, None


def grade_practice(
    *,
    language: str,
    construction_key: str,
    prompt_en: str,
    reference_fr: str,
    alternatives: list[str],
    answer: str,
    lang: LanguageProfile,
    llm: StructuredLLM | None = None,
    allow_llm: bool = True,
) -> dict[str, Any]:
    answer = (answer or "").strip()[:MAX_ANSWER_CHARS]
    if not answer:
        raise ValueError("empty answer")

    con = by_key(language, construction_key)

    # --- tier 1: did they use the construction? (deterministic) --------------
    if con is not None:
        structure_ok, missing = uses_markers(answer, con)
        structure_checked = True
    else:
        # Construction coined by the model rather than from the inventory — no markers to
        # check against, so structure can't be verified. Say so rather than guessing.
        structure_ok, missing, structure_checked = True, [], False

    # --- shortcut: matches the reference or a listed alternative -------------
    tolerance: str | None = None
    for candidate in [reference_fr, *alternatives]:
        same, note = _normalized_equal(answer, candidate, lang)
        if same:
            tolerance = note
            return _result(
                correct=structure_ok,
                score=1.0 if structure_ok else 0.5,
                structure_ok=structure_ok,
                structure_checked=structure_checked,
                missing=missing,
                meaning_ok=True,
                grammar_ok=True,
                issues=[],
                corrected=None,
                note=_MATCH_NOTES.get(tolerance) or "Exactly right.",
                tolerance=tolerance,
                reference=reference_fr,
                con=con,
                judged=False,
            )

    # --- tier 2: meaning and grammar (judge) --------------------------------
    if not allow_llm:
        return _result(
            correct=False,
            score=0.5 if structure_ok else 0.0,
            structure_ok=structure_ok,
            structure_checked=structure_checked,
            missing=missing,
            meaning_ok=None,
            grammar_ok=None,
            issues=[],
            corrected=None,
            note=(
                "Structure check only — meaning can't be verified offline. "
                f"A model answer: {reference_fr}"
            ),
            tolerance=None,
            reference=reference_fr,
            con=con,
            judged=False,
        )

    llm = llm or StructuredLLM()
    alt_block = (
        "Other acceptable answers:\n" + "\n".join(f'  "{a}"' for a in alternatives) + "\n"
        if alternatives
        else ""
    )
    try:
        raw = llm.complete_json(
            system=SYSTEM_PROMPT.format(language_name=lang.name_en),
            user=USER_PROMPT.format(
                language_name=lang.name_en,
                prompt_en=prompt_en,
                reference_fr=reference_fr,
                alternatives=alt_block,
                answer=answer,
            ),
            schema=JUDGE_SCHEMA,
            schema_name="translation_judgement",
            temperature=0.1,
            max_tokens=900,
        )
    except LLMError as exc:
        log.warning("practice judge failed: %s", exc)
        return _result(
            correct=False,
            score=0.5 if structure_ok else 0.0,
            structure_ok=structure_ok,
            structure_checked=structure_checked,
            missing=missing,
            meaning_ok=None,
            grammar_ok=None,
            issues=[],
            corrected=None,
            note=f"Couldn't check the meaning just now. A model answer: {reference_fr}",
            tolerance=None,
            reference=reference_fr,
            con=con,
            judged=False,
        )

    meaning_ok = bool(raw.get("meaning_ok"))
    grammar_ok = bool(raw.get("grammar_ok"))
    language_ok = meaning_ok and grammar_ok

    # Two independent signals -> four outcomes, each worth a different message.
    if structure_ok and language_ok:
        score, correct = 1.0, True
    elif language_ok and not structure_ok:
        score, correct = 0.6, False  # correct French that dodges the pattern
    elif structure_ok and not language_ok:
        score, correct = 0.5, False  # used the pattern, got the French wrong
    else:
        score, correct = 0.0, False

    return _result(
        correct=correct,
        score=score,
        structure_ok=structure_ok,
        structure_checked=structure_checked,
        missing=missing,
        meaning_ok=meaning_ok,
        grammar_ok=grammar_ok,
        issues=[
            {
                "fragment": (i.get("fragment") or "").strip(),
                "problem": (i.get("problem") or "").strip(),
                "fix": (i.get("fix") or "").strip(),
            }
            for i in raw.get("issues", [])
            if (i.get("problem") or "").strip()
        ],
        corrected=(raw.get("corrected_fr") or "").strip() or None,
        note=(raw.get("note_en") or "").strip() or None,
        tolerance=None,
        reference=reference_fr,
        con=con,
        judged=True,
        better_than_reference=bool(raw.get("better_than_reference")),
    )


_MATCH_NOTES = {
    "diacritics": "Right — watch the accents.",
    "typo": "Right — small typo.",
}


def _result(
    *,
    correct: bool,
    score: float,
    structure_ok: bool,
    structure_checked: bool,
    missing: list[str],
    meaning_ok: bool | None,
    grammar_ok: bool | None,
    issues: list[dict[str, Any]],
    corrected: str | None,
    note: str | None,
    tolerance: str | None,
    reference: str,
    con: Any,
    judged: bool,
    better_than_reference: bool = False,
) -> dict[str, Any]:
    # The headline is chosen so the structure signal is never buried behind the score.
    if correct:
        headline = "Correct"
    elif structure_checked and not structure_ok and meaning_ok:
        headline = f"Good French — but it avoids {con.schema_form if con else 'the structure'}"
    elif structure_checked and not structure_ok:
        headline = "The structure is missing"
    elif meaning_ok is False:
        headline = "Not quite"
    else:
        headline = "Partly there"

    return {
        "correct": correct,
        "score": round(score, 3),
        "headline": headline,
        "structure": {
            "checked": structure_checked,
            "used": structure_ok,
            "missing_markers": missing,
            "schema_form": con.schema_form if con else None,
        },
        "meaning_ok": meaning_ok,
        "grammar_ok": grammar_ok,
        "issues": issues,
        "corrected_fr": corrected,
        "note_en": note,
        "tolerance": tolerance,
        "reference_fr": reference,
        "better_than_reference": better_than_reference,
        "judged": judged,
    }


__all__ = ["grade_practice"]
