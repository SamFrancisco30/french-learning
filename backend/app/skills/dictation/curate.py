"""Turn listening units into curated dictation items.

Every item is derived from material already in the library — the punctuated transcript and its
word-level timings — so curation is a matter of choosing well and rejecting honestly rather than
of authoring anything new.

The quality bar below is set from measurements on this corpus, not from taste. Numbers quoted in
the comments come from running this over all 69 units; re-running the CLI reprints them, so a
future ingest that shifts the distribution shows up rather than silently degrading the items.

Difficulty is NOT simply the parent unit's difficulty. A unit's CEFR is an average over two
minutes; a single sentence inside it can be far easier or harder, and paragraph mode adds a burden
sentence mode does not have — holding structure across sentence boundaries while typing. So each
item is scored on its own text and its own audio window, plus a length term.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...languages import LanguageProfile, get_language
from ...models import (
    DICTATION_KINDS,
    EX_DICTATION_PASSAGE,
    EX_DICTATION_SENTENCE,
    SKILL_DICTATION,
    Exercise,
    ListeningUnit,
)
from ..listening.difficulty import analyze, cefr_for_score
from .segment import Passage, Sentence, passages_for_unit, sentences_for_unit

log = logging.getLogger(__name__)

# ---- the quality bar, with the measurement that justifies each rule ----
#
# Of 981 raw sentences across the corpus, this bar keeps 780. The rejections break down as
# 119 too short, 39 too brief, 35 too long, 1 too slow — i.e. the bar is doing real work at both
# ends rather than nominally passing everything.
MIN_SENTENCE_WORDS = 5   # below this there is nothing to dictate; 119 sentences are 1-4 words
MAX_SENTENCE_WORDS = 45  # p90 is 33 words, so this trims the tail without cutting into the body
MIN_SENTENCE_S = 1.5     # shorter clips are a fragment, not a sentence
MAX_SENTENCE_S = 20.0    # beyond this it stops being a "sentence" task

# Typing burden, used to lift the difficulty of longer items. 70 words — the median passage — is
# taken as the reference load; a 20-word sentence is materially easier to hold and type than a
# 90-word passage even when the language is identical.
LENGTH_REFERENCE_WORDS = 70.0
LENGTH_WEIGHT = 12.0


@dataclass
class Item:
    """A curated dictation item, before it becomes an Exercise row."""

    kind: str
    order_idx: int
    text: str
    start_s: float
    end_s: float
    word_count: int
    sentence_count: int
    score: float
    cefr: str
    detail: dict[str, Any]


def dictation_difficulty(
    text: str, duration_s: float, word_count: int, lang: LanguageProfile
) -> tuple[float, str, dict[str, Any]]:
    """Score an item on its own merits, then add the typing load.

    The base score is the listening scorer, so a dictation item's level is comparable with the
    rest of the library. The length term is additive and capped, because a long passage of simple
    language is harder to transcribe than a short one but should not be pushed two levels up by
    word count alone.
    """
    report = analyze(text, duration_s, lang)
    load = min(1.0, word_count / LENGTH_REFERENCE_WORDS)
    score = min(100.0, report.score + LENGTH_WEIGHT * load)
    detail = {
        "base_score": report.score,
        "length_load": round(load, 3),
        "wpm": report.wpm,
        "mean_zipf": report.mean_zipf,
        "rare_ratio": report.rare_ratio,
        "mean_sentence_words": report.mean_sentence_words,
        "components": report.components,
    }
    return round(score, 1), cefr_for_score(score), detail


def _sentence_ok(s: Sentence) -> str | None:
    """None when the sentence is usable, else the reason it was rejected."""
    if s.word_count < MIN_SENTENCE_WORDS:
        return f"under {MIN_SENTENCE_WORDS} words"
    if s.word_count > MAX_SENTENCE_WORDS:
        return f"over {MAX_SENTENCE_WORDS} words"
    if s.duration_s < MIN_SENTENCE_S:
        return f"under {MIN_SENTENCE_S}s"
    if s.duration_s > MAX_SENTENCE_S:
        return f"over {MAX_SENTENCE_S}s"
    return None


def items_for_unit(
    unit: ListeningUnit, lang: LanguageProfile
) -> tuple[list[Item], dict[str, int]]:
    """Curated items for one unit, plus a tally of what was rejected and why."""
    rejected: dict[str, int] = {}
    text = unit.text or ""
    words = unit.words_json or []

    sentences = sentences_for_unit(
        text, words, lang, unit_start_s=unit.start_s, unit_end_s=unit.end_s
    )
    if not sentences:
        rejected["unit unusable (weak alignment)"] = 1
        return [], rejected

    items: list[Item] = []
    order = 0
    for s in sentences:
        why = _sentence_ok(s)
        if why:
            rejected[why] = rejected.get(why, 0) + 1
            continue
        score, cefr, detail = dictation_difficulty(s.text, s.duration_s, s.word_count, lang)
        items.append(
            Item(
                kind=EX_DICTATION_SENTENCE,
                order_idx=order,
                text=s.text,
                start_s=s.start_s,
                end_s=s.end_s,
                word_count=s.word_count,
                sentence_count=1,
                score=score,
                cefr=cefr,
                detail={**detail, "lead_gap_s": s.lead_gap_s},
            )
        )
        order += 1

    for p in passages_for_unit(
        text, words, lang, unit_start_s=unit.start_s, unit_end_s=unit.end_s
    ):
        score, cefr, detail = dictation_difficulty(p.text, p.duration_s, p.word_count, lang)
        items.append(
            Item(
                kind=EX_DICTATION_PASSAGE,
                order_idx=p.idx,
                text=p.text,
                start_s=p.start_s,
                end_s=p.end_s,
                word_count=p.word_count,
                sentence_count=p.sentence_count,
                score=score,
                cefr=cefr,
                detail=detail,
            )
        )
    return items, rejected


def _prompt_for(item: Item) -> str:
    if item.kind == EX_DICTATION_SENTENCE:
        return "Listen and type the sentence exactly as you hear it."
    return (
        f"Listen and type the passage — {item.sentence_count} sentences, "
        f"about {item.word_count} words. Replay as often as you need."
    )


def sync_unit(db: Session, unit: ListeningUnit, lang: LanguageProfile) -> dict[str, int]:
    """Create or update this unit's dictation exercises. Idempotent.

    Keyed on (unit, kind, order_idx) so re-running after a transcript fix updates in place instead
    of duplicating, and items that no longer survive the bar are deleted rather than left behind as
    orphans pointing at audio windows that moved.
    """
    items, rejected = items_for_unit(unit, lang)

    existing = {
        (e.kind, e.order_idx): e
        for e in db.scalars(
            select(Exercise).where(
                Exercise.unit_id == unit.id, Exercise.kind.in_(DICTATION_KINDS)
            )
        )
    }
    seen: set[tuple[str, int]] = set()
    created = updated = 0

    for item in items:
        key = (item.kind, item.order_idx)
        seen.add(key)
        payload = {
            "word_count": item.word_count,
            "sentence_count": item.sentence_count,
            "duration_s": round(item.end_s - item.start_s, 3),
            "difficulty_score": item.score,
            "difficulty_detail": item.detail,
        }
        # The reference text is the ANSWER, never the payload: the payload is sent to the client
        # before the attempt, and a dictation whose text ships with the audio is not a dictation.
        answer = {"text": item.text}

        row = existing.get(key)
        if row is None:
            db.add(
                Exercise(
                    unit_id=unit.id,
                    skill=SKILL_DICTATION,
                    kind=item.kind,
                    order_idx=item.order_idx,
                    prompt=_prompt_for(item),
                    payload=payload,
                    answer=answer,
                    audio_start_s=item.start_s,
                    audio_end_s=item.end_s,
                    cefr=item.cefr,
                    generator="deterministic",
                )
            )
            created += 1
        else:
            row.prompt = _prompt_for(item)
            row.payload = payload
            row.answer = answer
            row.audio_start_s = item.start_s
            row.audio_end_s = item.end_s
            row.cefr = item.cefr
            row.generator = "deterministic"
            updated += 1

    removed = 0
    for key, row in existing.items():
        if key not in seen:
            db.delete(row)
            removed += 1

    return {
        "created": created,
        "updated": updated,
        "removed": removed,
        "sentences": sum(1 for i in items if i.kind == EX_DICTATION_SENTENCE),
        "passages": sum(1 for i in items if i.kind == EX_DICTATION_PASSAGE),
        **{f"rejected: {k}": v for k, v in rejected.items()},
    }


def sync_all(db: Session, language: str = "fr") -> dict[str, int]:
    lang = get_language(language)
    totals: dict[str, int] = {}
    units = db.scalars(
        select(ListeningUnit).join(ListeningUnit.lesson).order_by(ListeningUnit.id)
    ).all()
    for unit in units:
        for k, v in sync_unit(db, unit, lang).items():
            totals[k] = totals.get(k, 0) + v
    totals["units"] = len(units)
    return totals
