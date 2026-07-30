"""Remove local working files once their objects are safely in the store.

Three pipeline stages need a real file on a filesystem — yt-dlp writes the download,
ffmpeg reads it to cut clips, and ASR reads it to transcribe — so local disk is
unavoidable as scratch space. What was missing is anything that clears it afterwards, so
disk grew by roughly 3 MB per minute of ingested video and never shrank.

The safety rule that makes this non-destructive: **a file is only deleted after
`store.exists()` confirms its object is actually in the bucket.** A failed or partial
upload therefore leaves the local copy alone, which is the difference between freeing
space and losing data.

Derived files (`.upload.mp3` chunking copies, `.asr.wav` normalizations, chunk
directories) are never uploaded at all and are regenerable from the source, so they are
removed unconditionally.

No-ops entirely under the local storage backend, where the "local file" *is* the stored
object and deleting it would destroy the library.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .base import ObjectStore

log = logging.getLogger(__name__)


@dataclass
class CleanupReport:
    deleted: list[Path] = field(default_factory=list)
    kept: list[Path] = field(default_factory=list)
    bytes_freed: int = 0
    skipped_reason: str | None = None

    @property
    def mb_freed(self) -> float:
        return self.bytes_freed / 1e6

    def summary(self) -> str:
        if self.skipped_reason:
            return f"cleanup skipped: {self.skipped_reason}"
        parts = [f"freed {self.mb_freed:.1f} MB ({len(self.deleted)} files)"]
        if self.kept:
            parts.append(f"kept {len(self.kept)} whose objects were not confirmed uploaded")
        return "; ".join(parts)


def _unlink(path: Path, report: CleanupReport) -> None:
    try:
        size = path.stat().st_size
        path.unlink()
        report.deleted.append(path)
        report.bytes_freed += size
    except OSError as exc:
        log.warning("could not delete %s: %s", path, exc)
        report.kept.append(path)


def prune_after_upload(
    store: ObjectStore,
    *,
    uploaded: list[tuple[str, Path]],
    derived: list[Path] | None = None,
    dirs: list[Path] | None = None,
) -> CleanupReport:
    """Delete local scratch files whose objects are confirmed present in `store`.

    `uploaded` is (object key, local path) pairs — each deleted only if its key exists
    remotely. `derived` are regenerable working files, deleted unconditionally. `dirs` are
    scratch directories to remove wholesale.
    """
    report = CleanupReport()

    if getattr(store, "name", "") == "local":
        report.skipped_reason = "storage backend is 'local' — these files are the library"
        return report

    for key, path in uploaded:
        if not path.exists():
            continue
        try:
            present = store.exists(key)
        except Exception as exc:  # noqa: BLE001 - never delete on an inconclusive check
            log.warning("could not confirm %s in store (%s); keeping local copy", key, exc)
            report.kept.append(path)
            continue
        if present:
            _unlink(path, report)
        else:
            log.warning("%s is not in the store; keeping %s", key, path.name)
            report.kept.append(path)

    for path in derived or []:
        if path.exists():
            _unlink(path, report)

    for d in dirs or []:
        if d.is_dir():
            try:
                size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                shutil.rmtree(d)
                report.bytes_freed += size
                report.deleted.append(d)
            except OSError as exc:
                log.warning("could not remove %s: %s", d, exc)

    # Tidy any directories left empty, so data/clips doesn't fill with husks.
    for path in list(report.deleted):
        parent = path.parent
        try:
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass

    return report


def derived_paths(audio_dir: Path, stem: str) -> tuple[list[Path], list[Path]]:
    """(files, directories) of regenerable ASR working artifacts for one source."""
    files = [audio_dir / f"{stem}.upload.mp3", audio_dir / f"{stem}.asr.wav"]
    dirs = [audio_dir / f"{stem}_chunks"]
    return files, dirs
