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
from . import itemquality
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
N_OPTIONS = 4
# More distractors are requested than are shown. The surplus is what makes the length balancing in
# `itemquality.choose_distractors` possible: with exactly three there is nothing to choose between,
# and the correct option stayed the longest in 41.5% of items (chance is 25%).
N_DISTRACTOR_CANDIDATES = 5

# The traps a distractor may use. Naming them in the schema forces the model to commit to a
# mechanism per option rather than producing three vaguely wrong paraphrases, and it makes the
# generated items auditable afterwards — `payload.traps` records what was used.
TRAP_TYPES = [
    "wrong_referent",
    "attribution_swap",
    "time_inversion",
    "negation_flip",
    "scope_shift",
    "plausible_unstated",
    "near_homophone",
]

# What the item writer is told it is writing for. The named exams set a register the model already
# knows well, which turns out to steer trap quality better than an abstract "make it harder".
EXAM_HINTS = {
    "fr": "DELF/DALF",
    "ru": "ТРКИ (TORFL)",
    "zh": "HSK",
}
DEFAULT_EXAM_HINT = "an official proficiency exam"

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
                "required": [
                    "question",
                    "answer",
                    "distractors",
                    "explanation_en",
                    "quote",
                ],
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question, in the TARGET language.",
                    },
                    # The key is its own field rather than an index into a list. Asking for an index
                    # invites the model to write the answer first and pad the rest, which is how the
                    # correct option ends up the longest and most detailed one.
                    "answer": {
                        "type": "string",
                        "description": "The single correct option, in the TARGET language.",
                    },
                    "distractors": {
                        "type": "array",
                        "description": (
                            f"Exactly {N_DISTRACTOR_CANDIDATES} wrong options in the TARGET "
                            "language, each using one of the named traps and each roughly the "
                            f"same length as `answer`. Only {N_OPTIONS - 1} will be used, chosen "
                            "to keep the correct option from standing out — so make every one of "
                            "them a genuine trap, not filler."
                        ),
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["text", "trap_type", "why_wrong_en"],
                            "properties": {
                                "text": _STR,
                                "trap_type": {"type": "string", "enum": TRAP_TYPES},
                                "why_wrong_en": {
                                    "type": "string",
                                    "description": "The misunderstanding this punishes, in English.",
                                },
                            },
                        },
                    },
                    "explanation_en": {
                        "type": "string",
                        "description": "Why the key is right and what the traps punish, in "
                        "English, 1-2 sentences.",
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
working from an automatic transcript of authentic audio. You write for a real certification \
exam ({exam_hint}), not for a textbook exercise. Assume the candidate is competent and is \
trying to pass without fully understanding: your job is to make that impossible.

Hard rules:
1. Every answer MUST be derivable from THIS passage alone. Never write a question a \
learner could answer from general knowledge without listening.
2. Write questions, options, statements and definitions in {language_name}. Write \
`explanation_en` and `gloss_en` in English.
3. Calibrate the LANGUAGE to CEFR {cefr} — do not use vocabulary harder than the passage. \
Difficulty must come from the reasoning the question demands, never from obscure wording.
4. `quote` must be copied VERBATIM from the transcript — same words, same order. It is \
matched programmatically against audio timestamps, so paraphrase breaks the feature.
5. The transcript is machine-generated and may contain errors. Ignore garbled spans \
rather than writing questions about them.
6. Vary what you probe: main idea, specific detail, numbers/quantities, cause/effect, \
speaker stance, implication. Do not make every question a detail-retrieval question.

How to build the distractors — this is the part that matters:

Every distractor must be something the passage makes a candidate WANT to choose. The test is: \
a listener who caught most of the words but not the precise relationship should be drawn to it.

All four options must be about THE SAME element of the passage — the same claim, the same figure, \
the same decision — and differ in what is asserted about it. Options describing four different \
topics are worthless: a candidate who caught one content word eliminates three of them without \
understanding anything. If your distractors could be told apart from the key by someone who heard \
only the gist, they are not distractors.

Never make the answer turn on recognising a name. Do not ask who someone is, where a place is, or \
which country was mentioned, and never build a set of options that are the same sentence with a \
different person, city or country dropped in. Proper nouns are labels, not language: a candidate \
who caught "Berlin" has not demonstrated comprehension. Test what was CLAIMED, ASSERTED, CAUSED, \
DENIED or IMPLIED. The same goes for bare numbers — a figure is only worth asking about when the \
question is which quantity it measures or what it is being compared to.

Use these traps, and name the one you used in `trap_type`:

- `wrong_referent`: a fact stated in the passage, attached to the wrong person, place or thing. \
  ("30% is real — but it was unemployment, not inflation.")
- `attribution_swap`: an opinion genuinely voiced, credited to the wrong speaker, or the \
  speaker's own view swapped with the view they are reporting or criticising.
- `time_inversion`: the right events in the wrong order, or a past state presented as current, \
  or a plan presented as something already done.
- `negation_flip`: the passage's claim with its polarity reversed — what did NOT happen, what is \
  no longer true, what was ruled out.
- `scope_shift`: true of part, stated as true of the whole ("some regions" -> "the country"), or \
  a possibility ("pourrait", "risque de") hardened into a certainty.
- `plausible_unstated`: entirely consistent with the passage and with common sense, but never \
  actually said. This is the trap for candidates who reason instead of listening.
- `near_homophone`: hinges on a sound the passage genuinely contains that a learner at this level \
  confuses ("cent"/"sans", "deux"/"douze", "peut"/"peu"). Only when the audio really supports it.

Vary the mechanism. Across the {n_distractors} distractors you write for a question, use at least \
three DIFFERENT `trap_type` values. `plausible_unstated` is the weakest of the seven — use it at \
most once per question, never as the whole set.

Also required:
7. Each distractor must be defensibly WRONG, not merely unsaid-but-arguable. `explanation_en` \
must say what makes the key right AND name the misunderstanding the distractors punish.
8. At most one question per unit may be answerable from a single short phrase. The others must \
require holding two pieces of the passage together.
9. Never signal the answer through its FORM. Count the characters: every distractor must be within \
about 20% of the key's length, and the key must not be the longest. Do not make the correct option \
the most detailed or the most hedged, and do not let it be the one that most repeats the question's \
wording. Write distractors with the same specificity as the key — a vague distractor next to a \
precise key is a free mark.
10. No "all of the above", no "none of the above", no joke options, no options that are absurd on \
their face. Exactly one option is defensibly correct.
11. Mix true and false statements — do not make them all true. False statements must fail for a \
specific, quotable reason, not because they are off-topic.
12. Vocabulary entries must be words worth learning — verbs, nouns, adjectives, set phrases that \
transfer to other contexts. Never a person's name, a place, an organisation, or a number."""

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

Write exactly {n_mcq} multiple-choice questions — each with one `answer` and exactly \
{n_distractors} trap distractors — exactly {n_tf} true/false statements, exactly {n_vocab} \
vocabulary entries, and 4-5 ordering statements.

Before you commit to each question, check it: could a candidate who understood only the topic and \
none of the detail still pick the key? If yes, the distractors are too weak — rewrite them."""


def _trim(items: list[Any], n: int) -> list[Any]:
    return items[:n] if len(items) > n else items


REPAIR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["question", "distractors"],
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Copy the question verbatim so the replacements can be matched to it.",
                    },
                    "distractors": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["text", "trap_type", "why_wrong_en"],
                            "properties": {
                                "text": _STR,
                                "trap_type": {"type": "string", "enum": TRAP_TYPES},
                                "why_wrong_en": _STR,
                            },
                        },
                    },
                },
            },
        }
    },
}

REPAIR_SYSTEM = """You are revising distractors for a {language_name} listening-comprehension \
exam at CEFR {cefr}. Each question below has a correct answer that is conspicuously LONGER than \
its wrong options, so a candidate can pick it out by shape without listening.

Write {n} replacement distractors per question. The hard constraint is length, stated in \
characters for each question — count them. Everything else you already know applies: each \
distractor must be about the same element of the passage as the key, must be defensibly wrong, \
must use one of the named traps, and must never turn on recognising a name or a bare number. Use \
at least three different trap types and `plausible_unstated` at most once per question."""

REPAIR_USER = """Transcript:
\"\"\"
{transcript}
\"\"\"

{questions}

Return replacements for every question, keyed by the question text copied verbatim."""


def _repair_length_cues(
    drafts: list[dict[str, Any]],
    *,
    llm: StructuredLLM,
    lang: LanguageProfile,
    cefr: str,
    transcript: str,
) -> None:
    """Ask once for longer distractors, for the questions whose key cannot be hidden.

    Mutates `drafts` in place, ADDING to each candidate list rather than replacing it: the original
    traps are often the better ones, and merging leaves the selector a wider field to choose from.

    One extra call per unit at most, and only when needed — the questions are batched. A failure
    here is not an error: the items are still valid, just more guessable, and that is recorded in
    `give_aways` rather than costing the learner the whole unit.
    """
    needing: list[dict[str, Any]] = []
    for draft in drafts:
        texts = [d["text"].strip() for d in draft["candidates"]]
        chosen = itemquality.choose_distractors(
            draft["correct"],
            texts,
            n=N_OPTIONS - 1,
            traps=[d.get("trap_type") for d in draft["candidates"]],
        )
        if itemquality.needs_longer_distractors(draft["correct"], chosen):
            needing.append(draft)

    if not needing:
        return

    lines = []
    for draft in needing:
        target = len(draft["correct"])
        # The band STRADDLES the key rather than sitting above it. Asking only for longer options
        # fixes "the key is the longest" by creating "the key is the shortest", which is the same
        # give-away wearing a different hat; a band around the key lets the selector close on a
        # tight group with the key inside it. The explicit "at least two longer" is what was
        # actually missing from the first pass.
        lines.append(
            f'- Question: "{draft["question"]}"\n'
            f'  Correct answer ({target} characters): "{draft["correct"]}"\n'
            f"  Each replacement must be between {int(target * 0.85)} and {int(target * 1.25)} "
            f"characters, and at least two of them must be LONGER than {target} characters."
        )

    try:
        raw = llm.complete_json(
            system=REPAIR_SYSTEM.format(
                language_name=lang.name_en, cefr=cefr, n=N_DISTRACTOR_CANDIDATES
            ),
            user=REPAIR_USER.format(transcript=transcript.strip(), questions="\n".join(lines)),
            schema=REPAIR_SCHEMA,
            schema_name="listening_distractor_repair",
            temperature=0.5,
        )
    except LLMError as exc:
        log.warning("distractor repair failed, keeping the originals: %s", exc)
        return

    by_question = {
        (item.get("question") or "").strip(): item.get("distractors") or []
        for item in raw.get("items", [])
        if isinstance(item, dict)
    }
    for draft in needing:
        extra = [
            d
            for d in by_question.get(draft["question"], [])
            if isinstance(d, dict) and (d.get("text") or "").strip()
        ]
        if not extra:
            log.info("no replacement distractors returned for %r", draft["question"])
            continue
        # New candidates first: on a tie the selector prefers earlier entries, and these were
        # written to the length the item actually needs.
        draft["candidates"] = extra + draft["candidates"]
        log.info("repaired %d distractors for %r", len(extra), draft["question"])


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
    repair_give_aways: bool = True,
) -> dict[str, Any]:
    """Return {"gist_en", "title", "topic", "exercises": [...]} for one unit.

    Raises LLMError if generation fails; callers may fall back to cloze-only lessons.
    """
    llm = llm or StructuredLLM()
    rng = rng or random.Random(7)

    raw = llm.complete_json(
        system=SYSTEM_PROMPT.format(
            language_name=lang.name_en,
            cefr=cefr,
            exam_hint=EXAM_HINTS.get(lang.code, DEFAULT_EXAM_HINT),
            n_distractors=N_DISTRACTOR_CANDIDATES,
        ),
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
            n_distractors=N_DISTRACTOR_CANDIDATES,
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

    # --- multiple choice, in two phases -------------------------------------------------------
    #
    # Phase one collects the questions and their candidate distractors. Phase two is the length
    # repair: the model writes a key that no offered distractor can cover often enough that a
    # single pass leaves the answer guessable — measured at 60% of items on a 10-unit live sample,
    # against 41.5% before any of this. Asking for matched lengths in the prompt did not fix it,
    # so the ones that are still conspicuous are sent back with explicit character counts.
    drafts: list[dict[str, Any]] = []
    for item in _trim(raw.get("mcq", []), N_MCQ):
        question = (item.get("question") or "").strip()
        correct = (item.get("answer") or "").strip()
        candidates = [
            d
            for d in item.get("distractors", [])
            if isinstance(d, dict) and (d.get("text") or "").strip()
        ]
        if not question or not correct or len(candidates) < N_OPTIONS - 1:
            log.warning("dropping malformed MCQ: %r", question or item)
            continue
        drafts.append(
            {
                "question": question,
                "correct": correct,
                "candidates": candidates,
                "quote": (item.get("quote") or "").strip(),
                "explanation": (item.get("explanation_en") or "").strip(),
            }
        )

    if repair_give_aways:
        _repair_length_cues(drafts, llm=llm, lang=lang, cefr=cefr, transcript=transcript)

    for draft in drafts:
        question, correct = draft["question"], draft["correct"]
        candidates = draft["candidates"]

        # Pick which distractors to show. This is where the answer stops being guessable from its
        # shape: the surplus candidates exist so a subset can be chosen that leaves the correct
        # option unremarkable in length.
        texts = [d["text"].strip() for d in candidates]
        chosen = itemquality.choose_distractors(
            correct,
            texts,
            n=N_OPTIONS - 1,
            traps=[d.get("trap_type") for d in candidates],
        )
        if len(chosen) < 2:
            log.warning("dropping MCQ with too few usable distractors: %r", question)
            continue
        trap_of = {d["text"].strip(): d.get("trap_type") for d in candidates}

        # Shuffle so the correct answer isn't biased toward a fixed position.
        shuffled = [correct, *chosen]
        rng.shuffle(shuffled)

        leaks = itemquality.audit_mcq(question, shuffled, shuffled.index(correct))
        fatal = sorted(set(leaks) & itemquality.DISQUALIFYING)
        if fatal:
            # Not worth serving. Chiefly the name-and-number items: "how old was X when he died",
            # with four ages as the options, tests recall of a label rather than comprehension.
            log.info("dropping MCQ for %s: %r", fatal, question)
            continue
        if leaks:
            # Recorded, not dropped: a residual length cue still leaves a valid question, and
            # `audit-items` reports these so the rate stays visible instead of being assumed gone.
            log.info("MCQ retains give-away %s: %r", leaks, question)

        a_s, a_e = window(draft["quote"])
        exercises.append(
            {
                "kind": EX_MCQ,
                "order_idx": order,
                "prompt": question,
                "payload": {
                    "options": shuffled,
                    "quote": draft["quote"],
                    # Per-option trap labels, aligned to the shuffled order (None for the key).
                    # Kept so the items can be reviewed by trap mechanism, and so a trap type that
                    # turns out to produce bad questions can be found and removed.
                    "traps": [trap_of.get(o) for o in shuffled],
                    **({"give_aways": leaks} if leaks else {}),
                },
                "answer": {"index": shuffled.index(correct), "value": correct},
                "explanation": draft["explanation"] or None,
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

    # Proper nouns and numbers are dropped rather than trusted to rule 12. "Guterres" and "2024"
    # are not vocabulary, and a matching exercise built on them spends the learner's attention on
    # recognising a label instead of on the language.
    vocab = []
    for v in _trim(raw.get("vocab", []), N_VOCAB):
        word = (v.get("word") or "").strip()
        if not word or not (v.get("gloss_en") or "").strip():
            continue
        if not itemquality.is_worth_learning(word):
            log.info("dropping vocabulary entry %r: not a learnable word", word)
            continue
        vocab.append(v)
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
