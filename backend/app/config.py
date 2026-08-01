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

    # --- object storage ---
    # "local" keeps audio under data_dir and serves it from /media (offline dev).
    # "supabase" uploads to a private bucket and serves short-lived signed URLs.
    storage_backend: str = "local"
    supabase_url: str | None = None
    # service_role key: bypasses RLS, full access. Server-side only, never the frontend.
    supabase_service_key: str | None = None
    supabase_bucket: str = "audio"
    # Signed URLs are minted per request. An hour outlives any single listening session
    # while keeping links from being shareable indefinitely.
    signed_url_ttl_s: int = 3600
    # Delete local working files once their objects are confirmed in the store. Only ever
    # applies to a cloud backend — under local storage those files ARE the library.
    cleanup_local_after_upload: bool = True

    # --- auth (Supabase Auth) ---
    # The project signs access tokens with ES256 and publishes the verifying public key at
    # {supabase_url}/auth/v1/.well-known/jwks.json, so the API validates them offline and needs
    # no shared secret of its own — there is deliberately no SUPABASE_JWT_SECRET here.
    #
    # The anon key is the browser's *publishable* key. Unlike supabase_service_key it does not
    # bypass RLS, and it is meant to ship to clients; it lives here only so /api/auth/config can
    # hand it to the frontend, which keeps SUPABASE_URL configured in exactly one place.
    supabase_anon_key: str | None = None
    # How long a fetched JWKS is reused. Ten minutes bounds how long a rotated-out key keeps
    # verifying while still costing at most one key fetch per ten minutes of traffic.
    auth_jwks_ttl_s: int = 600

    # --- entitlements ---
    # Listening units a learner may open, by tier. Premium is unlimited and has no setting.
    free_unit_limit: int = 2
    member_unit_limit: int = 5

    # --- billing (Stripe) ---
    # All three are absent by default and the billing endpoints answer 503 until they are set,
    # so the app runs perfectly well with no Stripe account at all — only the upgrade path is
    # unavailable. Secret key and webhook secret are server-side only.
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_price_id: str | None = None
    # Where Stripe returns the learner after checkout. Also the base for email links.
    app_base_url: str = "http://localhost:5173"

    # --- api ---
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def auth_enabled(self) -> bool:
        """Whether sign-in can work at all. Without a project URL there is nothing to verify
        tokens against, so the API stays anonymous-only rather than rejecting every caller."""
        return bool(self.supabase_url and self.supabase_anon_key)

    @property
    def billing_enabled(self) -> bool:
        return bool(self.stripe_secret_key and self.stripe_price_id)

    @property
    def jwks_url(self) -> str | None:
        if not self.supabase_url:
            return None
        return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"

    @property
    def auth_issuer(self) -> str | None:
        if not self.supabase_url:
            return None
        return f"{self.supabase_url.rstrip('/')}/auth/v1"

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
