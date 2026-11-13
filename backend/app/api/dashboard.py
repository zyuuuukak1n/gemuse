"""Dashboard API: aggregated command-center summary for Step 4 PWA."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import ApprovalRequest, Job, ProactiveNotification
from app.db.session import get_db_session

logger = get_logger(__name__)
router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
async def get_dashboard_summary(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """
    Aggregate multi-service command center data with graceful degradation:
    active jobs, pending approvals, Discord permissions, emails, calendar events, and alerts.
    """
    jobs_rows = (
        await db.execute(select(Job).order_by(desc(Job.created_at)).limit(20))
    ).scalars().all()
    approvals_rows = (
        await db.execute(
            select(ApprovalRequest)
            .where(ApprovalRequest.status == "pending")
            .order_by(desc(ApprovalRequest.created_at))
            .limit(10)
        )
    ).scalars().all()
    notif_rows = (
        await db.execute(
            select(ProactiveNotification)
            .order_by(desc(ProactiveNotification.created_at))
            .limit(10)
        )
    ).scalars().all()

    mcp_hub = getattr(request.app.state, "mcp_hub", None)
    discord_connector = getattr(request.app.state, "discord_connector", None)
    proactive_engine = getattr(request.app.state, "proactive_engine", None)

    connectors = mcp_hub.get_connector_statuses() if mcp_hub is not None else []
    discord_summary = (
        {
            **discord_connector.get_permissions_summary(),
            "configured": discord_connector.is_configured(),
        }
        if discord_connector is not None
        else {"default_policy": "disabled", "servers": [], "configured": False}
    )

    calendar_events: list[dict[str, Any]] = []
    emails: list[dict[str, Any]] = []
    if proactive_engine is not None:
        now = datetime.now(timezone.utc)
        try:
            calendar_events = await proactive_engine._fetch_calendar_safe(
                now, now + timedelta(days=7)
            )
        except Exception as exc:
            logger.debug("Dashboard calendar fetch skipped: %s", exc)
        try:
            emails = await proactive_engine._fetch_mail_safe(["重要", "面接", "締切"])
        except Exception as exc:
            logger.debug("Dashboard mail fetch skipped: %s", exc)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "jobs": [
            {
                "job_id": str(j.id),
                "conversation_id": str(j.conversation_id),
                "status": j.status,
                "thinking_level": j.thinking_level,
                "prompt_preview": j.prompt[:140],
                "result_summary": j.result_summary,
                "created_at": j.created_at.isoformat(),
                "updated_at": j.updated_at.isoformat(),
            }
            for j in jobs_rows
        ],
        "pending_approvals": [
            {
                "approval_id": str(a.id),
                "job_id": str(a.job_id) if a.job_id else None,
                "action_type": a.action_type,
                "payload": a.payload,
                "expires_at": a.expires_at.isoformat(),
            }
            for a in approvals_rows
        ],
        "notifications": [
            {
                "id": str(n.id),
                "rule_id": n.rule_id,
                "priority": n.priority,
                "title": n.title,
                "body": n.body,
                "is_read": n.is_read,
                "created_at": n.created_at.isoformat(),
            }
            for n in notif_rows
        ],
        "connectors": connectors,
        "discord": discord_summary,
        "calendar_events": calendar_events[:10],
        "emails": emails[:10],
    }

