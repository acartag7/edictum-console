"""Agentic loop for the AI contract assistant.

Orchestrates multi-turn tool calling: stream LLM response, detect tool calls,
execute them server-side, feed results back, repeat until the LLM responds
with text only or limits are reached.

The loop is provider-agnostic — it consumes ``StreamEvent`` from any provider
and handles Anthropic/OpenAI message format differences when assembling tool
results.

Architecture note: This orchestrator is decoupled from the HTTP transport.
It yields SSE-ready event dicts that the route serializes. In the future,
the same loop could power an MCP server or WebSocket interface.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from edictum_server.ai.base import AIProvider, StreamEvent, ToolCallChunk
from edictum_server.ai.tools import ToolContext, execute_tool, get_tool_definitions

logger = logging.getLogger(__name__)

# Safety limits to prevent runaway tool calling.
_DEFAULT_MAX_ITERATIONS = 5
_DEFAULT_MAX_TOOL_CALLS = 10


async def run_agent_loop(
    provider: AIProvider,
    messages: list[dict[str, Any]],
    system_prompt: str,
    tool_context: ToolContext,
    *,
    max_iterations: int = _DEFAULT_MAX_ITERATIONS,
    max_tool_calls: int = _DEFAULT_MAX_TOOL_CALLS,
) -> AsyncIterator[dict[str, Any]]:
    """Run the agentic tool-calling loop, yielding SSE event dicts.

    Yielded event types:
    - ``{"content": "chunk"}`` — text delta
    - ``{"type": "tool_call_start", "tool": name, "id": id}``
    - ``{"type": "tool_call_result", "id": id, "tool": name, "result": {...}, "duration_ms": ms}``
    - ``{"type": "usage", ...}`` — cumulative token usage (emitted once at end)
    """
    tool_defs = get_tool_definitions()
    has_tools = provider.supports_tools and tool_defs
    total_tool_calls = 0

    # Cumulative usage across all rounds
    cumulative_input = 0
    cumulative_output = 0

    for _iteration in range(max_iterations):
        # Stream from provider
        text_parts: list[str] = []
        tool_calls: list[ToolCallChunk] = []

        if has_tools and total_tool_calls < max_tool_calls:
            stream = provider.stream_with_tools(
                messages,
                system_prompt,
                tool_defs,
            )
        else:
            # No tools or limit reached — text-only
            simple = [
                {"role": m["role"], "content": m["content"]}
                for m in messages
                if isinstance(m.get("content"), str)
            ]
            stream = _text_only_stream(provider, simple, system_prompt)

        async for event in stream:
            if event.text is not None:
                text_parts.append(event.text)
                yield {"content": event.text}
            elif event.tool_call is not None:
                tool_calls.append(event.tool_call)

        # Accumulate usage from this round
        usage = provider.last_usage
        if usage:
            cumulative_input += usage.input_tokens
            cumulative_output += usage.output_tokens

        # No tool calls — LLM is done
        if not tool_calls:
            break

        # Build assistant message with text + tool calls for conversation history
        full_text = "".join(text_parts)

        if provider.name == "anthropic":
            # Anthropic format: assistant content is a list of blocks
            assistant_content: list[dict[str, Any]] = []
            if full_text:
                assistant_content.append({"type": "text", "text": full_text})
            for tc in tool_calls:
                assistant_content.append(
                    {
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    }
                )
            messages.append({"role": "assistant", "content": assistant_content})
        else:
            # OpenAI format: assistant message with tool_calls array
            openai_tool_calls = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in tool_calls
            ]
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "tool_calls": openai_tool_calls,
            }
            if full_text:
                assistant_msg["content"] = full_text
            messages.append(assistant_msg)

        # Execute each tool call and build result messages
        tool_result_messages: list[dict[str, Any]] = []

        for tc in tool_calls:
            total_tool_calls += 1
            if total_tool_calls > max_tool_calls:
                result = {"error": f"Tool call limit reached ({max_tool_calls})"}
                duration_ms = 0
            else:
                yield {
                    "type": "tool_call_start",
                    "tool": tc.name,
                    "id": tc.id,
                }

                start = time.monotonic()
                result = await execute_tool(tc.name, tc.arguments, tool_context)
                duration_ms = int((time.monotonic() - start) * 1000)

                yield {
                    "type": "tool_call_result",
                    "id": tc.id,
                    "tool": tc.name,
                    "result": result,
                    "duration_ms": duration_ms,
                }

            # Append tool result message in provider-specific format
            result_str = json.dumps(result)
            if provider.name == "anthropic":
                tool_result_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tc.id,
                                "content": result_str,
                            }
                        ],
                    }
                )
            else:
                tool_result_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_str,
                    }
                )

        # For Anthropic, all tool results go in a single user message
        if provider.name == "anthropic" and tool_result_messages:
            combined_content = []
            for msg in tool_result_messages:
                combined_content.extend(msg["content"])
            messages.append({"role": "user", "content": combined_content})
        else:
            messages.extend(tool_result_messages)

    # Emit cumulative usage at the end
    if cumulative_input > 0 or cumulative_output > 0:
        model = provider.model
        total_tokens = cumulative_input + cumulative_output
        yield {
            "type": "cumulative_usage",
            "input_tokens": cumulative_input,
            "output_tokens": cumulative_output,
            "total_tokens": total_tokens,
            "model": model,
        }


async def _text_only_stream(
    provider: AIProvider,
    messages: list[dict[str, str]],
    system_prompt: str,
) -> AsyncIterator[StreamEvent]:
    """Wrap stream_response as StreamEvent for the loop."""
    async for chunk in provider.stream_response(messages, system_prompt):
        yield StreamEvent(text=chunk)
