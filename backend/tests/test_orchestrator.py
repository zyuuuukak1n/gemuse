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


async def test_orchestrator_multimodal_inline_image_and_chat_endpoint(
    api_client,
    auth_headers: dict[str, str],
) -> None:
    import base64
    from types import SimpleNamespace
    from httpx import AsyncClient
    from app.agent.orchestrator import GoogleGenAIStreamProvider

    assert isinstance(api_client, AsyncClient)
    tiny_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
    )
    tiny_b64 = base64.b64encode(tiny_png).decode("ascii")

    # 1. End-to-end POST /api/chat with empty text + Base64 inline_attachments forwards inline image to LLM
    app = api_client._transport.app  # type: ignore[attr-defined]
    mock_llm = app.state.orchestrator.llm_provider
    calls_before = len(mock_llm.calls)

    resp = await api_client.post(
        "/api/chat",
        headers=auth_headers,
        json={
            "message": "",
            "thinking_level": "low",
            "mode": "sse",
            "attachments": ["screenshot.png"],
            "inline_attachments": [
                {
                    "filename": "screenshot.png",
                    "mime_type": "image/png",
                    "data_base64": tiny_b64,
                }
            ],
        },
    )
    assert resp.status_code == 200
    assert len(mock_llm.calls) > calls_before
    last_call = mock_llm.calls[-1]
    assert "skip_if_exists=True" in last_call["system_instruction"]
    last_user_turn = last_call["contents"][-1]
    assert last_user_turn["role"] == "user"
    assert len(last_user_turn["attachments"]) == 1
    assert last_user_turn["attachments"][0]["mime_type"] == "image/png"
    assert last_user_turn["attachments"][0]["data_base64"] == tiny_b64

    # 2. Verify GoogleGenAIStreamProvider converts inline Base64 attachments via types.Part.from_bytes
    captured_contents: list[object] = []

    class _FakeTypes:
        class GenerateContentConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class Content:
            def __init__(self, role, parts):
                self.role = role
                self.parts = parts

        class Part:
            @staticmethod
            def from_bytes(data: bytes, mime_type: str):
                return ("bytes_part", mime_type, data)

            @staticmethod
            def from_text(text: str):
                return ("text_part", text)

    class _FakeModels:
        async def generate_content_stream(self, *, model, contents, config):
            captured_contents.extend(contents)

            async def _gen():
                yield SimpleNamespace(
                    candidates=[
                        SimpleNamespace(
                            content=SimpleNamespace(
                                parts=[SimpleNamespace(thought=False, function_call=None, text="OK")]
                            )
                        )
                    ],
                    usage_metadata=None,
                )

            return _gen()

    provider = GoogleGenAIStreamProvider.__new__(GoogleGenAIStreamProvider)
    provider._client = SimpleNamespace(aio=SimpleNamespace(models=_FakeModels()))
    provider._types = _FakeTypes

    out_chunks = [
        chunk
        async for chunk in provider.stream_generate(
            model="gemini-4-hydrogen",
            system_instruction="sys",
            contents=[
                {
                    "role": "user",
                    "content": "画像を解析してください",
                    "attachments": [
                        {"filename": "s.png", "mime_type": "image/png", "data_base64": tiny_b64}
                    ],
                }
            ],
            thinking_level="low",
            tools=[],
        )
    ]
    assert any(c.kind == "text" and c.text == "OK" for c in out_chunks)
    assert len(captured_contents) == 1
    assert ("bytes_part", "image/png", tiny_png) in captured_contents[0].parts
    assert ("text_part", "画像を解析してください") in captured_contents[0].parts


async def test_orchestrator_round_limit_forces_final_report_without_tools(
    db_session: AsyncSession,
    memory_service: MemoryService,
) -> None:
    from app.agent.orchestrator import LLMStreamProvider

    approval_service = ApprovalService(TEST_HMAC_SECRET)
    registry = ToolRegistry(approval_service)

    class _DummySheetStub(MCPServerClient):
        def __init__(self) -> None:
            super().__init__("dummy")

        async def connect(self) -> None:
            return None

        async def disconnect(self) -> None:
            return None

        async def list_tools(self) -> list[MCPToolDefinition]:
            return [
                MCPToolDefinition(
                    name="dummy_step",
                    description="Dummy multi-step tool",
                    requires_approval=False,
                    source_type="mcp",
                )
            ]

        async def call_tool(self, tool_name: str, arguments: dict) -> str:
            return f"ok:{arguments.get('idx')}"

    stub = _DummySheetStub()
    registry.register_server(stub, await stub.list_tools())

    class _LoopingProvider(LLMStreamProvider):
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def stream_generate(
            self,
            *,
            model: str,
            system_instruction: str,
            contents: list[dict],
            thinking_level,
            tools,
        ):
            self.calls.append({"contents": contents, "tools": tools})
            if tools is None:
                yield StreamChunk(
                    kind="text",
                    text="### 完了した処理と残作業\n- `dummy_step` 2件を完了しました。\n- 残りの登録を続けて実行できます。",
                )
                return
            yield StreamChunk(
                kind="tool_call",
                tool_name="dummy_step",
                tool_args={"idx": len(self.calls)},
            )

    provider = _LoopingProvider()
    default_orch = AgentOrchestrator(
        model_name="gemini-4-hydrogen",
        llm_provider=provider,
        memory_service=memory_service,
        tool_registry=registry,
        approval_service=approval_service,
    )
    assert default_orch.max_tool_rounds >= 20

    limited_orch = AgentOrchestrator(
        model_name="gemini-4-hydrogen",
        llm_provider=provider,
        memory_service=memory_service,
        tool_registry=registry,
        approval_service=approval_service,
        max_tool_rounds=2,
    )

    conv = Conversation(id=uuid.uuid4(), title="Multi-step Limit Test")
    db_session.add(conv)
    await db_session.flush()
    await db_session.refresh(conv, attribute_names=["messages"])
    job = Job(
        id=uuid.uuid4(),
        conversation_id=conv.id,
        status="queued",
        prompt="複数社を一括登録して",
        thinking_level="high",
    )
    db_session.add(job)
    await db_session.commit()

    events = [
        ev
        async for ev in limited_orch.stream_chat(
            db_session,
            conversation=conv,
            job=job,
            user_prompt="複数社を一括登録して",
        )
    ]

    # 2 tool rounds + 1 final forced synthesis call with tools=None
    assert len(provider.calls) == 3
    assert provider.calls[0]["tools"]
    assert provider.calls[1]["tools"]
    assert provider.calls[2]["tools"] is None
    assert "上限（2回）" in provider.calls[2]["contents"][-1]["content"]
    assert events[-1].event == "done"
    assert "完了した処理と残作業" in events[-1].data["answer"]

