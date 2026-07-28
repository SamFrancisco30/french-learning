"""ffmpeg-backed audio utilities: normalization, silence-aware chunking, clip extraction."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# OpenAI's audio endpoints cap uploads at 25 MB; leave headroom for multipart overhead.
MAX_UPLOAD_BYTES = 24 * 1024 * 1024

ASR_SAMPLE_RATE = 16_000  # what Whisper-family models expect internally
UPLOAD_BITRATE_KBPS = 96  # 16 kHz mono mp3 @96k ~= 12 kB/s -> ~34 min per 24 MB chunk


class AudioError(RuntimeError):
    pass


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise AudioError(f"{tool} not found on PATH. Install ffmpeg (brew install ffmpeg).")
    return path


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    log.debug("run: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise AudioError(f"{cmd[0]} failed ({proc.returncode}):\n{proc.stderr[-2000:]}")
    return proc


def probe_duration(path: Path) -> float:
    """Duration in seconds."""
    out = _run(
        [
            _require("ffprobe"),
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(path),
        ]
    ).stdout
    try:
        return float(json.loads(out)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise AudioError(f"could not read duration of {path}") from exc


def to_asr_wav(src: Path, dst: Path) -> Path:
    """16 kHz mono 16-bit PCM — the canonical input for local faster-whisper."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            _require("ffmpeg"), "-y", "-i", str(src),
            "-vn",
            "-ac", "1",
            "-ar", str(ASR_SAMPLE_RATE),
            "-c:a", "pcm_s16le",
            str(dst),
        ]
    )
    return dst


def to_upload_mp3(src: Path, dst: Path) -> Path:
    """16 kHz mono mp3 — small enough to upload, lossless enough for ASR."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            _require("ffmpeg"), "-y", "-i", str(src),
            "-vn",
            "-ac", "1",
            "-ar", str(ASR_SAMPLE_RATE),
            "-c:a", "libmp3lame",
            "-b:a", f"{UPLOAD_BITRATE_KBPS}k",
            str(dst),
        ]
    )
    return dst


def extract_clip(src: Path, start_s: float, end_s: float, dst: Path) -> Path:
    """Cut [start, end) to a web-playable AAC/m4a clip.

    `-ss` before `-i` seeks fast; re-encoding (rather than stream copy) keeps the
    cut frame-accurate, which matters because exercise timings point into these clips.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.05, end_s - start_s)
    _run(
        [
            _require("ffmpeg"), "-y",
            "-ss", f"{max(0.0, start_s):.3f}",
            "-t", f"{duration:.3f}",
            "-i", str(src),
            "-vn",
            "-ac", "1",
            "-ar", "44100",
            "-c:a", "aac",
            "-b:a", "96k",
            "-movflags", "+faststart",
            str(dst),
        ]
    )
    return dst


# ---------------------------------------------------------------- silence detection


@dataclass(frozen=True)
class Silence:
    start: float
    end: float

    @property
    def mid(self) -> float:
        return (self.start + self.end) / 2.0


_SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


def detect_silences(path: Path, noise_db: int = -32, min_dur: float = 0.35) -> list[Silence]:
    """Find silent stretches, used to place chunk boundaries between sentences."""
    proc = subprocess.run(
        [
            _require("ffmpeg"), "-i", str(path),
            "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
    )
    # silencedetect writes to stderr; a non-zero exit here just means no usable output.
    starts: list[float] = [float(m) for m in _SIL_START.findall(proc.stderr)]
    ends: list[float] = [float(m) for m in _SIL_END.findall(proc.stderr)]
    silences = [Silence(s, e) for s, e in zip(starts, ends) if e > s]
    log.debug("detected %d silences in %s", len(silences), path.name)
    return silences


# ---------------------------------------------------------------- chunking


@dataclass(frozen=True)
class AudioChunk:
    path: Path
    offset_s: float  # where this chunk starts in the original timeline
    duration_s: float


def _pick_cut(target: float, silences: list[Silence], window: float, hard_limit: float) -> float:
    """Nearest silence midpoint to `target` within +/- window, else `target`."""
    best: float | None = None
    best_dist = window
    for sil in silences:
        mid = sil.mid
        dist = abs(mid - target)
        if dist <= best_dist and mid < hard_limit:
            best, best_dist = mid, dist
    return best if best is not None else target


def chunk_for_upload(
    src: Path,
    out_dir: Path,
    *,
    max_bytes: int = MAX_UPLOAD_BYTES,
    stem: str = "chunk",
) -> list[AudioChunk]:
    """Split `src` into upload-sized pieces, cutting on silence where possible.

    Returns one chunk covering the whole file when it already fits. Each chunk records
    its offset in the original timeline so ASR timestamps can be shifted back.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    total = probe_duration(src)
    size = src.stat().st_size

    if size <= max_bytes:
        return [AudioChunk(path=src, offset_s=0.0, duration_s=total)]

    # Derive a safe chunk length from the actual byte rate, then trim 10% for jitter.
    bytes_per_sec = size / max(total, 0.001)
    target_len = (max_bytes / bytes_per_sec) * 0.90
    silences = detect_silences(src)

    chunks: list[AudioChunk] = []
    cursor = 0.0
    idx = 0
    while cursor < total - 0.05:
        raw_end = min(cursor + target_len, total)
        if raw_end >= total - 0.05:
            end = total
        else:
            end = _pick_cut(
                raw_end,
                [s for s in silences if cursor + target_len * 0.5 < s.mid < raw_end + target_len * 0.1],
                window=target_len * 0.15,
                hard_limit=min(cursor + target_len, total),
            )
            end = max(end, cursor + target_len * 0.5)  # never emit a runt chunk

        dst = out_dir / f"{stem}_{idx:03d}.mp3"
        _run(
            [
                _require("ffmpeg"), "-y",
                "-ss", f"{cursor:.3f}",
                "-t", f"{max(0.05, end - cursor):.3f}",
                "-i", str(src),
                "-vn", "-ac", "1", "-ar", str(ASR_SAMPLE_RATE),
                "-c:a", "libmp3lame", "-b:a", f"{UPLOAD_BITRATE_KBPS}k",
                str(dst),
            ]
        )
        chunks.append(AudioChunk(path=dst, offset_s=cursor, duration_s=end - cursor))
        cursor = end
        idx += 1

    log.info("split %s into %d chunks", src.name, len(chunks))
    return chunks
