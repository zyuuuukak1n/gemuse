"""Tests for Step 3 ProactiveEngine (APScheduler, briefings, alerts, Redis idempotency, Discord & Web Push)."""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.proactive import ProactiveEngine
from app.core.config import Settings
from app.db.models import ProactiveNotification, PushSubscription
from app.db.session import get_db_session
from app.main import create_app
from tests.conftest import TEST_API_TOKEN
from tests.test_browser_ctrl import FakeAsyncRedis

SAMPLE_RULES_YAML = """
version: "1.0.0"
timezone: "Asia/Tokyo"
schedules:
  - id: morning_briefing
    enabled: true
    cron: "30 7 * * *"
  - id: event_watcher
    enabled: true
    interval_minutes: 15
alert_rules:
  - id: calendar_deadline_24h
    type: calendar_reminder
    enabled: true
    within_hours: 24
    title_keywords: ["提出", "締切"]
    priority: high
  - id: urgent_interview_email
    type: mail_keyword
    enabled: true
    keywords: ["面接", "至急"]
    cross_check_calendar: true
    priority: urgent
"""


@pytest.mark.asyncio
async def test_proactive_briefing_event_alerts_and_redis_idempotency(
    tmp_path: Path,
    db_session_factory: async_sessionmaker[AsyncSession],
    memory_service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rules_path = tmp_path / "proactive_rules.yaml"
    rules_path.write_text(SAMPLE_RULES_YAML, encoding="utf-8")

    tz = ZoneInfo("Asia/Tokyo")
    now = datetime(2026, 11, 14, 7, 30, tzinfo=tz)
    deadline_start = (now + timedelta(hours=6)).isoformat()

    async def fake_calendar(start: datetime, end: datetime):
        return [
            {
                "id": "ev-deadline-1",
                "summary": "企画書提出締切",
                "start": deadline_start,
                "end": deadline_start,
            }
        ]

    async def fake_mail(keywords: list[str]):
        return [
            {
                "provider": "gmail",
                "message_id": "msg-interview-99",
                "subject": "【至急】一次面接の日程調整について",
                "sender": "hr@example.com",
                "snippet": "来週の面接候補日をご連絡ください。",
            }
        ]

    discord_calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        discord_calls.append({"url": str(request.url), "body": request.content.decode("utf-8")})
        return httpx.Response(204)

    mock_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    webpush_calls: list[dict] = []

    def fake_webpush(**kwargs):
        webpush_calls.append(kwargs)

    monkeypatch.setattr("app.agent.proactive.webpush", fake_webpush)

    async with db_session_factory() as db:
        await memory_service.add_episode(
            db,
            content="来週中に新規企画書を提出する必要がある。",
            source_type="chat",
            source_ref="conv-1",
            deduplicate=False,
        )
        db.add(
            PushSubscription(
                endpoint="https://push.example.com/sub/1",
                p256dh="p256dh-key",
                auth="auth-key",
                is_active=True,
            )
        )
        await db.commit()

    redis_mock = FakeAsyncRedis()
    engine = ProactiveEngine(
        rules_path=rules_path,
        timezone_name="Asia/Tokyo",
        session_factory=db_session_factory,
        redis=redis_mock,
        discord_webhook_url="https://discord.com/api/webhooks/test/secret",
        vapid_public_key="vapid-pub",
        vapid_private_key="vapid-priv",
        memory_service=memory_service,
        calendar_fetcher=fake_calendar,
        mail_fetcher=fake_mail,
        http_client=mock_http,
    )

    # APScheduler job registration test
    scheduler = engine.start_scheduler()
    try:
        job_ids = {j.id for j in scheduler.get_jobs()}
        assert {"gemuse_morning_briefing", "gemuse_event_watcher"}.issubset(job_ids)
    finally:
        engine.stop_scheduler()

    # Run morning briefing -> dispatched once, second call skipped by Redis idempotency
    briefing_1 = await engine.run_morning_briefing(now=now)
    assert briefing_1.dispatched is True
    assert "discord_webhook" in briefing_1.channels_sent
    assert "web_push:1" in briefing_1.channels_sent

    briefing_2 = await engine.run_morning_briefing(now=now)
    assert briefing_2.dispatched is False
    assert briefing_2.skipped_duplicate is True

    # Run event alerts -> fires both 24h deadline & urgent interview email once
    alerts_1 = await engine.run_event_alerts(now=now)
    assert len(alerts_1) == 2
    assert all(a.dispatched for a in alerts_1)

    alerts_2 = await engine.run_event_alerts(now=now)
    assert all(a.skipped_duplicate for a in alerts_2)

    assert len(discord_calls) == 3  # 1 briefing + 2 unique alerts
    assert len(webpush_calls) == 3

    async with db_session_factory() as db:
        rows = (await db.execute(select(ProactiveNotification))).scalars().all()
        assert len(rows) == 3

    await mock_http.aclose()


@pytest.mark.asyncio
async def test_proactive_graceful_degradation_and_api_endpoints(
    tmp_path: Path,
    test_settings: Settings,
    db_session_factory: async_sessionmaker[AsyncSession],
    memory_service,
    mock_llm_provider,
) -> None:
    rules_path = tmp_path / "proactive_rules.yaml"
    rules_path.write_text(SAMPLE_RULES_YAML, encoding="utf-8")

    # Unconfigured Discord Webhook and VAPID keys -> still creates notification row without crashing
    engine = ProactiveEngine(
        rules_path=rules_path,
        timezone_name="Asia/Tokyo",
        session_factory=db_session_factory,
        redis=FakeAsyncRedis(),
        discord_webhook_url="",
        vapid_public_key="",
        vapid_private_key="",
    )
    app = create_app(
        settings_override=test_settings,
        llm_provider_override=mock_llm_provider,
        memory_service_override=memory_service,
        proactive_engine_override=engine,
        enable_redis=False,
    )

    async def _override_db():
        async with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_db
    headers = {"Authorization": f"Bearer {TEST_API_TOKEN}"}

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            # Subscribe Web Push endpoint
            sub_resp = await client.post(
                "/api/proactive/push/subscribe",
                json={
                    "endpoint": "https://fcm.googleapis.com/fcm/send/demo",
                    "keys": {"p256dh": "pub", "auth": "sec"},
                },
                headers=headers,
            )
            assert sub_resp.status_code == 200
            assert sub_resp.json()["status"] == "subscribed"

            # Manual briefing trigger succeeds gracefully without Discord/VAPID
            trig_resp = await client.post("/api/proactive/run-briefing", headers=headers)
            assert trig_resp.status_code == 200
            assert trig_resp.json()["dispatched"] is True
            assert trig_resp.json()["channels_sent"] == []

            list_resp = await client.get("/api/proactive/notifications", headers=headers)
            assert list_resp.status_code == 200
            items = list_resp.json()["items"]
            assert len(items) == 1
            notif_id = items[0]["id"]

            read_resp = await client.post(
                f"/api/proactive/notifications/{notif_id}/read", headers=headers
            )
            assert read_resp.status_code == 200
            assert read_resp.json()["is_read"] is True

