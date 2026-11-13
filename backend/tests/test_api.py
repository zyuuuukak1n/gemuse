"""End-to-end API integration tests for chat (SSE & background), jobs, memory, and approval."""

import json
import uuid
from unittest.mock import AsyncMock

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.memory import MemoryService
from app.core.config import Settings
from app.core.security import ApprovalService
from app.mcp_hub.base import ToolRegistry
from app.worker.tasks import run_agent_job
from tests.conftest import TEST_HMAC_SECRET, MockGeminiStreamProvider


def _parse_sse_events(raw_body: str) -> list[tuple[str, dict]]:
    parsed: list[tuple[str, dict]] = []
    for block in raw_body.strip().split("\n\n"):
        if not block.strip():
            continue
        event_name = ""
        data_json = "{}"
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_json = line[len("data:") :].strip()
        if event_name:
            parsed.append((event_name, json.loads(data_json)))
    return parsed


async def test_chat_sse_mode_streams_events_and_enqueues_consolidation(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
    mock_arq_pool: AsyncMock,
) -> None:
    response = await api_client.post(
        "/api/chat",
        headers=auth_headers,
        json={"message": "こんにちは", "thinking_level": "auto", "mode": "sse"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse_events(response.text)
    names = [name for name, _ in events]
    assert names[0] == "metadata"
    assert "thinking" in names
    assert "delta" in names
    assert names[-1] == "done"

    done_payload = events[-1][1]
    assert "[Memory: Core]" in done_payload["citations"]

    # Post-turn memory consolidation task must be enqueued
    mock_arq_pool.enqueue_job.assert_awaited_once()
    call_args = mock_arq_pool.enqueue_job.call_args[0]
    assert call_args[0] == "consolidate_memory_task"


async def test_chat_background_mode_and_job_status_recovery(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
    mock_arq_pool: AsyncMock,
    test_settings: Settings,
    db_session_factory: async_sessionmaker[AsyncSession],
    memory_service: MemoryService,
    mock_llm_provider: MockGeminiStreamProvider,
) -> None:
    resp = await api_client.post(
        "/api/chat",
        headers=auth_headers,
        json={
            "message": "バックグラウンドでドキュメント構成を分析してください",
            "thinking_level": "auto",
            "mode": "background",
        },
    )
    assert resp.status_code == 202
    data = resp.json()
    job_id = data["job_id"]
    assert data["status"] == "queued"
    assert data["thinking_level"] in {"low", "medium", "high", "xhigh"}

    mock_arq_pool.enqueue_job.assert_awaited_once_with("run_agent_job", job_id)

    # Check initial queued status
    job_resp = await api_client.get(f"/api/jobs/{job_id}", headers=auth_headers)
    assert job_resp.status_code == 200
    assert job_resp.json()["status"] == "queued"

    # Simulate ARQ worker executing the background job while client was closed
    worker_ctx = {
        "settings": test_settings,
        "session_factory": db_session_factory,
        "memory_service": memory_service,
        "approval_service": ApprovalService(TEST_HMAC_SECRET),
        "tool_registry": ToolRegistry(),
        "llm_provider": mock_llm_provider,
        "redis": mock_arq_pool,
    }
    worker_result = await run_agent_job(worker_ctx, job_id)
    assert worker_result["status"] == "completed"

    # Client reconnects and fetches final result from /api/jobs/{id}
    completed_resp = await api_client.get(f"/api/jobs/{job_id}", headers=auth_headers)
    assert completed_resp.status_code == 200
    body = completed_resp.json()
    assert body["status"] == "completed"
    assert "コアメモリを確認しました" in body["result_summary"]
    assert "[Memory: Core]" in body["latest_citations"]


async def test_job_cancellation_lifecycle(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    resp = await api_client.post(
        "/api/chat",
        headers=auth_headers,
        json={"message": "長時間タスク", "mode": "background"},
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    cancel_resp = await api_client.post(f"/api/jobs/{job_id}/cancel", headers=auth_headers)
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "failed"
    assert cancel_resp.json()["error_message"] == "Cancelled by user"

    # Cancelling an already-failed job returns 409 Conflict
    second_cancel = await api_client.post(f"/api/jobs/{job_id}/cancel", headers=auth_headers)
    assert second_cancel.status_code == 409

    # Unknown job returns 404
    missing_resp = await api_client.get(f"/api/jobs/{uuid.uuid4()}", headers=auth_headers)
    assert missing_resp.status_code == 404


async def test_memory_core_episodes_and_skills_endpoints(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    memory_service: MemoryService,
) -> None:
    # GET /api/memory/core
    get_resp = await api_client.get("/api/memory/core", headers=auth_headers)
    assert get_resp.status_code == 200
    assert "Japanese" in get_resp.json()["content"]

    # PUT /api/memory/core
    new_md = "# Core Memory\n- Updated Rule: Always run pytest before commit\n"
    put_resp = await api_client.put(
        "/api/memory/core",
        headers=auth_headers,
        json={"content": new_md},
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["content"] == new_md

    # Seed episode and skill, then query via API
    await memory_service.add_episode(
        db_session,
        content="Deployed Personal Muse backend on Ubuntu Server",
        source_type="conversation",
        importance_score=0.8,
    )
    await memory_service.upsert_skill(
        db_session,
        name="deploy_compose",
        description="Deploy stack with Docker Compose",
        trigger_keywords=["docker", "deploy"],
        steps_markdown="1. docker compose up -d --build",
    )

    ep_resp = await api_client.get(
        "/api/memory/episodes",
        headers=auth_headers,
        params={"q": "Ubuntu Docker", "threshold": 0.01},
    )
    assert ep_resp.status_code == 200
    assert len(ep_resp.json()) == 1
    assert "Personal Muse" in ep_resp.json()[0]["content"]

    skills_resp = await api_client.get("/api/memory/skills", headers=auth_headers)
    assert skills_resp.status_code == 200
    assert len(skills_resp.json()) == 1
    assert skills_resp.json()[0]["name"] == "deploy_compose"


async def test_approval_create_and_resolve_endpoints(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    create_resp = await api_client.post(
        "/api/approval",
        headers=auth_headers,
        json={"action_type": "x_post_tweet", "payload": {"text": "Hello world"}},
    )
    assert create_resp.status_code == 201
    created = create_resp.json()
    approval_id = created["approval_id"]
    token = created["one_time_token"]

    # Invalid token fails with 403
    bad_resolve = await api_client.post(
        f"/api/approval/{approval_id}/resolve",
        headers=auth_headers,
        json={"decision": "approved", "token": "invalid-token-0123456789"},
    )
    assert bad_resolve.status_code == 403

    # Valid token resolves cleanly
    ok_resolve = await api_client.post(
        f"/api/approval/{approval_id}/resolve",
        headers=auth_headers,
        json={"decision": "approved", "token": token},
    )
    assert ok_resolve.status_code == 200
    assert ok_resolve.json()["status"] == "approved"

    # Reusing the same token (replay) fails with 403
    replay_resolve = await api_client.post(
        f"/api/approval/{approval_id}/resolve",
        headers=auth_headers,
        json={"decision": "approved", "token": token},
    )
    assert replay_resolve.status_code == 403

