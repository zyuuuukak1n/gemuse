"""Pytest fixtures: isolated async SQLite DB, mocked Redis/ARQ, and mocked Gemini SDK."""

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

# Populate required environment variables BEFORE importing any app modules
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("API_SECRET_TOKEN", "test-api-secret-token-1234567890")
os.environ.setdefault("APPROVAL_HMAC_SECRET", "test-approval-hmac-secret-1234567890")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("GEMINI_API_KEY", "")

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agent.memory import DeterministicMockEmbeddingProvider, MemoryService
from app.agent.orchestrator import LLMStreamProvider, StreamChunk
from app.core.config import Settings
from app.core.router import ThinkingLevel
from app.db.models import Base
from app.db.session import get_db_session
from app.main import create_app
from app.mcp_hub.base import MCPToolDefinition

TEST_API_TOKEN = "test-api-secret-token-1234567890"
TEST_HMAC_SECRET = "test-approval-hmac-secret-1234567890"
TEST_INGEST_TOKEN = "test-gemuse-ingest-token-1234567890"


class MockGeminiStreamProvider(LLMStreamProvider):
    """Deterministic mock Gemini 4 Hydrogen stream provider for offline tests."""

    def __init__(self, chunks: list[StreamChunk] | None = None) -> None:
        self.chunks = chunks or [
            StreamChunk(kind="thinking", text="メモリとコンテキストを分析中..."),
            StreamChunk(kind="text", text="こんにちは！"),
            StreamChunk(kind="text", text=" コアメモリを確認しました [Memory: Core]。"),
            StreamChunk(kind="usage", input_tokens=42, output_tokens=18),
        ]
        self.calls: list[dict[str, Any]] = []

    async def stream_generate(
        self,
        *,
        model: str,
        system_instruction: str,
        contents: list[dict[str, Any]],
        thinking_level: ThinkingLevel,
        tools: list[MCPToolDefinition],
    ) -> AsyncIterator[StreamChunk]:
        self.calls.append(
            {
                "model": model,
                "system_instruction": system_instruction,
                "contents": contents,
                "thinking_level": thinking_level,
                "tools": tools,
            }
        )
        for chunk in self.chunks:
            yield chunk


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    core_path = tmp_path / "core_memory.md"
    core_path.write_text(
        "# Core Memory\n- Language: Japanese\n- Favorite drink: Green Tea\n",
        encoding="utf-8",
    )
    return Settings(
        APP_ENV="test",
        LOG_LEVEL="INFO",
        API_SECRET_TOKEN=TEST_API_TOKEN,
        APPROVAL_HMAC_SECRET=TEST_HMAC_SECRET,
        APPROVAL_TOKEN_TTL_SECONDS=600,
        GEMUSE_INGEST_TOKEN=TEST_INGEST_TOKEN,
        DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        REDIS_URL="redis://localhost:6379/0",
        GEMINI_API_KEY="",
        GEMINI_MODEL="gemini-4-hydrogen",
        CORE_MEMORY_PATH=core_path,
        SHARED_FILES_DIR=tmp_path / "shared",
        CONFIG_DIR=tmp_path / "config",
        MEMORY_SIMILARITY_THRESHOLD=0.2,
        MEMORY_TOP_K=5,
    )


@pytest.fixture
async def db_session_factory(
    test_settings: Settings,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(test_settings.DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
async def db_session(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with db_session_factory() as session:
        yield session


@pytest.fixture
def memory_service(test_settings: Settings) -> MemoryService:
    return MemoryService(
        core_memory_path=test_settings.CORE_MEMORY_PATH,
        embedding_provider=DeterministicMockEmbeddingProvider(),
        similarity_threshold=test_settings.MEMORY_SIMILARITY_THRESHOLD,
        top_k=test_settings.MEMORY_TOP_K,
    )


@pytest.fixture
def mock_llm_provider() -> MockGeminiStreamProvider:
    return MockGeminiStreamProvider()


@pytest.fixture
def mock_arq_pool() -> AsyncMock:
    pool = AsyncMock()
    pool.enqueue_job = AsyncMock(return_value=None)
    return pool


@pytest.fixture
async def api_client(
    test_settings: Settings,
    db_session_factory: async_sessionmaker[AsyncSession],
    memory_service: MemoryService,
    mock_llm_provider: MockGeminiStreamProvider,
    mock_arq_pool: AsyncMock,
) -> AsyncIterator[AsyncClient]:
    app = create_app(
        settings_override=test_settings,
        llm_provider_override=mock_llm_provider,
        memory_service_override=memory_service,
        enable_redis=False,
    )

    async def _override_db() -> AsyncIterator[AsyncSession]:
        async with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_db

    async with app.router.lifespan_context(app):
        app.state.arq_pool = mock_arq_pool
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_API_TOKEN}"}

