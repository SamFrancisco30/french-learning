"""Sentence-level grammar analysis: translation, constructions, and practice items.

The division of labour matters here. The deterministic matcher in `constructions.py` decides
*which* constructions are present; the model is asked only to explain the instance and to
write a practice item. Asking the model to find constructions produces confident
hallucinations — it will announce `ne ... que` in a sentence that merely contains both words.
Detection is a pattern problem; explanation is a language problem.

The model may still add a construction the inventory doesn't cover (`source: "llm"`), which
is how the inventory's gaps get surfaced — but those are marked so the UI can hedge.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..languages import LanguageProfile
from ..llm.openai_client import LLMError, StructuredLLM
from ..models import SentenceAnalysis
from .constructions import ConstructionHit, by_key, find_constructions

log = logging.getLogger(__name__)

MAX_SENTENCE_CHARS = 600
MAX_PRACTICES = 3
MAX_STRUCTURES = 5

# A selection needs at least this many tokens before it's treated as a sentence rather
# than a word or short phrase lookup.
MIN_SENTENCE_TOKENS = 4

ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["translation_en", "register", "structures", "practices", "notes"],
    "properties": {
        "translation_en": {
            "type": "string",
            "description": "Natural English translation of the whole sentence — not word-by-word.",
        },
        "register": {
            "type": "string",
            "enum": ["neutral", "formal", "literary", "colloquial", "journalistic"],
        },
        "structures": {
            "type": "array",
            "description": (
                "One entry per grammatical construction genuinely at work in this sentence. "
                "Cover every DETECTED construction listed in the prompt. You may add one you "
                "are certain of that was not detected; set detected=false for those."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "key",
                    "detected",
                    "schema_form",
                    "name_en",
                    "meaning_en",
                    "why_opaque",
                    "literal_trap",
                    "in_this_sentence",
                    "quote",
                    "cefr",
                ],
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "The detected construction's key if it was given to you; otherwise a snake_case name you coin.",
                    },
                    "detected": {
                        "type": "boolean",
                        "description": "True if this key was in the detected list given to you.",
                    },
                    "schema_form": {
                        "type": "string",
                        "description": "Schematic shape with placeholders, e.g. il n'y a pas que ...",
                    },
                    "name_en": {"type": "string"},
                    "meaning_en": {
                        "type": "string",
                        "description": "What the construction contributes, plainly.",
                    },
                    "why_opaque": {
                        "type": "string",
                        "description": "Why knowing each word individually does not give you this meaning.",
                    },
                    "literal_trap": {
                        "type": "string",
                        "description": "The wrong reading a learner lands on word-by-word. Empty string if there isn't one.",
                    },
                    "in_this_sentence": {
                        "type": "string",
                        "description": "What it specifically means HERE, referring to this sentence's own content.",
                    },
                    "quote": {
                        "type": "string",
                        "description": "The exact substring of the sentence realising the construction, copied verbatim.",
                    },
                    "cefr": {"type": "string", "enum": ["A1", "A2", "B1", "B2", "C1", "C2"]},
                },
            },
        },
        "practices": {
            "type": "array",
            "description": (
                "One production exercise per structure, max 3. Give an ENGLISH sentence whose "
                "natural French rendering REQUIRES that construction. Different content from "
                "the original sentence — the learner must transfer the pattern, not recall it."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["construction_key", "prompt_en", "reference_fr", "alternatives", "hint_en"],
                "properties": {
                    "construction_key": {"type": "string"},
                    "prompt_en": {
                        "type": "string",
                        "description": "The English sentence to translate. Everyday content, one clause or two.",
                    },
                    "reference_fr": {
                        "type": "string",
                        "description": "A model French answer using the construction.",
                    },
                    "alternatives": {
                        "type": "array",
                        "description": "Other fully acceptable French renderings, up to 3.",
                        "items": {"type": "string"},
                    },
                    "hint_en": {
                        "type": "string",
                        "description": "A nudge that names the structure without giving the answer away.",
                    },
                },
            },
        },
        "notes": {
            "type": "string",
            "description": "Anything else worth knowing about the sentence's syntax. Empty string if nothing.",
        },
    },
}

SYSTEM_PROMPT = """You explain {language_name} sentence grammar to English-speaking learners.

The learner's specific difficulty: they often know every individual word in a sentence and \
still cannot work out what the sentence means, because the meaning lives in the \
CONSTRUCTION rather than the words. Your job is to make those constructions visible.

Rules:
1. A list of DETECTED constructions is given to you. It comes from a pattern matcher, so it \
is reliable. Explain every one of them as it operates in THIS sentence.
2. Do NOT invent constructions. If you add one that wasn't detected, you must be certain it \
is really there, and you must set detected=false. When in doubt, leave it out.
3. `quote` must be copied VERBATIM from the sentence. It is matched programmatically.
4. `why_opaque` is the heart of this. Say precisely why word-by-word reading fails. If the \
words individually suggest a different reading, put that in `literal_trap`.
5. `in_this_sentence` must refer to the sentence's actual content, not restate the general rule.
6. For each practice, the English prompt must be phrased so that a natural French \
translation genuinely needs the construction — otherwise the learner can dodge it. Use \
different subject matter from the original sentence.
7. Write explanations in English. Write French only in the French fields."""

USER_PROMPT = """Language: {language_name}
Sentence:
\"\"\"
{sentence}
\"\"\"

DETECTED constructions (from the pattern matcher — explain all of these):
{detected}

Analyse the sentence: translate it, explain each construction, and write one practice item
per construction (max {max_practices})."""


def _key(lang: LanguageProfile, text: str) -> str:
    norm = lang.normalize_answer(text, fold_diacritics=True)
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:32]


def is_sentence_selection(text: str, lang: LanguageProfile) -> bool:
    """Whether a selection is long enough to warrant sentence analysis."""
    return len(lang.tokenize(text)) >= MIN_SENTENCE_TOKENS


def _describe(hits: list[ConstructionHit]) -> str:
    if not hits:
        return "  (none detected — the sentence may be structurally plain; say so if it is)"
    return "\n".join(
        f"  - key={h.construction.key} | form={h.construction.schema_form} | "
        f"{h.construction.name_en}: {h.construction.meaning_en}"
        for h in hits
    )


def to_payload(row: SentenceAnalysis) -> dict[str, Any]:
    return {
        "text": row.text,
        "translation_en": row.translation_en,
        "register_note": row.register,
        "structures": row.structures or [],
        "practices": row.practices or [],
        "notes": row.notes,
    }


def analyze_sentence(
    db: Session,
    text: str,
    lang: LanguageProfile,
    *,
    llm: StructuredLLM | None = None,
    allow_llm: bool = True,
    refresh: bool = False,
) -> tuple[dict[str, Any], str]:
    """Analyse `text`. Returns (payload, source) with source cache|live|offline."""
    text = text.strip()[:MAX_SENTENCE_CHARS]
    if not text:
        raise ValueError("empty sentence")

    tkey = _key(lang, text)
    if not refresh:
        cached = db.scalar(
            select(SentenceAnalysis).where(
                SentenceAnalysis.language == lang.code, SentenceAnalysis.text_key == tkey
            )
        )
        if cached:
            cached.hits += 1
            db.flush()
            return to_payload(cached), "cache"

    hits = find_constructions(text, lang.code)

    if not allow_llm:
        # Still useful offline: the matcher alone names the constructions and explains
        # them from the inventory, just without a sentence-specific reading.
        return {
            "text": text,
            "translation_en": "",
            "register_note": None,
            "structures": [
                {**h.to_dict(), "detected": True, "in_this_sentence": "", "source": "pattern"}
                for h in hits
            ],
            "practices": [],
            "notes": "Translation unavailable offline; constructions detected by pattern only.",
        }, "offline"

    llm = llm or StructuredLLM()
    raw = llm.complete_json(
        system=SYSTEM_PROMPT.format(language_name=lang.name_en),
        user=USER_PROMPT.format(
            language_name=lang.name_en,
            sentence=text,
            detected=_describe(hits),
            max_practices=MAX_PRACTICES,
        ),
        schema=ANALYSIS_SCHEMA,
        schema_name="sentence_analysis",
        temperature=0.25,
        max_tokens=3000,
    )

    hit_by_key = {h.construction.key: h for h in hits}
    structures: list[dict[str, Any]] = []
    for s in raw.get("structures", [])[:MAX_STRUCTURES]:
        key = (s.get("key") or "").strip()
        quote = (s.get("quote") or "").strip()
        hit = hit_by_key.get(key)

        # Anchor the explanation to real character offsets: prefer the matcher's span,
        # fall back to locating the model's quote, and drop nothing on failure — an
        # unanchored structure is still explanatory, it just can't be highlighted.
        if hit:
            span = (hit.char_start, hit.char_end)
            marker_spans = hit.marker_spans
        else:
            from .anchor import find_span

            found = find_span(text, quote) if quote else None
            span = found or (0, 0)
            marker_spans = [list(span)] if found else []

        structures.append(
            {
                "key": key,
                "schema_form": (s.get("schema_form") or "").strip(),
                "name_en": (s.get("name_en") or "").strip(),
                "meaning_en": (s.get("meaning_en") or "").strip(),
                "why_opaque": (s.get("why_opaque") or "").strip(),
                "literal_trap": (s.get("literal_trap") or "").strip() or None,
                "in_this_sentence": (s.get("in_this_sentence") or "").strip(),
                "quote": quote,
                "cefr": s.get("cefr") or (hit.construction.cefr if hit else "B1"),
                "char_start": span[0],
                "char_end": span[1],
                "marker_spans": marker_spans,
                # Pattern-detected is trustworthy; model-proposed is not, and the UI says so.
                "source": "pattern" if hit else "llm",
            }
        )

    known = {s["key"] for s in structures}
    practices: list[dict[str, Any]] = []
    for p in raw.get("practices", [])[:MAX_PRACTICES]:
        ckey = (p.get("construction_key") or "").strip()
        prompt = (p.get("prompt_en") or "").strip()
        ref = (p.get("reference_fr") or "").strip()
        if not prompt or not ref or ckey not in known:
            continue
        con = by_key(lang.code, ckey)
        practices.append(
            {
                "construction_key": ckey,
                "schema_form": next(
                    (s["schema_form"] for s in structures if s["key"] == ckey), ""
                ),
                "prompt_en": prompt,
                "reference_fr": ref,
                "alternatives": [
                    a.strip() for a in p.get("alternatives", [])[:3] if a and a.strip()
                ],
                "hint_en": (p.get("hint_en") or "").strip() or None,
                # Present only for inventory constructions; without markers the grader
                # falls back to judging meaning alone.
                "required_markers": ["/".join(sorted(g)) for g in (con.required_markers if con else ())],
            }
        )

    row = SentenceAnalysis(
        language=lang.code,
        text_key=tkey,
        text=text,
        translation_en=(raw.get("translation_en") or "").strip(),
        register=(raw.get("register") or "").strip() or None,
        structures=structures,
        practices=practices,
        notes=(raw.get("notes") or "").strip() or None,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(SentenceAnalysis).where(
                SentenceAnalysis.language == lang.code, SentenceAnalysis.text_key == tkey
            )
        )
        if existing:
            return to_payload(existing), "cache"
        raise

    log.info("analysed sentence: %d structures, %d practices", len(structures), len(practices))
    return to_payload(row), "live"


__all__ = ["analyze_sentence", "is_sentence_selection", "LLMError"]
