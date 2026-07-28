"""Generate comprehension exercises for a listening unit.

One LLM call per unit produces the whole set (gist, MCQ, true/false, vocabulary,
ordering); the cloze is generated deterministically in `cloze.py` because it must be
anchored to real ASR timestamps rather than to whatever the model believes it heard.

The prompt enforces the two properties that make or break a listening exercise:
  1. answers must be derivable *from this audio*, not from general knowledge
  2. every item cites a verbatim transcript quote, which we match back to timestamps
     so a wrong answer can be replayed instead of just marked red
"""

from __future__ import annotations

import logging
import random
from typing import Any

from ...asr.base import Word
from ...languages import LanguageProfile
from ...llm.openai_client import LLMError, StructuredLLM
from ...models import EX_MCQ, EX_ORDERING, EX_TRUE_FALSE, EX_VOCAB_MATCH
from .quotes import locate_quote

log = logging.getLogger(__name__)

TOPICS = [
    "world_news",
    "politics",
    "economics",
    "geography",
    "biology",
    "science",
    "technology",
    "environment",
    "history",
    "culture",
    "society",
    "sport",
    "other",
]

N_MCQ = 3
N_TRUE_FALSE = 3
N_VOCAB = 4
N_ORDERING_MIN = 3

_STR = {"type": "string"}

UNIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["gist_en", "title_target", "topic", "mcq", "true_false", "vocab", "ordering"],
    "properties": {
        "gist_en": {
            "type": "string",
            "description": "One sentence in English describing what this passage is about, "
            "specific enough to be useful as a preview but without revealing answers.",
        },
        "title_target": {
            "type": "string",
            "description": "Short title (max 8 words) in the TARGET language.",
        },
        "topic": {"type": "string", "enum": TOPICS},
        "mcq": {
            "type": "array",
            "description": f"Exactly {N_MCQ} multiple-choice comprehension questions.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["question", "options", "correct_index", "explanation_en", "quote"],
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question, in the TARGET language.",
                    },
                    "options": {
                        "type": "array",
                        "description": "Exactly 4 options in the TARGET language.",
                        "items": _STR,
                    },
                    "correct_index": {"type": "integer", "enum": [0, 1, 2, 3]},
                    "explanation_en": {
                        "type": "string",
                        "description": "Why the answer is right, in English, 1-2 sentences.",
                    },
                    "quote": {
                        "type": "string",
                        "description": "Verbatim phrase from the transcript that proves it.",
                    },
                },
            },
        },
        "true_false": {
            "type": "array",
            "description": f"Exactly {N_TRUE_FALSE} true/false statements, mixed truth values.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["statement", "is_true", "explanation_en", "quote"],
                "properties": {
                    "statement": {"type": "string"},
                    "is_true": {"type": "boolean"},
                    "explanation_en": _STR,
                    "quote": _STR,
                },
            },
        },
        "vocab": {
            "type": "array",
            "description": f"Exactly {N_VOCAB} useful words/expressions actually spoken.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["word", "gloss_en", "definition_target", "example_from_audio"],
                "properties": {
                    "word": {"type": "string", "description": "Dictionary form."},
                    "gloss_en": {"type": "string", "description": "Short English gloss."},
                    "definition_target": {
                        "type": "string",
                        "description": "Learner-friendly definition in the TARGET language.",
                    },
                    "example_from_audio": {
                        "type": "string",
                        "description": "The clause from the transcript containing the word.",
                    },
                },
            },
        },
        "ordering": {
            "type": "array",
            "description": "4-5 short statements in the TARGET language describing the "
            "passage's key points, listed in the order they were said.",
            "items": _STR,
        },
    },
}

SYSTEM_PROMPT = """You are an expert {language_name} listening-comprehension item writer \
working from an automatic transcript of authentic audio.

Hard rules:
1. Every answer MUST be derivable from THIS passage alone. Never write a question a \
learner could answer from general knowledge without listening.
2. Write questions, options, statements and definitions in {language_name}. Write \
`explanation_en` and `gloss_en` in English.
3. Calibrate the language of your questions to CEFR {cefr}. Do not use vocabulary \
harder than the passage itself.
4. Distractors must be plausible and belong to the same semantic category as the \
answer (same kind of number, place, or actor). No joke options, no "all of the above", \
no options that are obviously absurd. Exactly one option is defensibly correct.
5. `quote` must be copied VERBATIM from the transcript — same words, same order. It is \
matched programmatically against audio timestamps, so paraphrase breaks the feature.
6. Vary what you probe: main idea, specific detail, numbers/quantities, cause/effect, \
speaker stance. Do not make every question a detail-retrieval question.
7. The transcript is machine-generated and may contain errors. Ignore garbled spans \
rather than writing questions about them.
8. Mix true and false statements — do not make them all true."""

USER_PROMPT = """Passage metadata
- Language: {language_name}
- Estimated level: CEFR {cefr}
- Speech rate: {wpm:.0f} words/min
- Duration: {duration:.0f} seconds
- Source: {source_title}

Transcript:
\"\"\"
{transcript}
\"\"\"

Write exactly {n_mcq} multiple-choice questions (4 options each), exactly \
{n_tf} true/false statements, exactly {n_vocab} vocabulary entries, and 4-5 ordering \
statements."""


def _trim(items: list[Any], n: int) -> list[Any]:
    return items[:n] if len(items) > n else items


def generate_unit_exercises(
    *,
    transcript: str,
    words: list[Word],
    lang: LanguageProfile,
    cefr: str,
    wpm: float,
    unit_start_s: float,
    unit_end_s: float,
    source_title: str,
    llm: StructuredLLM | None = None,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Return {"gist_en", "title", "topic", "exercises": [...]} for one unit.

    Raises LLMError if generation fails; callers may fall back to cloze-only lessons.
    """
    llm = llm or StructuredLLM()
    rng = rng or random.Random(7)

    raw = llm.complete_json(
        system=SYSTEM_PROMPT.format(language_name=lang.name_en, cefr=cefr),
        user=USER_PROMPT.format(
            language_name=lang.name_en,
            cefr=cefr,
            wpm=wpm,
            duration=unit_end_s - unit_start_s,
            source_title=source_title,
            transcript=transcript.strip(),
            n_mcq=N_MCQ,
            n_tf=N_TRUE_FALSE,
            n_vocab=N_VOCAB,
        ),
        schema=UNIT_SCHEMA,
        schema_name="listening_unit_exercises",
        temperature=0.5,
    )

    def window(quote: str) -> tuple[float | None, float | None]:
        found = locate_quote(
            words, quote, lang, clamp_start=unit_start_s, clamp_end=unit_end_s
        )
        return found if found else (unit_start_s, unit_end_s)

    exercises: list[dict[str, Any]] = []
    order = 0

    for item in _trim(raw.get("mcq", []), N_MCQ):
        options = [o for o in item.get("options", []) if isinstance(o, str) and o.strip()]
        ci = int(item.get("correct_index", 0))
        if len(options) < 3 or not (0 <= ci < len(options)):
            log.warning("dropping malformed MCQ: %s", item.get("question"))
            continue
        # Shuffle so the correct answer isn't biased toward a fixed position.
        correct = options[ci]
        shuffled = options[:]
        rng.shuffle(shuffled)
        a_s, a_e = window(item.get("quote", ""))
        exercises.append(
            {
                "kind": EX_MCQ,
                "order_idx": order,
                "prompt": item["question"].strip(),
                "payload": {"options": shuffled, "quote": item.get("quote", "").strip()},
                "answer": {"index": shuffled.index(correct), "value": correct},
                "explanation": item.get("explanation_en", "").strip() or None,
                "audio_start_s": a_s,
                "audio_end_s": a_e,
                "generator": "llm",
            }
        )
        order += 1

    for item in _trim(raw.get("true_false", []), N_TRUE_FALSE):
        statement = (item.get("statement") or "").strip()
        if not statement:
            continue
        a_s, a_e = window(item.get("quote", ""))
        exercises.append(
            {
                "kind": EX_TRUE_FALSE,
                "order_idx": order,
                "prompt": statement,
                "payload": {"quote": item.get("quote", "").strip()},
                "answer": {"value": bool(item.get("is_true"))},
                "explanation": item.get("explanation_en", "").strip() or None,
                "audio_start_s": a_s,
                "audio_end_s": a_e,
                "generator": "llm",
            }
        )
        order += 1

    vocab = [
        v
        for v in _trim(raw.get("vocab", []), N_VOCAB)
        if (v.get("word") or "").strip() and (v.get("gloss_en") or "").strip()
    ]
    if len(vocab) >= 2:
        pairs = [
            {
                "word": v["word"].strip(),
                "gloss_en": v["gloss_en"].strip(),
                "definition_target": (v.get("definition_target") or "").strip(),
                "example": (v.get("example_from_audio") or "").strip(),
                "zipf": round(lang.zipf(v["word"].strip()), 2),
            }
            for v in vocab
        ]
        shuffled_glosses = [p["gloss_en"] for p in pairs]
        rng.shuffle(shuffled_glosses)
        exercises.append(
            {
                "kind": EX_VOCAB_MATCH,
                "order_idx": order,
                "prompt": "Associez chaque mot à sa traduction."
                if lang.code == "fr"
                else "Match each word to its meaning.",
                "payload": {
                    "words": [p["word"] for p in pairs],
                    "glosses": shuffled_glosses,
                    "details": pairs,
                },
                "answer": {"pairs": {p["word"]: p["gloss_en"] for p in pairs}},
                "explanation": None,
                "audio_start_s": unit_start_s,
                "audio_end_s": unit_end_s,
                "generator": "llm",
            }
        )
        order += 1

    ordering = [s.strip() for s in raw.get("ordering", []) if isinstance(s, str) and s.strip()]
    if len(ordering) >= N_ORDERING_MIN:
        shuffled = ordering[:]
        # Guarantee the presented order differs from the answer.
        for _ in range(10):
            rng.shuffle(shuffled)
            if shuffled != ordering:
                break
        exercises.append(
            {
                "kind": EX_ORDERING,
                "order_idx": order,
                "prompt": "Remettez ces éléments dans l'ordre où ils sont mentionnés."
                if lang.code == "fr"
                else "Put these in the order they were mentioned.",
                "payload": {"items": shuffled},
                "answer": {"order": ordering},
                "explanation": None,
                "audio_start_s": unit_start_s,
                "audio_end_s": unit_end_s,
                "generator": "llm",
            }
        )
        order += 1

    return {
        "gist_en": (raw.get("gist_en") or "").strip() or None,
        "title": (raw.get("title_target") or "").strip() or None,
        "topic": raw.get("topic") or "other",
        "vocab": vocab,
        "exercises": exercises,
    }


__all__ = ["generate_unit_exercises", "LLMError", "TOPICS"]
