"""YouTube ingest via yt-dlp.

Only the audio track is fetched — this pipeline never needs video. Licence metadata
is captured so a deployment can restrict its library to redistributable material
(`--require-cc` keeps only Creative Commons sources).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CC_LICENSE_MARKERS = ("creative commons", "cc-by", "cc by")


class IngestError(RuntimeError):
    pass


@dataclass
class MediaInfo:
    provider: str
    provider_id: str
    url: str
    title: str
    channel: str | None
    uploader_url: str | None
    duration_s: float | None
    license_name: str | None
    upload_date: str | None
    description: str | None
    audio_path: Path

    @property
    def is_creative_commons(self) -> bool:
        lic = (self.license_name or "").lower()
        return any(m in lic for m in CC_LICENSE_MARKERS)


def _flatten(info: dict[str, Any]) -> dict[str, Any]:
    """Playlist URLs come back as a container; take the first real entry."""
    while info.get("_type") in {"playlist", "multi_video"}:
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise IngestError("URL resolved to an empty playlist")
        info = entries[0]
    return info


def probe(url: str) -> dict[str, Any]:
    """Fetch metadata without downloading."""
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return _flatten(ydl.extract_info(url, download=False))


def download_audio(
    url: str,
    out_dir: Path,
    *,
    max_duration_s: float | None = 3600.0,
    require_cc: bool = False,
) -> MediaInfo:
    """Download bestaudio for `url` into `out_dir` and return its metadata."""
    import yt_dlp

    out_dir.mkdir(parents=True, exist_ok=True)

    meta = probe(url)
    duration = meta.get("duration")
    license_name = meta.get("license")

    if max_duration_s and duration and duration > max_duration_s:
        raise IngestError(
            f"{meta.get('title')!r} is {duration / 60:.0f} min, over the "
            f"{max_duration_s / 60:.0f} min ingest cap. Raise --max-duration to allow it."
        )
    if require_cc and not any(m in (license_name or "").lower() for m in CC_LICENSE_MARKERS):
        raise IngestError(
            f"{meta.get('title')!r} is not Creative Commons (licence: {license_name or 'standard'})."
        )

    video_id = meta.get("id") or "unknown"
    # yt-dlp fills in the real extension; m4a keeps AAC without a re-encode.
    template = str(out_dir / f"{video_id}.%(ext)s")
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": template,
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "m4a", "preferredquality": "0"}
        ],
        "retries": 3,
        "fragment_retries": 3,
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = _flatten(ydl.extract_info(url, download=True))

    audio_path = _locate_audio(out_dir, video_id)
    log.info("downloaded %.1f MB -> %s", audio_path.stat().st_size / 1e6, audio_path.name)

    return MediaInfo(
        provider="youtube",
        provider_id=video_id,
        url=info.get("webpage_url") or url,
        title=info.get("title") or f"Untitled {video_id}",
        channel=info.get("channel") or info.get("uploader"),
        uploader_url=info.get("channel_url") or info.get("uploader_url"),
        duration_s=float(duration) if duration else None,
        license_name=license_name,
        upload_date=info.get("upload_date"),
        description=(info.get("description") or "")[:4000] or None,
        audio_path=audio_path,
    )


def _locate_audio(out_dir: Path, video_id: str) -> Path:
    candidates = sorted(
        (p for p in out_dir.glob(f"{video_id}.*") if p.suffix.lower() != ".part"),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    if not candidates:
        raise IngestError(f"download finished but no audio file found for {video_id} in {out_dir}")
    return candidates[0]
