"""Upload drill media to object storage, resumably.

The keys are already in the database — `import_drill.py` computed them from file
content — so this reads the distinct ones, finds each local file through the
`*_source` path kept in the row's provenance, and puts the bytes where the key
points. Nothing here decides a key; deciding it twice is how the two halves
would drift apart.

    python scripts/upload_drill_media.py --data ../../coco-scrape/data --dry-run
    python scripts/upload_drill_media.py --data ../../coco-scrape/data

Safe to interrupt and re-run. A key already in the bucket is skipped, so a run
that dies at 60% resumes there rather than starting over.

Drill media goes to its own bucket. Study mode's bucket is restricted to audio
mime types — a deliberate property, and 2730 of these files are PNG. Widening
that list to admit images would loosen the guarantee for the module that has no
images in the first place.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.drill.models import DrillQuestion  # noqa: E402
from app.storage.base import drill_media_key  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_BUCKET = "drill"
# Audio the bank ships plus the image types its reading items are rendered as.
BUCKET_MIME = [
    "audio/mpeg",
    "audio/mp4",
    "audio/aac",
    "image/png",
    "image/jpeg",
    "video/mp4",
    "application/json",
]


def planned(data_root: Path) -> tuple[dict[str, Path], Counter]:
    """key -> local file, for every distinct media object the rows reference."""
    stats = Counter()
    out: dict[str, Path] = {}
    with session_scope() as session:
        rows = session.execute(
            select(
                DrillQuestion.image_key, DrillQuestion.audio_key, DrillQuestion.provenance
            ).where(
                DrillQuestion.image_key.is_not(None) | DrillQuestion.audio_key.is_not(None)
            )
        ).all()

    for image_key, audio_key, provenance in rows:
        provenance = provenance or {}
        for key, source in (
            (image_key, provenance.get("image_source")),
            (audio_key, provenance.get("audio_source")),
        ):
            if not key:
                continue
            if key in out:
                stats["shared"] += 1
                continue
            if not source:
                stats["no_source_path"] += 1
                continue
            suffix = source.split("data/media/", 1)[-1]
            path = data_root / "media" / suffix
            if not path.exists():
                stats["missing_on_disk"] += 1
                continue
            out[key] = path
    return out, stats


def key_matches(key: str, path: Path) -> bool:
    """Does the file still hash to the key the database recorded for it?"""
    return drill_media_key(path) == key


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--data", type=Path, required=True, help="the scraper's data directory")
    ap.add_argument("--bucket", default=DEFAULT_BUCKET)
    ap.add_argument("--dry-run", action="store_true", help="report, upload nothing")
    ap.add_argument("--limit", type=int, help="stop after N uploads (for a first look)")
    ap.add_argument(
        "--no-verify",
        action="store_true",
        help="skip re-hashing each file against its key (faster, but a disk that "
             "returns different bytes than it did at import time goes unnoticed)",
    )
    args = ap.parse_args()

    plan, stats = planned(args.data)
    total_bytes = sum(p.stat().st_size for p in plan.values())
    print(f"{len(plan)} distinct objects, {total_bytes / 1e6:.0f} MB")
    for k, v in stats.most_common():
        print(f"  {v:6d}  {k}")

    if args.dry_run:
        by_suffix = Counter(p.suffix.lower() for p in plan.values())
        print("  by type: " + ", ".join(f"{k}={v}" for k, v in by_suffix.most_common()))
        print("\n(dry run — nothing uploaded)")
        return

    if settings.storage_backend != "supabase":
        sys.exit(
            f"storage_backend is {settings.storage_backend!r}; set it to 'supabase' "
            "to upload (or run with --dry-run)"
        )

    from app.storage.supabase_store import SupabaseStore

    store = SupabaseStore(bucket=args.bucket)
    try:
        store.ensure_bucket()
    except Exception as exc:  # noqa: BLE001
        print(f"could not ensure bucket {args.bucket!r}: {exc}", file=sys.stderr)
        raise
    # ensure_bucket's default mime list is study mode's, which rejects images.
    _widen_mime_types(store, args.bucket)

    done = skipped = failed = mismatched = 0
    sent_bytes = 0
    items = list(plan.items())
    if args.limit:
        items = items[: args.limit]

    for i, (key, path) in enumerate(items, 1):
        try:
            if store.exists(key):
                skipped += 1
            elif not args.no_verify and not key_matches(key, path):
                # The key is a hash of the file's contents, taken when the row was
                # imported. If a re-read disagrees, the bytes on disk changed
                # underneath us — the machine this ran on has a failing disk, and
                # has already produced corrupt git objects and a spliced JSONL.
                # Uploading anyway would put content under a key that does not
                # describe it, and nothing downstream would ever notice.
                mismatched += 1
                print(
                    f"  HASH MISMATCH {path} no longer matches {key} — skipped",
                    file=sys.stderr,
                )
            else:
                store.put_file(key, path)
                done += 1
                sent_bytes += path.stat().st_size
        except Exception as exc:  # noqa: BLE001 - one bad object shouldn't end the run
            failed += 1
            print(f"  FAILED {key} ({path.name}): {exc}", file=sys.stderr)
        if i % 200 == 0:
            print(
                f"  {i}/{len(items)}  uploaded {done}, already there {skipped}, "
                f"failed {failed}, mismatched {mismatched}, "
                f"{sent_bytes / 1e6:.0f} MB sent",
                flush=True,
            )

    print(
        f"\nuploaded {done}, already present {skipped}, failed {failed}, "
        f"hash mismatches {mismatched}, {sent_bytes / 1e6:.0f} MB sent "
        f"-> bucket {args.bucket!r}"
    )
    if failed:
        print("re-run to retry the failures; objects already uploaded are skipped")
    if mismatched:
        print(
            f"{mismatched} files no longer hash to the key recorded for them. Re-run the "
            "importer to pick up their current contents, then check those files are not "
            "damaged — a changed hash means the bytes changed."
        )


def _widen_mime_types(store, bucket: str) -> None:
    """Allow images in the drill bucket.

    `ensure_bucket` creates with study mode's audio-only list, which would
    reject every PNG here with a 415. Updating is idempotent and only ever
    touches the drill bucket.
    """
    try:
        store._client.storage.update_bucket(  # noqa: SLF001 - no public wrapper
            bucket,
            options={
                "public": False,
                "file_size_limit": 52428800,
                "allowed_mime_types": BUCKET_MIME,
            },
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  note: could not update bucket mime types ({exc}); "
              f"image uploads may be rejected", file=sys.stderr)


if __name__ == "__main__":
    main()
