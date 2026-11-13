"""Memory Consolidator: post-turn fact extraction, contradiction resolution, and storage."""

import json
import re
from typing import Protocol

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.memory import CoreMemoryUpdate, MemoryService
from app.core.logging import get_logger

logger = get_logger(__name__)


class ConsolidationClientProtocol(Protocol):
    """Protocol for LLM text completion used by MemoryConsolidator."""

    async def generate_json(self, system_prompt: str, user_prompt: str) -> str:
        """Generate a JSON string using low-thinking-level inference."""


class ExtractedEpisode(BaseModel):
    """Candidate episode memory extracted from a conversation turn."""

    content: str = Field(..., min_length=1)
    source_type: str = "conversation"
    importance_score: float = Field(default=0.6, ge=0.0, le=1.0)


class ExtractedSkill(BaseModel):
    """Candidate reusable skill extracted from a successful multi-step turn."""

    name: str = Field(..., min_length=1, max_length=128)
    description: str = Field(..., min_length=1)
    trigger_keywords: list[str] = Field(default_factory=list)
    steps_markdown: str = Field(..., min_length=1)


class ConsolidationPlan(BaseModel):
    """Structured extraction output returned by Gemini (low thinking level)."""

    core_memory_updates: list[CoreMemoryUpdate] = Field(default_factory=list)
    episodes: list[ExtractedEpisode] = Field(default_factory=list)
    skill_candidate: ExtractedSkill | None = None


class ConsolidationResult(BaseModel):
    """Summary of persisted changes after running MemoryConsolidator."""

    core_updated: bool
    core_updates_applied: int
    episodes_saved: int
    skill_saved: str | None = None


_CONSOLIDATION_SYSTEM_PROMPT = """You are the Memory Consolidator for Gemuse.
Analyze the latest conversation turn alongside the existing Layer 1 Core Memory.
Extract ONLY durable, high-signal facts about the user's preferences, rules, schedule, projects, or important knowledge.

Return ONLY valid JSON matching this schema:
{
  "core_memory_updates": [
    {"action": "add" | "replace", "target_fact": "<short substring of outdated line if action==replace, else null>", "new_fact": "<concise bullet text>"}
  ],
  "episodes": [
    {"content": "<self-contained factual summary>", "source_type": "conversation", "importance_score": 0.0-1.0}
  ],
  "skill_candidate": null | {
    "name": "<slug>",
    "description": "<when to use>",
    "trigger_keywords": ["..."],
    "steps_markdown": "1. ..."
  }
}

Rules:
- If a new user preference or fact contradicts an existing line in Core Memory, emit action="replace" with target_fact matching the old line and new_fact containing the latest truth.
- Do not duplicate facts already accurately stated in Core Memory.
- If nothing durable was learned, return empty arrays and null.
"""


def _strip_json_fences(raw: str) -> str:
    cleaned = raw.strip()
    fence_match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL | re.IGNORECASE)
    if fence_match:
        return fence_match.group(1).strip()
    return cleaned


class GeminiConsolidationClient:
    """Google GenAI client configured at `low` thinking level for memory extraction."""

    def __init__(self, api_key: str, model_name: str = "gemini-4-hydrogen") -> None:
        from google import genai
        from google.genai import types

        self._client = genai.Client(api_key=api_key)
        self._model_name = model_name
        self._types = types

    async def generate_json(self, system_prompt: str, user_prompt: str) -> str:
        config_kwargs: dict[str, object] = {
            "system_instruction": system_prompt,
            "temperature": 0.1,
            "response_mime_type": "application/json",
        }
        thinking_cls = getattr(self._types, "ThinkingConfig", None)
        if thinking_cls is not None:
            try:
                config_kwargs["thinking_config"] = thinking_cls(thinking_level="low")
            except TypeError:
                config_kwargs["thinking_config"] = thinking_cls(thinking_budget=256)

        response = await self._client.aio.models.generate_content(
            model=self._model_name,
            contents=user_prompt,
            config=self._types.GenerateContentConfig(**config_kwargs),
        )
        return response.text or "{}"


class MemoryConsolidator:
    """Extracts facts and resolves contradictions across Layer 1, Layer 2, and Layer 3."""

    def __init__(
        self,
        memory_service: MemoryService,
        llm_client: ConsolidationClientProtocol,
    ) -> None:
        self.memory_service = memory_service
        self.llm_client = llm_client

    async def consolidate_turn(
        self,
        db: AsyncSession,
        *,
        user_message: str,
        assistant_message: str,
        conversation_id: str | None = None,
    ) -> ConsolidationResult:
        """Analyze a completed conversation turn and persist memory updates."""
        current_core = await self.memory_service.read_core_memory()
        prompt = (
            f"### Existing Core Memory\n{current_core}\n\n"
            f"### Latest Turn\n"
            f"User: {user_message}\n"
            f"Assistant: {assistant_message}\n"
        )

        raw_json = await self.llm_client.generate_json(_CONSOLIDATION_SYSTEM_PROMPT, prompt)
        try:
            parsed_dict = json.loads(_strip_json_fences(raw_json))
            plan = ConsolidationPlan.model_validate(parsed_dict)
        except Exception as exc:
            logger.warning(
                "Memory consolidation returned invalid JSON; skipping turn",
                extra={"error": str(exc), "conversation_id": conversation_id},
            )
            return ConsolidationResult(
                core_updated=False,
                core_updates_applied=0,
                episodes_saved=0,
            )

        core_updated = False
        if plan.core_memory_updates:
            await self.memory_service.apply_core_memory_updates(plan.core_memory_updates)
            core_updated = True

        episodes_saved = 0
        for ep in plan.episodes:
            await self.memory_service.add_episode(
                db,
                content=ep.content,
                source_type=ep.source_type,
                source_ref=conversation_id or "",
                importance_score=ep.importance_score,
                deduplicate=True,
            )
            episodes_saved += 1

        skill_saved: str | None = None
        if plan.skill_candidate is not None:
            skill = await self.memory_service.upsert_skill(
                db,
                name=plan.skill_candidate.name,
                description=plan.skill_candidate.description,
                trigger_keywords=plan.skill_candidate.trigger_keywords,
                steps_markdown=plan.skill_candidate.steps_markdown,
            )
            skill_saved = skill.name

        logger.info(
            "Memory consolidation completed",
            extra={
                "conversation_id": conversation_id,
                "core_updates": len(plan.core_memory_updates),
                "episodes_saved": episodes_saved,
                "skill_saved": skill_saved,
            },
        )
        return ConsolidationResult(
            core_updated=core_updated,
            core_updates_applied=len(plan.core_memory_updates),
            episodes_saved=episodes_saved,
            skill_saved=skill_saved,
        )

