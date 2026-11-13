"""Orchestrator tests: thinking-level routing, SSE sequence, citations, and approval block."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.memory import MemoryService
from app.agent.orchestrator import AgentOrchestrator, StreamChunk, extract_citations
from app.core.router import route_thinking_level
from app.core.security import ApprovalService
from app.db.models import Conversation, Job
from app.mcp_hub.base import MCPServerClient, MCPToolDefinition, ToolRegistry
from tests.conftest import TEST_HMAC_SECRET, MockGeminiStreamProvider


def test_thinking_level_router_boundaries() -> None:
    # Low: simple greeting
    d_low = route_thinking_level("おはよう！")
    assert d_low.level == "low"

    # Medium: standard summary/explanation
    d_med = route_thinking_level("今日の会議メモを要約して説明してください。")
    assert d_med.level == "medium"

    # High: multi-step implementation / debugging
    d_high = route_thinking_level(
        "FastAPIとPostgreSQLのN+1問題を調査して、実装を最適化する方法を比較してください。"
    )
    assert d_high.level == "high"

    # XHigh: architecture design + security audit + multi-question long prompt
    d_xhigh = route_thinking_level(
        "Gemuseの全体構成とアーキテクチャ設計について、トレードオフとセキュリティ監査の"
        "観点から段階的に比較検討してください。\n1. DB設計は？\n2. 承認ゲートは？\n3. MCP拡張は？"
    )
    assert d_xhigh.level == "xhigh"

    # Manual override must always take precedence
    d_manual = route_thinking_level("おはよう", requested_level="xhigh")
    assert d_manual.level == "xhigh"
    assert d_manual.score == -1


async def test_orchestrator_sse_streaming_order_and_citations(
    db_session: AsyncSession,
    memory_service: MemoryService,
) -> None:
    approval_service = ApprovalService(TEST_HMAC_SECRET)
    registry = ToolRegistry(approval_service)
    mock_llm = MockGeminiStreamProvider(
        chunks=[
            StreamChunk(kind="thinking", text="コアメモリと過去の記録を照合しています。"),
            StreamChunk(kind="text", text="お好みの言語は日本語です [Memory: Core]。"),
            StreamChunk(kind="text", text="詳細は未確認です。"),
            StreamChunk(kind="usage", input_tokens=100, output_tokens=25),
        ]
    )
    orchestrator = AgentOrchestrator(
        model_name="gemini-4-hydrogen",
        llm_provider=mock_llm,
        memory_service=memory_service,
        tool_registry=registry,
        approval_service=approval_service,
    )

    conv = Conversation(id=uuid.uuid4(), title="Test Chat")
    db_session.add(conv)
    await db_session.flush()
    await db_session.refresh(conv, attribute_names=["messages"])

    job = Job(
        id=uuid.uuid4(),
        conversation_id=conv.id,
        status="queued",
        prompt="私の言語設定を教えてください",
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
            user_prompt="私の言語設定を教えてください",
            requested_thinking_level="auto",
        )
    ]

    event_names = [e.event for e in events]
    assert event_names[0] == "metadata"
    assert "thinking" in event_names
    assert "delta" in event_names
    assert event_names[-1] == "done"
    assert event_names.index("metadata") < event_names.index("thinking") < event_names.index("delta") < event_names.index("done")

    done_data = events[-1].data
    assert "[Memory: Core]" in done_data["citations"]
    assert done_data["usage"] == {"input_tokens": 100, "output_tokens": 25}
    assert job.status == "completed"

    # Anti-hallucination prompt rules must be injected
    system_prompt = mock_llm.calls[0]["system_instruction"]
    assert "未確認" in system_prompt
    assert "<untrusted_external_data" in system_prompt
    assert extract_citations("Check [Memory: Core] and [Episode: 2026-11-13] and [Memory: Core]") == [
        "[Memory: Core]",
        "[Episode: 2026-11-13]",
    ]


class _StubDiscordMCP(MCPServerClient):
    def __init__(self) -> None:
        super().__init__("discord")
        self.called = False

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def list_tools(self) -> list[MCPToolDefinition]:
        return [
            MCPToolDefinition(
                name="discord_send_message",
                description="Send a Discord message (irreversible)",
                requires_approval=True,
                source_type="discord",
            )
        ]

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        self.called = True
        return "sent"


async def test_orchestrator_physically_blocks_irreversible_tool_for_approval(
    db_session: AsyncSession,
    memory_service: MemoryService,
) -> None:
    approval_service = ApprovalService(TEST_HMAC_SECRET)
    registry = ToolRegistry(approval_service)
    stub_mcp = _StubDiscordMCP()
    registry.register_server(stub_mcp, await stub_mcp.list_tools())

    mock_llm = MockGeminiStreamProvider(
        chunks=[
            StreamChunk(
                kind="tool_call",
                tool_name="discord_send_message",
                tool_args={"channel": "#general", "text": "Hello"},
            )
        ]
    )
    orchestrator = AgentOrchestrator(
        model_name="gemini-4-hydrogen",
        llm_provider=mock_llm,
        memory_service=memory_service,
        tool_registry=registry,
        approval_service=approval_service,
    )

    conv = Conversation(id=uuid.uuid4(), title="Approval Test")
    db_session.add(conv)
    await db_session.flush()
    await db_session.refresh(conv, attribute_names=["messages"])

    job = Job(
        id=uuid.uuid4(),
        conversation_id=conv.id,
        status="queued",
        prompt="Discordに挨拶を送って",
        thinking_level="low",
    )
    db_session.add(job)
    await db_session.commit()

    events = [
        ev
        async for ev in orchestrator.stream_chat(
            db_session,
            conversation=conv,
            job=job,
            user_prompt="Discordに挨拶を送って",
        )
    ]

    assert stub_mcp.called is False
    assert job.status == "waiting_approval"
    assert events[-1].event == "approval_required"
    assert events[-1].data["action_type"] == "discord_send_message"
    assert "one_time_token" in events[-1].data

