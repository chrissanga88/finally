"""LLM chat assistant: Claude Haiku 5.5 with Structured Outputs (PLAN §10)."""

from .client import MODEL, LLMError, call_llm, is_available, is_mock_mode
from .history import build_messages
from .mock import mock_response
from .models import ChatResponse, TradeRequest, WatchlistChange
from .prompt import build_system_prompt
from .service import ChatError, execute_actions, handle_chat

__all__ = [
    "MODEL",
    "ChatError",
    "ChatResponse",
    "LLMError",
    "TradeRequest",
    "WatchlistChange",
    "build_messages",
    "build_system_prompt",
    "call_llm",
    "execute_actions",
    "handle_chat",
    "is_available",
    "is_mock_mode",
    "mock_response",
]
