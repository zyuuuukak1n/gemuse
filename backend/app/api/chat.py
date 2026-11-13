"""Chat API: SSE real-time streaming & ARQ background job submission."""

import uuid
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent.orchestrator import AgentOrchestrator
from app.api import get_arq_pool, get_orchestrator
from app.core.logging import get_logger, mask_secrets
from app.core.router import RequestedThinkingLevel, route_thinking_level
from app.db.models import Conversation, Job, Message
from app.db.session import get_db_session

logger = get_logger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    """Validated payload for POST /api/chat."""

    message: str = Field(..., min_length=1, max_length=32000)
    conversation_id: uuid.UUID | None = None
    thinking_level: RequestedThinkingLevel = "auto"
    mode: Literal["sse", "background"] = "sse"
    attachments: list[str] = Field(default_factory=list, max_length=20)


class BackgroundChatResponse(BaseModel):
    """Response returned when mode='background'."""

    job_id: uuid.UUID
    conversation_id: uuid.UUID
    status: str
    thinking_level: str


@router.post("")
async def chat_endpoint(
    payload: ChatRequest,
    db: AsyncSession = Depends(get_db_session),
    orchestrator: AgentOrchestrator = Depends(get_orchestrator),
    arq_pool: Any | None = Depends(get_arq_pool),
) -> Any:
    """
    Process a user message via SSE streaming (`mode='sse'`) or ARQ (`mode='background'`).
    """
    if payload.conversation_id is not None:
        conv_result = await db.execute(
            select(Conversation)
            .options(selectinload(Conversation.messages))
            .where(Conversation.id == payload.conversation_id)
        )
        conversation = conv_result.scalar_one_or_none()
        if conversation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )
    else:
        title = payload.message.strip().splitlines()[0][:80] or "New Conversation"
        conversation = Conversation(id=uuid.uuid4(), title=title)
        db.add(conversation)
        await db.flush()
        # Ensure messages collection is initialized cleanly without lazy-load
        await db.refresh(conversation, attribute_names=["messages"])

    available_tools = orchestrator.tool_registry.list_all_tools()
    decision = route_thinking_level(
        payload.message,
        payload.thinking_level,
        has_tools=bool(available_tools),
        history_length=len(conversation.messages or []),
    )

    job = Job(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        status="queued",
        prompt=payload.message,
        thinking_level=decision.level,
    )
    db.add(job)

    if payload.mode == "background":
        user_msg = Message(
            id=uuid.uuid4(),
            conversation_id=conversation.id,
            role="user",
            content=payload.message,
            attachments=payload.attachments,
        )
        db.add(user_msg)
        await db.commit()

        if arq_pool is not None:
            await arq_pool.enqueue_job("run_agent_job", str(job.id))
        else:
            logger.warning(
                "ARQ Redis pool unavailable; job queued in DB without immediate enqueue",
                extra={"job_id": str(job.id)},
            )

        body = BackgroundChatResponse(
            job_id=job.id,
            conversation_id=conversation.id,
            status=job.status,
            thinking_level=decision.level,
        )
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=body.model_dump(mode="json"))

    await db.commit()

    async def _post_turn_consolidate(user_msg: str, assistant_msg: str, conv_id: str) -> None:
        if arq_pool is not None:
            await arq_pool.enqueue_job(
                "consolidate_memory_task",
                user_msg,
                assistant_msg,
                conv_id,
            )

    async def _event_stream() -> AsyncIterator[str]:
        try:
            async for event in orchestrator.stream_chat(
                db,
                conversation=conversation,
                job=job,
                user_prompt=payload.message,
                requested_thinking_level=payload.thinking_level,
                attachments=payload.attachments,
                persist_user_message=True,
                post_turn_hook=_post_turn_consolidate,
            ):
                yield event.encode()
        except Exception as exc:
            safe_err = mask_secrets(str(exc))
            logger.exception("Unhandled SSE stream error", extra={"job_id": str(job.id)})
            yield (
                f'event: error\ndata: {{"code": "STREAM_ERROR", "message": "{safe_err}"}}\n\n'
            )

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

