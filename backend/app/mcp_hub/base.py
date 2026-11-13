"""Abstract MCP server interface and central tool registry with approval enforcement."""

import uuid
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import ApprovalRequiredError, ApprovalService, wrap_untrusted_data

logger = get_logger(__name__)


class MCPToolDefinition(BaseModel):
    """Schema describing a tool exposed by an MCP server plugin."""

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


class MCPServerClient(ABC):
    """Abstract base class for Step 2 MCP server connectors (Discord, Email, SNS)."""

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
        self._approval_service = approval_service

    def register_server(self, client: MCPServerClient, tools: list[MCPToolDefinition]) -> None:
        """Register an MCP server and its advertised tools."""
        self._servers[client.server_name] = client
        for tool in tools:
            self._tools[tool.name] = (client.server_name, tool)
        logger.info(
            "Registered MCP server",
            extra={"server_name": client.server_name, "tool_count": len(tools)},
        )

    def list_all_tools(self) -> list[MCPToolDefinition]:
        """Return all currently registered tool definitions."""
        return [tool for _, tool in self._tools.values()]

    def get_tool(self, tool_name: str) -> tuple[MCPServerClient, MCPToolDefinition]:
        """Look up a tool and its owning client."""
        entry = self._tools.get(tool_name)
        if entry is None:
            raise KeyError(f"Unknown tool: {tool_name}")
        server_name, tool_def = entry
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
        client, tool_def = self.get_tool(tool_name)

        if tool_def.requires_approval:
            if self._approval_service is None or db is None or approval_id is None:
                raise ApprovalRequiredError(
                    f"Tool '{tool_name}' requires Human-in-the-loop approval",
                    action_type=tool_name,
                )
            await self._approval_service.assert_approved_for_execution(
                db,
                approval_id=approval_id,
                action_type=tool_name,
                consume=True,
            )

        raw_output = await client.call_tool(tool_name, arguments)
        safe_output = wrap_untrusted_data(f"{tool_def.source_type}:{tool_name}", raw_output)
        return ToolExecutionResult(
            tool_name=tool_name,
            success=True,
            wrapped_output=safe_output,
        )

