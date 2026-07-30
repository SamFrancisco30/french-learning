"""Supabase Storage backend.

Uses the `service_role` key, so this must only ever run server-side: that key bypasses
Row Level Security entirely. It is read from the environment and never returned in any API
response.

Buckets are created private. Playback therefore goes through short-lived signed URLs rather
than public object URLs — a public bucket would let anyone who ever saw a link keep
streaming the audio, which is precisely the exposure to avoid with YouTube-derived material.

Signed URLs are minted per request and not cached in the database, because caching them
would store credentials with an expiry the database knows nothing about.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import settings
from .base import content_type_for

log = logging.getLogger(__name__)

# Supabase caps uploads at 50 MB on the free plan; per-bucket limits cannot exceed the
# project's global limit. Fail loudly before the API does, with a message that says what to
# do about it.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


class SupabaseStore:
    name = "supabase"

    def __init__(
        self,
        url: str | None = None,
        service_key: str | None = None,
        bucket: str | None = None,
    ) -> None:
        from supabase import create_client

        self.url = url or settings.supabase_url
        key = service_key or settings.supabase_service_key
        self.bucket = bucket or settings.supabase_bucket
        if not self.url or not key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set to use the supabase "
                "storage backend (see backend/.env.example)"
            )
        self._client = create_client(self.url, key)

    # ---------------------------------------------------------------- bucket

    def ensure_bucket(self, *, public: bool = False) -> None:
        """Create the bucket if it doesn't exist. Safe to call repeatedly."""
        storage = self._client.storage
        try:
            existing = {b.name for b in storage.list_buckets()}
        except Exception as exc:  # noqa: BLE001 - surface auth/network problems plainly
            raise RuntimeError(f"could not list Supabase buckets: {exc}") from exc

        if self.bucket in existing:
            return
        storage.create_bucket(
            self.bucket,
            options={
                "public": public,
                "file_size_limit": MAX_UPLOAD_BYTES,
                "allowed_mime_types": ["audio/mpeg", "audio/mp4", "audio/aac", "application/json"],
            },
        )
        log.info("created Supabase bucket %r (public=%s)", self.bucket, public)

    # ---------------------------------------------------------------- writes

    def put_file(self, key: str, path: Path, *, overwrite: bool = False) -> str:
        size = path.stat().st_size
        if size > MAX_UPLOAD_BYTES:
            raise RuntimeError(
                f"{path.name} is {size / 1e6:.1f} MB, over Supabase's "
                f"{MAX_UPLOAD_BYTES / 1e6:.0f} MB per-file limit. Split it, re-encode at a "
                "lower bitrate, or raise the project's global file size limit."
            )
        if not overwrite and self.exists(key):
            return key
        with path.open("rb") as fh:
            self._upload(key, fh, overwrite=overwrite)
        return key

    def put_bytes(self, key: str, data: bytes, *, overwrite: bool = False) -> str:
        if not overwrite and self.exists(key):
            return key
        self._upload(key, data, overwrite=overwrite)
        return key

    def _upload(self, key: str, body, *, overwrite: bool) -> None:
        # An explicit content-type matters: without one the API defaults to text/html and
        # browsers refuse to play the object.
        opts = {
            "content-type": content_type_for(key),
            "cache-control": "3600",
            "upsert": "true" if overwrite else "false",
        }
        try:
            self._client.storage.from_(self.bucket).upload(path=key, file=body, file_options=opts)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"upload of {key!r} failed: {exc}") from exc

    # ---------------------------------------------------------------- reads

    def exists(self, key: str) -> bool:
        folder, _, name = key.rpartition("/")
        try:
            entries = self._client.storage.from_(self.bucket).list(folder or None)
        except Exception:  # noqa: BLE001 - a missing folder is a normal "not found"
            return False
        return any(e.get("name") == name for e in entries or [])

    def size(self, key: str) -> int | None:
        folder, _, name = key.rpartition("/")
        try:
            entries = self._client.storage.from_(self.bucket).list(folder or None)
        except Exception:  # noqa: BLE001
            return None
        for e in entries or []:
            if e.get("name") == name:
                meta = e.get("metadata") or {}
                return meta.get("size")
        return None

    def url_for(self, key: str) -> str:
        """Short-lived signed URL. Supports Range requests, so <audio> seeking works."""
        try:
            res = self._client.storage.from_(self.bucket).create_signed_url(
                key, settings.signed_url_ttl_s
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"could not sign {key!r}: {exc}") from exc
        # The client has returned this under different keys across versions.
        return res.get("signedURL") or res.get("signedUrl") or res.get("signed_url") or ""

    def delete(self, key: str) -> None:
        try:
            self._client.storage.from_(self.bucket).remove([key])
        except Exception as exc:  # noqa: BLE001
            log.warning("delete of %r failed: %s", key, exc)
