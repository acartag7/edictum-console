"""Anthropic AI provider — Claude models via the Anthropic SDK."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import structlog

from edictum_server.ai.base import (
    AIProvider,
    AiUsageResult,
    StreamEvent,
    ToolCallChunk,
    ToolDefinition,
)

try:
    import anthropic  # type: ignore[import-not-found]

    _HAS_ANTHROPIC = True
except ImportError:
    _HAS_ANTHROPIC = False

logger = structlog.get_logger(__name__)

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


class AnthropicProvider(AIProvider):
    """Streaming AI provider using the Anthropic Python SDK.

    Supports tool use via the Anthropic messages API. When tools are
    provided, the model may emit ``tool_use`` content blocks alongside
    text blocks. This provider translates both into ``StreamEvent``.

    Requires: ``pip install anthropic``
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
    ) -> None:
        if not _HAS_ANTHROPIC:
            raise RuntimeError(
                "anthropic package is not installed. Install it with: pip install anthropic"
            )
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._last_usage: AiUsageResult | None = None

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def model(self) -> str:
        return self._model

    @property
    def supports_tools(self) -> bool:
        return True

    async def stream_response(
        self,
        messages: list[dict[str, str]],
        system_prompt: str,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        async with self._client.messages.stream(
            model=self._model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text
            msg = await stream.get_final_message()
            self._last_usage = AiUsageResult(
                input_tokens=msg.usage.input_tokens,
                output_tokens=msg.usage.output_tokens,
                model=self._model,
            )

    async def stream_with_tools(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str,
        tools: list[ToolDefinition],
        max_tokens: int = 4096,
    ) -> AsyncIterator[StreamEvent]:
        """Stream with Anthropic tool use support.

        Anthropic emits content blocks: ``text`` blocks yield StreamEvent(text=),
        ``tool_use`` blocks accumulate input_json_delta and yield
        StreamEvent(tool_call=) on block stop.
        """
        anthropic_tools = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters,
            }
            for t in tools
        ]

        # Track tool_use blocks being accumulated
        current_tool_id: str | None = None
        current_tool_name: str | None = None
        current_tool_json: list[str] = []

        async with self._client.messages.stream(
            model=self._model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages,
            tools=anthropic_tools,
        ) as stream:
            async for event in stream:
                event_type = event.type

                if event_type == "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        current_tool_id = block.id
                        current_tool_name = block.name
                        current_tool_json = []

                elif event_type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        yield StreamEvent(text=delta.text)
                    elif delta.type == "input_json_delta":
                        current_tool_json.append(delta.partial_json)

                elif event_type == "content_block_stop":
                    if current_tool_id and current_tool_name:
                        raw_json = "".join(current_tool_json)
                        try:
                            arguments = json.loads(raw_json) if raw_json else {}
                        except json.JSONDecodeError:
                            logger.warning(
                                "tool_call_json_decode_error",
                                provider="anthropic",
                                model=self._model,
                                tool_name=current_tool_name,
                                raw_length=len(raw_json),
                            )
                            arguments = {"_raw": raw_json}
                        yield StreamEvent(
                            tool_call=ToolCallChunk(
                                id=current_tool_id,
                                name=current_tool_name,
                                arguments=arguments,
                            )
                        )
                        current_tool_id = None
                        current_tool_name = None
                        current_tool_json = []

            msg = await stream.get_final_message()
            self._last_usage = AiUsageResult(
                input_tokens=msg.usage.input_tokens,
                output_tokens=msg.usage.output_tokens,
                model=self._model,
            )

    async def close(self) -> None:
        await self._client.close()
