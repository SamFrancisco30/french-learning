"""OpenAI hosted Whisper backend.

Model choice: `whisper-1`, deliberately, even though `gpt-4o-transcribe` scores a
lower WER. The newer transcription models only emit `json`/`text` response formats —
no `verbose_json`, so no word or segment timestamps — and this pipeline is built
around timestamp-anchored exercises. `whisper-1` supports
`timestamp_granularities=["word", "segment"]`, so it's the one that fits.

Use the `local` backend (faster-whisper large-v3) when you want better accuracy
than whisper-1 *and* keep timestamps.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from ..config import settings
from ..media import audio as audio_utils
from .base import ASRResult, ASRSegment, Transcriber, Word, attach_words_to_segments, stitch

log = logging.getLogger(__name__)

MAX_RETRIES = 4


class OpenAIWhisperTranscriber(Transcriber):
    name = "openai"

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        from openai import OpenAI

        key = api_key or settings.openai_api_key
        if not key:
            raise RuntimeError("OPENAI_API_KEY is not set (see backend/.env.example)")
        self.model = model or settings.asr_openai_model
        self._client = OpenAI(api_key=key, timeout=600.0)

    def transcribe(
        self, audio_path: Path, *, language: str, prompt: str | None = None
    ) -> ASRResult:
        work_dir = settings.audio_dir / f"{audio_path.stem}_chunks"
        upload = settings.audio_dir / f"{audio_path.stem}.upload.mp3"

        if not upload.exists():
            audio_utils.to_upload_mp3(audio_path, upload)

        chunks = audio_utils.chunk_for_upload(upload, work_dir, stem=audio_path.stem)
        log.info(
            "transcribing %s via %s in %d chunk(s)", audio_path.name, self.model, len(chunks)
        )

        pieces: list[tuple[ASRResult, float]] = []
        for i, chunk in enumerate(chunks, 1):
            log.info("  chunk %d/%d (offset %.1fs)", i, len(chunks), chunk.offset_s)
            pieces.append((self._transcribe_one(chunk.path, language, prompt), chunk.offset_s))

        return stitch(pieces)

    def _transcribe_one(self, path: Path, language: str, prompt: str | None) -> ASRResult:
        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                with path.open("rb") as fh:
                    resp = self._client.audio.transcriptions.create(
                        model=self.model,
                        file=fh,
                        language=language,
                        response_format="verbose_json",
                        timestamp_granularities=["word", "segment"],
                        **({"prompt": prompt} if prompt else {}),
                    )
                return self._parse(resp)
            except Exception as exc:  # noqa: BLE001 - retry any transport/rate-limit error
                last_exc = exc
                if attempt == MAX_RETRIES:
                    break
                backoff = 2.0**attempt
                log.warning("chunk failed (%s), retrying in %.0fs", exc, backoff)
                time.sleep(backoff)
        raise RuntimeError(f"transcription failed for {path.name}: {last_exc}") from last_exc

    def _parse(self, resp) -> ASRResult:
        raw_segments = getattr(resp, "segments", None) or []
        raw_words = getattr(resp, "words", None) or []

        segments = [
            ASRSegment(
                idx=i,
                start=float(getattr(s, "start", 0.0) or 0.0),
                end=float(getattr(s, "end", 0.0) or 0.0),
                text=(getattr(s, "text", "") or "").strip(),
                avg_logprob=_maybe_float(getattr(s, "avg_logprob", None)),
                no_speech_prob=_maybe_float(getattr(s, "no_speech_prob", None)),
            )
            for i, s in enumerate(raw_segments)
        ]
        words = [
            Word(
                text=(getattr(w, "word", "") or "").strip(),
                start=float(getattr(w, "start", 0.0) or 0.0),
                end=float(getattr(w, "end", 0.0) or 0.0),
                probability=_maybe_float(getattr(w, "probability", None)),
            )
            for w in raw_words
            if (getattr(w, "word", "") or "").strip()
        ]

        text = (getattr(resp, "text", "") or "").strip()
        duration = float(getattr(resp, "duration", 0.0) or 0.0)

        if not segments and words:
            # Degenerate response: synthesize one segment so downstream code still works.
            segments = [ASRSegment(idx=0, start=words[0].start, end=words[-1].end, text=text)]

        attach_words_to_segments(segments, words)
        return ASRResult(
            text=text,
            segments=segments,
            language=str(getattr(resp, "language", "") or ""),
            duration_s=duration or (segments[-1].end if segments else 0.0),
            backend=self.name,
            model=self.model,
        )


def _maybe_float(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None
