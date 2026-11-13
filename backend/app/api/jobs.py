"""Jobs API: status lookup & cancellation for disconnected background tasks."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import desc, select
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


@router.get("")
async def list_jobs(
    limit: int = Query(default=25, ge=1, le=100),
    status_filter: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, list[dict[str, object]]]:
    """List recent background and streaming jobs ordered newest first."""
    stmt = select(Job).order_by(desc(Job.created_at)).limit(limit)
    if status_filter:
        stmt = stmt.where(Job.status == status_filter)
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "items": [
            {
                "job_id": str(j.id),
                "conversation_id": str(j.conversation_id),
                "status": j.status,
                "thinking_level": j.thinking_level,
                "prompt_preview": j.prompt[:160],
                "result_summary": j.result_summary,
                "error_message": j.error_message,
                "created_at": j.created_at.isoformat(),
                "updated_at": j.updated_at.isoformat(),
            }
            for j in rows
        ]
    }


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

