"""Claude Haiku 5.5 call via the official Anthropic SDK (claude-haiku skill)."""

import logging
import os

from anthropic import AsyncAnthropic

from .models import ChatResponse

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-5-5"
MAX_TOKENS = 16000
OUTPUT_CONFIG = {"effort": "low"}

_client: AsyncAnthropic | None = None


class LLMError(Exception):
    """The LLM call failed, was refused, or was truncated (-> 502 LLM_ERROR)."""


def is_mock_mode() -> bool:
    return os.getenv("LLM_MOCK", "").strip().lower() == "true"


def is_available() -> bool:
    """True if a real LLM call can be attempted (API key present)."""
    return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())


def get_client() -> AsyncAnthropic:
    """Lazily create the shared async client (reads ANTHROPIC_API_KEY from env)."""
    global _client
    if _client is None:
        _client = AsyncAnthropic()
    return _client


def set_client(client: AsyncAnthropic | None) -> None:
    """Override the client (tests)."""
    global _client
    _client = client


async def call_llm(system: str, messages: list[dict]) -> ChatResponse:
    """Call Haiku with Structured Outputs. Raises LLMError on any failure."""
    try:
        response = await get_client().messages.parse(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=messages,
            output_config=OUTPUT_CONFIG,
            output_format=ChatResponse,
        )
    except Exception as exc:
        logger.warning("LLM call failed: %s", exc)
        raise LLMError(f"LLM call failed: {exc.__class__.__name__}") from exc

    if response.stop_reason == "refusal":
        raise LLMError("The assistant declined to respond to that request.")
    if response.stop_reason == "max_tokens":
        raise LLMError("The assistant's response was cut off.")

    parsed = response.parsed_output
    if parsed is None:
        raise LLMError("The assistant returned an unparseable response.")
    return parsed
