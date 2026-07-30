"""Object storage behind one interface.

The key insight that makes migration cheap: both backends use the *same* key format
(`clips/VIDEOID/unit_000.m4a`). Local storage writes that path under `data_dir` and serves
it from `/media/<key>`; Supabase uploads it to a bucket and serves a signed URL. So the
stored value in the database is a backend-agnostic key, and switching backends is a config
change plus a one-off upload — not a data rewrite.

Buckets are private by design. Public buckets make audio hotlinkable by anyone who sees a
URL, which for YouTube-derived material is exactly the exposure worth avoiding.
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)


# Explicit types for everything the pipeline produces, checked BEFORE mimetypes.
#
# `mimetypes.guess_type` cannot be trusted for audio: on macOS it returns
# "audio/mp4a-latm" for .m4a, which Supabase Storage rejects with a 415, and which no
# browser would treat as playable audio either. It returns *something*, so a
# "fall back only if guess_type fails" ordering never corrects it.
_AUDIO_TYPES = {
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".aac": "audio/aac",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".opus": "audio/opus",
    ".ogg": "audio/ogg",
    ".webm": "audio/webm",
    ".flac": "audio/flac",
    ".json": "application/json",
}


def content_type_for(key: str) -> str:
    suffix = Path(key).suffix.lower()
    if suffix in _AUDIO_TYPES:
        return _AUDIO_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(key)
    return guessed or "application/octet-stream"


@runtime_checkable
class ObjectStore(Protocol):
    name: str

    def put_file(self, key: str, path: Path, *, overwrite: bool = False) -> str:
        """Store a local file under `key`. Returns the key."""
        ...

    def put_bytes(self, key: str, data: bytes, *, overwrite: bool = False) -> str:
        ...

    def exists(self, key: str) -> bool:
        ...

    def get_bytes(self, key: str) -> bytes:
        """Read an object back. Needed to derive variants from a stored original."""
        ...

    def url_for(self, key: str) -> str:
        """A URL a browser can play. May be short-lived."""
        ...

    def size(self, key: str) -> int | None:
        ...

    def delete(self, key: str) -> None:
        ...


# ---------------------------------------------------------------- key conventions
#
# Kept in one place so the migration script and the pipeline cannot disagree about where
# an object lives.


def source_key(provider_id: str, suffix: str) -> str:
    return f"sources/{provider_id}/original{suffix}"


def clip_key(provider_id: str, unit_idx: int) -> str:
    return f"clips/{provider_id}/unit_{unit_idx:03d}.m4a"


# Bump when the slow-audio algorithm changes, so cached variants are regenerated instead
# of serving output from the previous version.
#   v1  silence inserted at every word boundary — audibly cut elisions like "l'emprise"
#   v2  silence only lengthened where the waveform already has it
VARIANT_VERSION = "v2"


def variant_clip_key(provider_id: str, unit_idx: int, speed: float) -> str:
    """Naturally-slowed variant, e.g. clips/ID/unit_000@0.75v2.m4a."""
    return f"clips/{provider_id}/unit_{unit_idx:03d}@{speed:g}{VARIANT_VERSION}.m4a"


def variant_map_key(provider_id: str, unit_idx: int, speed: float) -> str:
    """Original->stretched time map for a variant, stored beside it so replay windows
    survive a speed change without regenerating the audio."""
    return f"clips/{provider_id}/unit_{unit_idx:03d}@{speed:g}{VARIANT_VERSION}.map.json"


def transcript_key(provider_id: str, backend: str) -> str:
    return f"transcripts/{provider_id}.{backend}.json"


# Derived working files that exist only to feed the ASR API (chunked upload copies,
# normalized wavs). They are regenerable from the source and are ~40% of local disk, so
# they are never uploaded.
DERIVED_SUFFIXES = (".upload.mp3", ".asr.wav")


def is_derived(path: Path) -> bool:
    return any(path.name.endswith(s) for s in DERIVED_SUFFIXES)
