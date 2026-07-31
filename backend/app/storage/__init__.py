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
    is_derived,
    source_key,
    transcript_key,
    variant_clip_key,
    variant_map_key,
)
from .local import LocalStore


@lru_cache(maxsize=4)
def _make(backend: str) -> ObjectStore:
    if backend == "local":
        return LocalStore()
    if backend == "supabase":
        from .supabase_store import SupabaseStore

        return SupabaseStore()
    raise ValueError(f"Unknown storage backend {backend!r}. Use 'local' or 'supabase'.")


def get_store(backend: str | None = None) -> ObjectStore:
    return _make((backend or settings.storage_backend).lower())


__all__ = [
    "ObjectStore",
    "LocalStore",
    "get_store",
    "clip_key",
    "dictation_clip_key",
    "source_key",
    "transcript_key",
    "variant_clip_key",
    "variant_map_key",
    "content_type_for",
    "is_derived",
    "DERIVED_SUFFIXES",
]
