"""Context-aware glossing for a selected word or phrase, memoized in the database.

Context is part of the cache key, not decoration: `feu` is fire, a traffic light, or
gunfire depending on the sentence, and a context-free dictionary lookup would confidently
give a learner the wrong one. So the sentence around the selection is hashed into the key.

The same call also reports whether the selection is *itself* an expression. That is what
makes arbitrary, never-ingested text work: precomputed annotations cover transcripts, and
this covers everything else.
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
from ..models import GlossCache

log = logging.getLogger(__name__)

MAX_SELECTION_CHARS = 180
CONTEXT_KEY_LEN = 32

GLOSS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "gloss_en",
        "lemma",
        "pos",
        "is_expression",
        "canonical",
        "literal_en",
        "note",
        "other_senses",
    ],
    "properties": {
        "gloss_en": {
            "type": "string",
            "description": "The English meaning IN THIS CONTEXT. Concise — a gloss, not a sentence.",
        },
        "lemma": {"type": "string", "description": "Dictionary form of the selection."},
        "pos": {
            "type": "string",
            "description": "Part of speech in this context: noun, verb, adjective, adverb, "
            "preposition, conjunction, pronoun, determiner, phrase, name.",
        },
        "is_expression": {
            "type": "boolean",
            "description": "True if the selection is itself a fixed expression/idiom whose "
            "meaning is not the sum of its words.",
        },
        "canonical": {
            "type": "string",
            "description": "Dictionary form of the expression if is_expression, else empty string.",
        },
        "literal_en": {
            "type": "string",
            "description": "Word-by-word literal rendering when it differs instructively "
            "from the real meaning. Empty string otherwise.",
        },
        "note": {
            "type": "string",
            "description": "One short learner-useful note: gender for nouns (le/la), "
            "irregular conjugation, register, or a false-friend warning. Empty if none.",
        },
        "other_senses": {
            "type": "array",
            "description": "Up to 3 OTHER common meanings this word carries elsewhere, so the "
            "learner sees it is not one-to-one. Empty for unambiguous words.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["gloss_en", "when"],
                "properties": {
                    "gloss_en": {"type": "string"},
                    "when": {
                        "type": "string",
                        "description": "Short cue for when that sense applies, e.g. "
                        '"in traffic contexts", "as a military term".',
                    },
                },
            },
        },
    },
}

SYSTEM_PROMPT = """You gloss {language_name} for English-speaking learners.

You are given a selection and the sentence it came from. Translate the selection AS USED IN \
THAT SENTENCE — this is the whole point. If the sentence makes "feu" a traffic light, do \
not say "fire".

Rules:
- `gloss_en` is a gloss, not a translation of the sentence. Keep it tight.
- Give the dictionary form in `lemma` (verbs to the infinitive, nouns to the singular).
- For nouns in gendered languages, put the gender in `note` (e.g. "le feu — masculine").
- `other_senses` should list genuinely different meanings, not shades of the same one.
- If the selection is a fixed expression, set is_expression and give its `canonical` form.
- If the selection is a fragment that means nothing on its own (a bare article, a clitic), \
say so plainly in `gloss_en` rather than inventing a meaning."""

USER_PROMPT = """Language: {language_name}
Selection: "{selection}"
Sentence it appears in: "{context}"

Gloss the selection as used in that sentence."""


def _context_key(lang: LanguageProfile, context: str) -> str:
    """Stable hash of normalized context, so trivial whitespace changes still cache-hit."""
    if not context.strip():
        return ""
    norm = lang.normalize_answer(context, fold_diacritics=True)
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:CONTEXT_KEY_LEN]


def _to_payload(row: GlossCache) -> dict[str, Any]:
    return {
        "surface": row.surface,
        "lemma": row.lemma,
        "pos": row.pos,
        "gloss_en": row.gloss_en,
        "other_senses": row.senses or [],
        "note": row.note,
        "zipf": row.zipf,
    }


def gloss_selection(
    db: Session,
    selection: str,
    context: str,
    lang: LanguageProfile,
    *,
    llm: StructuredLLM | None = None,
    allow_llm: bool = True,
) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
    """Gloss `selection` in `context`.

    Returns (word_payload, self_expression_or_None, source) where source is
    "cache" | "live" | "offline".
    """
    selection = selection.strip()[:MAX_SELECTION_CHARS]
    if not selection:
        raise ValueError("empty selection")

    surface_key = lang.normalize_answer(selection, fold_diacritics=True)[:255]
    ctx_key = _context_key(lang, context)

    cached = db.scalar(
        select(GlossCache).where(
            GlossCache.language == lang.code,
            GlossCache.surface_key == surface_key,
            GlossCache.context_key == ctx_key,
        )
    )
    if cached:
        cached.hits += 1
        db.flush()
        return _to_payload(cached), None, "cache"

    if not allow_llm:
        return _offline_payload(selection, lang), None, "offline"

    llm = llm or StructuredLLM()
    raw = llm.complete_json(
        system=SYSTEM_PROMPT.format(language_name=lang.name_en),
        user=USER_PROMPT.format(
            language_name=lang.name_en,
            selection=selection,
            context=(context.strip() or selection)[:1200],
        ),
        schema=GLOSS_SCHEMA,
        schema_name="gloss",
        temperature=0.1,
        max_tokens=900,
    )

    senses = [
        {"gloss_en": s.get("gloss_en", "").strip(), "when": s.get("when", "").strip()}
        for s in raw.get("other_senses", [])[:3]
        if (s.get("gloss_en") or "").strip()
    ]
    lemma = (raw.get("lemma") or "").strip() or None

    row = GlossCache(
        language=lang.code,
        surface_key=surface_key,
        context_key=ctx_key,
        surface=selection,
        lemma=lemma,
        pos=(raw.get("pos") or "").strip() or None,
        gloss_en=(raw.get("gloss_en") or "").strip(),
        senses=senses,
        note=(raw.get("note") or "").strip() or None,
        zipf=round(lang.zipf(lemma or selection), 2) or None,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        # Concurrent identical lookup won the race; use theirs.
        db.rollback()
        existing = db.scalar(
            select(GlossCache).where(
                GlossCache.language == lang.code,
                GlossCache.surface_key == surface_key,
                GlossCache.context_key == ctx_key,
            )
        )
        if existing:
            return _to_payload(existing), None, "cache"
        raise

    self_expr: dict[str, Any] | None = None
    if raw.get("is_expression"):
        canonical = (raw.get("canonical") or "").strip() or selection
        self_expr = {
            "canonical": canonical,
            "surface": selection,
            "kind": "idiom",
            "gloss_en": (raw.get("gloss_en") or "").strip(),
            "literal_en": (raw.get("literal_en") or "").strip() or None,
            "note": (raw.get("note") or "").strip() or None,
            "component_spans": [],
            "confidence": 0.9,
            "source": "live",
        }

    return _to_payload(row), self_expr, "live"


def _offline_payload(selection: str, lang: LanguageProfile) -> dict[str, Any]:
    """No API key / LLM disabled: still return something honest and useful."""
    zipf = lang.zipf(selection)
    band = (
        "very common"
        if zipf >= 5
        else "common"
        if zipf >= 4
        else "moderately common"
        if zipf >= 3
        else "rare"
    )
    return {
        "surface": selection,
        "lemma": lang.headword(selection),
        "pos": None,
        "gloss_en": "",
        "other_senses": [],
        "note": f"Translation unavailable offline. Frequency: {band} (Zipf {zipf:.1f}).",
        "zipf": round(zipf, 2),
    }


__all__ = ["gloss_selection", "LLMError"]
