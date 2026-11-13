"""Memory API: Layer 1 Core Markdown, Layer 2 Episode search, and Layer 3 Skills."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.memory import MemoryService
from app.api import get_memory_service
from app.db.models import EpisodeMemory
from app.db.session import get_db_session

router = APIRouter(prefix="/api/memory", tags=["memory"])


class CoreMemoryPayload(BaseModel):
    """Request/response payload for Layer 1 core_memory.md."""

    content: str = Field(..., min_length=1, max_length=200_000)


class EpisodeItem(BaseModel):
    """Serialized Layer 2 episode memory search hit."""

    id: uuid.UUID
    content: str
    source_type: str
    source_ref: str
    importance_score: float
    similarity: float | None = None
    created_at: datetime


class SkillItem(BaseModel):
    """Serialized Layer 3 procedural skill memory."""

    id: uuid.UUID
    name: str
    description: str
    trigger_keywords: list[str]
    steps_markdown: str
    usage_count: int
    updated_at: datetime


@router.get("/core", response_model=CoreMemoryPayload)
async def get_core_memory(
    memory_service: MemoryService = Depends(get_memory_service),
) -> CoreMemoryPayload:
    """Return Layer 1 core_memory.md contents."""
    content = await memory_service.read_core_memory()
    return CoreMemoryPayload(content=content)


@router.put("/core", response_model=CoreMemoryPayload)
async def update_core_memory(
    payload: CoreMemoryPayload,
    memory_service: MemoryService = Depends(get_memory_service),
) -> CoreMemoryPayload:
    """Safely overwrite Layer 1 core_memory.md under an async file lock."""
    updated = await memory_service.write_core_memory(payload.content)
    return CoreMemoryPayload(content=updated)


@router.get("/episodes", response_model=list[EpisodeItem])
async def search_episodes(
    q: str | None = Query(default=None, max_length=2000),
    top_k: int = Query(default=5, ge=1, le=50),
    threshold: float = Query(default=0.0, ge=0.0, le=1.0),
    db: AsyncSession = Depends(get_db_session),
    memory_service: MemoryService = Depends(get_memory_service),
) -> list[EpisodeItem]:
    """Semantic search over Layer 2 EpisodeMemory (or list recent episodes if `q` omitted)."""
    if q and q.strip():
        hits = await memory_service.search_episodes(
            db, query=q.strip(), top_k=top_k, threshold=threshold
        )
        return [
            EpisodeItem(
                id=hit.episode.id,
                content=hit.episode.content,
                source_type=hit.episode.source_type,
                source_ref=hit.episode.source_ref,
                importance_score=hit.episode.importance_score,
                similarity=round(hit.similarity, 4),
                created_at=hit.episode.created_at,
            )
            for hit in hits
        ]

    stmt = select(EpisodeMemory).order_by(EpisodeMemory.created_at.desc()).limit(top_k)
    episodes = (await db.execute(stmt)).scalars().all()
    return [
        EpisodeItem(
            id=ep.id,
            content=ep.content,
            source_type=ep.source_type,
            source_ref=ep.source_ref,
            importance_score=ep.importance_score,
            similarity=None,
            created_at=ep.created_at,
        )
        for ep in episodes
    ]


@router.get("/skills", response_model=list[SkillItem])
async def list_skills(
    limit: int = Query(default=50, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
    memory_service: MemoryService = Depends(get_memory_service),
) -> list[SkillItem]:
    """List Layer 3 procedural skills ordered by usage."""
    skills = await memory_service.list_skills(db, limit=limit)
    return [
        SkillItem(
            id=s.id,
            name=s.name,
            description=s.description,
            trigger_keywords=list(s.trigger_keywords),
            steps_markdown=s.steps_markdown,
            usage_count=s.usage_count,
            updated_at=s.updated_at,
        )
        for s in skills
    ]

