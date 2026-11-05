"""Run + report persistence. SQLite locally, Neon Postgres (asyncpg) in production."""

import logging
import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import JSON, Column, Text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel import Field, SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings


def _now() -> datetime:
    return datetime.now(UTC)


class Run(SQLModel, table=True):
    id: str = Field(primary_key=True, max_length=32)
    topic: str = Field(max_length=300)
    status: str = Field(default="pending", index=True, max_length=16)
    model: str = Field(default="", max_length=120)
    created_at: datetime = Field(default_factory=_now, index=True)
    updated_at: datetime = Field(default_factory=_now)
    events: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    report: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    markdown: str | None = Field(default=None, sa_column=Column(Text))
    error: str | None = Field(default=None, sa_column=Column(Text))


def normalize_db_url(url: str) -> tuple[str, dict[str, Any]]:
    """Neon gives `postgres://...?sslmode=require`; asyncpg wants `postgresql+asyncpg://` + an ssl arg."""
    connect_args: dict[str, Any] = {}
    if url.startswith(("postgres://", "postgresql://")):
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query))
        sslmode = query.pop("sslmode", None)
        query.pop("channel_binding", None)
        if sslmode and sslmode != "disable":
            connect_args["ssl"] = "require"
        url = urlunsplit(("postgresql+asyncpg", parts.netloc, parts.path, urlencode(query), parts.fragment))
    return url, connect_args


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        url, connect_args = normalize_db_url(get_settings().database_url)
        if url.startswith("sqlite") and os.environ.get("VERCEL"):
            # Only /tmp is writable on Vercel and it is per-instance: fine for a preview, not for history.
            logging.getLogger(__name__).warning("No DATABASE_URL on Vercel; using ephemeral /tmp SQLite")
            url = "sqlite+aiosqlite:////tmp/deep_synth.db"
        kwargs: dict[str, Any] = {"connect_args": connect_args}
        if url.startswith("postgresql"):
            # Serverless-friendly: small pool, drop stale connections (Neon scales to zero).
            kwargs |= {"pool_size": 2, "max_overflow": 2, "pool_pre_ping": True, "pool_recycle": 300}
        _engine = create_async_engine(url, **kwargs)
        _sessionmaker = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
    return _engine


def session() -> AsyncSession:
    engine()
    assert _sessionmaker is not None
    return _sessionmaker()


async def init_db() -> None:
    async with engine().begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def get_run(run_id: str) -> Run | None:
    async with session() as s:
        return await s.get(Run, run_id)


async def list_runs(limit: int = 30) -> list[Run]:
    async with session() as s:
        res = await s.exec(select(Run).order_by(Run.created_at.desc()).limit(limit))
        return list(res.all())


async def save_run(run: Run) -> Run:
    run.updated_at = _now()
    async with session() as s:
        merged = await s.merge(run)
        await s.commit()
        return merged
