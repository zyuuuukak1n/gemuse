"""BrowserMCPConnector: exposes isolated browser automation, file upload, and audit tools."""

from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.browser_ctrl import BrowserController
from app.core.security import wrap_untrusted_data
from app.db.models import BrowserAuditLog
from app.mcp_hub.base import BaseMCPConnector, MCPTool


class BrowserMCPConnector(BaseMCPConnector):
    """MCP connector wrapping BrowserController for safe sandboxed web operations."""

    connector_name = "browser"

    def __init__(
        self,
        *,
        controller: BrowserController,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        configured: bool = True,
    ) -> None:
        self._controller = controller
        self._session_factory = session_factory
        self._configured = configured

    def is_configured(self) -> bool:
        return self._configured

    async def browser_navigate_and_run(
        self,
        goal: str,
        start_url: str = "",
        session_id: str = "",
        max_steps: int = 10,
    ) -> str:
        """Navigate and autonomously execute a multi-step browser goal with auto-pause guardrails."""
        if self._session_factory is not None:
            async with self._session_factory() as db:
                result = await self._controller.run_task(
                    db,
                    goal=goal,
                    start_url=start_url or None,
                    session_id=session_id or None,
                    max_steps=max_steps,
                )
        else:
            result = await self._controller.run_task(
                None,
                goal=goal,
                start_url=start_url or None,
                session_id=session_id or None,
                max_steps=max_steps,
            )
        return wrap_untrusted_data("browser:run", result.model_dump_json(indent=2))

    async def browser_upload_file(
        self,
        session_id: str,
        selector: str,
        file_names: list[str],
    ) -> str:
        """Attach files from /shared_files/uploads/ to a file input inside the isolated browser."""
        snapshot = await self._controller.sandbox.execute_action(
            session_id,
            {
                "action_type": "upload_files",
                "selector": selector,
                "file_names": file_names,
            },
        )
        if self._session_factory is not None:
            async with self._session_factory() as db:
                await self._controller.record_audit(
                    db,
                    session_id=session_id,
                    action="upload_files",
                    target_url=snapshot.get("url"),
                    details={"selector": selector, "file_names": file_names},
                )
        summary = {
            "session_id": session_id,
            "url": snapshot.get("url", ""),
            "uploaded_files": file_names,
        }
        return wrap_untrusted_data("browser:upload", str(summary))

    async def browser_list_downloads(self, session_id: str) -> str:
        """List files downloaded inside /shared_files/downloads/{session_id}."""
        files = self._controller.list_downloads(session_id)
        return wrap_untrusted_data("browser:downloads", str(files))

    async def browser_audit_history(self, session_id: str = "", limit: int = 20) -> str:
        """Fetch recent browser audit trail entries."""
        if self._session_factory is None:
            return wrap_untrusted_data("browser:audit", "[]")
        safe_limit = max(1, min(limit, 100))
        async with self._session_factory() as db:
            stmt = select(BrowserAuditLog).order_by(desc(BrowserAuditLog.created_at)).limit(safe_limit)
            if session_id:
                stmt = stmt.where(BrowserAuditLog.session_id == session_id)
            res = await db.execute(stmt)
            rows = res.scalars().all()
        items: list[dict[str, Any]] = [
            {
                "id": str(row.id),
                "session_id": row.session_id,
                "action": row.action,
                "target_url": row.target_url,
                "operator": row.operator,
                "screenshot_path": row.screenshot_path,
                "details": row.details,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
        return wrap_untrusted_data("browser:audit", str(items))

    def get_tools(self) -> list[MCPTool]:
        return [
            MCPTool(
                name="browser_navigate_and_run",
                description=(
                    "Run an isolated Chromium Computer Use task toward a user goal. "
                    "Automatically pauses before logins, 2FA, CAPTCHA, or final checkout buttons."
                ),
                handler=self.browser_navigate_and_run,
                requires_approval=False,
                source_type="browser",
                already_wrapped=True,
            ),
            MCPTool(
                name="browser_upload_file",
                description="Attach uploaded files from /shared_files/uploads/ into a browser file input.",
                handler=self.browser_upload_file,
                requires_approval=False,
                source_type="browser",
                already_wrapped=True,
            ),
            MCPTool(
                name="browser_list_downloads",
                description="List downloaded files saved in /shared_files/downloads for a browser session.",
                handler=self.browser_list_downloads,
                requires_approval=False,
                source_type="browser",
                already_wrapped=True,
            ),
            MCPTool(
                name="browser_audit_history",
                description="Inspect recent isolated browser audit logs.",
                handler=self.browser_audit_history,
                requires_approval=False,
                source_type="browser",
                already_wrapped=True,
            ),
        ]

