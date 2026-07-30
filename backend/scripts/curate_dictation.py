#!/usr/bin/env python
"""Curate dictation items from the listening library.

    python scripts/curate_dictation.py plan      what would be produced, changing nothing
    python scripts/curate_dictation.py sync      write the items
    python scripts/curate_dictation.py report    what exists now, by mode and level
    python scripts/curate_dictation.py show 42   the items for one unit, with their text

`plan` first is the point of having it: curation rejects material, and the rejection tally is the
interesting output — it says whether the quality bar is doing real work or nominally passing
everything. Run it after any ingest.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.languages import get_language  # noqa: E402
from app.models import (  # noqa: E402
    CEFR_LEVELS,
    DICTATION_KINDS,
    EX_DICTATION_PASSAGE,
    EX_DICTATION_SENTENCE,
    Exercise,
    Lesson,
    ListeningUnit,
)
from app.skills.dictation.curate import items_for_unit, sync_all  # noqa: E402


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    return v[min(len(v) - 1, int(len(v) * p))]


def cmd_plan(args: argparse.Namespace) -> None:
    logging.disable(logging.WARNING)  # the per-unit alignment warnings are summarised below
    db = SessionLocal()
    lang = get_language(args.language)
    units = db.scalars(select(ListeningUnit).order_by(ListeningUnit.id)).all()

    rejected: Counter[str] = Counter()
    by_mode_level: dict[str, Counter[str]] = {"sentence": Counter(), "paragraph": Counter()}
    words = {"sentence": [], "paragraph": []}
    secs = {"sentence": [], "paragraph": []}
    unusable: list[int] = []

    for u in units:
        items, rej = items_for_unit(u, lang)
        for k, v in rej.items():
            rejected[k] += v
        if not items and "unit unusable (weak alignment)" in rej:
            unusable.append(u.id)
        for it in items:
            mode = "sentence" if it.kind == EX_DICTATION_SENTENCE else "paragraph"
            by_mode_level[mode][it.cefr] += 1
            words[mode].append(it.word_count)
            secs[mode].append(it.end_s - it.start_s)

    print(f"units scanned: {len(units)}   unusable: {len(unusable)} {unusable or ''}")
    print()
    for mode in ("sentence", "paragraph"):
        n = sum(by_mode_level[mode].values())
        print(f"{mode.upper():10s} {n} items")
        if n:
            w, s = words[mode], secs[mode]
            print(
                "           words  p10 %d  median %d  p90 %d  max %d"
                % (_pct(w, 0.1), _pct(w, 0.5), _pct(w, 0.9), max(w))
            )
            print(
                "           audio  p10 %.0fs median %.0fs p90 %.0fs max %.0fs"
                % (_pct(s, 0.1), _pct(s, 0.5), _pct(s, 0.9), max(s))
            )
            spread = "  ".join(
                f"{lv} {by_mode_level[mode].get(lv, 0)}" for lv in CEFR_LEVELS
            )
            print(f"           levels {spread}")
            empty = [lv for lv in CEFR_LEVELS if not by_mode_level[mode].get(lv)]
            if empty:
                print(f"           GAP: no items at {', '.join(empty)}")
        print()

    print("rejected, and why:")
    for reason, n in rejected.most_common():
        print(f"   {reason:34s} {n}")
    total_kept = sum(sum(c.values()) for c in by_mode_level.values())
    total = total_kept + sum(rejected.values())
    print(f"\nkept {total_kept} of {total} candidates ({100 * total_kept / max(1, total):.0f}%)")


def cmd_sync(args: argparse.Namespace) -> None:
    logging.disable(logging.WARNING)
    db = SessionLocal()
    totals = sync_all(db, args.language)
    db.commit()
    for k in ("units", "sentences", "passages", "created", "updated", "removed"):
        if k in totals:
            print(f"{k:12s} {totals[k]}")
    for k, v in sorted(totals.items()):
        if k.startswith("rejected"):
            print(f"   {k:36s} {v}")


def cmd_report(args: argparse.Namespace) -> None:
    db = SessionLocal()
    rows = db.execute(
        select(Exercise.kind, Exercise.cefr, func.count(Exercise.id))
        .join(ListeningUnit, Exercise.unit_id == ListeningUnit.id)
        .join(Lesson, ListeningUnit.lesson_id == Lesson.id)
        .where(Exercise.kind.in_(DICTATION_KINDS), Lesson.language == args.language)
        .group_by(Exercise.kind, Exercise.cefr)
    ).all()
    if not rows:
        print("no dictation items yet — run: python scripts/curate_dictation.py sync")
        return
    for kind, label in ((EX_DICTATION_SENTENCE, "sentence"), (EX_DICTATION_PASSAGE, "paragraph")):
        counts = {c: n for k, c, n in rows if k == kind}
        total = sum(counts.values())
        spread = "  ".join(f"{lv} {counts.get(lv, 0)}" for lv in CEFR_LEVELS)
        print(f"{label:10s} {total:4d}   {spread}")


def cmd_show(args: argparse.Namespace) -> None:
    db = SessionLocal()
    rows = db.scalars(
        select(Exercise)
        .where(Exercise.unit_id == args.unit_id, Exercise.kind.in_(DICTATION_KINDS))
        .order_by(Exercise.kind, Exercise.order_idx)
    ).all()
    if not rows:
        print(f"unit {args.unit_id} has no dictation items")
        return
    for e in rows:
        p = e.payload or {}
        mode = "sentence" if e.kind == EX_DICTATION_SENTENCE else "paragraph"
        print(
            f"[{e.id}] {mode:9s} {e.cefr}  {p.get('word_count')}w  "
            f"{p.get('duration_s')}s  score {p.get('difficulty_score')}"
        )
        print(f"        {(e.answer or {}).get('text', '')[:150]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--language", default="fr")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan").set_defaults(fn=cmd_plan)
    sub.add_parser("sync").set_defaults(fn=cmd_sync)
    sub.add_parser("report").set_defaults(fn=cmd_report)
    show = sub.add_parser("show")
    show.add_argument("unit_id", type=int)
    show.set_defaults(fn=cmd_show)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
