"""Tests for Step 4 additive backend endpoints supporting the Next.js PWA BFF."""

import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import Conversation, Job, Message, ProactiveNotification


async def test_chat_conversations_list_and_detail(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    conv_id = uuid.uuid4()
    job_id = uuid.uuid4()
    async with db_session_factory() as session:
        conv = Conversation(id=conv_id, title="Step 4 会話一覧テスト")
        session.add(conv)
        await session.flush()
        session.add(
            Message(
                conversation_id=conv_id,
                role="user",
                content="進捗を教えてください",
                citations=[],
                attachments=[],
            )
        )
        session.add(
            Message(
                conversation_id=conv_id,
                role="assistant",
                content="順調に進んでいます [Memory: Core]。",
                thinking_summary="確認完了",
                citations=["[Memory: Core]"],
                attachments=[],
            )
        )
        session.add(
            Job(
                id=job_id,
                conversation_id=conv_id,
                prompt="進捗を教えてください",
                thinking_level="low",
                status="completed",
                result_summary="順調に進んでいます",
            )
        )
        await session.commit()

    list_resp = await api_client.get("/api/chat/conversations", headers=auth_headers)
    assert list_resp.status_code == 200
    items = list_resp.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == str(conv_id)
    assert items[0]["latest_job_status"] == "completed"

    detail_resp = await api_client.get(
        f"/api/chat/conversations/{conv_id}", headers=auth_headers
    )
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["title"] == "Step 4 会話一覧テスト"
    assert len(detail["messages"]) == 2
    assert detail["jobs"][0]["job_id"] == str(job_id)


async def test_jobs_list_and_dashboard_summary(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    conv_id = uuid.uuid4()
    async with db_session_factory() as session:
        session.add(Conversation(id=conv_id, title="Dashboard Summary Conv"))
        await session.flush()
        session.add(
            Job(
                conversation_id=conv_id,
                prompt="重要なメール確認",
                thinking_level="medium",
                status="running",
            )
        )
        session.add(
            ProactiveNotification(
                rule_id="morning_briefing",
                event_hash="hash-step4-summary",
                priority="normal",
                title="朝のブリーフィング",
                body="予定が2件あります。",
                channels_sent=["in_app"],
                is_read=False,
            )
        )
        await session.commit()

    jobs_resp = await api_client.get("/api/jobs?status_filter=running", headers=auth_headers)
    assert jobs_resp.status_code == 200
    jobs_items = jobs_resp.json()["items"]
    assert len(jobs_items) == 1
    assert jobs_items[0]["status"] == "running"

    summary_resp = await api_client.get("/api/dashboard/summary", headers=auth_headers)
    assert summary_resp.status_code == 200
    summary = summary_resp.json()
    assert len(summary["jobs"]) >= 1
    assert len(summary["notifications"]) >= 1
    assert "discord" in summary
    assert "connectors" in summary


async def test_vnc_ticket_issue_and_verify(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
    test_settings: Settings,
) -> None:
    from app.api.browser import _verify_vnc_ticket

    resp = await api_client.post(
        "/api/browser/sessions/sess-xyz/vnc-ticket", headers=auth_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "sess-xyz"
    ticket = data["ticket"]
    assert _verify_vnc_ticket(test_settings.APPROVAL_HMAC_SECRET, "sess-xyz", ticket) is True
    assert _verify_vnc_ticket(test_settings.APPROVAL_HMAC_SECRET, "other-sess", ticket) is False


async def test_mcp_execute_enforces_approval_gate(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    from app.mcp_hub.base import MCPTool

    async def _gated_action(target: str) -> str:
        return f"executed:{target}"

    app = api_client._transport.app  # type: ignore[attr-defined]
    app.state.mcp_hub.tool_registry.register_tool(
        MCPTool(
            name="dummy_gated_action",
            description="Test gated tool",
            handler=_gated_action,
            requires_approval=True,
            connector_name="test",
        )
    )

    # 1. Calling without approval_id must be rejected with 403 Forbidden
    unapproved_resp = await api_client.post(
        "/api/mcp/execute",
        headers=auth_headers,
        json={
            "tool_name": "dummy_gated_action",
            "arguments": {"target": "prod"},
        },
    )
    assert unapproved_resp.status_code == 403

    # 2. Issue + resolve one-time approval, then execute successfully
    create_resp = await api_client.post(
        "/api/approval",
        headers=auth_headers,
        json={"action_type": "dummy_gated_action", "payload": {"target": "prod"}},
    )
    assert create_resp.status_code == 201
    created = create_resp.json()
    approval_id = created["approval_id"]
    one_time_token = created["one_time_token"]

    resolve_resp = await api_client.post(
        f"/api/approval/{approval_id}/resolve",
        headers=auth_headers,
        json={"decision": "approved", "token": one_time_token},
    )
    assert resolve_resp.status_code == 200

    exec_resp = await api_client.post(
        "/api/mcp/execute",
        headers=auth_headers,
        json={
            "tool_name": "dummy_gated_action",
            "arguments": {"target": "prod"},
            "approval_id": approval_id,
        },
    )
    assert exec_resp.status_code == 200
    assert exec_resp.json()["success"] is True
    assert "executed:prod" in exec_resp.json()["wrapped_output"]

    # 3. Reusing consumed approval_id must be rejected with 403
    reuse_resp = await api_client.post(
        "/api/mcp/execute",
        headers=auth_headers,
        json={
            "tool_name": "dummy_gated_action",
            "arguments": {"target": "prod"},
            "approval_id": approval_id,
        },
    )
    assert reuse_resp.status_code == 403

    # 4. Unknown tool returns 404 cleanly
    unknown_resp = await api_client.post(
        "/api/mcp/execute",
        headers=auth_headers,
        json={"tool_name": "nonexistent_tool_xyz", "arguments": {}},
    )
    assert unknown_resp.status_code == 404
