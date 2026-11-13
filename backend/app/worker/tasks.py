"""ARQ asynchronous background worker tasks for long-running jobs & Memory Consolidator."""

import uuid
from datetime import datetime
from typing import Any

from arq.connections import RedisSettings
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.agent.browser_ctrl import (
    BrowserController,
    BrowserSandboxClient,
    ComputerUsePlanner,
    GeminiComputerUsePlanner,
    HttpxBrowserSandboxClient,
)
from app.agent.consolidator import GeminiConsolidationClient, MemoryConsolidator
from app.agent.memory import (
    DeterministicMockEmbeddingProvider,
    EmbeddingProvider,
    GeminiEmbeddingProvider,
    MemoryService,
)
from app.agent.orchestrator import AgentOrchestrator, GoogleGenAIStreamProvider
from app.agent.proactive import ProactiveEngine
from app.agent.voice_bridge import VoiceBridgeRegistry
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger, register_secret
from app.core.security import ApprovalService
from app.db.models import Conversation, Job
from app.db.session import close_db, get_session_factory, init_db
from app.mcp_hub.manager import MCPHubManager
from app.mcp_hub.servers.browser_mcp import BrowserMCPConnector
from app.mcp_hub.servers.discord_mcp import DiscordMCPConnector
from app.mcp_hub.servers.google_mcp import GoogleWorkspaceMCPConnector
from app.mcp_hub.servers.mail_mcp import (
    GmailMailClient,
    ICloudMailClient,
    MailMCPConnector,
    OutlookMailClient,
)
from app.mcp_hub.servers.social_mcp import SocialMediaMCPConnector
from app.mcp_hub.servers.voice_mcp import VoiceMCPConnector

logger = get_logger(__name__)


def register_settings_secrets(settings: Settings) -> None:
    """Register all configured secrets so SecretMaskingFilter redacts them."""
    for secret_val in (
        settings.API_SECRET_TOKEN,
        settings.APPROVAL_HMAC_SECRET,
        settings.GEMUSE_INGEST_TOKEN,
        settings.GEMINI_API_KEY,
        settings.DISCORD_BOT_TOKEN,
        settings.OUTLOOK_CLIENT_SECRET,
        settings.OUTLOOK_REFRESH_TOKEN,
        settings.ICLOUD_APP_PASSWORD,
        settings.GOOGLE_OAUTH_CLIENT_SECRET,
        settings.GOOGLE_OAUTH_REFRESH_TOKEN,
        settings.X_BEARER_TOKEN,
        settings.YOUTUBE_API_KEY,
        settings.YOUTUBE_OAUTH_REFRESH_TOKEN,
        settings.NOVNC_PASSWORD,
        settings.DISCORD_NOTIFY_WEBHOOK_URL,
        settings.VAPID_PRIVATE_KEY,
        settings.TWILIO_ACCOUNT_SID,
        settings.TWILIO_AUTH_TOKEN,
    ):
        register_secret(secret_val)


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


def build_browser_controller(
    settings: Settings,
    *,
    redis_client: Redis | None = None,
    sandbox_override: BrowserSandboxClient | None = None,
    planner_override: ComputerUsePlanner | None = None,
) -> BrowserController:
    """Construct BrowserController targeting the isolated gemuse-browser container."""
    sandbox = sandbox_override or HttpxBrowserSandboxClient(base_url=settings.BROWSER_SANDBOX_URL)
    if planner_override is not None:
        planner: ComputerUsePlanner | None = planner_override
    elif settings.GEMINI_API_KEY and not settings.GEMINI_API_KEY.startswith("your-"):
        planner = GeminiComputerUsePlanner(
            api_key=settings.GEMINI_API_KEY,
            model_name=settings.GEMINI_MODEL,
        )
    else:
        planner = None
    return BrowserController(
        sandbox=sandbox,
        planner=planner,
        redis=redis_client,
        shared_files_dir=settings.SHARED_FILES_DIR,
        artifact_ttl_seconds=settings.BROWSER_ARTIFACT_TTL_SECONDS,
    )


def build_voice_bridge_registry(settings: Settings) -> VoiceBridgeRegistry:
    """Construct VoiceBridgeRegistry configured for Gemini Live API."""
    api_key = (
        settings.GEMINI_API_KEY
        if settings.GEMINI_API_KEY and not settings.GEMINI_API_KEY.startswith("your-")
        else ""
    )
    return VoiceBridgeRegistry(
        api_key=api_key,
        live_model=settings.GEMINI_LIVE_MODEL,
    )


def build_proactive_engine(
    settings: Settings,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    redis_client: Redis | None = None,
    memory_service: MemoryService | None = None,
) -> ProactiveEngine:
    """Construct ProactiveEngine with graceful connector-backed calendar & mail fetchers."""
    google_client = GoogleWorkspaceMCPConnector(
        client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
        client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
        refresh_token=settings.GOOGLE_OAUTH_REFRESH_TOKEN,
        calendar_id=settings.GOOGLE_CALENDAR_ID,
        approval_service=ApprovalService(
            settings.APPROVAL_HMAC_SECRET,
            default_ttl_seconds=settings.APPROVAL_TOKEN_TTL_SECONDS,
        ),
        session_factory=session_factory,
    )
    mail_clients = {
        "outlook": OutlookMailClient(
            client_id=settings.OUTLOOK_CLIENT_ID,
            client_secret=settings.OUTLOOK_CLIENT_SECRET,
            tenant_id=settings.OUTLOOK_TENANT_ID,
            refresh_token=settings.OUTLOOK_REFRESH_TOKEN,
        ),
        "icloud": ICloudMailClient(
            email_address=settings.ICLOUD_EMAIL,
            app_password=settings.ICLOUD_APP_PASSWORD,
            imap_host=settings.ICLOUD_IMAP_HOST,
            imap_port=settings.ICLOUD_IMAP_PORT,
            smtp_host=settings.ICLOUD_SMTP_HOST,
            smtp_port=settings.ICLOUD_SMTP_PORT,
        ),
        "gmail": GmailMailClient(
            client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
            client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
            refresh_token=settings.GOOGLE_OAUTH_REFRESH_TOKEN,
        ),
    }

    async def _fetch_calendar(start: datetime, end: datetime) -> list[dict[str, Any]]:
        if not google_client.is_configured():
            return []
        try:
            return await google_client.list_events_structured(
                time_min=start.isoformat(),
                time_max=end.isoformat(),
            )
        except Exception as exc:
            logger.warning("Proactive calendar fetch skipped: %s", exc)
            return []

    async def _fetch_mail(keywords: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        query = " ".join(keywords[:3]) if keywords else ""
        for provider_name, client in mail_clients.items():
            if not client.is_configured():
                continue
            try:
                emails = await client.search(query=query, limit=10)
                for item in emails:
                    results.append(
                        {
                            "provider": provider_name,
                            "message_id": item.message_id,
                            "subject": item.subject,
                            "sender": item.sender,
                            "snippet": item.snippet,
                            "received_at": item.received_at.isoformat(),
                        }
                    )
            except Exception as exc:
                logger.warning("Proactive mail fetch skipped provider=%s: %s", provider_name, exc)
        return results

    return ProactiveEngine(
        rules_path=settings.PROACTIVE_RULES_PATH,
        timezone_name=settings.PROACTIVE_TIMEZONE,
        session_factory=session_factory,
        redis=redis_client,
        discord_webhook_url=settings.DISCORD_NOTIFY_WEBHOOK_URL,
        vapid_public_key=settings.VAPID_PUBLIC_KEY,
        vapid_private_key=settings.VAPID_PRIVATE_KEY,
        vapid_claims_email=settings.VAPID_CLAIMS_EMAIL,
        memory_service=memory_service,
        calendar_fetcher=_fetch_calendar,
        mail_fetcher=_fetch_mail,
    )


async def build_mcp_hub(
    settings: Settings,
    *,
    approval_service: ApprovalService,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    load_external: bool = True,
    browser_controller: BrowserController | None = None,
    voice_bridge_registry: VoiceBridgeRegistry | None = None,
) -> tuple[MCPHubManager, DiscordMCPConnector]:
    """Construct and initialize MCPHubManager with graceful credential degradation."""
    hub = MCPHubManager(
        approval_service=approval_service,
        tool_timeout_seconds=settings.MCP_TOOL_TIMEOUT_SECONDS,
    )

    discord_connector = DiscordMCPConnector(
        bot_token=settings.DISCORD_BOT_TOKEN,
        permissions_path=settings.CONFIG_DIR / "discord_permissions.yaml",
        approval_service=approval_service,
        session_factory=session_factory,
        api_base_url=settings.DISCORD_API_BASE_URL,
    )
    mail_connector = MailMCPConnector(
        providers={
            "outlook": OutlookMailClient(
                client_id=settings.OUTLOOK_CLIENT_ID,
                client_secret=settings.OUTLOOK_CLIENT_SECRET,
                tenant_id=settings.OUTLOOK_TENANT_ID,
                refresh_token=settings.OUTLOOK_REFRESH_TOKEN,
            ),
            "icloud": ICloudMailClient(
                email_address=settings.ICLOUD_EMAIL,
                app_password=settings.ICLOUD_APP_PASSWORD,
                imap_host=settings.ICLOUD_IMAP_HOST,
                imap_port=settings.ICLOUD_IMAP_PORT,
                smtp_host=settings.ICLOUD_SMTP_HOST,
                smtp_port=settings.ICLOUD_SMTP_PORT,
            ),
            "gmail": GmailMailClient(
                client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
                client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
                refresh_token=settings.GOOGLE_OAUTH_REFRESH_TOKEN,
            ),
        },
        approval_service=approval_service,
        session_factory=session_factory,
    )
    google_connector = GoogleWorkspaceMCPConnector(
        client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
        client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
        refresh_token=settings.GOOGLE_OAUTH_REFRESH_TOKEN,
        calendar_id=settings.GOOGLE_CALENDAR_ID,
        approval_service=approval_service,
        session_factory=session_factory,
    )
    social_connector = SocialMediaMCPConnector(
        x_bearer_token=settings.X_BEARER_TOKEN,
        x_user_id=settings.X_USER_ID,
        youtube_api_key=settings.YOUTUBE_API_KEY,
    )
    active_browser_ctrl = browser_controller or build_browser_controller(settings)
    browser_connector = BrowserMCPConnector(
        controller=active_browser_ctrl,
        session_factory=session_factory,
        configured=bool(settings.BROWSER_SANDBOX_URL),
    )
    active_voice_registry = voice_bridge_registry or build_voice_bridge_registry(settings)
    voice_connector = VoiceMCPConnector(
        account_sid=settings.TWILIO_ACCOUNT_SID,
        auth_token=settings.TWILIO_AUTH_TOKEN,
        from_number=settings.TWILIO_PHONE_NUMBER,
        public_base_url=settings.PUBLIC_BASE_URL,
        bridge_registry=active_voice_registry,
        approval_service=approval_service,
        session_factory=session_factory,
    )

    for connector in (
        discord_connector,
        mail_connector,
        google_connector,
        social_connector,
        browser_connector,
        voice_connector,
    ):
        await hub.register_connector(connector)

    if load_external:
        await hub.load_external_mcp_servers(settings.CONFIG_DIR / "mcp_servers.json")

    return hub, discord_connector


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

    mcp_hub: MCPHubManager | None = ctx.get("mcp_hub")
    if mcp_hub is None:
        mcp_hub, _ = await build_mcp_hub(
            settings,
            approval_service=approval_service,
            session_factory=session_factory,
            load_external=False,
        )
    tool_registry = ctx.get("tool_registry") or mcp_hub.tool_registry

    llm_provider = ctx.get("llm_provider")
    if llm_provider is None:
        llm_provider = GoogleGenAIStreamProvider(api_key=settings.GEMINI_API_KEY)

    orchestrator = AgentOrchestrator(
        model_name=settings.GEMINI_MODEL,
        llm_provider=llm_provider,
        memory_service=memory_service,
        tool_registry=tool_registry,
        approval_service=approval_service,
        mcp_hub=mcp_hub,
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


async def cleanup_browser_artifacts_task(ctx: dict[str, Any]) -> dict[str, int]:
    """Periodic worker task removing expired browser screenshots and download artifacts."""
    browser_ctrl: BrowserController | None = ctx.get("browser_controller")
    if browser_ctrl is None:
        settings: Settings = ctx.get("settings") or get_settings()
        browser_ctrl = build_browser_controller(settings)
    removed = browser_ctrl.cleanup_expired_artifacts()
    return {"removed_count": removed}


async def startup(ctx: dict[str, Any]) -> None:
    """Initialize worker resources on ARQ startup."""
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL)
    register_settings_secrets(settings)

    await init_db()
    session_factory = get_session_factory()
    approval_service = ApprovalService(
        settings.APPROVAL_HMAC_SECRET,
        default_ttl_seconds=settings.APPROVAL_TOKEN_TTL_SECONDS,
    )
    redis_client = ctx.get("redis")
    memory_service = build_memory_service(settings)
    browser_controller = build_browser_controller(settings, redis_client=redis_client)
    voice_registry = build_voice_bridge_registry(settings)

    mcp_hub, _ = await build_mcp_hub(
        settings,
        approval_service=approval_service,
        session_factory=session_factory,
        browser_controller=browser_controller,
        voice_bridge_registry=voice_registry,
    )

    proactive_engine = build_proactive_engine(
        settings,
        session_factory=session_factory,
        redis_client=redis_client,
        memory_service=memory_service,
    )
    proactive_engine.start_scheduler()

    ctx["settings"] = settings
    ctx["session_factory"] = session_factory
    ctx["memory_service"] = memory_service
    ctx["approval_service"] = approval_service
    ctx["browser_controller"] = browser_controller
    ctx["voice_bridge_registry"] = voice_registry
    ctx["proactive_engine"] = proactive_engine
    ctx["mcp_hub"] = mcp_hub
    ctx["tool_registry"] = mcp_hub.tool_registry
    logger.info("Gemuse ARQ worker initialized with Step 3 ProactiveScheduler")


async def shutdown(ctx: dict[str, Any]) -> None:
    """Dispose worker resources on ARQ shutdown."""
    proactive_engine: ProactiveEngine | None = ctx.get("proactive_engine")
    if proactive_engine is not None:
        proactive_engine.stop_scheduler()
    mcp_hub: MCPHubManager | None = ctx.get("mcp_hub")
    if mcp_hub is not None:
        await mcp_hub.shutdown()
    await close_db()
    logger.info("Gemuse ARQ worker shut down")


def _get_redis_settings() -> RedisSettings:
    settings = get_settings()
    return RedisSettings.from_dsn(settings.REDIS_URL)


class WorkerSettings:
    """ARQ WorkerSettings entrypoint (`arq app.worker.tasks.WorkerSettings`)."""

    functions = [run_agent_job, consolidate_memory_task, cleanup_browser_artifacts_task]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = _get_redis_settings()
    max_jobs = 10
    job_timeout = 600
