"""Three-layer personal memory manager (Core Markdown, pgvector Episodes, Skills)."""

import asyncio
import hashlib
import math
import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import aiofiles
from google import genai
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import wrap_untrusted_data
from app.db.models import EpisodeMemory, SkillMemory

logger = get_logger(__name__)

EMBEDDING_DIM = 768

_DEFAULT_CORE_MEMORY = """# Core Memory (Layer 1)

## User Profile
- Name: (Not yet configured)
- Primary Language: Japanese

## Preferences & Communication Style
- Concise, factual, and cited answers.

## Key Facts & Ongoing Context
- (Auto-updated by Memory Consolidator)
"""

# Path-keyed asyncio locks to prevent concurrent write races on core_memory.md
_CORE_MEMORY_LOCKS: dict[Path, asyncio.Lock] = {}


def _get_lock_for_path(path: Path) -> asyncio.Lock:
    resolved = path.resolve()
    lock = _CORE_MEMORY_LOCKS.get(resolved)
    if lock is None:
        lock = asyncio.Lock()
        _CORE_MEMORY_LOCKS[resolved] = lock
    return lock


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two equal-length vectors."""
    if len(vec_a) != len(vec_b) or not vec_a:
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class CoreMemoryUpdate(BaseModel):
    """Structured update instruction for Layer 1 core_memory.md."""

    action: str = Field(..., pattern="^(add|replace)$")
    target_fact: str | None = None
    new_fact: str = Field(..., min_length=1)


@dataclass(frozen=True)
class EpisodeSearchResult:
    """Search hit from Layer 2 EpisodeMemory with similarity score."""

    episode: EpisodeMemory
    similarity: float


class EmbeddingProvider(ABC):
    """Abstract interface for text embedding generation."""

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """Convert text into a 768-dimensional float vector."""


class GeminiEmbeddingProvider(EmbeddingProvider):
    """Google GenAI embedding provider using official google-genai SDK."""

    def __init__(self, api_key: str, model_name: str = "gemini-embedding-001") -> None:
        self._client = genai.Client(api_key=api_key)
        self._model_name = model_name

    async def embed(self, text: str) -> list[float]:
        response = await self._client.aio.models.embed_content(
            model=self._model_name,
            contents=text,
            config={"output_dimensionality": EMBEDDING_DIM},
        )
        embeddings = getattr(response, "embeddings", None)
        if not embeddings:
            raise ValueError("Gemini embed_content returned empty embeddings")
        values = list(embeddings[0].values)
        if len(values) != EMBEDDING_DIM:
            raise ValueError(f"Expected {EMBEDDING_DIM}-dim vector, got {len(values)}")
        return values


class DeterministicMockEmbeddingProvider(EmbeddingProvider):
    """
    Deterministic 768-dim semantic hash embedder for offline tests and fallbacks.

    Uses character 2/3-grams and word tokens hashed into 768 dimensions and L2-normalized,
    producing meaningful cosine similarities across English and Japanese text.
    """

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self._dim = dim

    async def embed(self, text: str) -> list[float]:
        normalized = re.sub(r"\s+", " ", text.strip().lower())
        vec = [0.0] * self._dim
        if not normalized:
            vec[0] = 1.0
            return vec

        tokens: list[str] = []
        words = re.findall(r"[a-z0-9_]+|[\u3040-\u30ff\u4e00-\u9fff]+", normalized)
        tokens.extend(words)
        # Character bi-grams and tri-grams handle Japanese/CJK without a tokenizer
        compact = normalized.replace(" ", "")
        for n in (2, 3):
            for idx in range(max(0, len(compact) - n + 1)):
                tokens.append(compact[idx : idx + n])

        if not tokens:
            tokens = [normalized]

        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self._dim
            sign = 1.0 if (digest[4] & 1) == 0 else -1.0
            vec[bucket] += sign

        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            vec[0] = 1.0
            return vec
        return [v / norm for v in vec]


class MemoryService:
    """Manages Layer 1 (Core Markdown), Layer 2 (Episode RAG), and Layer 3 (Skill Memory)."""

    def __init__(
        self,
        core_memory_path: Path,
        embedding_provider: EmbeddingProvider,
        *,
        similarity_threshold: float = 0.55,
        top_k: int = 5,
    ) -> None:
        self.core_memory_path = Path(core_memory_path)
        self.embedding_provider = embedding_provider
        self.similarity_threshold = similarity_threshold
        self.top_k = top_k

    # ------------------------------------------------------------------
    # Layer 1: Core Memory (Markdown + aiofiles + asyncio.Lock)
    # ------------------------------------------------------------------
    async def read_core_memory(self) -> str:
        """Read Layer 1 core_memory.md safely under an async lock."""
        lock = _get_lock_for_path(self.core_memory_path)
        async with lock:
            if not self.core_memory_path.exists():
                self.core_memory_path.parent.mkdir(parents=True, exist_ok=True)
                async with aiofiles.open(self.core_memory_path, "w", encoding="utf-8") as fp:
                    await fp.write(_DEFAULT_CORE_MEMORY)
                return _DEFAULT_CORE_MEMORY

            async with aiofiles.open(self.core_memory_path, "r", encoding="utf-8") as fp:
                return await fp.read()

    async def write_core_memory(self, content: str) -> str:
        """Overwrite Layer 1 core_memory.md safely under an async lock."""
        lock = _get_lock_for_path(self.core_memory_path)
        async with lock:
            self.core_memory_path.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(self.core_memory_path, "w", encoding="utf-8") as fp:
                await fp.write(content)
            return content

    async def apply_core_memory_updates(self, updates: list[CoreMemoryUpdate]) -> str:
        """
        Atomically read, merge/resolve contradictions, and write core_memory.md.

        Performed under a single lock acquisition to prevent lost updates.
        """
        if not updates:
            return await self.read_core_memory()

        lock = _get_lock_for_path(self.core_memory_path)
        async with lock:
            if not self.core_memory_path.exists():
                self.core_memory_path.parent.mkdir(parents=True, exist_ok=True)
                current = _DEFAULT_CORE_MEMORY
            else:
                async with aiofiles.open(self.core_memory_path, "r", encoding="utf-8") as fp:
                    current = await fp.read()

            lines = current.splitlines()
            for upd in updates:
                new_line = upd.new_fact.strip()
                if not new_line.startswith("- "):
                    new_line = f"- {new_line}"

                if upd.action == "replace" and upd.target_fact:
                    target_norm = upd.target_fact.strip().lower()
                    replaced = False
                    for idx, existing in enumerate(lines):
                        if target_norm in existing.lower():
                            lines[idx] = new_line
                            replaced = True
                            break
                    if not replaced:
                        lines.append(new_line)
                else:
                    # Avoid duplicate line insertion
                    if not any(new_line.lower() == existing.strip().lower() for existing in lines):
                        lines.append(new_line)

            updated_content = "\n".join(lines).rstrip() + "\n"
            async with aiofiles.open(self.core_memory_path, "w", encoding="utf-8") as fp:
                await fp.write(updated_content)
            return updated_content

    # ------------------------------------------------------------------
    # Layer 2: Episode / Knowledge RAG (pgvector + cosine fallback)
    # ------------------------------------------------------------------
    async def add_episode(
        self,
        db: AsyncSession,
        *,
        content: str,
        source_type: str = "conversation",
        source_ref: str = "",
        importance_score: float = 0.5,
        deduplicate: bool = True,
    ) -> EpisodeMemory:
        """Embed and persist an EpisodeMemory entry, deduplicating near-identical facts."""
        cleaned = content.strip()
        if not cleaned:
            raise ValueError("Episode content cannot be empty")

        embedding = await self.embedding_provider.embed(cleaned)

        if deduplicate:
            existing_hits = await self.search_episodes(
                db,
                query=cleaned,
                top_k=3,
                threshold=0.96,
                query_embedding=embedding,
            )
            for hit in existing_hits:
                if hit.similarity >= 0.96 or hit.episode.content.strip().lower() == cleaned.lower():
                    hit.episode.importance_score = max(
                        hit.episode.importance_score, importance_score
                    )
                    await db.commit()
                    await db.refresh(hit.episode)
                    return hit.episode

        episode = EpisodeMemory(
            id=uuid.uuid4(),
            content=cleaned,
            source_type=source_type,
            source_ref=source_ref,
            embedding=embedding,
            importance_score=max(0.0, min(1.0, importance_score)),
        )
        db.add(episode)
        await db.commit()
        await db.refresh(episode)
        return episode

    async def search_episodes(
        self,
        db: AsyncSession,
        *,
        query: str,
        top_k: int | None = None,
        threshold: float | None = None,
        query_embedding: list[float] | None = None,
    ) -> list[EpisodeSearchResult]:
        """Search Layer 2 EpisodeMemory by cosine similarity."""
        limit = top_k if top_k is not None else self.top_k
        min_sim = threshold if threshold is not None else self.similarity_threshold
        query_vec = query_embedding or await self.embedding_provider.embed(query)

        bind = db.get_bind()
        dialect_name = bind.dialect.name if bind is not None else "sqlite"

        if dialect_name == "postgresql" and hasattr(EpisodeMemory.embedding, "cosine_distance"):
            distance_expr = EpisodeMemory.embedding.cosine_distance(query_vec)
            stmt = (
                select(EpisodeMemory, (1.0 - distance_expr).label("similarity"))
                .where(EpisodeMemory.embedding.is_not(None))
                .order_by(distance_expr.asc())
                .limit(limit)
            )
            rows = (await db.execute(stmt)).all()
            return [
                EpisodeSearchResult(episode=row[0], similarity=float(row[1]))
                for row in rows
                if float(row[1]) >= min_sim
            ]

        # SQLite / non-pgvector column fallback: compute exact cosine similarity in Python
        stmt = select(EpisodeMemory).order_by(EpisodeMemory.created_at.desc())
        episodes = (await db.execute(stmt)).scalars().all()
        scored: list[EpisodeSearchResult] = []
        for ep in episodes:
            if not ep.embedding:
                continue
            sim = cosine_similarity(query_vec, list(ep.embedding))
            if sim >= min_sim:
                scored.append(EpisodeSearchResult(episode=ep, similarity=sim))
        scored.sort(key=lambda item: item.similarity, reverse=True)
        return scored[:limit]

    # ------------------------------------------------------------------
    # Layer 3: Skill Memory
    # ------------------------------------------------------------------
    async def upsert_skill(
        self,
        db: AsyncSession,
        *,
        name: str,
        description: str,
        trigger_keywords: list[str],
        steps_markdown: str,
    ) -> SkillMemory:
        """Create or update a reusable procedural skill."""
        result = await db.execute(select(SkillMemory).where(SkillMemory.name == name))
        skill = result.scalar_one_or_none()
        if skill is None:
            skill = SkillMemory(
                id=uuid.uuid4(),
                name=name,
                description=description,
                trigger_keywords=trigger_keywords,
                steps_markdown=steps_markdown,
                usage_count=1,
            )
            db.add(skill)
        else:
            skill.description = description
            skill.trigger_keywords = trigger_keywords
            skill.steps_markdown = steps_markdown
            skill.usage_count += 1

        await db.commit()
        await db.refresh(skill)
        return skill

    async def list_skills(self, db: AsyncSession, limit: int = 50) -> list[SkillMemory]:
        """List stored procedural skills ordered by usage count."""
        stmt = select(SkillMemory).order_by(SkillMemory.usage_count.desc()).limit(limit)
        return list((await db.execute(stmt)).scalars().all())

    async def match_skills(
        self, db: AsyncSession, query: str, limit: int = 3
    ) -> list[SkillMemory]:
        """Find relevant procedural skills matching query keywords."""
        lowered = query.lower()
        all_skills = await self.list_skills(db, limit=100)
        matched: list[tuple[int, SkillMemory]] = []
        for skill in all_skills:
            score = 0
            if skill.name.lower() in lowered:
                score += 3
            for kw in skill.trigger_keywords:
                if kw.lower() in lowered:
                    score += 2
            if score > 0:
                matched.append((score, skill))
        matched.sort(key=lambda pair: (pair[0], pair[1].usage_count), reverse=True)
        return [skill for _, skill in matched[:limit]]

    async def build_prompt_memory_bundle(self, db: AsyncSession, query: str) -> dict[str, str]:
        """
        Assemble Layer 1, Layer 2 (sandboxed), and Layer 3 context for the orchestrator.
        """
        core_text = await self.read_core_memory()
        episodes = await self.search_episodes(db, query=query, top_k=self.top_k)
        skills = await self.match_skills(db, query=query, limit=3)

        episode_blocks: list[str] = []
        for hit in episodes:
            ep = hit.episode
            date_tag = ep.created_at.strftime("%Y-%m-%d") if ep.created_at else "unknown"
            source_label = f"Episode:{date_tag}:{ep.source_type}"
            raw_line = f"[Episode: {date_tag}] (score={hit.similarity:.2f}, ref={ep.source_ref or 'none'}) {ep.content}"
            if ep.source_type in {"discord", "email", "web", "mcp"}:
                episode_blocks.append(wrap_untrusted_data(source_label, raw_line))
            else:
                episode_blocks.append(raw_line)

        skill_blocks = [
            f"### [Skill: {s.name}]\n{s.description}\n{s.steps_markdown}"
            for s in skills
        ]

        return {
            "core_memory": core_text,
            "episodes_text": "\n".join(episode_blocks) if episode_blocks else "(該当エピソードなし)",
            "skills_text": "\n\n".join(skill_blocks) if skill_blocks else "(該当スキルなし)",
        }