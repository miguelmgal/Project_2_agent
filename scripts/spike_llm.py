"""LLM provider integration spike -- Phase 0.

Answers, with real data, the questions that block the rest of the project:

  1. Which model IDs are available?      (to set agent + judge in .env)
  2. Does tool-calling work via LangChain? (the whole agent depends on this)
  3. Is `temperature` accepted?          -> P-001
  4. Is `seed` accepted (reproducibility)? -> P-003
  5. Which exact versions am I running?  (for BITACORA.md -> "Versiones verificadas")

Usage:
    uv run python -m scripts.spike_llm                 # full diagnostic
    uv run python -m scripts.spike_llm --model <id>    # probe a specific model

Results are recorded by hand in BITACORA.md. This is not a test: it is a one-off
exploratory diagnostic, which is why it lives in scripts/ and not tests/. A test
asserts something that must stay true; this answers a question once.
"""

from __future__ import annotations

import argparse
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from pydantic import BaseModel, Field

from supportops.config import get_settings

# ------------------------------------------------------------------------- helpers


def _h(title: str) -> None:
    print(f"\n{'=' * 70}\n  {title}\n{'=' * 70}")


def _api_key() -> str:
    """Read the API key through the project's own configuration layer.

    P-004: a `.env` file is not the same thing as an environment variable. The
    OpenAI SDK reads `os.environ` and never looks at `.env`, so constructing
    `OpenAI()` with no arguments raises AuthenticationError even when the key sits
    right there in the file. `pydantic-settings` is what actually loads `.env`, so
    the key is read from Settings and passed to the clients explicitly.

    Going through Settings rather than `load_dotenv()` also means this spike
    exercises the same credential path as the rest of the project -- one way to
    read secrets, not two.
    """
    key = get_settings().openai_api_key
    if key is None:  # pragma: no cover -- Settings validates this at startup
        msg = "OPENAI_API_KEY is not configured. Copy .env.example to .env."
        raise ValueError(msg)
    return key.get_secret_value()


def _pkg(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "(not installed)"


# --------------------------------------------------------------------------- steps


def report_versions() -> None:
    """Step 5: exact versions, for the reproducibility table."""
    _h("Installed versions")
    print(f"  python           {sys.version.split()[0]}")
    for pkg in (
        "langgraph",
        "langchain",
        "langchain-core",
        "langchain-openai",
        "openai",
        "pydantic",
        "langsmith",
        "agentevals",
        "deepeval",
        "faker",
    ):
        print(f"  {pkg:<17}{_pkg(pkg)}")


def list_models(limit: int = 40) -> list[str]:
    """Step 1: which models this API key can reach.

    IDs are read from the provider, never invented, then pinned in .env.
    """
    _h("Available models")
    from openai import OpenAI

    client = OpenAI(api_key=_api_key())
    ids = sorted(m.id for m in client.models.list())

    # Heuristic filter: chat/reasoning models are the candidates we care about.
    excluded = ("embedding", "whisper", "tts", "dall-e", "moderation", "audio", "image")
    chat_like = [i for i in ids if not any(x in i for x in excluded)]

    print(f"  {len(ids)} models total; {len(chat_like)} chat candidates:\n")
    for model_id in chat_like[:limit]:
        print(f"    {model_id}")
    if len(chat_like) > limit:
        print(f"    ... and {len(chat_like) - limit} more")
    return chat_like


class GetOrderStatusArgs(BaseModel):
    """Probe schema mirroring the real tool.

    Note what is NOT here: `customer_id`. CLAUDE.md §3 R1 -- customer identity is
    injected from graph state and the LLM never sees it.
    """

    order_id: str = Field(description="The order identifier, e.g. ORD-1042")


def test_tool_calling(model_id: str) -> bool:
    """Step 2: tool-calling works end to end through LangChain."""
    _h(f"Tool-calling with '{model_id}'")
    from langchain_core.tools import StructuredTool
    from langchain_openai import ChatOpenAI

    def _get_order_status(order_id: str) -> str:
        return f"Order {order_id} is in transit."

    tool = StructuredTool.from_function(
        func=_get_order_status,
        name="get_order_status",
        description="Look up the status of an order for the authenticated customer.",
        args_schema=GetOrderStatusArgs,
    )

    llm = ChatOpenAI(model=model_id, api_key=_api_key(), timeout=120).bind_tools([tool])
    response = llm.invoke("Where is my order ORD-1042?")

    calls: list[dict[str, Any]] = getattr(response, "tool_calls", []) or []
    if not calls:
        print("  FAIL -- the model did not request the tool. Text returned:")
        print(f"     {str(response.content)[:300]}")
        return False

    print(f"  OK -- {len(calls)} tool call(s):")
    for call in calls:
        print(f"       name: {call.get('name')}")
        print(f"       args: {call.get('args')}")

    if any("customer_id" in (c.get("args") or {}) for c in calls):
        print("  WARN -- the model invented `customer_id`; it is not in the schema.")
    return True


def probe_param(model_id: str, label: str, **kwargs: Any) -> bool:
    """Check whether the provider accepts a given parameter (P-001 / P-003)."""
    from langchain_openai import ChatOpenAI

    try:
        ChatOpenAI(model=model_id, api_key=_api_key(), timeout=60, **kwargs).invoke("Say OK.")
    except Exception as exc:
        # Broad catch on purpose: this is a diagnostic and we want the raw error
        # text verbatim, to paste into the BITACORA problem registry.
        print(f"  REJECTED -- {label}")
        print(f"       {type(exc).__name__}: {str(exc)[:220]}")
        return False
    print(f"  ACCEPTED -- {label}")
    return True


def test_sampling_params(model_id: str) -> None:
    """Steps 3 and 4: which determinism levers actually exist."""
    _h(f"Sampling parameters and determinism -- '{model_id}'")
    print("  P-001 -- is `temperature` accepted?")
    probe_param(model_id, "temperature=0", temperature=0)
    print("\n  P-003 -- is `seed` accepted (best-effort reproducibility)?")
    probe_param(model_id, "seed=42", model_kwargs={"seed": 42})
    # ASCII only: the Windows console defaults to cp1252 and mangles characters
    # like the section sign, which turns diagnostic output into noise (P-006).
    print(
        "\n  NOTE: even when `seed` is accepted it does NOT guarantee identical\n"
        "  outputs. The K=3 run protocol and the trajectory-stability metric stay\n"
        "  in place (see PLAN_IMPLEMENTACION.md section 6.4)."
    )


# ---------------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM integration spike (Phase 0)")
    parser.add_argument("--model", help="model ID to probe (omit to only list models)")
    parser.add_argument("--skip-models", action="store_true", help="skip model listing")
    args = parser.parse_args()

    report_versions()

    available: list[str] = []
    if not args.skip_models:
        try:
            available = list_models()
        except Exception as exc:
            # Same rationale as probe_param: surface the raw failure to the operator.
            print(f"\n  FAIL -- could not list models: {type(exc).__name__}: {exc}")
            print("     Is OPENAI_API_KEY set in the environment or in .env?")
            return 1

    target = args.model
    if target is None:
        _h("Next step")
        print(
            "  Pick from the list above:\n"
            "    * AGENT: fast and solid at tool-calling\n"
            "    * JUDGE: the most capable model you can reach\n"
            "             (D-002: must differ from the agent)\n\n"
            "  Then run:\n"
            "    uv run python -m scripts.spike_llm --model <agent-id>"
        )
        return 0

    if available and target not in available:
        print(f"\n  WARN -- '{target}' is not in the available list. Trying anyway.")

    ok = test_tool_calling(target)
    test_sampling_params(target)

    _h("Result")
    if ok:
        print("  Spike passed. Record in BITACORA.md:")
        print("       * versions (the 'Versiones verificadas' table)")
        print("       * outcome of P-001 and P-003")
        print("       * chosen agent and judge IDs -> .env")
    else:
        print("  Tool-calling failed. Try a different model before moving on.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
