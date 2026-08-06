"""Provider-agnostic model layer.

**D-011.** This is the ONLY file that knows which LLM provider is in use. The graph,
the tools and the whole evaluation suite are indifferent: they receive a
`BaseChatModel` and never ask where it came from.

Why it matters:

1. There is no Bedrock access yet, so the project starts on OpenAI. When access
   arrives, migrating is an environment variable -- not an agent rewrite.
2. It enables **model-swap regression testing**: running the same evaluation suite
   against two providers and comparing metrics. That is the real problem companies
   face whenever a new model ships ("can I migrate without breaking anything?").

WARNING -- CLAUDE.md §3 R4: never pass `temperature`, `top_p` or `top_k`.
   - Claude 5 rejects them with a 400 error.
   - OpenAI reasoning models restrict them too.
   Use `effort`/`reasoning` to control reasoning depth and cost; use prompting to
   steer behaviour.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, assert_never, cast

from supportops.config import get_settings

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel


class ModelRole(StrEnum):
    """What the model is used for. Determines which ID is resolved."""

    AGENT = "agent"
    """The model that reasons in the REASON node."""

    JUDGE = "judge"
    """The model that scores evaluations. D-002: different from and more capable
    than AGENT."""


def build_chat_model(role: ModelRole = ModelRole.AGENT) -> BaseChatModel:
    """Build the chat model for the configured provider.

    Args:
        role: whether this is the agent's model or the evaluation judge.

    Returns:
        A `BaseChatModel` ready for `.bind_tools()`.

    Raises:
        ValueError: if the model ID is not configured.
    """
    settings = get_settings()
    model_id = settings.agent_model if role is ModelRole.AGENT else settings.judge_model

    if not model_id:
        msg = (
            f"No model ID configured for role '{role}' with "
            f"LLM_PROVIDER={settings.llm_provider}. Discover the available models with "
            f"`uv run python -m scripts.spike_llm` and set them in .env."
        )
        raise ValueError(msg)

    match settings.llm_provider:
        case "openai":
            from langchain_openai import ChatOpenAI

            api_key = settings.openai_api_key
            if api_key is None:  # pragma: no cover -- Settings validates this at startup
                msg = "OPENAI_API_KEY is not configured."
                raise ValueError(msg)

            # No temperature/top_p: see R4 above.
            return ChatOpenAI(model=model_id, api_key=api_key, timeout=120)

        case "bedrock":
            # Requires `uv sync --extra bedrock`.
            from langchain_aws import ChatBedrockConverse

            # `langchain-aws` ships incomplete types; the cast documents the contract.
            return cast(
                "BaseChatModel",
                ChatBedrockConverse(model=model_id, region_name=settings.aws_region),
            )

        case unreachable:  # pragma: no cover
            # Exhaustiveness checked by mypy: adding a provider to `LLMProvider`
            # without handling it here is a type error, not a runtime surprise.
            assert_never(unreachable)
