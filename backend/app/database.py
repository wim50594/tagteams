"""
Database access layer.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings

try:
    import redis.asyncio as aioredis
except ImportError:  # pragma: no cover - redis is an optional dependency
    aioredis = None  # type: ignore


# ── Engine / session factory ──────────────────────────────────

engine = create_async_engine(get_settings().database_url, echo=False)
async_session_factory = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


def _migrate_schema(sync_conn) -> None:
    """Apply the small additive migrations used before Alembic is introduced."""
    inspector = inspect(sync_conn)
    if not inspector.has_table("account_invitations"):
        return
    columns = {
        column["name"]
        for column in inspector.get_columns("account_invitations")
    }
    if "is_admin" not in columns:
        sync_conn.execute(
            text(
                "ALTER TABLE account_invitations "
                "ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT FALSE"
            )
        )


async def init_db() -> None:
    """Create tables and apply backwards-compatible additive schema changes."""
    # Ensure all SQLModel tables are registered even when this function is
    # invoked outside the FastAPI application startup path.
    import app.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        await conn.run_sync(_migrate_schema)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a database session."""
    async with async_session_factory() as session:
        yield session


# ── Optional Redis cache ──────────────────────────────────────

redis_client: "aioredis.Redis | None" = None


async def connect_cache() -> None:
    """Try to connect to Redis. Failure is non-fatal – cache is disabled."""
    global redis_client
    settings = get_settings()
    if not settings.redis_url or aioredis is None:
        redis_client = None
        return
    try:
        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        await client.ping()
        redis_client = client
    except Exception:
        redis_client = None


async def disconnect_cache() -> None:
    if redis_client:
        await redis_client.close()


async def cache_get(key: str) -> Any | None:
    """Return cached JSON value, or None on miss / when cache is disabled."""
    if redis_client is None:
        return None
    try:
        raw = await redis_client.get(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


async def cache_set(key: str, value: Any, ttl: int | None = None) -> None:
    """Cache a JSON-serialisable value. No-op when cache is disabled."""
    if redis_client is None:
        return
    ttl = ttl if ttl is not None else get_settings().cache_ttl_seconds
    try:
        await redis_client.set(key, json.dumps(value), ex=ttl)
    except Exception:
        pass


async def cache_delete(*keys: str) -> None:
    """Invalidate one or more cache keys. No-op when cache is disabled."""
    if redis_client is None or not keys:
        return
    try:
        await redis_client.delete(*keys)
    except Exception:
        pass


# ── Cache key helpers ──────────────────────────────────────────

def cache_key_session(session_id: str) -> str:
    return f"cache:session:{session_id}"


def cache_key_project(project_id: int) -> str:
    return f"cache:project:{project_id}"


def cache_key_item(item_id: str) -> str:
    return f"cache:item:{item_id}"


def cache_key_progress(session_id: str) -> str:
    return f"cache:progress:{session_id}"
