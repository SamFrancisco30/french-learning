"""Ingest-time multiword-expression extraction.

Why this runs at ingest rather than on selection: the popup has to feel instant, and a
learner selects words faster than any LLM round trip can answer. Transcripts are known
ahead of time, so every expression in them is found once, stored with character spans,
and thereafter served by an offset query.

The model is asked for **surface strings**, never character offsets — models miscount
offsets, and a wrong offset silently highlights the wrong words. Every returned component
is re-located in the real text by `anchor.find_component_spans`, and anything that can't
be placed is dropped. That makes hallucinated expressions mostly self-limiting: an
expression the model invented usually isn't in the passage to be found.
"""

from __future__ import annotations

import logging
from typing import Any

from ..languages import LanguageProfile
from ..llm.openai_client import LLMError, StructuredLLM
from ..models import EXPRESSION_KINDS
from .anchor import find_component_spans

log = logging.getLogger(__name__)

MAX_EXPRESSIONS_PER_UNIT = 25

EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["expressions"],
    "properties": {
        "expressions": {
            "type": "array",
            "description": "Every multiword expression actually present in the passage.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "components",
                    "canonical",
                    "kind",
                    "gloss_en",
                    "literal_en",
                    "note",
                    "content_lemmas",
                ],
                "properties": {
                    "components": {
                        "type": "array",
                        "description": (
                            "The expression's parts EXACTLY as they appear in the passage, "
                            "in order. Use ONE element for a contiguous expression "
                            '("feu d\'artifice"). Use SEVERAL when other words interrupt it: '
                            'for "il y a mis le feu" the components are ["mis", "le feu"]. '
                            "Copy verbatim from the passage — do not normalize or inflect."
                        ),
                        "items": {"type": "string"},
                    },
                    "canonical": {
                        "type": "string",
                        "description": 'Dictionary/infinitive form, e.g. "mettre le feu (à)".',
                    },
                    "kind": {"type": "string", "enum": list(EXPRESSION_KINDS)},
                    "gloss_en": {
                        "type": "string",
                        "description": "What it actually MEANS in English, idiomatically.",
                    },
                    "literal_en": {
                        "type": "string",
                        "description": (
                            "Word-by-word literal rendering, to show the learner why the "
                            "idiom is not decomposable. Empty string if identical to gloss."
                        ),
                    },
                    "note": {
                        "type": "string",
                        "description": (
                            "Short usage note: register, typical subject/object, common "
                            "confusion with a similar expression. Empty string if none."
                        ),
                    },
                    "content_lemmas": {
                        "type": "array",
                        "description": (
                            "Dictionary forms of the CONTENT words only (no articles, "
                            'pronouns, auxiliaries). For "a mis le feu": ["mettre", "feu"].'
                        ),
                        "items": {"type": "string"},
                    },
                },
            },
        }
    },
}

SYSTEM_PROMPT = """You are a {language_name} lexicographer annotating multiword \
expressions in a passage for language learners.

Find every expression whose meaning a learner could NOT reliably work out from its \
individual words, plus conventionalized combinations worth learning as a unit:

- idiom: non-compositional ("coup de foudre", "pleuvoir des cordes")
- collocation: compositional but conventional ("prendre une décision", "poser une question")
- phrasal_verb: verb + particle/preposition with a specific sense ("s'en aller")
- fixed_phrase: discourse formula ("en revanche", "au fur et à mesure", "bien sûr")
- compound: lexicalized compound noun ("feu d'artifice", "pomme de terre", "chemin de fer")
- proper_noun: multiword name a learner might not parse ("Bab-el-Mandeb", "Arabie saoudite")

Rules that matter more than coverage:

1. Only annotate what is ACTUALLY IN THE PASSAGE. Never add an expression because it is \
common in the language. If you are not certain it appears here, omit it.
2. `components` must be copied VERBATIM from the passage, in the order they appear. They \
are matched programmatically against the text; a normalized or inflected copy is discarded.
3. Split into multiple components ONLY where other words interrupt the expression. \
"il a mis le feu" -> ["a mis le feu"] is wrong if contiguous; prefer the smallest set of \
verbatim runs that covers the expression's own words. For "il y a mis le feu", the \
expression's words are "mis" and "le feu", so components are ["mis", "le feu"].
4. Distinguish expressions that share a word. "feu rouge" (traffic light) and "feu \
d'artifice" (firework) and "mettre le feu" (to set fire) are three different entries. If \
a word is used literally and is NOT part of an expression, do not invent one for it.
5. `content_lemmas` gives dictionary forms of content words only — this is the key used \
to recognise the same expression elsewhere, so be precise ("mis" -> "mettre").
6. Prefer precision over recall. A missed expression costs a learner little; a fabricated \
one teaches them something false."""

USER_PROMPT = """Passage ({language_name}):
\"\"\"
{passage}
\"\"\"

Annotate every multiword expression present. Copy `components` verbatim from the passage."""


def extract_expressions(
    text: str,
    lang: LanguageProfile,
    *,
    llm: StructuredLLM | None = None,
) -> list[dict[str, Any]]:
    """Return expression dicts with verified `component_spans` for `text`.

    Raises LLMError on generation failure; callers should treat expressions as optional
    enrichment and continue without them.
    """
    if not text.strip():
        return []
    llm = llm or StructuredLLM()

    raw = llm.complete_json(
        system=SYSTEM_PROMPT.format(language_name=lang.name_en),
        user=USER_PROMPT.format(language_name=lang.name_en, passage=text.strip()),
        schema=EXTRACT_SCHEMA,
        schema_name="expression_annotations",
        temperature=0.2,  # extraction, not creativity
        max_tokens=4096,
    )

    out: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    dropped = 0

    for item in raw.get("expressions", [])[:MAX_EXPRESSIONS_PER_UNIT]:
        components = [c.strip() for c in item.get("components", []) if isinstance(c, str) and c.strip()]
        canonical = (item.get("canonical") or "").strip()
        gloss = (item.get("gloss_en") or "").strip()
        if not components or not canonical or not gloss:
            dropped += 1
            continue

        spans = find_component_spans(text, components)
        if spans is None:
            # The model quoted something not in the passage — almost always a fabrication.
            dropped += 1
            log.debug("dropped unanchorable expression %r (components=%r)", canonical, components)
            continue

        envelope = (spans[0][0], spans[-1][1])
        if envelope in seen:
            continue
        seen.add(envelope)

        lemmas = sorted(
            {
                lang.headword(l.strip()).casefold()
                for l in item.get("content_lemmas", [])
                if isinstance(l, str) and l.strip()
            }
        )
        literal = (item.get("literal_en") or "").strip()
        note = (item.get("note") or "").strip()

        out.append(
            {
                "surface": text[envelope[0] : envelope[1]],
                "canonical": canonical,
                "lemma_key": "|".join(lemmas),
                "kind": item.get("kind") or "idiom",
                "gloss_en": gloss,
                "literal_en": literal or None,
                "note": note or None,
                "char_start": envelope[0],
                "char_end": envelope[1],
                "component_spans": [list(s) for s in spans],
                # A discontinuous match is slightly less certain than a contiguous one.
                "confidence": 1.0 if len(spans) == 1 else 0.85,
            }
        )

    out.sort(key=lambda e: (e["char_start"], -e["char_end"]))
    log.info("extracted %d expressions (%d dropped as unanchorable)", len(out), dropped)
    return out


__all__ = ["extract_expressions", "LLMError"]
