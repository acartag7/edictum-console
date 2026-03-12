"""AI assistant tool definitions and executors.

Defines the tools available to the AI contract assistant via function calling.
Each tool has a JSON Schema definition (for the LLM) and an async executor
(runs server-side).

Architecture note: This module is intentionally decoupled from HTTP transport.
Tool definitions and executors can be exposed as an MCP server endpoint in the
future — the JSON schemas map directly to MCP tool definitions. See also
``resources.py`` for MCP-style resources.

Security:
- Tool executors receive ``ToolContext`` with ``tenant_id`` from the
  authenticated session — never from LLM arguments.
- Only tools in ``TOOL_REGISTRY`` can be called; the LLM cannot invoke
  arbitrary functions.
- Input is bounded by JSON Schema constraints (max lengths, enums).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog
import yaml

from edictum_server.ai.base import ToolDefinition

logger = structlog.get_logger(__name__)

_TOOL_TIMEOUT_SECONDS = 5.0


@dataclass
class ToolContext:
    """Execution context passed to every tool executor.

    ``tenant_id`` comes from the authenticated session (route handler),
    never from the LLM's tool arguments.
    ``db_session_factory`` creates async DB sessions for tenant-scoped queries.
    """

    tenant_id: uuid.UUID
    db_session_factory: Callable[..., Any]


# -- Tool executor type --
ToolExecutor = Callable[[dict[str, Any], ToolContext], Awaitable[dict[str, Any]]]


# ---------------------------------------------------------------------------
# Tool 1: validate_contract
# ---------------------------------------------------------------------------

VALIDATE_CONTRACT_DEF = ToolDefinition(
    name="validate_contract",
    description=(
        "Validate contract YAML against the edictum schema. "
        "Accepts either a single contract (id/type/tool/when/then) or a full "
        "bundle (apiVersion/kind/metadata/contracts). Returns validation errors "
        "or confirms the contract is valid. Always call this before presenting "
        "a contract to the user."
    ),
    parameters={
        "type": "object",
        "properties": {
            "yaml_content": {
                "type": "string",
                "description": "The contract YAML to validate.",
                "maxLength": 50000,
            },
        },
        "required": ["yaml_content"],
        "additionalProperties": False,
    },
)


async def execute_validate_contract(
    args: dict[str, Any],
    _ctx: ToolContext,
) -> dict[str, Any]:
    """Validate contract YAML against the edictum-v1 schema."""
    yaml_content: str = args.get("yaml_content", "")
    if not yaml_content.strip():
        return {"valid": False, "errors": ["Empty YAML content"]}

    try:
        bundle_yaml = _wrap_as_bundle(yaml_content)
    except Exception as exc:
        return {"valid": False, "errors": [f"YAML parse error: {exc}"]}

    try:
        from edictum.yaml_engine.loader import load_bundle_string

        load_bundle_string(bundle_yaml)
        return {"valid": True, "errors": []}
    except Exception as exc:
        return {"valid": False, "errors": [str(exc)]}


# ---------------------------------------------------------------------------
# Tool 2: evaluate_contract
# ---------------------------------------------------------------------------

EVALUATE_CONTRACT_DEF = ToolDefinition(
    name="evaluate_contract",
    description=(
        "Test a contract against a simulated tool call. Returns the verdict "
        "(allow/deny/flag/require_approval) and which contract decided. "
        "Use this to prove a contract catches the scenario the user described."
    ),
    parameters={
        "type": "object",
        "properties": {
            "yaml_content": {
                "type": "string",
                "description": "The contract YAML to evaluate.",
                "maxLength": 50000,
            },
            "tool_name": {
                "type": "string",
                "description": "The tool name to simulate (e.g. 'read_file', 'bash').",
                "maxLength": 200,
            },
            "tool_args": {
                "type": "object",
                "description": "Arguments for the simulated tool call.",
            },
            "environment": {
                "type": "string",
                "description": "Environment context.",
                "enum": ["production", "staging", "development"],
                "default": "production",
            },
        },
        "required": ["yaml_content", "tool_name", "tool_args"],
        "additionalProperties": False,
    },
)


async def execute_evaluate_contract(
    args: dict[str, Any],
    _ctx: ToolContext,
) -> dict[str, Any]:
    """Evaluate a contract against a simulated tool call."""
    yaml_content: str = args.get("yaml_content", "")
    tool_name: str = args.get("tool_name", "")
    tool_args: dict[str, Any] = args.get("tool_args", {})
    environment: str = args.get("environment", "production")

    if not yaml_content.strip():
        return {"error": "Empty YAML content"}
    if not tool_name:
        return {"error": "tool_name is required"}

    try:
        bundle_yaml = _wrap_as_bundle(yaml_content)
    except Exception as exc:
        return {"error": f"YAML parse error: {exc}"}

    from edictum_server.services.evaluate_service import evaluate_contracts

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                evaluate_contracts,
                yaml_content=bundle_yaml,
                tool_name=tool_name,
                tool_args=tool_args,
                environment=environment,
            ),
            timeout=_TOOL_TIMEOUT_SECONDS,
        )
        return {
            "verdict": result.verdict,
            "deciding_contract": result.deciding_contract,
            "contracts_evaluated": [
                {
                    "id": c.id,
                    "matched": c.matched,
                    "effect": c.effect,
                    "message": c.message,
                }
                for c in result.contracts_evaluated
            ],
            "evaluation_time_ms": result.evaluation_time_ms,
        }
    except TimeoutError:
        return {"error": "Evaluation timed out (contract may be too complex)"}
    except ValueError as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wrap_as_bundle(yaml_content: str) -> str:
    """Wrap a single contract YAML in a minimal bundle if needed.

    If the YAML already has ``apiVersion``, return as-is (it's a full bundle).
    Otherwise, wrap the individual contract in a bundle structure.
    """
    try:
        parsed = yaml.safe_load(yaml_content)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML syntax: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("YAML must be a mapping")

    # Already a full bundle
    if "apiVersion" in parsed:
        return yaml_content

    # Individual contract — wrap in bundle
    if "id" not in parsed:
        raise ValueError("Contract must have an 'id' field")

    bundle = {
        "apiVersion": "edictum/v1",
        "kind": "ContractBundle",
        "metadata": {"name": "validation-check"},
        "defaults": {"mode": "enforce"},
        "contracts": [parsed],
    }
    return yaml.safe_dump(bundle, default_flow_style=False, sort_keys=False)


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

TOOL_REGISTRY: dict[str, tuple[ToolDefinition, ToolExecutor]] = {
    "validate_contract": (VALIDATE_CONTRACT_DEF, execute_validate_contract),
    "evaluate_contract": (EVALUATE_CONTRACT_DEF, execute_evaluate_contract),
}


def get_tool_definitions() -> list[ToolDefinition]:
    """Return all tool definitions (for passing to providers)."""
    return [defn for defn, _ in TOOL_REGISTRY.values()]


async def execute_tool(
    name: str,
    arguments: dict[str, Any],
    ctx: ToolContext,
) -> dict[str, Any]:
    """Execute a tool by name. Returns result dict or error dict.

    Unknown tools return an error (the LLM cannot call arbitrary functions).
    Executor exceptions are caught and returned as error dicts so the LLM
    can adjust its approach.
    """
    entry = TOOL_REGISTRY.get(name)
    if entry is None:
        return {"error": f"Unknown tool: {name!r}"}

    _, executor = entry
    try:
        result = await asyncio.wait_for(
            executor(arguments, ctx),
            timeout=_TOOL_TIMEOUT_SECONDS,
        )
        return result
    except TimeoutError:
        return {"error": f"Tool '{name}' timed out after {_TOOL_TIMEOUT_SECONDS}s"}
    except Exception as exc:
        logger.warning("Tool '%s' failed: %s", name, exc)
        return {"error": f"Tool execution failed: {exc}"}
