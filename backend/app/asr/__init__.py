"""ASR backend factory."""

from __future__ import annotations

from ..config import settings
from .base import ASRResult, ASRSegment, Transcriber, Word

# Nudges the model toward correct orthography for the target language: accents,
# punctuation and register. Whisper conditions on this as a style hint.
ASR_PROMPTS: dict[str, str] = {
    "fr": (
        "Transcription en français standard, avec la ponctuation, les majuscules "
        "et tous les accents (é, è, ê, à, ù, ç). Vocabulaire d'actualité, de "
        "géographie et de sciences."
    ),
    "ru": "Транскрипция на литературном русском языке, со знаками препинания.",
    "zh": "使用简体中文转写，包含标点符号。",
}


def get_transcriber(backend: str | None = None, model: str | None = None) -> Transcriber:
    name = (backend or settings.asr_backend).lower()
    if name in {"openai", "api", "whisper-1"}:
        from .openai_whisper import OpenAIWhisperTranscriber

        return OpenAIWhisperTranscriber(model=model)
    if name in {"local", "faster-whisper", "faster_whisper"}:
        from .faster_whisper_local import FasterWhisperTranscriber

        return FasterWhisperTranscriber(model=model)
    raise ValueError(f"Unknown ASR backend {name!r}. Use 'openai' or 'local'.")


def prompt_for(language: str) -> str | None:
    return ASR_PROMPTS.get(language)


__all__ = [
    "ASRResult",
    "ASRSegment",
    "Transcriber",
    "Word",
    "get_transcriber",
    "prompt_for",
]
