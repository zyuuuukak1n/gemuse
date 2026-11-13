"""Tests for MCPHubManager: graceful degradation, parallel timeout tolerance, & orchestrator loop."""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.memory import MemoryService
from app.agent.orchestrator import AgentOrchestrator, LLMStreamProvider, StreamChunk
from app.core.router import ThinkingLevel
from app.core.security import ApprovalService
from app.db.models import Conversation, Job
from app.mcp_hub.base import BaseMCPConnector, MCPTool, MCPToolDefinition
from app.mcp_hub.manager import MCPHubManager, ToolCallRequest
from tests.conftest import TEST_HMAC_SECRET


class _UnconfiguredConnector(BaseMCPConnector):
    connector_name = "unconfigured_demo"

    def is_configured(self) -> bool:
        return False

    def get_tools(self) -> list[MCPTool]:
        return [
            MCPTool(
                name="never_registered",
                description="Should not be registered",
                handler=self._noop,
            )
        ]

    async def _noop(self) -> str:
        return "noop"


class _WorkingConnector(BaseMCPConnector):
    connector_name = "working_demo"

    def is_configured(self) -> bool:
        return True

    async def fast_tool(self, query: str) -> str:
        return f"result for {query}"

    async def slow_tool(self) -> str:
        await asyncio.sleep(2.0)
        return "too late"

    async def failing_tool(self) -> str:
        raise RuntimeError("boom with AIzaSySecretKey1234567890abcdef")

    def get_tools(self) -> list[MCPTool]:
        return [
            MCPTool(name="fast_tool", description="Fast lookup", handler=self.fast_tool),
            MCPTool(name="slow_tool", description="Slow lookup", handler=self.slow_tool),
            MCPTool(name="failing_tool", description="Failing lookup", handler=self.failing_tool),
        ]


async def test_mcp_hub_graceful_skip_and_external_empty_config(
    tmp_path: Path,
    caplog,
) -> None:
    caplog.set_level(logging.INFO)
    approval = ApprovalService(TEST_HMAC_SECRET)
    hub = MCPHubManager(approval_service=approval, tool_timeout_seconds=0.2)

    assert await hub.register_connector(_UnconfiguredConnector()) is False
    assert await hub.register_connector(_WorkingConnector()) is True

    cfg_path = tmp_path / "mcp_servers.json"
    cfg_path.write_text(json.dumps({"version": "1.0.0", "servers": []}), encoding="utf-8")
    assert await hub.load_external_mcp_servers(cfg_path) == 0

    registered_names = {t.name for t in hub.list_all_tools()}
    assert "never_registered" not in registered_names
    assert "fast_tool" in registered_names

    statuses = {s["name"]: s for s in hub.get_connector_statuses()}
    assert statuses["unconfigured_demo"]["status"] == "disabled_missing_credentials"
    assert statuses["working_demo"]["status"] == "enabled"


async def test_mcp_hub_parallel_execution_timeout_and_failure_isolation() -> None:
    approval = ApprovalService(TEST_HMAC_SECRET)
    hub = MCPHubManager(approval_service=approval, tool_timeout_seconds=0.15)
    await hub.register_connector(_WorkingConnector())

    results = await hub.execute_tools_parallel(
        [
            ToolCallRequest(call_id="c1", tool_name="fast_tool", arguments={"query": "gemuse"}),
            ToolCallRequest(call_id="c2", tool_name="slow_tool", arguments={}),
            ToolCallRequest(call_id="c3", tool_name="failing_tool", arguments={}),
        ]
    )

    by_id = {r.call_id: r for r in results}
    assert by_id["c1"].success is True
    assert "<untrusted_external_data" in by_id["c1"].output
    assert "result for gemuse" in by_id["c1"].output

    assert by_id["c2"].success is False
    assert by_id["c2"].timed_out is True

    assert by_id["c3"].success is False
    assert "AIzaSySecretKey" not in (by_id["c3"].error or "")
    assert "***REDACTED***" in (by_id["c3"].error or "")


class _MultiRoundFunctionCallingLLM(LLMStreamProvider):
    """Round 1 emits 2 parallel tool calls; Round 2 synthesizes final cited answer."""

    def __init__(self) -> None:
        self.round = 0
        self.received_contents: list[list[dict[str, Any]]] = []

    async def stream_generate(
        self,
        *,
        model: str,
        system_instruction: str,
        contents: list[dict[str, Any]],
        thinking_level: ThinkingLevel,
        tools: list[MCPToolDefinition],
    ) -> AsyncIterator[StreamChunk]:
        self.round += 1
        self.received_contents.append(list(contents))
        if self.round == 1:
            yield StreamChunk(kind="thinking", text="外部ツールを並列実行します。")
            yield StreamChunk(kind="tool_call", tool_name="fast_tool", tool_args={"query": "A"})
            yield StreamChunk(kind="tool_call", tool_name="fast_tool", tool_args={"query": "B"})
        else:
            yield StreamChunk(kind="text", text="調査結果を統合しました [MCP: fast_tool]。")
            yield StreamChunk(kind="usage", input_tokens=80, output_tokens=30)


async def test_orchestrator_parallel_function_calling_loop(
    db_session: AsyncSession,
    memory_service: MemoryService,
) -> None:
    approval = ApprovalService(TEST_HMAC_SECRET)
    hub = MCPHubManager(approval_service=approval, tool_timeout_seconds=1.0)
    await hub.register_connector(_WorkingConnector())

    llm = _MultiRoundFunctionCallingLLM()
    orchestrator = AgentOrchestrator(
        model_name="gemini-4-hydrogen",
        llm_provider=llm,
        memory_service=memory_service,
        tool_registry=hub.tool_registry,
        approval_service=approval,
        mcp_hub=hub,
    )

    conv = Conversation(id=uuid.uuid4(), title="MCP Parallel Loop")
    db_session.add(conv)
    await db_session.flush()
    await db_session.refresh(conv, attribute_names=["messages"])
    job = Job(
        id=uuid.uuid4(),
        conversation_id=conv.id,
        status="queued",
        prompt="AとBを並列で調べて",
        thinking_level="medium",
    )
    db_session.add(job)
    await db_session.commit()

    events = [
        ev
        async for ev in orchestrator.stream_chat(
            db_session,
            conversation=conv,
            job=job,
            user_prompt="AとBを並列で調べて",
        )
    ]

    assert llm.round == 2
    assert any(
        ev.event == "tool_call" and ev.data.get("status") == "completed" for ev in events
    )
    assert events[-1].event == "done"
    assert "[MCP: fast_tool]" in events[-1].data["citations"]
    # Round 2 prompt must receive wrapped tool outputs
    assert "result for A" in llm.received_contents[1][-1]["content"]
    assert "result for B" in llm.received_contents[1][-1]["content"]


async def test_mcp_admin_endpoints(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    status_resp = await api_client.get("/api/mcp/status", headers=auth_headers)
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert "connectors" in body
    assert "tools" in body

    perm_resp = await api_client.get("/api/mcp/discord/permissions", headers=auth_headers)
    assert perm_resp.status_code == 200
    assert perm_resp.json()["default_policy"] == "disabled"

