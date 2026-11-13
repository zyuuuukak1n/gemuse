"""MCP Hub Admin API: connector statuses and Discord permission summaries."""

from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import ApprovalRequiredError
from app.db.session import get_db_session
from app.mcp_hub.manager import MCPHubManager

router = APIRouter(prefix="/api/mcp", tags=["mcp-admin"])


class ExecuteToolRequest(BaseModel):
    """Payload to invoke an MCP tool explicitly (used after ApprovalCard resolution or Settings test)."""

    tool_name: str = Field(..., min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    approval_id: uuid.UUID | None = None


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


@router.post("/execute")
async def execute_mcp_tool(
    body: ExecuteToolRequest,
    mcp_hub: MCPHubManager = Depends(get_mcp_hub),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Execute a registered MCP tool through ToolRegistry (physically enforces ApprovalService)."""
    try:
        result = await mcp_hub.tool_registry.execute_tool(
            body.tool_name,
            body.arguments,
            db=db,
            approval_id=body.approval_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ApprovalRequiredError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    await db.commit()
    return result.model_dump()


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

