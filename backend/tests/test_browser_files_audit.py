"""Tests for Step 3 shared_files upload/download APIs, BrowserMCPConnector, audit logs & TTL cleanup."""

import os
import time
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.browser_ctrl import BrowserController
from app.core.config import Settings
from app.db.models import BrowserAuditLog
from app.db.session import get_db_session
from app.main import create_app
from app.mcp_hub.servers.browser_mcp import BrowserMCPConnector
from tests.conftest import TEST_API_TOKEN
from tests.test_browser_ctrl import FakeAsyncRedis, FakeSandboxClient


@pytest.mark.asyncio
async def test_shared_files_upload_download_mcp_and_audit_logs(
    test_settings: Settings,
    db_session_factory: async_sessionmaker[AsyncSession],
    memory_service,
    mock_llm_provider,
) -> None:
    sandbox = FakeSandboxClient()
    ctrl = BrowserController(
        sandbox=sandbox,
        redis=FakeAsyncRedis(),
        shared_files_dir=test_settings.SHARED_FILES_DIR,
    )
    app = create_app(
        settings_override=test_settings,
        llm_provider_override=mock_llm_provider,
        memory_service_override=memory_service,
        browser_controller_override=ctrl,
        enable_redis=False,
    )

    async def _override_db():
        async with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_db
    headers = {"Authorization": f"Bearer {TEST_API_TOKEN}"}

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            # 1. Upload file to /shared_files/uploads/
            up_resp = await client.post(
                "/api/browser/upload",
                files={"file": ("resume.pdf", b"%PDF-1.4 dummy resume", "application/pdf")},
                headers=headers,
            )
            assert up_resp.status_code == 200
            assert up_resp.json()["filename"] == "resume.pdf"
            assert (ctrl.uploads_dir / "resume.pdf").exists()

            # List uploads
            list_up = await client.get("/api/browser/uploads", headers=headers)
            assert list_up.status_code == 200
            assert "resume.pdf" in list_up.json()["files"]

            # 2. BrowserMCPConnector uploads file inside sandbox & writes BrowserAuditLog
            connector = BrowserMCPConnector(
                controller=ctrl,
                session_factory=db_session_factory,
            )
            mcp_out = await connector.browser_upload_file(
                session_id="sess-file-1",
                selector='input[type="file"]',
                file_names=["resume.pdf"],
            )
            assert "<untrusted_external_data" in mcp_out
            assert sandbox.actions[-1][1]["action_type"] == "upload_files"
            assert sandbox.actions[-1][1]["file_names"] == ["resume.pdf"]

            # 3. Simulate browser downloading an invoice into /shared_files/downloads/sess-file-1/
            dl_dir = ctrl.downloads_dir / "sess-file-1"
            dl_dir.mkdir(parents=True, exist_ok=True)
            (dl_dir / "invoice.pdf").write_bytes(b"%PDF-1.4 invoice bytes")

            dl_list = await client.get("/api/browser/downloads/sess-file-1", headers=headers)
            assert dl_list.status_code == 200
            assert dl_list.json()["files"][0]["filename"] == "invoice.pdf"

            dl_file = await client.get(
                "/api/browser/downloads/sess-file-1/invoice.pdf", headers=headers
            )
            assert dl_file.status_code == 200
            assert dl_file.content == b"%PDF-1.4 invoice bytes"

            # Path traversal must be rejected
            traversal = await client.get(
                "/api/browser/downloads/sess-file-1/..%2F..%2Fsecret.txt", headers=headers
            )
            assert traversal.status_code in (400, 404)

            # 4. Audit logs API
            audit_resp = await client.get(
                "/api/browser/audit-logs?session_id=sess-file-1", headers=headers
            )
            assert audit_resp.status_code == 200
            items = audit_resp.json()["items"]
            assert any(item["action"] == "upload_files" for item in items)


@pytest.mark.asyncio
async def test_artifact_ttl_cleanup_removes_only_expired_files(
    test_settings: Settings,
) -> None:
    ctrl = BrowserController(
        sandbox=FakeSandboxClient(),
        shared_files_dir=test_settings.SHARED_FILES_DIR,
        artifact_ttl_seconds=60,
    )
    art_dir = ctrl.downloads_dir / "sess-ttl" / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)
    old_file = art_dir / "old_shot.png"
    fresh_file = art_dir / "fresh_shot.png"
    old_file.write_bytes(b"old")
    fresh_file.write_bytes(b"fresh")

    stale_ts = time.time() - 3600
    os.utime(old_file, (stale_ts, stale_ts))

    removed = ctrl.cleanup_expired_artifacts(ttl_seconds=120)
    assert removed == 1
    assert not old_file.exists()
    assert fresh_file.exists()

