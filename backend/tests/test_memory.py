"""Memory tests: Layer 1 concurrency lock, Layer 2 vector search, and MemoryConsolidator."""

import asyncio
import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.consolidator import MemoryConsolidator
from app.agent.memory import CoreMemoryUpdate, MemoryService


class _FakeConsolidationLLM:
    def __init__(self, response_payload: dict | str) -> None:
        self._payload = response_payload

    async def generate_json(self, system_prompt: str, user_prompt: str) -> str:
        if isinstance(self._payload, str):
            return self._payload
        return json.dumps(self._payload, ensure_ascii=False)


async def test_core_memory_read_write_and_concurrent_updates(
    memory_service: MemoryService,
) -> None:
    initial = await memory_service.read_core_memory()
    assert "Japanese" in initial

    # Launch 15 concurrent core memory additions; asyncio.Lock must preserve every update
    tasks = [
        memory_service.apply_core_memory_updates(
            [CoreMemoryUpdate(action="add", new_fact=f"Concurrent fact #{idx}")]
        )
        for idx in range(15)
    ]
    await asyncio.gather(*tasks)

    final_text = await memory_service.read_core_memory()
    for idx in range(15):
        assert f"- Concurrent fact #{idx}" in final_text


async def test_episode_memory_storage_dedup_and_cosine_search(
    db_session: AsyncSession,
    memory_service: MemoryService,
) -> None:
    ep1 = await memory_service.add_episode(
        db_session,
        content="Python FastAPI server uses pgvector for semantic RAG memory",
        source_type="conversation",
        importance_score=0.9,
    )
    ep2 = await memory_service.add_episode(
        db_session,
        content="Weekend grocery list: apples, bananas, and oat milk",
        source_type="conversation",
        importance_score=0.4,
    )
    # Near-identical duplicate should return existing episode instead of creating a duplicate
    ep1_dup = await memory_service.add_episode(
        db_session,
        content="Python FastAPI server uses pgvector for semantic RAG memory",
        source_type="conversation",
        importance_score=0.95,
    )
    assert ep1_dup.id == ep1.id
    assert ep1_dup.importance_score == 0.95

    hits = await memory_service.search_episodes(
        db_session,
        query="FastAPI pgvector semantic memory",
        top_k=2,
        threshold=0.05,
    )
    assert len(hits) >= 1
    assert hits[0].episode.id == ep1.id
    assert hits[0].similarity > hits[-1].similarity or hits[0].episode.id != ep2.id


async def test_memory_consolidator_extracts_facts_and_resolves_contradictions(
    db_session: AsyncSession,
    memory_service: MemoryService,
) -> None:
    # Existing core memory has "- Favorite drink: Green Tea"
    fake_llm = _FakeConsolidationLLM(
        {
            "core_memory_updates": [
                {
                    "action": "replace",
                    "target_fact": "Favorite drink:",
                    "new_fact": "Favorite drink: Espresso (updated)",
                },
                {
                    "action": "add",
                    "target_fact": None,
                    "new_fact": "Building Gemuse on Ubuntu Docker",
                },
            ],
            "episodes": [
                {
                    "content": "User switched favorite drink from Green Tea to Espresso on 2026-11-13.",
                    "source_type": "conversation",
                    "importance_score": 0.85,
                }
            ],
            "skill_candidate": {
                "name": "docker_health_check",
                "description": "Verify Docker Compose container health status",
                "trigger_keywords": ["docker", "health", "compose"],
                "steps_markdown": "1. Run `docker compose ps`\n2. Inspect `/health` endpoint",
            },
        }
    )
    consolidator = MemoryConsolidator(memory_service=memory_service, llm_client=fake_llm)

    result = await consolidator.consolidate_turn(
        db_session,
        user_message="実は最近、好きな飲み物が緑茶からエスプレッソに変わったんだ。",
        assistant_message="承知しました！好みを更新しておきます。",
        conversation_id="conv-123",
    )

    assert result.core_updated is True
    assert result.core_updates_applied == 2
    assert result.episodes_saved == 1
    assert result.skill_saved == "docker_health_check"

    updated_core = await memory_service.read_core_memory()
    assert "Green Tea" not in updated_core
    assert "- Favorite drink: Espresso (updated)" in updated_core
    assert "- Building Gemuse on Ubuntu Docker" in updated_core

    episodes = await memory_service.search_episodes(
        db_session, query="Espresso drink", top_k=3, threshold=0.05
    )
    assert len(episodes) == 1
    assert "Espresso" in episodes[0].episode.content

    skills = await memory_service.match_skills(db_session, "docker compose health")
    assert len(skills) == 1
    assert skills[0].name == "docker_health_check"

