"""API router package and shared FastAPI dependencies."""

from typing import Any

from fastapi import Request

from app.agent.memory import MemoryService
from app.agent.orchestrator import AgentOrchestrator
from app.core.security import ApprovalService
from app.mcp_hub.base import ToolRegistry


def get_memory_service(request: Request) -> MemoryService:
    return request.app.state.memory_service


def get_approval_service(request: Request) -> ApprovalService:
    return request.app.state.approval_service


def get_tool_registry(request: Request) -> ToolRegistry:
    return request.app.state.tool_registry


def get_orchestrator(request: Request) -> AgentOrchestrator:
    return request.app.state.orchestrator


def get_arq_pool(request: Request) -> Any | None:
    return getattr(request.app.state, "arq_pool", None)

