"""Local faster-whisper backend — the production default.

Why this is the better choice once you're past testing:
  * large-v3 has materially lower French WER than whisper-1 (which is large-v2).
  * Word timestamps come straight from the model, no re-joining needed.
  * No per-minute cost and no audio leaving the machine.
  * Built-in VAD drops music stings and dead air that otherwise produce hallucinated
    text in news intros.

Install with:  uv pip install -e '.[local-asr]'
On Apple Silicon, CTranslate2 has no Metal backend, so this runs on CPU with int8
quantization — still roughly real-time on an M-series chip for large-v3.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import settings
from ..media import audio as audio_utils
from .base import ASRResult, ASRSegment, Transcriber, Word

log = logging.getLogger(__name__)


def _resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception:  # noqa: BLE001 - ctranslate2 absent or CUDA probe failed
        pass
    return "cpu"


class FasterWhisperTranscriber(Transcriber):
    name = "local"

    def __init__(
        self,
        model: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
    ) -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "faster-whisper is not installed. Run: uv pip install -e '.[local-asr]'"
            ) from exc

        self.model = model or settings.asr_local_model
        device = _resolve_device(device or settings.asr_local_device)
        compute = compute_type or settings.asr_local_compute_type
        if device == "cuda" and compute == "int8":
            compute = "float16"  # int8 on GPU costs accuracy for no real speedup

        log.info("loading faster-whisper %s on %s (%s)", self.model, device, compute)
        self._model = WhisperModel(
            self.model,
            device=device,
            compute_type=compute,
            download_root=str(settings.data_dir / "models"),
        )

    def transcribe(
        self, audio_path: Path, *, language: str, prompt: str | None = None
    ) -> ASRResult:
        # No upload limit here, so no chunking — just normalize to 16 kHz mono.
        wav = settings.audio_dir / f"{audio_path.stem}.asr.wav"
        if not wav.exists():
            audio_utils.to_asr_wav(audio_path, wav)

        seg_iter, info = self._model.transcribe(
            str(wav),
            language=language,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            beam_size=5,
            condition_on_previous_text=False,  # avoids repetition loops on long news audio
            initial_prompt=prompt,
        )

        segments: list[ASRSegment] = []
        for i, s in enumerate(seg_iter):
            words = [
                Word(
                    text=(w.word or "").strip(),
                    start=float(w.start),
                    end=float(w.end),
                    probability=float(w.probability) if w.probability is not None else None,
                )
                for w in (s.words or [])
                if (w.word or "").strip()
            ]
            segments.append(
                ASRSegment(
                    idx=i,
                    start=float(s.start),
                    end=float(s.end),
                    text=(s.text or "").strip(),
                    words=words,
                    avg_logprob=float(s.avg_logprob) if s.avg_logprob is not None else None,
                    no_speech_prob=(
                        float(s.no_speech_prob) if s.no_speech_prob is not None else None
                    ),
                )
            )

        return ASRResult(
            text=" ".join(s.text for s in segments).strip(),
            segments=segments,
            language=info.language or language,
            duration_s=float(info.duration or (segments[-1].end if segments else 0.0)),
            backend=self.name,
            model=self.model,
        )
