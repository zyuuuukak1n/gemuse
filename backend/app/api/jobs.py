"""Jobs API: status lookup & cancellation for disconnected background tasks."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, Message
from app.db.session import get_db_session

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobResponse(BaseModel):
    """Serialized state of a background or streaming Job."""

    job_id: uuid.UUID
    conversation_id: uuid.UUID
    status: str
    thinking_level: str
    result_summary: str | None
    error_message: str | None
    latest_citations: list[str]
    created_at: datetime
    updated_at: datetime


@router.get("/{job_id}", response_model=JobResponse)
async def get_job_status(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
) -> JobResponse:
    """Retrieve persistent job status and latest assistant citations for session recovery."""
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    msg_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == job.conversation_id, Message.role == "assistant")
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    latest_assistant = msg_result.scalar_one_or_none()

    return JobResponse(
        job_id=job.id,
        conversation_id=job.conversation_id,
        status=job.status,
        thinking_level=job.thinking_level,
        result_summary=job.result_summary,
        error_message=job.error_message,
        latest_citations=list(latest_assistant.citations) if latest_assistant else [],
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.post("/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
) -> JobResponse:
    """Cancel an active or queued job."""
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    if job.status in ("completed", "failed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel job in '{job.status}' state",
        )

    job.status = "failed"
    job.error_message = "Cancelled by user"
    await db.commit()
    await db.refresh(job)

    return JobResponse(
        job_id=job.id,
        conversation_id=job.conversation_id,
        status=job.status,
        thinking_level=job.thinking_level,
        result_summary=job.result_summary,
        error_message=job.error_message,
        latest_citations=[],
        created_at=job.created_at,
        updated_at=job.updated_at,
    )

