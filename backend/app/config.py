"""Runtime configuration, loaded from environment variables / .env."""

import os
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    # --- LLM ---
    llm_provider: Literal["ollama", "google"] = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_num_ctx: int = 8192
    google_api_key: str | None = None
    gemini_model: str = "gemini-3-flash-preview"
    llm_temperature: float = 0.2
    structured_retries: int = 2

    # --- Search ---
    tavily_api_key: str | None = None
    results_per_query: int = 4
    sources_per_question: int = 3
    max_source_chars: int = 12_000  # stored per source for verification
    llm_source_chars: int = 2_800  # fed to the LLM per source (small-context friendly)

    # --- Graph budget ---
    max_sub_questions: int = Field(default=4, ge=1, le=6)
    max_revisions: int = Field(default=2, ge=0, le=4)
    max_parallel_researchers: int = 4
    # Vercel functions stop at 300s, so stay under that there; local Ollama runs need far longer.
    run_deadline_s: int = Field(default_factory=lambda: 270 if os.environ.get("VERCEL") else 1200)
    verify_threshold: int = 80  # rapidfuzz partial_ratio to accept an evidence quote

    # --- Infra ---
    database_url: str = "sqlite+aiosqlite:///./deep_synth.db"
    cors_origins: str = "http://localhost:3000"
    rate_limit_per_hour: int = 20
    # Only trust X-Forwarded-For / X-Real-IP behind a proxy that overwrites them (Vercel does).
    trust_proxy_headers: bool = Field(default_factory=lambda: bool(os.environ.get("VERCEL")))

    # --- Observability ---
    phoenix_collector_endpoint: str | None = None
    phoenix_api_key: str | None = None
    phoenix_project_name: str = "deep-synth"

    @property
    def model_name(self) -> str:
        return self.ollama_model if self.llm_provider == "ollama" else self.gemini_model


@lru_cache
def get_settings() -> Settings:
    return Settings()
