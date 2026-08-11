"""Object storage factory."""

from __future__ import annotations

from functools import lru_cache

from ..config import settings
from .base import (
    DERIVED_SUFFIXES,
    ObjectStore,
    clip_key,
    dictation_clip_key,
    content_type_for,
    drill_media_key,
    is_derived,
    sha256_of,
    source_key,
    transcript_key,
    variant_clip_key,
    variant_map_key,
)
from .local import LocalStore


@lru_cache(maxsize=8)
def _make(backend: str, bucket: str | None = None) -> ObjectStore:
    if backend == "local":
        # Local storage has no buckets. The key's own prefix ("drill/…") is the
        # separation, and it is the same key either backend stores under.
        return LocalStore()
    if backend == "supabase":
        from .supabase_store import SupabaseStore

        return SupabaseStore(bucket=bucket) if bucket else SupabaseStore()
    raise ValueError(f"Unknown storage backend {backend!r}. Use 'local' or 'supabase'.")


def get_store(backend: str | None = None) -> ObjectStore:
    return _make((backend or settings.storage_backend).lower())


def get_drill_store(backend: str | None = None) -> ObjectStore:
    """The store holding drill media, which is a different bucket from study mode's."""
    return _make(
        (backend or settings.storage_backend).lower(), settings.supabase_drill_bucket
    )


__all__ = [
    "ObjectStore",
    "LocalStore",
    "get_store",
    "get_drill_store",
    "clip_key",
    "dictation_clip_key",
    "drill_media_key",
    "sha256_of",
    "source_key",
    "transcript_key",
    "variant_clip_key",
    "variant_map_key",
    "content_type_for",
    "is_derived",
    "DERIVED_SUFFIXES",
]
