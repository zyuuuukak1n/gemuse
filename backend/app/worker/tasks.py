"""ARQ asynchronous background worker tasks for long-running jobs & Memory Consolidator."""

import uuid
from typing import Any

from arq.connections import RedisSettings
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.agent.consolidator import GeminiConsolidationClient, MemoryConsolidator
from app.agent.memory import (
    DeterministicMockEmbeddingProvider,
    EmbeddingProvider,
    GeminiEmbeddingProvider,
    MemoryService,
)
from app.agent.orchestrator import AgentOrchestrator, GoogleGenAIStreamProvider
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger, register_secret
from app.core.security import ApprovalService
from app.db.models import Conversation, Job
from app.db.session import close_db, get_session_factory, init_db
from app.mcp_hub.base import ToolRegistry

logger = get_logger(__name__)


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Create production Gemini embedder when API key exists, else deterministic fallback."""
    if settings.GEMINI_API_KEY and not settings.GEMINI_API_KEY.startswith("your-"):
        return GeminiEmbeddingProvider(
            api_key=settings.GEMINI_API_KEY,
            model_name=settings.GEMINI_EMBEDDING_MODEL,
        )
    return DeterministicMockEmbeddingProvider()


def build_memory_service(settings: Settings) -> MemoryService:
    """Construct MemoryService from Settings."""
    return MemoryService(
        core_memory_path=settings.CORE_MEMORY_PATH,
        embedding_provider=build_embedding_provider(settings),
        similarity_threshold=settings.MEMORY_SIMILARITY_THRESHOLD,
        top_k=settings.MEMORY_TOP_K,
    )


async def consolidate_memory_task(
    ctx: dict[str, Any],
    user_message: str,
    assistant_message: str,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Background ARQ task: extract facts & resolve contradictions across 3-layer memory."""
    settings: Settings = ctx.get("settings") or get_settings()
    session_factory = ctx.get("session_factory") or get_session_factory()
    memory_service: MemoryService = ctx.get("memory_service") or build_memory_service(settings)

    llm_client = ctx.get("consolidation_client")
    if llm_client is None:
        llm_client = GeminiConsolidationClient(
            api_key=settings.GEMINI_API_KEY,
            model_name=settings.GEMINI_MODEL,
        )

    consolidator = MemoryConsolidator(memory_service=memory_service, llm_client=llm_client)
    async with session_factory() as db:
        result = await consolidator.consolidate_turn(
            db,
            user_message=user_message,
            assistant_message=assistant_message,
            conversation_id=conversation_id,
        )
    return result.model_dump()


async def run_agent_job(ctx: dict[str, Any], job_id: str) -> dict[str, Any]:
    """Background ARQ task: execute a queued Job to completion even if client disconnected."""
    settings: Settings = ctx.get("settings") or get_settings()
    session_factory = ctx.get("session_factory") or get_session_factory()
    memory_service: MemoryService = ctx.get("memory_service") or build_memory_service(settings)
    approval_service: ApprovalService = ctx.get("approval_service") or ApprovalService(
        settings.APPROVAL_HMAC_SECRET,
        default_ttl_seconds=settings.APPROVAL_TOKEN_TTL_SECONDS,
    )
    tool_registry: ToolRegistry = ctx.get("tool_registry") or ToolRegistry(approval_service)

    llm_provider = ctx.get("llm_provider")
    if llm_provider is None:
        llm_provider = GoogleGenAIStreamProvider(api_key=settings.GEMINI_API_KEY)

    orchestrator = AgentOrchestrator(
        model_name=settings.GEMINI_MODEL,
        llm_provider=llm_provider,
        memory_service=memory_service,
        tool_registry=tool_registry,
        approval_service=approval_service,
    )

    parsed_job_id = uuid.UUID(job_id)
    async with session_factory() as db:
        job_result = await db.execute(select(Job).where(Job.id == parsed_job_id))
        job = job_result.scalar_one_or_none()
        if job is None:
            logger.error("Background job not found", extra={"job_id": job_id})
            return {"status": "not_found", "job_id": job_id}

        if job.status in ("completed", "failed"):
            return {"status": job.status, "job_id": job_id}

        conv_result = await db.execute(
            select(Conversation)
            .options(selectinload(Conversation.messages))
            .where(Conversation.id == job.conversation_id)
        )
        conversation = conv_result.scalar_one()

        async def _post_turn(user_msg: str, assistant_msg: str, conv_id: str) -> None:
            redis_pool = ctx.get("redis")
            if redis_pool is not None:
                await redis_pool.enqueue_job(
                    "consolidate_memory_task",
                    user_msg,
                    assistant_msg,
                    conv_id,
                )

        async for _event in orchestrator.stream_chat(
            db,
            conversation=conversation,
            job=job,
            user_prompt=job.prompt,
            requested_thinking_level=job.thinking_level,  # type: ignore[arg-type]
            persist_user_message=False,
            post_turn_hook=_post_turn,
        ):
            pass

        await db.refresh(job)
        return {"status": job.status, "job_id": str(job.id)}


async def startup(ctx: dict[str, Any]) -> None:
    """Initialize worker resources on ARQ startup."""
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL)
    register_secret(settings.API_SECRET_TOKEN)
    register_secret(settings.APPROVAL_HMAC_SECRET)
    register_secret(settings.GEMINI_API_KEY)

    await init_db()
    ctx["settings"] = settings
    ctx["session_factory"] = get_session_factory()
    ctx["memory_service"] = build_memory_service(settings)
    ctx["approval_service"] = ApprovalService(
        settings.APPROVAL_HMAC_SECRET,
        default_ttl_seconds=settings.APPROVAL_TOKEN_TTL_SECONDS,
    )
    ctx["tool_registry"] = ToolRegistry(ctx["approval_service"])
    logger.info("ARQ worker initialized")


async def shutdown(ctx: dict[str, Any]) -> None:
    """Dispose worker resources on ARQ shutdown."""
    await close_db()
    logger.info("ARQ worker shut down")


def _get_redis_settings() -> RedisSettings:
    settings = get_settings()
    return RedisSettings.from_dsn(settings.REDIS_URL)


class WorkerSettings:
    """ARQ WorkerSettings entrypoint (`arq app.worker.tasks.WorkerSettings`)."""

    functions = [run_agent_job, consolidate_memory_task]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = _get_redis_settings()
    max_jobs = 10
    job_timeout = 600

