"""AI provider protocol for pluggable LLM backends.

Follows the same ABC pattern as notifications/base.py.
Each provider wraps a specific SDK and yields streaming text chunks.

Tool use support: providers that support function calling override
``supports_tools`` and ``stream_with_tools``. The tool/resource layer
is designed to be exposable as an MCP server in the future — tool
definitions and executors live in ``tools.py``, resources in
``resources.py``, decoupled from the HTTP transport.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AiUsageResult:
    """Token usage from a single AI request."""

    input_tokens: int
    output_tokens: int
    model: str


@dataclass(frozen=True, slots=True)
class ToolCallChunk:
    """A completed tool call emitted by the LLM during streaming."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """A single event from a provider stream — either text or a tool call.

    Exactly one of ``text`` or ``tool_call`` is set per event.
    """

    text: str | None = None
    tool_call: ToolCallChunk | None = None


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Provider-agnostic tool definition.

    ``parameters`` is a JSON Schema object describing the tool's input.
    Conversion to provider-specific formats (Anthropic ``input_schema``,
    OpenAI ``function.parameters``) happens in the provider implementations.

    NOTE: This layer is intentionally decoupled from the HTTP transport so
    it can later be exposed as an MCP server endpoint. See tools.py for the
    registry and executor implementations.
    """

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


class AIProvider(ABC):
    """Protocol for pluggable AI/LLM backends.

    Implementations: AnthropicProvider, OpenAICompatibleProvider, OllamaProvider.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier (e.g. 'anthropic', 'openai', 'ollama')."""
        ...

    @property
    @abstractmethod
    def model(self) -> str:
        """Model identifier currently configured."""
        ...

    @property
    def last_usage(self) -> AiUsageResult | None:
        """Token usage from the most recent stream_response call."""
        return getattr(self, "_last_usage", None)

    @property
    def supports_tools(self) -> bool:
        """Whether this provider supports tool use (function calling).

        Providers that return True must override ``stream_with_tools``.
        """
        return False

    @abstractmethod
    def stream_response(
        self,
        messages: list[dict[str, str]],
        system_prompt: str,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """Stream a response as text chunks.

        Yields plain text deltas. Caller is responsible for
        assembling the full response if needed.

        Args:
            messages: Conversation history (role/content dicts).
            system_prompt: System-level instructions.
            max_tokens: Maximum tokens in the response.

        Yields:
            Text chunks as they arrive from the provider.
        """
        ...

    async def stream_with_tools(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str,
        tools: list[ToolDefinition],  # noqa: ARG002
        max_tokens: int = 4096,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a response with tool calling support.

        Yields ``StreamEvent`` instances — either text deltas or completed
        tool calls. The agent loop (``agent_loop.py``) handles executing
        tools and feeding results back.

        Default implementation ignores tools and delegates to
        ``stream_response``, wrapping each text chunk in a ``StreamEvent``.
        Providers that support tools override this method.

        Args:
            messages: Conversation history (may include tool_result messages).
            system_prompt: System-level instructions.
            tools: Tool definitions (provider converts to its wire format).
            max_tokens: Maximum tokens in the response.
        """
        # Fallback for providers without tool support (e.g. Ollama):
        # strip to simple role/content dicts and stream text only.
        simple_messages = [
            {"role": m["role"], "content": m["content"]}
            for m in messages
            if isinstance(m.get("content"), str)
        ]
        async for chunk in self.stream_response(simple_messages, system_prompt, max_tokens):
            yield StreamEvent(text=chunk)

    async def close(self) -> None:  # noqa: B027
        """Clean up resources (e.g. httpx clients). Override if needed."""
