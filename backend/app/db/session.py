"""Async SQLAlchemy engine and session factory with N+1 safe defaults."""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.db.models import Base


def create_engine_and_sessionmaker(
    database_url: str,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Build an AsyncEngine and async_sessionmaker for PostgreSQL or SQLite."""
    is_sqlite = database_url.startswith("sqlite")
    engine_kwargs: dict[str, object] = {
        "echo": False,
        "pool_pre_ping": not is_sqlite,
    }
    if not is_sqlite:
        engine_kwargs["pool_size"] = 10
        engine_kwargs["max_overflow"] = 20

    engine = create_async_engine(database_url, **engine_kwargs)
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    return engine, session_factory


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return lazily initialized global AsyncEngine."""
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        _engine, _session_factory = create_engine_and_sessionmaker(settings.DATABASE_URL)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return lazily initialized global async_sessionmaker."""
    global _engine, _session_factory
    if _session_factory is None:
        settings = get_settings()
        _engine, _session_factory = create_engine_and_sessionmaker(settings.DATABASE_URL)
    return _session_factory


async def init_db(engine: AsyncEngine | None = None) -> None:
    """Ensure pgvector extension (when PostgreSQL) and all ORM tables exist."""
    target_engine = engine or get_engine()
    async with target_engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        if conn.dialect.name == "postgresql":
            await conn.execute(
                text(
                    "ALTER TABLE browser_audit_logs "
                    "ADD COLUMN IF NOT EXISTS session_id VARCHAR(128), "
                    "ADD COLUMN IF NOT EXISTS screenshot_path TEXT"
                )
            )


async def close_db() -> None:
    """Dispose global AsyncEngine cleanly on shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an AsyncSession."""
    factory = getattr(request.app.state, "session_factory", None) or get_session_factory()
    async with factory() as session:
        yield session
