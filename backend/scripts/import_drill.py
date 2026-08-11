"""Load the exported TCF bank into drill mode's tables.

Reads the four JSONL files produced by the scraper's build step and writes
collections, questions and options. It does not touch study mode's tables and
does not upload anything — media keys are computed here, from file content, and
`upload_drill_media.py` puts the bytes where those keys point.

    python scripts/import_drill.py --data ../../coco-scrape/data
    python scripts/import_drill.py --data ... --dry-run

Re-running is safe. Rows are matched on (exam, external_id) — the vendor's own
numeric id — so a second run updates in place rather than inserting a second
copy, and a corrected export can be re-imported without clearing anything.

Two passes are needed, not one. `duplicate_of` points at another question in the
same table, and the target may not be inserted yet when the pointer is written;
so every row goes in first, and the links are set once all the ids exist.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.drill.models import (  # noqa: E402
    EXAM_TCF,
    KIND_GUIDE,
    KIND_MCQ,
    KIND_PRODUCTION,
    DrillCollection,
    DrillOption,
    DrillQuestion,
)
from app.storage.base import drill_media_key, sha256_of  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# (filename, skill, the field holding the document the learner is given)
SOURCES = [
    ("tcf_reading.jsonl", "reading", "passage"),
    ("tcf_listening.jsonl", "listening", "transcript"),
    ("tcf_production.jsonl", None, "prompt"),  # skill is per-row here
]


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


class MediaKeys:
    """Content hashes for the media the rows reference.

    Hashing is the slow part of the import — 2.8 GB — so each distinct file is
    hashed once and remembered, even though several questions point at it.
    """

    def __init__(self, media_root: Path) -> None:
        self.root = media_root
        self._cache: dict[str, str] = {}
        self.missing: list[str] = []

    def key_for(self, rel_path: str | None) -> str | None:
        if not rel_path:
            return None
        if rel_path in self._cache:
            return self._cache[rel_path]
        # exports carry "data/media/upload/..."; the media root is the "data" part
        suffix = rel_path.split("data/media/", 1)[-1]
        path = self.root / "media" / suffix
        if not path.exists():
            self.missing.append(rel_path)
            self._cache[rel_path] = None
            return None
        key = drill_media_key(path, digest=sha256_of(path))
        self._cache[rel_path] = key
        return key


def collection_for(
    session: Session, cache: dict[str, DrillCollection], name: str, skill: str,
    level: str | None,
) -> DrillCollection:
    if name in cache:
        return cache[name]
    found = session.scalar(
        select(DrillCollection).where(
            DrillCollection.exam == EXAM_TCF, DrillCollection.name == name
        )
    )
    if found is None:
        found = DrillCollection(exam=EXAM_TCF, skill=skill, name=name, level=level)
        session.add(found)
        session.flush()
    cache[name] = found
    return found


def collection_level(rows: list[dict]) -> str | None:
    """A level for the bank only where every item in it shares one.

    The difficulty buckets ("READING 21分题库") are single-level by construction;
    the numbered mock tests run A1 to C2 and get null.
    """
    levels = {r.get("level") for r in rows}
    return levels.pop() if len(levels) == 1 else None


def question_fields(row: dict, skill: str, body_field: str, media: MediaKeys) -> dict:
    kind = KIND_MCQ
    if row.get("kind") == "guide":
        kind = KIND_GUIDE
    elif skill in ("speaking", "writing"):
        kind = KIND_PRODUCTION

    # Listening carries the recording the item is built on; speaking carries a
    # model answer read aloud. Different fields in the export, the same column
    # here — both are "the audio that belongs to this question".
    audio_path = row.get("audio_path") or row.get("model_audio_path")
    audio_url = row.get("audio") or row.get("model_audio")

    provenance = dict(row.get("provenance") or {})
    # The uploader needs to find the local file; the vendor's own path is also
    # what makes a content-addressed key traceable back to where it came from.
    if row.get("image_path"):
        provenance["image_source"] = row["image_path"]
    if audio_path:
        provenance["audio_source"] = audio_path
        provenance["audio_role"] = (
            "model_answer" if row.get("model_audio_path") else "document"
        )
    if row.get("image"):
        provenance["image_url"] = row["image"]
    if audio_url:
        provenance["audio_url"] = audio_url

    return {
        "exam": EXAM_TCF,
        "external_id": row["id"],
        "skill": skill,
        "kind": kind,
        "seq": row.get("question_number") or row.get("seq"),
        "title": row.get("title") or row.get("topic") or None,
        "level": row.get("level"),
        "score": row.get("score"),
        "difficulty": row.get("difficulty"),
        "time_limit_s": row.get("time_limit"),
        "document": row.get(body_field) or "",
        "document_corrected": row.get("passage_corrected"),
        "document_zh": row.get("passage_translation_zh") or row.get("translation_zh"),
        "question": row.get("question") or None,
        "explanation": row.get("explanation") or None,
        "model_answer": row.get("model_answer") or None,
        "model_answer_fr": row.get("model_answer_fr") or None,
        "answer": row.get("answer") if isinstance(row.get("answer"), str) else None,
        "image_key": media.key_for(row.get("image_path")),
        "audio_key": media.key_for(audio_path),
        "canonical": bool(row.get("canonical", True)),
        "copies": row.get("copies") or 1,
        "provenance": provenance,
        "warnings": row.get("warnings") or [],
        "corrections": row.get("corrections") or [],
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--data",
        type=Path,
        required=True,
        help="the scraper's data directory (contains questions/ and media/)",
    )
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = ap.parse_args()

    questions_dir = args.data / "questions"
    if not questions_dir.exists():
        sys.exit(f"{questions_dir} does not exist")

    media = MediaKeys(args.data)
    stats = Counter()
    # external_id -> the row's own duplicate_of target, resolved in pass two
    dup_links: dict[int, int] = {}

    with session_scope() as session:
        cache: dict[str, DrillCollection] = {}
        existing = {
            q.external_id: q
            for q in session.scalars(
                select(DrillQuestion).where(DrillQuestion.exam == EXAM_TCF)
            )
        }
        print(f"{len(existing)} drill questions already in the database")

        for filename, fixed_skill, body_field in SOURCES:
            rows = read_jsonl(questions_dir / filename)
            if not rows:
                print(f"  {filename}: absent or empty, skipped")
                continue
            by_collection: dict[str, list[dict]] = {}
            for row in rows:
                by_collection.setdefault(row["collection"], []).append(row)
            print(f"  {filename}: {len(rows)} rows in {len(by_collection)} collections")

            for name, group in by_collection.items():
                skill = fixed_skill or (group[0].get("skill") or "").lower()
                coll = collection_for(session, cache, name, skill, collection_level(group))
                coll.item_count = len(group)

                for row in group:
                    fields = question_fields(row, skill, body_field, media)
                    q = existing.get(row["id"])
                    if q is None:
                        q = DrillQuestion(collection_id=coll.id, **fields)
                        session.add(q)
                        existing[row["id"]] = q
                        stats["inserted"] += 1
                    else:
                        q.collection_id = coll.id
                        for k, v in fields.items():
                            setattr(q, k, v)
                        # options are rewritten wholesale rather than diffed
                        q.options.clear()
                        stats["updated"] += 1
                    session.flush()

                    for idx, opt in enumerate(row.get("options") or []):
                        session.add(
                            DrillOption(
                                question_id=q.id,
                                label=opt.get("id") or chr(65 + idx),
                                text=opt.get("text") or "",
                                text_corrected=opt.get("text_corrected"),
                                is_correct=bool(opt.get("correct")),
                            )
                        )
                        stats["options"] += 1

                    if row.get("duplicate_of"):
                        dup_links[row["id"]] = row["duplicate_of"]

        session.flush()
        # pass two: every id exists now, so the self-references can be set
        for external_id, target_external in dup_links.items():
            src, dst = existing.get(external_id), existing.get(target_external)
            if src is not None and dst is not None:
                src.duplicate_of = dst.id
                stats["linked"] += 1
            else:
                stats["dangling_duplicate_of"] += 1

        print(
            f"\ninserted {stats['inserted']}   updated {stats['updated']}   "
            f"options {stats['options']}   duplicate links {stats['linked']}"
        )
        if stats["dangling_duplicate_of"]:
            print(f"  {stats['dangling_duplicate_of']} duplicate_of targets not found")
        if media.missing:
            print(f"  {len(media.missing)} media files referenced but absent from disk")
            for p in media.missing[:5]:
                print(f"      {p}")

        if args.dry_run:
            print("\n(dry run — rolling back)")
            session.rollback()
            raise SystemExit(0)

    print("committed")


if __name__ == "__main__":
    main()
