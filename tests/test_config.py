"""Configuration tests.

Deterministic, no LLM, runs on every PR (CLAUDE.md §7, "unit" level).

What is tested here is not trivial: that configuration **fails at startup** when a
credential is missing, instead of dying halfway through a 40-ticket evaluation run.
A late failure wastes both budget and wall-clock time.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from supportops.config import Settings


def _settings(**overrides: object) -> Settings:
    """Build Settings without reading the real .env, to keep tests isolated."""
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


class TestFailFast:
    """Configuration must reject invalid states at startup."""

    def test_openai_without_api_key_fails(self) -> None:
        with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
            _settings(llm_provider="openai", openai_api_key=None)

    def test_openai_with_api_key_is_valid(self) -> None:
        settings = _settings(llm_provider="openai", openai_api_key="sk-test")
        assert settings.llm_provider == "openai"

    def test_bedrock_does_not_require_openai_key(self) -> None:
        """Bedrock authenticates with AWS credentials, not an OpenAI API key."""
        settings = _settings(llm_provider="bedrock", openai_api_key=None)
        assert settings.llm_provider == "bedrock"

    @pytest.mark.parametrize("value", [0, -1, 51])
    def test_max_agent_steps_out_of_range_fails(self, value: int) -> None:
        """A loop cutoff of 0 never executes; an absurdly high one defeats the point."""
        with pytest.raises(ValidationError):
            _settings(openai_api_key="sk-test", max_agent_steps=value)


class TestModelResolution:
    """The right model ID for a given provider and role."""

    def test_resolves_openai_ids(self) -> None:
        settings = _settings(
            llm_provider="openai",
            openai_api_key="sk-test",
            openai_agent_model="agent-model",
            openai_judge_model="judge-model",
        )
        assert settings.agent_model == "agent-model"
        assert settings.judge_model == "judge-model"

    def test_resolves_bedrock_ids(self) -> None:
        settings = _settings(
            llm_provider="bedrock",
            bedrock_agent_model="anthropic.agent",
            bedrock_judge_model="anthropic.judge",
        )
        assert settings.agent_model == "anthropic.agent"
        assert settings.judge_model == "anthropic.judge"

    def test_agent_and_judge_can_differ(self) -> None:
        """D-002: the judge must be a different model from the agent.

        This does not enforce the policy (IDs come from .env); it verifies the
        configuration can *express* it -- that agent and judge are independent
        fields rather than one shared value.
        """
        settings = _settings(
            openai_api_key="sk-test",
            openai_agent_model="model-a",
            openai_judge_model="model-b",
        )
        assert settings.agent_model != settings.judge_model


class TestSecurity:
    """Security invariants of the configuration."""

    def test_canary_is_disabled_by_default(self) -> None:
        """D-012: the canary deliberately exposes `customer_id` to the LLM.

        If that default ever flips to True this test fails -- which is its entire
        reason for existing.
        """
        assert _settings(openai_api_key="sk-test").insecure_canary is False

    def test_api_key_does_not_leak_in_repr(self) -> None:
        """CLAUDE.md §3 R5: the key must not surface in logs or error traces.

        `SecretStr` masks it in any repr/str -- which is exactly what ends up pasted
        into a support ticket or a CI log.
        """
        settings = _settings(openai_api_key="sk-super-secret")
        assert "sk-super-secret" not in repr(settings)
        assert "sk-super-secret" not in str(settings.openai_api_key)
        # And it is still explicitly retrievable where genuinely needed.
        assert settings.openai_api_key is not None
        assert settings.openai_api_key.get_secret_value() == "sk-super-secret"
