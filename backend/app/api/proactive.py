"""Proactive notifications, Web Push subscription, and manual trigger APIs."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.proactive import ProactiveEngine
from app.core.config import Settings, get_settings
from app.db.models import ProactiveNotification, PushSubscription
from app.db.session import get_db_session

router = APIRouter(prefix="/api/proactive", tags=["proactive"])


class PushSubscriptionKeys(BaseModel):
    p256dh: str = Field(..., min_length=1)
    auth: str = Field(..., min_length=1)


class PushSubscriptionRequest(BaseModel):
    endpoint: str = Field(..., min_length=1)
    keys: PushSubscriptionKeys
    user_agent: str | None = None


def _get_proactive_engine(request: Request) -> ProactiveEngine:
    engine = getattr(request.app.state, "proactive_engine", None)
    if engine is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ProactiveEngine is not initialized",
        )
    return engine


@router.get("/notifications")
async def get_notifications(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """List persisted proactive briefings and alerts."""
    engine = _get_proactive_engine(request)
    rows = await engine.list_notifications(db, limit=limit)
    return {
        "items": [
            {
                "id": str(row.id),
                "rule_id": row.rule_id,
                "event_hash": row.event_hash,
                "priority": row.priority,
                "title": row.title,
                "body": row.body,
                "payload": row.payload,
                "channels_sent": row.channels_sent,
                "is_read": row.is_read,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
    }


@router.post("/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    res = await db.execute(
        select(ProactiveNotification).where(ProactiveNotification.id == notification_id)
    )
    row = res.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    row.is_read = True
    await db.commit()
    return {"id": str(row.id), "is_read": True}


@router.get("/push/vapid-public-key")
async def get_vapid_public_key(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return {
        "configured": bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY),
        "vapid_public_key": settings.VAPID_PUBLIC_KEY,
    }


@router.post("/push/subscribe")
async def register_push_subscription(
    payload: PushSubscriptionRequest,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Register or update a Web Push client subscription."""
    res = await db.execute(
        select(PushSubscription).where(PushSubscription.endpoint == payload.endpoint)
    )
    existing = res.scalar_one_or_none()
    if existing is not None:
        existing.p256dh = payload.keys.p256dh
        existing.auth = payload.keys.auth
        existing.user_agent = payload.user_agent
        existing.is_active = True
        await db.commit()
        return {"id": str(existing.id), "status": "updated"}

    sub = PushSubscription(
        endpoint=payload.endpoint,
        p256dh=payload.keys.p256dh,
        auth=payload.keys.auth,
        user_agent=payload.user_agent,
        is_active=True,
    )
    db.add(sub)
    await db.commit()
    return {"id": str(sub.id), "status": "subscribed"}


@router.post("/run-briefing")
async def trigger_morning_briefing(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Manually trigger the morning briefing evaluation (idempotent per date)."""
    engine = _get_proactive_engine(request)
    res = await engine.run_morning_briefing(db=db)
    return res.model_dump()


@router.post("/run-alerts")
async def trigger_event_alerts(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Manually trigger event-driven alert evaluation."""
    engine = _get_proactive_engine(request)
    results = await engine.run_event_alerts(db=db)
    return {"results": [r.model_dump() for r in results]}

