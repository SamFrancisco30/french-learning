"""Application settings, loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM ---
    openai_api_key: str | None = None
    llm_model: str = "gpt-4o"

    # --- ASR ---
    asr_backend: str = "openai"  # "openai" | "local"
    asr_openai_model: str = "whisper-1"
    asr_local_model: str = "large-v3"
    asr_local_device: str = "auto"
    asr_local_compute_type: str = "int8"

    # --- storage ---
    data_dir: Path = BACKEND_ROOT / "data"
    database_url: str = "sqlite:///./data/polyglot.sqlite"

    # --- api ---
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def clip_dir(self) -> Path:
        return self.data_dir / "clips"

    @property
    def transcript_dir(self) -> Path:
        return self.data_dir / "transcripts"

    def model_post_init(self, __context) -> None:
        # Resolve DATA_DIR against the backend root, not the process cwd, so the CLI
        # and the API server always agree on where media lives.
        if not self.data_dir.is_absolute():
            object.__setattr__(self, "data_dir", (BACKEND_ROOT / self.data_dir).resolve())

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.audio_dir, self.clip_dir, self.transcript_dir):
            d.mkdir(parents=True, exist_ok=True)

    def resolved_database_url(self) -> str:
        """Make relative sqlite paths absolute so the CLI and the server agree on one DB."""
        prefix = "sqlite:///"
        if self.database_url.startswith(prefix):
            raw = self.database_url[len(prefix) :]
            if not raw.startswith("/"):
                return prefix + str((BACKEND_ROOT / raw).resolve())
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s


settings = get_settings()
