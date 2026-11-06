"""Durable LangGraph checkpoints, so a failed run can continue from its last completed step.

LangGraph saves state after every step (superstep). When a step fails, the finished steps and any
parallel branches that succeeded stay saved; resuming the thread re-runs only what failed.

- Postgres (Neon on Vercel) when DATABASE_URL is Postgres, so any instance can resume any run.
- Otherwise a SQLite file next to the app database (separate file: two connections writing one
  SQLite file would contend for its lock).
"""

import inspect
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel

from app import schemas
from app.config import get_settings


def serializer() -> JsonPlusSerializer:
    """Allow exactly our own state models to be restored from checkpoints, nothing else."""
    allowed = [
        ("app.schemas", name)
        for name, obj in vars(schemas).items()
        if inspect.isclass(obj) and issubclass(obj, BaseModel) and obj.__module__ == "app.schemas"
    ]
    return JsonPlusSerializer(allowed_msgpack_modules=allowed)


def _target() -> tuple[str, str]:
    url = get_settings().database_url
    if url.startswith(("postgres://", "postgresql://", "postgresql+asyncpg://")):
        return "postgres", url.replace("postgresql+asyncpg://", "postgresql://", 1)
    path = url.split(":///", 1)[1] if ":///" in url else "deep_synth.db"
    if os.environ.get("VERCEL"):
        path = "/tmp/deep_synth.db"  # only writable place; per instance, so continue is best-effort there
    root, ext = os.path.splitext(path)
    return "sqlite", f"{root}.checkpoints{ext or '.db'}"


@asynccontextmanager
async def open_checkpointer() -> AsyncIterator[BaseCheckpointSaver]:
    kind, target = _target()
    if kind == "postgres":
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        # from_conn_string uses autocommit and disables prepared statements (safe behind Neon's pooler).
        async with AsyncPostgresSaver.from_conn_string(target, serde=serializer()) as saver:
            yield saver
    else:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        async with aiosqlite.connect(target) as conn:
            yield AsyncSqliteSaver(conn, serde=serializer())


async def setup_checkpointer() -> None:
    """Create checkpoint tables (idempotent). Called once at API startup."""
    async with open_checkpointer() as saver:
        await saver.setup()
