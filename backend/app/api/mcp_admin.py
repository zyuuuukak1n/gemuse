"""MCP Hub Admin API: connector statuses and Discord permission summaries."""

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.mcp_hub.manager import MCPHubManager

router = APIRouter(prefix="/api/mcp", tags=["mcp-admin"])


def get_mcp_hub(request: Request) -> MCPHubManager:
    return request.app.state.mcp_hub


@router.get("/status")
async def get_mcp_status(
    mcp_hub: MCPHubManager = Depends(get_mcp_hub),
) -> dict[str, Any]:
    """Return status of all built-in and external MCP connectors (secrets redacted)."""
    connectors = mcp_hub.get_connector_statuses()
    tools = [t.name for t in mcp_hub.list_all_tools()]
    return {
        "total_tools": len(tools),
        "tools": tools,
        "connectors": connectors,
    }


@router.get("/discord/permissions")
async def get_discord_permissions(
    request: Request,
) -> dict[str, Any]:
    """Return active Discord server-by-server permission whitelist configuration."""
    discord_connector = getattr(request.app.state, "discord_connector", None)
    if discord_connector is None:
        return {"default_policy": "disabled", "servers": [], "configured": False}
    summary = discord_connector.get_permissions_summary()
    summary["configured"] = discord_connector.is_configured()
    return summary

