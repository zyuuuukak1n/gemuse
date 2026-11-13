"""Abstract MCP connector & tool definitions with JSON Schema generation and approval gating."""

import inspect
import json
import types
import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, Union, get_args, get_origin, get_type_hints

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import ApprovalRequiredError, ApprovalService, wrap_untrusted_data

logger = get_logger(__name__)


def _python_type_to_json_schema(annotation: Any) -> dict[str, Any]:
    """Convert basic Python type annotations into JSON Schema property objects."""
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {"type": "string"}

    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        non_none = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(non_none) == 1:
            return _python_type_to_json_schema(non_none[0])
        return {"type": "string"}

    if origin in (list, tuple, set):
        args = get_args(annotation)
        item_schema = _python_type_to_json_schema(args[0]) if args else {"type": "string"}
        return {"type": "array", "items": item_schema}

    if origin is dict:
        return {"type": "object"}

    if annotation is str:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}

    return {"type": "string"}


def build_schema_from_callable(func: Callable[..., Any]) -> dict[str, Any]:
    """Generate an OpenAPI/Gemini-compatible JSON Schema from a callable's signature."""
    sig = inspect.signature(func)
    try:
        hints = get_type_hints(func)
    except Exception:
        hints = {}

    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls", "db", "context"):
            continue
        annotation = hints.get(param_name, param.annotation)
        prop_schema = _python_type_to_json_schema(annotation)
        properties[param_name] = prop_schema
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


class MCPToolDefinition(BaseModel):
    """Schema describing a tool exposed to Gemini Function Calling."""

    name: str = Field(..., min_length=1, max_length=128)
    description: str = Field(..., min_length=1)
    parameters_schema: dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = False
    source_type: str = "mcp"


class ToolExecutionResult(BaseModel):
    """Result returned from executing a registered tool."""

    tool_name: str
    success: bool
    wrapped_output: str
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class MCPTool:
    """Executable MCP tool binding metadata, schema, and async handler."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        handler: Callable[..., Awaitable[Any]],
        parameters_schema: dict[str, Any] | None = None,
        requires_approval: bool = False,
        source_type: str = "mcp",
        connector_name: str = "",
        already_wrapped: bool = False,
    ) -> None:
        self.name = name
        self.description = description
        self.handler = handler
        self.parameters_schema = parameters_schema or build_schema_from_callable(handler)
        self.requires_approval = requires_approval
        self.source_type = source_type
        self.connector_name = connector_name
        self.already_wrapped = already_wrapped

    def to_definition(self) -> MCPToolDefinition:
        return MCPToolDefinition(
            name=self.name,
            description=self.description,
            parameters_schema=self.parameters_schema,
            requires_approval=self.requires_approval,
            source_type=self.source_type,
        )

    async def execute(self, arguments: dict[str, Any]) -> str:
        """Invoke the underlying async handler and normalize output to string."""
        raw = await self.handler(**arguments)
        if isinstance(raw, str):
            text = raw
        elif isinstance(raw, BaseModel):
            text = raw.model_dump_json(indent=2)
        else:
            text = json.dumps(raw, ensure_ascii=False, default=str)

        if self.already_wrapped:
            return text
        return wrap_untrusted_data(f"{self.source_type}:{self.name}", text)


class BaseMCPConnector(ABC):
    """Abstract base class for built-in and plugin MCP connectors."""

    connector_name: str = "base"

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True only if required credentials/settings are present."""

    @abstractmethod
    def get_tools(self) -> list[MCPTool]:
        """Return all MCPTool instances provided by this connector."""

    async def startup(self) -> None:
        """Optional async lifecycle hook on hub startup."""
        return None

    async def shutdown(self) -> None:
        """Optional async lifecycle hook on hub shutdown."""
        return None


class MCPServerClient(ABC):
    """Legacy Step 1 abstract server interface (retained for backwards compatibility)."""

    def __init__(self, server_name: str) -> None:
        self.server_name = server_name

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the underlying MCP server."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection cleanly."""

    @abstractmethod
    async def list_tools(self) -> list[MCPToolDefinition]:
        """Return tool definitions exposed by this server."""

    @abstractmethod
    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool on this server and return raw string output."""


class ToolRegistry:
    """
    Central registry mediating tool discovery and execution.

    Enforces two mandatory invariants:
    1. Irreversible tools (`requires_approval=True`) physically block unless an
       unconsumed, approved `ApprovalRequest` exists.
    2. All external tool outputs are wrapped via `wrap_untrusted_data` before
       entering the LLM context.
    """

    def __init__(self, approval_service: ApprovalService | None = None) -> None:
        self._servers: dict[str, MCPServerClient] = {}
        self._tools: dict[str, tuple[str, MCPToolDefinition]] = {}
        self._callable_tools: dict[str, MCPTool] = {}
        self._approval_service = approval_service

    def register_server(self, client: MCPServerClient, tools: list[MCPToolDefinition]) -> None:
        """Register an MCPServerClient and its advertised tools."""
        self._servers[client.server_name] = client
        for tool in tools:
            self._tools[tool.name] = (client.server_name, tool)
        logger.info(
            "Registered MCP server",
            extra={"server_name": client.server_name, "tool_count": len(tools)},
        )

    def register_tool(self, tool: MCPTool) -> None:
        """Register a standalone MCPTool from a BaseMCPConnector."""
        self._callable_tools[tool.name] = tool
        self._tools[tool.name] = (tool.connector_name or "builtin", tool.to_definition())

    def list_all_tools(self) -> list[MCPToolDefinition]:
        """Return all currently registered tool definitions."""
        return [tool for _, tool in self._tools.values()]

    def get_tool(self, tool_name: str) -> tuple[MCPServerClient | MCPTool, MCPToolDefinition]:
        """Look up a tool definition and its owning client or MCPTool."""
        entry = self._tools.get(tool_name)
        if entry is None:
            raise KeyError(f"Unknown tool: {tool_name}")
        server_name, tool_def = entry
        if tool_name in self._callable_tools:
            return self._callable_tools[tool_name], tool_def
        return self._servers[server_name], tool_def

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        db: AsyncSession | None = None,
        approval_id: uuid.UUID | None = None,
    ) -> ToolExecutionResult:
        """Execute a tool while enforcing approval gates and untrusted-output isolation."""
        owner, tool_def = self.get_tool(tool_name)

        if tool_def.requires_approval:
            if self._approval_service is None or db is None:
                raise ApprovalRequiredError(
                    f"Tool '{tool_name}' requires Human-in-the-loop approval",
                    action_type=tool_name,
                )
            raw_token = str(arguments.get("approval_token", "") or "").strip()
            if approval_id is not None:
                await self._approval_service.assert_approved_for_execution(
                    db,
                    approval_id=approval_id,
                    action_type=tool_name,
                    consume=True,
                )
            elif raw_token:
                await self._approval_service.verify_and_consume_by_token(
                    db,
                    action_type=tool_name,
                    raw_token=raw_token,
                )
            else:
                raise ApprovalRequiredError(
                    f"Tool '{tool_name}' requires Human-in-the-loop approval",
                    action_type=tool_name,
                )

        if isinstance(owner, MCPTool):
            safe_output = await owner.execute(arguments)
        else:
            raw_output = await owner.call_tool(tool_name, arguments)
            safe_output = wrap_untrusted_data(f"{tool_def.source_type}:{tool_name}", raw_output)

        return ToolExecutionResult(
            tool_name=tool_name,
            success=True,
            wrapped_output=safe_output,
        )
