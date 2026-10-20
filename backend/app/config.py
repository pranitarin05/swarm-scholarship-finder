"""Application configuration loaded from .env (secrets never committed)."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime settings. All values come from environment / .env."""

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    groq_fallback_model: str = "qwen/qwen3.8-27b"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    tavily_api_key: str = ""
    serper_api_key: str = ""
    github_token: str = ""

    data_dir: Path = ROOT_DIR / "data"
    fetch_timeout: float = 20.0
    candidate_timeout: float = 45.0
    per_domain_delay: float = 1.0
    min_candidates: int = 10
    max_scout_loops: int = 2
    max_critic_rounds: int = 2

    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def demo_cache_dir(self) -> Path:
        return self.data_dir / "demo_cache"

    @property
    def sample_dir(self) -> Path:
        return self.data_dir / "sample_rahul"


settings = Settings()
