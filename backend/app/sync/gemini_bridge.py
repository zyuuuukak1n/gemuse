"""Official Gemini one-click chat ingest, context handoff generator, and Takeout importer."""

import json
import uuid
from typing import Any

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent.consolidator import ConsolidationClientProtocol, MemoryConsolidator
from app.agent.memory import MemoryService
from app.core.logging import get_logger
from app.core.security import wrap_untrusted_data
from app.db.models import Conversation, EpisodeMemory

logger = get_logger(__name__)


class IngestTurn(BaseModel):
    """Single message turn captured from the official Gemini web UI."""

    role: str = Field(..., min_length=1, max_length=32)
    content: str = Field(..., min_length=1)


class IngestResult(BaseModel):
    """Summary returned after consolidating an ingested external Gemini chat."""

    status: str = "consolidated"
    title: str
    core_updated: bool
    core_updates_applied: int
    episodes_saved: int
    skill_saved: str | None = None
    core_memory_preview: str


class HandoffPackage(BaseModel):
    """Structured prompt package ready to paste into official Gemini Deep Research."""

    conversation_id: uuid.UUID | None = None
    conversation_title: str
    message_count: int
    handoff_prompt: str


class TakeoutImportResult(BaseModel):
    """Counts summarizing a Google Takeout Gemini activity import."""

    format_detected: str
    parsed_count: int
    imported_count: int
    skipped_duplicates: int


class GeminiBridgeService:
    """Bridges official Gemini web workflows with Gemuse's 3-layer memory."""

    def __init__(
        self,
        *,
        memory_service: MemoryService,
        consolidation_client: ConsolidationClientProtocol,
    ) -> None:
        self.memory_service = memory_service
        self.consolidation_client = consolidation_client
        self.consolidator = MemoryConsolidator(
            memory_service=memory_service,
            llm_client=consolidation_client,
        )

    async def ingest_chat(
        self,
        db: AsyncSession,
        *,
        title: str,
        source_url: str,
        messages: list[IngestTurn] | None = None,
        raw_text: str | None = None,
    ) -> IngestResult:
        """
        Safely isolate an official Gemini conversation and run MemoryConsolidator.
        """
        if messages:
            user_turns = [m.content for m in messages if m.role.lower() == "user"]
            model_turns = [
                m.content for m in messages if m.role.lower() in ("model", "assistant", "gemini")
            ]
            combined = "\n".join(f"[{m.role}] {m.content}" for m in messages)
            user_text = "\n".join(user_turns) or combined
            assistant_text = "\n".join(model_turns) or "(External Gemini response)"
        elif raw_text and raw_text.strip():
            combined = raw_text.strip()
            user_text = combined
            assistant_text = "(Imported from external Gemini transcript)"
        else:
            raise ValueError("Either `messages` or `raw_text` must be non-empty")

        isolated_user = wrap_untrusted_data(f"gemini_web:{source_url}", user_text)
        isolated_assistant = wrap_untrusted_data(f"gemini_web:{source_url}", assistant_text)

        result = await self.consolidator.consolidate_turn(
            db,
            user_message=f"[External Title: {title}]\n{isolated_user}",
            assistant_message=isolated_assistant,
            conversation_id=f"ingest:{title[:60]}",
        )
        updated_core = await self.memory_service.read_core_memory()

        return IngestResult(
            status="consolidated",
            title=title,
            core_updated=result.core_updated,
            core_updates_applied=result.core_updates_applied,
            episodes_saved=result.episodes_saved,
            skill_saved=result.skill_saved,
            core_memory_preview=updated_core[:1500],
        )

    async def generate_handoff(
        self,
        db: AsyncSession,
        *,
        conversation_id: uuid.UUID | None = None,
        goal: str | None = None,
    ) -> HandoffPackage:
        """Build a structured handoff prompt from conversation history and Core Memory."""
        if conversation_id is not None:
            stmt = (
                select(Conversation)
                .options(selectinload(Conversation.messages))
                .where(Conversation.id == conversation_id)
            )
        else:
            stmt = (
                select(Conversation)
                .options(selectinload(Conversation.messages))
                .order_by(Conversation.updated_at.desc())
                .limit(1)
            )

        conv = (await db.execute(stmt)).scalar_one_or_none()
        core_memory = await self.memory_service.read_core_memory()

        if conv is None:
            prompt = (
                "# Gemuse Context Handoff\n\n"
                "## Core Profile & Constraints\n"
                f"{core_memory.strip()}\n\n"
                "## Goal / Next Task\n"
                f"{goal.strip() if goal else '（ここにDeep Research等への依頼内容を入力してください）'}\n"
            )
            return HandoffPackage(
                conversation_id=None,
                conversation_title="No active conversation",
                message_count=0,
                handoff_prompt=prompt,
            )

        recent_messages = list(conv.messages or [])[-12:]
        transcript_lines = [
            f"- **{m.role.upper()}**: {m.content.strip()[:500]}"
            for m in recent_messages
            if m.role in ("user", "assistant")
        ]
        recent_episodes = await self.memory_service.search_episodes(
            db,
            query=goal or conv.title or "recent context",
            top_k=3,
            threshold=0.05,
        )
        episode_lines = [f"- {hit.episode.content}" for hit in recent_episodes]

        handoff_prompt = (
            f"# Gemuse Context Handoff: {conv.title}\n\n"
            "以下はパーソナルAI基盤「Gemuse」から引き継ぐ前提条件と直近の対話文脈です。"
            "この前提を踏まえて作業・調査を続行してください。\n\n"
            "## 1. ユーザー設定・基本ルール（Core Memory）\n"
            f"{core_memory.strip()}\n\n"
            "## 2. 関連エピソード記憶\n"
            f"{chr(10).join(episode_lines) if episode_lines else '- （該当エピソードなし）'}\n\n"
            "## 3. 直近の会話コンテキスト\n"
            f"{chr(10).join(transcript_lines) if transcript_lines else '- （履歴なし）'}\n\n"
            "## 4. 引き継ぎタスク・目的\n"
            f"{goal.strip() if goal else '上記の文脈を引き継ぎ、次のステップを提案・調査してください。'}\n"
        )
        return HandoffPackage(
            conversation_id=conv.id,
            conversation_title=conv.title,
            message_count=len(conv.messages or []),
            handoff_prompt=handoff_prompt,
        )

    @staticmethod
    def parse_takeout_entries(raw_bytes: bytes, filename: str = "") -> tuple[str, list[dict[str, str]]]:
        """Parse Google Takeout Gemini Apps Activity JSON or HTML into normalized entries."""
        text = raw_bytes.decode("utf-8", errors="replace").strip()
        lower_name = filename.lower()
        entries: list[dict[str, str]] = []

        if lower_name.endswith(".json") or text.startswith("[") or text.startswith("{"):
            data = json.loads(text)
            records = data if isinstance(data, list) else data.get("activities", [])
            for item in records:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or item.get("prompt") or "").strip()
                time_str = str(item.get("time") or item.get("timestamp") or "").strip()
                details_parts: list[str] = []
                for html_item in item.get("safeHtmlItem") or []:
                    if isinstance(html_item, dict) and html_item.get("html"):
                        soup = BeautifulSoup(str(html_item["html"]), "html.parser")
                        details_parts.append(soup.get_text(" ", strip=True))
                if item.get("response"):
                    details_parts.append(str(item["response"]))
                combined = " | ".join(p for p in [title, *details_parts] if p).strip()
                if len(combined) >= 10:
                    entries.append({"content": combined, "time": time_str})
            return "json", entries

        # HTML format (Google Takeout MyActivity.html)
        soup = BeautifulSoup(text, "html.parser")
        cells = soup.select(".outer-cell, .content-cell")
        seen_texts: set[str] = set()
        for cell in cells:
            cell_text = " ".join(cell.get_text(" ", strip=True).split())
            if len(cell_text) >= 15 and cell_text not in seen_texts:
                seen_texts.add(cell_text)
                entries.append({"content": cell_text[:2000], "time": ""})
        return "html", entries

    async def import_takeout(
        self,
        db: AsyncSession,
        *,
        raw_bytes: bytes,
        filename: str = "",
        max_items: int = 100,
    ) -> TakeoutImportResult:
        """Parse Google Takeout Gemini activity and batch-register deduplicated episodes."""
        fmt, entries = self.parse_takeout_entries(raw_bytes, filename=filename)
        sliced = entries[:max_items]

        before_count = int(
            (await db.execute(select(func.count()).select_from(EpisodeMemory))).scalar_one()
        )
        seen_ids: set[uuid.UUID] = set()
        imported = 0
        skipped = 0

        for idx, entry in enumerate(sliced):
            existing_hits = await self.memory_service.search_episodes(
                db,
                query=entry["content"],
                top_k=1,
                threshold=0.96,
            )
            if existing_hits or any(
                entry["content"].strip().lower() == prev["content"].strip().lower()
                for prev in sliced[:idx]
            ):
                skipped += 1
                continue

            ep = await self.memory_service.add_episode(
                db,
                content=entry["content"],
                source_type="gemini_takeout",
                source_ref=entry.get("time") or filename or "takeout",
                importance_score=0.65,
                deduplicate=True,
            )
            if ep.id in seen_ids:
                skipped += 1
            else:
                seen_ids.add(ep.id)
                imported += 1

        after_count = int(
            (await db.execute(select(func.count()).select_from(EpisodeMemory))).scalar_one()
        )
        actual_imported = max(0, after_count - before_count)
        actual_skipped = max(0, len(sliced) - actual_imported)
        logger.info(
            "Takeout import completed",
            extra={
                "format": fmt,
                "parsed": len(sliced),
                "imported": actual_imported,
                "skipped_duplicates": actual_skipped,
            },
        )
        return TakeoutImportResult(
            format_detected=fmt,
            parsed_count=len(sliced),
            imported_count=actual_imported,
            skipped_duplicates=actual_skipped,
        )

