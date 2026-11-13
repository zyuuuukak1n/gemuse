"""MCPHubManager: plugin connector lifecycle, external MCP servers, and parallel execution."""

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger, mask_secrets
from app.core.security import ApprovalRequiredError, ApprovalService, wrap_untrusted_data
from app.mcp_hub.base import (
    BaseMCPConnector,
    MCPTool,
    MCPToolDefinition,
    ToolRegistry,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class ToolCallRequest:
    """Single tool invocation requested by Gemini Function Calling."""

    call_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)


class ParallelToolResult(BaseModel):
    """Outcome of a single tool call inside a parallel execution batch."""

    call_id: str
    tool_name: str
    success: bool
    output: str = ""
    error: str | None = None
    timed_out: bool = False
    approval_required: bool = False


class ExternalMCPServerConfig(BaseModel):
    """Schema for entries in config/mcp_servers.json."""

    name: str = Field(..., min_length=1)
    enabled: bool = True
    transport: str = Field(default="stdio", pattern="^(stdio|sse)$")
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    requires_approval: bool = False


class ExternalMCPConnector(BaseMCPConnector):
    """Dynamic connector bridging standard MCP stdio/SSE servers via the `mcp` SDK."""

    def __init__(self, server_cfg: ExternalMCPServerConfig) -> None:
        self.server_cfg = server_cfg
        self.connector_name = f"external:{server_cfg.name}"
        self._discovered_tools: list[MCPTool] = []
        self._connected = False

    def is_configured(self) -> bool:
        if not self.server_cfg.enabled:
            return False
        if self.server_cfg.transport == "stdio":
            return bool(self.server_cfg.command)
        if self.server_cfg.transport == "sse":
            return bool(self.server_cfg.url)
        return False

    async def startup(self) -> None:
        """Discover advertised tools from the external MCP server."""
        if not self.is_configured():
            return
        try:
            tools_meta = await self._list_remote_tools()
            self._discovered_tools = [
                MCPTool(
                    name=f"{self.server_cfg.name}__{t['name']}",
                    description=t.get("description") or f"External MCP tool {t['name']}",
                    parameters_schema=t.get("inputSchema")
                    or {"type": "object", "properties": {}},
                    handler=self._make_remote_caller(t["name"]),
                    requires_approval=self.server_cfg.requires_approval,
                    source_type=f"mcp:{self.server_cfg.name}",
                    connector_name=self.connector_name,
                )
                for t in tools_meta
            ]
            self._connected = True
        except Exception as exc:
            self._connected = False
            logger.warning(
                "External MCP server unavailable; skipping gracefully",
                extra={
                    "server": self.server_cfg.name,
                    "status": "disabled_connection_error",
                    "error": mask_secrets(str(exc)),
                },
            )

    def _make_remote_caller(self, remote_tool_name: str):
        async def _caller(**kwargs: Any) -> str:
            return await self._call_remote_tool(remote_tool_name, kwargs)

        return _caller

    async def _list_remote_tools(self) -> list[dict[str, Any]]:
        from mcp import ClientSession
        from mcp.client.sse import sse_client
        from mcp.client.stdio import StdioServerParameters, stdio_client

        if self.server_cfg.transport == "stdio":
            params = StdioServerParameters(
                command=self.server_cfg.command or "",
                args=self.server_cfg.args,
                env=self.server_cfg.env or None,
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    res = await session.list_tools()
                    return [
                        {
                            "name": t.name,
                            "description": t.description or "",
                            "inputSchema": getattr(t, "inputSchema", None)
                            or {"type": "object", "properties": {}},
                        }
                        for t in res.tools
                    ]
        else:
            async with sse_client(self.server_cfg.url or "") as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    res = await session.list_tools()
                    return [
                        {
                            "name": t.name,
                            "description": t.description or "",
                            "inputSchema": getattr(t, "inputSchema", None)
                            or {"type": "object", "properties": {}},
                        }
                        for t in res.tools
                    ]

    async def _call_remote_tool(self, remote_tool_name: str, arguments: dict[str, Any]) -> str:
        from mcp import ClientSession
        from mcp.client.sse import sse_client
        from mcp.client.stdio import StdioServerParameters, stdio_client

        if self.server_cfg.transport == "stdio":
            params = StdioServerParameters(
                command=self.server_cfg.command or "",
                args=self.server_cfg.args,
                env=self.server_cfg.env or None,
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(remote_tool_name, arguments)
        else:
            async with sse_client(self.server_cfg.url or "") as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(remote_tool_name, arguments)

        parts = [getattr(c, "text", str(c)) for c in getattr(result, "content", [])]
        return "\n".join(parts) if parts else ""

    def get_tools(self) -> list[MCPTool]:
        return list(self._discovered_tools)


class MCPHubManager:
    """
    Plugin-based MCP Hub orchestrating built-in connectors and external MCP servers.

    - Gracefully disables connectors lacking credentials without crashing startup.
    - Enforces ApprovalService gates on irreversible tools.
    - Executes concurrent tool calls with per-tool timeouts and exception isolation.
    """

    def __init__(
        self,
        *,
        approval_service: ApprovalService,
        tool_registry: ToolRegistry | None = None,
        tool_timeout_seconds: float = 15.0,
    ) -> None:
        self.approval_service = approval_service
        self.tool_registry = tool_registry or ToolRegistry(approval_service=approval_service)
        self.tool_timeout_seconds = tool_timeout_seconds
        self._connectors: list[BaseMCPConnector] = []
        self._connector_statuses: list[dict[str, Any]] = []

    async def register_connector(self, connector: BaseMCPConnector) -> bool:
        """Validate credentials, run startup hook, and register tools if configured."""
        self._connectors.append(connector)
        if not connector.is_configured():
            logger.info(
                "Connector skipped due to missing credentials",
                extra={
                    "connector": connector.connector_name,
                    "status": "disabled_missing_credentials",
                },
            )
            self._connector_statuses.append(
                {
                    "name": connector.connector_name,
                    "configured": False,
                    "status": "disabled_missing_credentials",
                    "tools": [],
                }
            )
            return False

        await connector.startup()
        tools = connector.get_tools()
        for tool in tools:
            tool.connector_name = connector.connector_name
            self.tool_registry.register_tool(tool)

        tool_names = [t.name for t in tools]
        logger.info(
            "Connector enabled",
            extra={
                "connector": connector.connector_name,
                "status": "enabled",
                "tool_count": len(tool_names),
            },
        )
        self._connector_statuses.append(
            {
                "name": connector.connector_name,
                "configured": True,
                "status": "enabled",
                "tools": tool_names,
            }
        )
        return True

    async def load_external_mcp_servers(self, config_path: Path) -> int:
        """Load external MCP server definitions from `config/mcp_servers.json`."""
        if not config_path.exists():
            logger.info("No mcp_servers.json found; skipping external MCP servers")
            return 0

        try:
            raw_data = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(
                "Failed to parse mcp_servers.json; skipping external servers",
                extra={"error": mask_secrets(str(exc))},
            )
            return 0

        loaded = 0
        for entry in raw_data.get("servers", []):
            try:
                cfg = ExternalMCPServerConfig.model_validate(entry)
                connector = ExternalMCPConnector(cfg)
                if await self.register_connector(connector):
                    loaded += 1
            except Exception as exc:
                logger.warning(
                    "Invalid external MCP server entry skipped",
                    extra={"error": mask_secrets(str(exc))},
                )
        return loaded

    async def shutdown(self) -> None:
        """Gracefully shut down all registered connectors."""
        for connector in self._connectors:
            try:
                await connector.shutdown()
            except Exception as exc:
                logger.warning(
                    "Connector shutdown error",
                    extra={
                        "connector": connector.connector_name,
                        "error": mask_secrets(str(exc)),
                    },
                )

    def get_connector_statuses(self) -> list[dict[str, Any]]:
        """Return safe status summary (without secrets) for admin diagnostics."""
        registry_tools = {t.name for t in self.tool_registry.list_all_tools()}
        return [
            {
                **entry,
                "active_tools": [name for name in entry["tools"] if name in registry_tools],
            }
            for entry in self._connector_statuses
        ]

    def list_all_tools(self) -> list[MCPToolDefinition]:
        return self.tool_registry.list_all_tools()

    async def execute_tools_parallel(
        self,
        calls: list[ToolCallRequest],
        *,
        db: AsyncSession | None = None,
    ) -> list[ParallelToolResult]:
        """
        Execute multiple tool calls concurrently with per-tool timeouts and isolation.

        One failing or timing-out tool never crashes sibling calls.
        """
        if not calls:
            return []

        async def _run_one(call: ToolCallRequest) -> ParallelToolResult:
            try:
                res = await asyncio.wait_for(
                    self.tool_registry.execute_tool(
                        call.tool_name,
                        call.arguments,
                        db=db,
                    ),
                    timeout=self.tool_timeout_seconds,
                )
                return ParallelToolResult(
                    call_id=call.call_id,
                    tool_name=call.tool_name,
                    success=True,
                    output=res.wrapped_output,
                )
            except ApprovalRequiredError as exc:
                return ParallelToolResult(
                    call_id=call.call_id,
                    tool_name=call.tool_name,
                    success=False,
                    error=str(exc),
                    approval_required=True,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Tool execution timed out",
                    extra={
                        "tool_name": call.tool_name,
                        "timeout_seconds": self.tool_timeout_seconds,
                    },
                )
                return ParallelToolResult(
                    call_id=call.call_id,
                    tool_name=call.tool_name,
                    success=False,
                    error=f"Tool '{call.tool_name}' timed out after {self.tool_timeout_seconds}s",
                    timed_out=True,
                )
            except Exception as exc:
                safe_err = mask_secrets(str(exc)) or "Tool execution error"
                logger.warning(
                    "Tool execution failed",
                    extra={"tool_name": call.tool_name, "error": safe_err},
                )
                return ParallelToolResult(
                    call_id=call.call_id,
                    tool_name=call.tool_name,
                    success=False,
                    error=wrap_untrusted_data(f"tool_error:{call.tool_name}", safe_err),
                )

        results = await asyncio.gather(*(_run_one(c) for c in calls), return_exceptions=True)
        normalized: list[ParallelToolResult] = []
        for call, outcome in zip(calls, results):
            if isinstance(outcome, BaseException):
                normalized.append(
                    ParallelToolResult(
                        call_id=call.call_id,
                        tool_name=call.tool_name,
                        success=False,
                        error=mask_secrets(str(outcome)),
                    )
                )
            else:
                normalized.append(outcome)
        return normalized

