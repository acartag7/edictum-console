"""OpenAI-compatible AI provider — works with OpenAI and OpenRouter APIs."""

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
    import openai  # type: ignore[import-not-found]

    _HAS_OPENAI = True
except ImportError:
    _HAS_OPENAI = False

logger = structlog.get_logger(__name__)

DEFAULT_OPENAI_MODEL = "gpt-5-mini"
DEFAULT_OPENROUTER_MODEL = "qwen/qwen3-4b:free"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenAICompatibleProvider(AIProvider):
    """Streaming AI provider using the OpenAI Python SDK.

    Works with any OpenAI-compatible API (OpenAI, OpenRouter, etc.)
    by setting ``base_url``. Supports tool use via the OpenAI function
    calling API — tool call arguments arrive incrementally across
    multiple stream chunks and are buffered until complete.

    Requires: ``pip install openai``
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        provider_name: str = "openai",
        base_url: str | None = None,
    ) -> None:
        if not _HAS_OPENAI:
            raise RuntimeError(
                "openai package is not installed. Install it with: pip install openai"
            )
        self._provider_name = provider_name
        self._model = model
        self._client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        self._last_usage: AiUsageResult | None = None

    @property
    def name(self) -> str:
        return self._provider_name

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
        all_messages = [{"role": "system", "content": system_prompt}, *messages]
        stream = await self._client.chat.completions.create(
            model=self._model,
            max_completion_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True},
            messages=all_messages,
        )
        input_tokens = 0
        output_tokens = 0
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
            if chunk.usage is not None:
                input_tokens = chunk.usage.prompt_tokens or 0
                output_tokens = chunk.usage.completion_tokens or 0
        self._last_usage = AiUsageResult(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=self._model,
        )

    async def stream_with_tools(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str,
        tools: list[ToolDefinition],
        max_tokens: int = 4096,
    ) -> AsyncIterator[StreamEvent]:
        """Stream with OpenAI/OpenRouter tool use support.

        OpenAI streams tool call arguments incrementally across chunks
        via ``delta.tool_calls[i].function.arguments``. We buffer the
        JSON fragments per tool call index and emit completed
        ``StreamEvent(tool_call=)`` when ``finish_reason`` is ``tool_calls``.
        """
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]

        all_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            *messages,
        ]

        stream = await self._client.chat.completions.create(
            model=self._model,
            max_completion_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True},
            messages=all_messages,
            tools=openai_tools,
        )

        # Buffer for incremental tool call assembly.
        # Key: tool_call index, Value: {id, name, arguments_parts}
        tool_buffers: dict[int, dict[str, Any]] = {}
        input_tokens = 0
        output_tokens = 0

        async for chunk in stream:
            if chunk.usage is not None:
                input_tokens = chunk.usage.prompt_tokens or 0
                output_tokens = chunk.usage.completion_tokens or 0

            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            delta = choice.delta

            # Text content
            if delta.content:
                yield StreamEvent(text=delta.content)

            # Tool call deltas — accumulate incrementally
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_buffers:
                        tool_buffers[idx] = {
                            "id": tc_delta.id or "",
                            "name": "",
                            "argument_parts": [],
                        }
                    buf = tool_buffers[idx]
                    if tc_delta.id:
                        buf["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            buf["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            buf["argument_parts"].append(tc_delta.function.arguments)

            # When finish_reason is "tool_calls", emit all buffered tool calls
            if choice.finish_reason == "tool_calls":
                for _idx, buf in sorted(tool_buffers.items()):
                    raw_args = "".join(buf["argument_parts"])
                    try:
                        arguments = json.loads(raw_args) if raw_args else {}
                    except json.JSONDecodeError:
                        logger.warning(
                            "tool_call_json_decode_error",
                            provider=self._provider_name,
                            model=self._model,
                            tool_name=buf["name"],
                            raw_length=len(raw_args),
                        )
                        arguments = {"_raw": raw_args}
                    yield StreamEvent(
                        tool_call=ToolCallChunk(
                            id=buf["id"],
                            name=buf["name"],
                            arguments=arguments,
                        )
                    )
                tool_buffers.clear()

        self._last_usage = AiUsageResult(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=self._model,
        )

    async def close(self) -> None:
        await self._client.close()
