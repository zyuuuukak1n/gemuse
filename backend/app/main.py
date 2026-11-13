"""FastAPI application entrypoint, lifecycle, auth protection, and error handlers."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import Depends, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agent.consolidator import ConsolidationClientProtocol, GeminiConsolidationClient
from app.agent.memory import MemoryService
from app.agent.orchestrator import AgentOrchestrator, GoogleGenAIStreamProvider, LLMStreamProvider
from app.api import approval, chat, jobs, mcp_admin, memory, sync
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger, mask_secrets
from app.core.security import ApprovalRequiredError, ApprovalService, verify_api_auth
from app.db.session import create_engine_and_sessionmaker, init_db
from app.sync.gemini_bridge import GeminiBridgeService
from app.worker.tasks import build_mcp_hub, build_memory_service, register_settings_secrets

logger = get_logger(__name__)


class _UnconfiguredLLMProvider(LLMStreamProvider):
    """Safe fallback provider when GEMINI_API_KEY is not configured."""

    async def stream_generate(self, **kwargs):  # type: ignore[override]
        raise RuntimeError("GEMINI_API_KEY is not configured")
        yield  # pragma: no cover


class _UnconfiguredConsolidationClient:
    """Safe fallback consolidation client when GEMINI_API_KEY is not configured."""

    async def generate_json(self, system_prompt: str, user_prompt: str) -> str:
        return '{"core_memory_updates": [], "episodes": [], "skill_candidate": null}'


def create_app(
    settings_override: Settings | None = None,
    *,
    llm_provider_override: LLMStreamProvider | None = None,
    memory_service_override: MemoryService | None = None,
    consolidation_client_override: ConsolidationClientProtocol | None = None,
    enable_redis: bool = True,
) -> FastAPI:
    """Build and configure the Gemuse FastAPI application."""
    settings = settings_override or get_settings()
    configure_logging(settings.LOG_LEVEL)
    register_settings_secrets(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db_engine, db_session_factory = create_engine_and_sessionmaker(settings.DATABASE_URL)
        await init_db(db_engine)
        app.state.db_engine = db_engine
        app.state.session_factory = db_session_factory

        memory_service = memory_service_override or build_memory_service(settings)
        approval_service = ApprovalService(
            settings.APPROVAL_HMAC_SECRET,
            default_ttl_seconds=settings.APPROVAL_TOKEN_TTL_SECONDS,
        )
        mcp_hub, discord_connector = await build_mcp_hub(
            settings,
            approval_service=approval_service,
            session_factory=db_session_factory,
        )

        if llm_provider_override is not None:
            llm_provider = llm_provider_override
        elif settings.GEMINI_API_KEY and not settings.GEMINI_API_KEY.startswith("your-"):
            llm_provider = GoogleGenAIStreamProvider(api_key=settings.GEMINI_API_KEY)
        else:
            llm_provider = _UnconfiguredLLMProvider()

        if consolidation_client_override is not None:
            consolidation_client: ConsolidationClientProtocol = consolidation_client_override
        elif settings.GEMINI_API_KEY and not settings.GEMINI_API_KEY.startswith("your-"):
            consolidation_client = GeminiConsolidationClient(
                api_key=settings.GEMINI_API_KEY,
                model_name=settings.GEMINI_MODEL,
            )
        else:
            consolidation_client = _UnconfiguredConsolidationClient()

        orchestrator = AgentOrchestrator(
            model_name=settings.GEMINI_MODEL,
            llm_provider=llm_provider,
            memory_service=memory_service,
            tool_registry=mcp_hub.tool_registry,
            approval_service=approval_service,
            mcp_hub=mcp_hub,
        )
        gemini_bridge = GeminiBridgeService(
            memory_service=memory_service,
            consolidation_client=consolidation_client,
        )

        arq_pool = None
        if enable_redis:
            try:
                arq_pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
            except Exception as exc:
                logger.warning(
                    "Could not connect to Redis for ARQ pool; continuing without queue",
                    extra={"error": mask_secrets(str(exc))},
                )

        app.state.settings = settings
        app.state.memory_service = memory_service
        app.state.approval_service = approval_service
        app.state.mcp_hub = mcp_hub
        app.state.discord_connector = discord_connector
        app.state.tool_registry = mcp_hub.tool_registry
        app.state.orchestrator = orchestrator
        app.state.gemini_bridge = gemini_bridge
        if not hasattr(app.state, "arq_pool") or app.state.arq_pool is None:
            app.state.arq_pool = arq_pool

        logger.info("Gemuse API started", extra={"env": settings.APP_ENV})
        try:
            yield
        finally:
            await mcp_hub.shutdown()
            if arq_pool is not None:
                await arq_pool.close()
            await db_engine.dispose()
            logger.info("Gemuse API stopped")

    app = FastAPI(
        title=settings.APP_NAME,
        version="0.2.0",
        lifespan=lifespan,
    )

    if settings_override is not None:
        app.dependency_overrides[get_settings] = lambda: settings_override

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Cf-Access-Jwt-Assertion",
            "X-Gemuse-Ingest-Token",
        ],
    )

    @app.exception_handler(ApprovalRequiredError)
    async def approval_exception_handler(
        request: Request, exc: ApprovalRequiredError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={
                "error": "approval_required",
                "detail": str(exc),
                "approval_id": exc.approval_id,
                "action_type": exc.action_type,
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled API exception", extra={"path": request.url.path})
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "internal_server_error", "detail": "An unexpected error occurred"},
        )

    @app.get("/health", tags=["system"])
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok", "service": settings.APP_NAME}

    auth_dep = [Depends(verify_api_auth)]
    app.include_router(chat.router, dependencies=auth_dep)
    app.include_router(jobs.router, dependencies=auth_dep)
    app.include_router(memory.router, dependencies=auth_dep)
    app.include_router(approval.router, dependencies=auth_dep)
    app.include_router(mcp_admin.router, dependencies=auth_dep)
    app.include_router(sync.router)

    return app


def get_application() -> FastAPI:
    """Lazy ASGI app factory wrapper for Uvicorn."""
    return create_app()


app = get_application()
