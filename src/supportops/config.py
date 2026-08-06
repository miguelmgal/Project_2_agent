"""Project configuration, loaded from environment variables.

CLAUDE.md §3 R5: no secrets in the repository. Everything arrives via the
environment or `.env` (which is git-ignored). If a required variable is missing the
process fails at startup with a clear message -- never halfway through an
evaluation run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProvider = Literal["openai", "bedrock"]

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Global configuration. Instantiated once through `get_settings()`."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Immutable: nothing may mutate configuration mid-run and skew results.
        frozen=True,
    )

    # ----------------------------------------------------------------- provider
    # D-011: the model layer is swappable. See src/supportops/llm.py.
    llm_provider: LLMProvider = "openai"

    # --- OpenAI ---
    openai_api_key: SecretStr | None = None
    # Model IDs are set in .env after verifying them with `client.models.list()`
    # (BITACORA.md -> "Versiones verificadas"). No invented defaults: a wrong
    # default that looks plausible fails in confusing ways.
    openai_agent_model: str = ""
    openai_judge_model: str = ""

    # --- Bedrock (no access yet; wired up and ready to migrate) ---
    aws_region: str = "us-east-1"
    bedrock_agent_model: str = "anthropic.claude-sonnet-5"
    bedrock_judge_model: str = "anthropic.claude-opus-5"

    # ------------------------------------------------------------ observability
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "supportops-agent"

    # -------------------------------------------------------------------- agent
    # Infinite-loop cutoff: the GUARD node escalates to a human once exceeded.
    max_agent_steps: int = Field(default=8, ge=1, le=50)

    # --------------------------------------------------------------------- data
    db_path: Path = PROJECT_ROOT / "supportops.db"
    knowledge_base_path: Path = PROJECT_ROOT / "env" / "knowledge_base"
    faker_seed: int = 42

    # --------------------------------------------------------------- evaluation
    eval_runs_per_ticket: int = Field(default=3, ge=1, le=10)
    """K in the measurement protocol: gates apply to the mean of K runs because the
    system is not deterministic (BITACORA.md -> P-003)."""

    # ----------------------------------------------------------------- security
    insecure_canary: bool = False
    """D-012: exposes `customer_id` to the LLM in order to MEASURE cross-customer
    access attempts. Red-teaming suite only. Never in production, never by default."""

    @model_validator(mode="after")
    def _validate_provider_credentials(self) -> Settings:
        """Fail at startup rather than halfway through a run."""
        if self.llm_provider == "openai" and self.openai_api_key is None:
            msg = (
                "LLM_PROVIDER=openai requires OPENAI_API_KEY. "
                "Copy .env.example to .env and fill it in."
            )
            raise ValueError(msg)
        return self

    @property
    def agent_model(self) -> str:
        """ID of the model that reasons in the REASON node."""
        return (
            self.openai_agent_model if self.llm_provider == "openai" else self.bedrock_agent_model
        )

    @property
    def judge_model(self) -> str:
        """ID of the judge model used by the evaluation suite.

        D-002: MUST be a different, more capable model than `agent_model`. Reusing
        the same model introduces self-preference bias and invalidates the task
        completion metric.
        """
        return (
            self.openai_judge_model if self.llm_provider == "openai" else self.bedrock_judge_model
        )


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the configuration (lazy singleton)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
