"""Sync API: official Gemini one-click ingest, handoff prompt, and Takeout import."""

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_api_auth, verify_ingest_auth
from app.db.session import get_db_session
from app.sync.gemini_bridge import (
    GeminiBridgeService,
    HandoffPackage,
    IngestResult,
    IngestTurn,
    TakeoutImportResult,
)

router = APIRouter(prefix="/api/sync", tags=["sync"])


def get_gemini_bridge(request: Request) -> GeminiBridgeService:
    return request.app.state.gemini_bridge


class IngestChatRequest(BaseModel):
    """Payload accepted from Chrome bookmarklet or PWA for one-click consolidation."""

    title: str = Field(default="Gemini Chat Import", max_length=300)
    source_url: str = Field(default="https://gemini.google.com", max_length=1000)
    messages: list[IngestTurn] | None = None
    raw_text: str | None = Field(default=None, max_length=200_000)


@router.post(
    "/ingest-chat",
    response_model=IngestResult,
    dependencies=[Depends(verify_ingest_auth)],
)
async def ingest_external_chat(
    payload: IngestChatRequest,
    db: AsyncSession = Depends(get_db_session),
    bridge: GeminiBridgeService = Depends(get_gemini_bridge),
) -> IngestResult:
    """Ingest a conversation from official Gemini web app and consolidate into memory."""
    try:
        return await bridge.ingest_chat(
            db,
            title=payload.title,
            source_url=payload.source_url,
            messages=payload.messages,
            raw_text=payload.raw_text,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get(
    "/handoff",
    response_model=HandoffPackage,
    dependencies=[Depends(verify_api_auth)],
)
async def get_handoff_prompt(
    conversation_id: uuid.UUID | None = Query(default=None),
    goal: str | None = Query(default=None, max_length=2000),
    db: AsyncSession = Depends(get_db_session),
    bridge: GeminiBridgeService = Depends(get_gemini_bridge),
) -> HandoffPackage:
    """Generate a copy-paste-ready handoff prompt for official Gemini Deep Research."""
    return await bridge.generate_handoff(
        db,
        conversation_id=conversation_id,
        goal=goal,
    )


@router.post(
    "/import-takeout",
    response_model=TakeoutImportResult,
    dependencies=[Depends(verify_api_auth)],
)
async def import_google_takeout(
    file: UploadFile = File(...),
    max_items: int = Query(default=100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db_session),
    bridge: GeminiBridgeService = Depends(get_gemini_bridge),
) -> TakeoutImportResult:
    """Import Google Takeout Gemini Apps Activity (JSON or HTML) into Layer 2 EpisodeMemory."""
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )
    try:
        return await bridge.import_takeout(
            db,
            raw_bytes=raw_bytes,
            filename=file.filename or "",
            max_items=max_items,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to parse Takeout export: {exc}",
        ) from exc

