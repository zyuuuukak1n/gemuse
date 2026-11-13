"""Approval API: Human-in-the-loop one-time HMAC token issuance & resolution."""

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import get_approval_service
from app.core.security import ApprovalRequiredError, ApprovalService
from app.db.session import get_db_session

router = APIRouter(prefix="/api/approval", tags=["approval"])


class ApprovalCreateRequest(BaseModel):
    """Payload to issue a new pending Human-in-the-loop approval request."""

    action_type: str = Field(..., min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)
    job_id: uuid.UUID | None = None
    ttl_seconds: int | None = Field(default=None, ge=10, le=86400)


class ApprovalCreateResponse(BaseModel):
    """Issued approval request with one-time HMAC-signed token."""

    approval_id: uuid.UUID
    action_type: str
    status: str
    expires_at: datetime
    one_time_token: str


class ApprovalResolveRequest(BaseModel):
    """Decision and one-time HMAC token required to resolve an approval request."""

    decision: Literal["approved", "rejected"]
    token: str = Field(..., min_length=10, max_length=512)


class ApprovalResolveResponse(BaseModel):
    """Outcome after validating and consuming a one-time approval token."""

    approval_id: uuid.UUID
    action_type: str
    status: str
    resolved_at: datetime


@router.post("", response_model=ApprovalCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_approval_request(
    body: ApprovalCreateRequest,
    db: AsyncSession = Depends(get_db_session),
    approval_service: ApprovalService = Depends(get_approval_service),
) -> ApprovalCreateResponse:
    """Create a pending ApprovalRequest and issue a signed one-time token."""
    record, raw_token = await approval_service.create_request(
        db,
        action_type=body.action_type,
        payload=body.payload,
        job_id=body.job_id,
        ttl_seconds=body.ttl_seconds,
    )
    return ApprovalCreateResponse(
        approval_id=record.id,
        action_type=record.action_type,
        status=record.status,
        expires_at=record.expires_at,
        one_time_token=raw_token,
    )


@router.post("/{approval_id}/resolve", response_model=ApprovalResolveResponse)
async def resolve_approval_request(
    approval_id: uuid.UUID,
    body: ApprovalResolveRequest,
    db: AsyncSession = Depends(get_db_session),
    approval_service: ApprovalService = Depends(get_approval_service),
) -> ApprovalResolveResponse:
    """Verify HMAC one-time token and approve or reject an ApprovalRequest."""
    try:
        record = await approval_service.resolve_request(
            db,
            approval_id=approval_id,
            decision=body.decision,
            raw_token=body.token,
        )
    except ApprovalRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    assert record.resolved_at is not None
    return ApprovalResolveResponse(
        approval_id=record.id,
        action_type=record.action_type,
        status=record.status,
        resolved_at=record.resolved_at,
    )

