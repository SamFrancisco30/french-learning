"""Resolve a text selection into a translation payload.

Resolution order, fastest and most trustworthy first:

1. **Precomputed** — if the selection is inside a known unit, look for stored Expression
   rows whose component spans overlap it. Pure SQL, no model call, and the annotation was
   made against this exact sentence so precision is high. This is the common path.
2. **Lexicon-inferred** — otherwise, try to recognise an expression learned from another
   passage via lemma keys, gated on all its content lemmas appearing near the selection.
   Returned with reduced confidence and marked `inferred`.
3. **Live** — gloss the selection itself (cached), which also reports whether the
   selection is an expression in its own right. Covers arbitrary text.

Steps 1 and 3 compose: selecting `feu` in a transcript returns both the word gloss *and*
`mettre le feu`, which is the behaviour the feature exists for.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..languages import get_language
from ..llm.openai_client import LLMError, StructuredLLM
from ..models import Expression, ListeningUnit
from .anchor import find_span, sentence_around, sentence_bounds, snap_to_words
from .lemmas import (
    lemma_proximity,
    locate_lemmas_near,
    selection_lemmas,
    spacy_available,
)
from .translate import gloss_selection

log = logging.getLogger(__name__)

MAX_TEXT_CHARS = 20_000
MAX_INFERRED = 3
INFERRED_CONFIDENCE_PENALTY = 0.55


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def _expression_payload(
    expr: Expression, *, source: str, confidence: float | None = None, spans: list[list[int]] | None = None
) -> dict[str, Any]:
    return {
        "id": expr.id,
        "canonical": expr.canonical,
        "surface": expr.surface,
        "kind": expr.kind,
        "gloss_en": expr.gloss_en,
        "literal_en": expr.literal_en,
        "note": expr.note,
        "component_spans": spans if spans is not None else (expr.component_spans or []),
        "char_start": expr.char_start,
        "char_end": expr.char_end,
        "confidence": confidence if confidence is not None else expr.confidence,
        "source": source,
    }


def _audio_window(
    unit: ListeningUnit | None, text: str, start: int, end: int
) -> tuple[float | None, float | None]:
    """Map a character selection to an audio time window, so the popup can play it.

    Reuses the ASR-word/text alignment: the stored word list has timestamps but no
    character spans, so we recover the spans and find the words overlapping the selection.
    Linear in the unit's word count, which is a few hundred — cheap enough to do per
    lookup, and it works on units ingested before this feature existed.
    """
    if unit is None or not unit.words_json:
        return None, None

    from ..asr.base import Word
    from ..skills.listening.align import align_words_to_text

    words = [
        Word(
            text=w.get("word", ""),
            start=float(w.get("start") or 0.0),
            end=float(w.get("end") or 0.0),
            probability=w.get("probability"),
        )
        for w in unit.words_json
        if (w.get("word") or "").strip()
    ]
    if not words:
        return None, None

    spans = align_words_to_text(words, text)
    hits = [
        words[i]
        for i, span in enumerate(spans)
        if span is not None and _overlaps(start, end, span[0], span[1])
    ]
    if not hits:
        return None, None

    # Pad slightly so the word isn't clipped, and clamp to the unit.
    lo = max(unit.start_s, min(h.start for h in hits) - 0.35)
    hi = min(unit.end_s, max(h.end for h in hits) + 0.35)
    return (round(lo, 3), round(hi, 3)) if hi > lo else (None, None)


def _authoritative_text(
    db: Session, unit_id: int | None, client_text: str, start: int, end: int
) -> tuple[str, int, int, ListeningUnit | None]:
    """Prefer the stored unit text, re-anchoring the selection if the client drifted.

    The client sends offsets into whatever it rendered. Normally that is byte-identical to
    the stored unit text, but a stale page or a differently-rendered passage would make
    those offsets point at the wrong words — and silently returning a gloss for the wrong
    word is worse than a miss. So when a unit is named, its stored text wins, and the
    selection is re-located inside it.
    """
    if unit_id is None:
        return client_text, start, end, None

    unit = db.get(ListeningUnit, unit_id)
    if unit is None or not unit.text:
        return client_text, start, end, None

    selection = client_text[start:end]
    if unit.text[start:end] == selection:
        return unit.text, start, end, unit  # offsets agree

    span = find_span(unit.text, selection, start_at=max(0, start - 200))
    if span is None:
        span = find_span(unit.text, selection)
    if span is None:
        log.debug("selection %r not found in stored unit %s text; using client text", selection, unit_id)
        return client_text, start, end, unit
    log.debug("re-anchored selection from %d to %d in unit %s", start, span[0], unit_id)
    return unit.text, span[0], span[1], unit


def resolve_selection(
    db: Session,
    *,
    language: str,
    text: str,
    char_start: int,
    char_end: int,
    unit_id: int | None = None,
    allow_llm: bool = True,
    llm: StructuredLLM | None = None,
) -> dict[str, Any]:
    lang = get_language(language)
    text = (text or "")[:MAX_TEXT_CHARS]
    if not text:
        raise ValueError("text is required")

    text, char_start, char_end, unit = _authoritative_text(
        db, unit_id, text, char_start, char_end
    )

    start, end = snap_to_words(text, char_start, char_end, lang)
    if start >= end:
        raise ValueError("selection contains no words")
    selection = text[start:end]
    context = sentence_around(text, start, end)

    expressions: list[dict[str, Any]] = []

    # --- 1. precomputed, span-overlap ---------------------------------------
    if unit is not None:
        rows = db.scalars(
            select(Expression)
            .where(Expression.unit_id == unit.id)
            .order_by(Expression.char_start)
        ).all()
        for expr in rows:
            spans = expr.component_spans or [[expr.char_start, expr.char_end]]
            if any(_overlaps(start, end, int(s), int(e)) for s, e in spans):
                expressions.append(_expression_payload(expr, source="precomputed"))

    # --- 2. lexicon-inferred, lemma-keyed ----------------------------------
    inferred_used = False
    if not expressions:
        hit_lemmas, all_lemmas = selection_lemmas(text, start, end, lang)
        # An expression cannot straddle a sentence boundary. Without this restriction a
        # literal "le feu brûle." matches "feu rouge" because "rouge" happens to appear in
        # the following sentence — a false positive that teaches the learner something
        # untrue, which is the one failure mode worth being strict about.
        sent_lo, sent_hi = sentence_bounds(text, start, end)
        all_lemmas = [(lem, s, e) for lem, s, e in all_lemmas if s >= sent_lo and e <= sent_hi]
        if hit_lemmas:
            candidates = db.scalars(
                select(Expression)
                .where(Expression.language == lang.code, Expression.lemma_key != "")
                .order_by(Expression.confidence.desc())
                .limit(500)
            ).all()
            # Score every plausible candidate, then keep the closest. One sentence can
            # contain two expressions sharing the selected word ("mis le feu ... feu
            # rouge"), so admitting the first match that passes a gate picks arbitrarily.
            scored: list[tuple[int, int, Expression, list[list[int]]]] = []
            seen_keys: set[str] = set()
            for expr in candidates:
                required = {l for l in expr.lemma_key.split("|") if l}
                if not required or expr.lemma_key in seen_keys:
                    continue
                # The selected word must itself be part of the expression.
                if not required.intersection(hit_lemmas):
                    continue
                distance = lemma_proximity(required, all_lemmas, start, end)
                if distance is None:
                    continue
                seen_keys.add(expr.lemma_key)
                scored.append(
                    (
                        distance,
                        -len(required),  # tie-break toward the more specific expression
                        expr,
                        locate_lemmas_near(required, all_lemmas, start, end),
                    )
                )

            scored.sort(key=lambda t: (t[0], t[1]))
            for distance, _, expr, spans in scored[:MAX_INFERRED]:
                # Confidence decays with distance: an adjacent match is far more likely to
                # be the intended reading than one four tokens away.
                decay = INFERRED_CONFIDENCE_PENALTY / (1.0 + 0.25 * distance)
                expressions.append(
                    _expression_payload(
                        expr,
                        source="inferred",
                        confidence=round(expr.confidence * decay, 3),
                        spans=spans,
                    )
                )
                inferred_used = True

    # --- 3. live gloss (cached) -------------------------------------------
    word: dict[str, Any]
    source = "offline"
    llm_error: str | None = None
    try:
        word, self_expr, source = gloss_selection(
            db, selection, context, lang, llm=llm, allow_llm=allow_llm
        )
        if self_expr and not any(
            e["canonical"].casefold() == self_expr["canonical"].casefold() for e in expressions
        ):
            self_expr["id"] = None
            self_expr["char_start"] = start
            self_expr["char_end"] = end
            self_expr["component_spans"] = [[start, end]]
            expressions.insert(0, self_expr)
    except LLMError as exc:
        log.warning("live gloss failed for %r: %s", selection, exc)
        from .translate import _offline_payload

        word = _offline_payload(selection, lang)
        llm_error = str(exc)
        source = "error"

    # Longest / most confident expression first — that is what the popup headlines.
    expressions.sort(
        key=lambda e: (e["confidence"], e["char_end"] - e["char_start"]), reverse=True
    )

    audio_start, audio_end = _audio_window(unit, text, start, end)

    return {
        "language": lang.code,
        "selection": selection,
        "char_start": start,
        "char_end": end,
        "context": context,
        "audio_start_s": audio_start,
        "audio_end_s": audio_end,
        "word": word,
        "expressions": expressions,
        "source": source,
        "unit_id": unit.id if unit else None,
        "lemmatizer": "spacy" if spacy_available(lang) else "headword",
        "inferred": inferred_used,
        "error": llm_error,
    }


def expression_spans_for_unit(db: Session, unit_id: int) -> list[dict[str, Any]]:
    """All expression spans in a unit, so the UI can underline them up front."""
    rows = db.scalars(
        select(Expression).where(Expression.unit_id == unit_id).order_by(Expression.char_start)
    ).all()
    return [
        {
            "id": e.id,
            "canonical": e.canonical,
            "kind": e.kind,
            "component_spans": e.component_spans or [[e.char_start, e.char_end]],
            "char_start": e.char_start,
            "char_end": e.char_end,
        }
        for e in rows
    ]
