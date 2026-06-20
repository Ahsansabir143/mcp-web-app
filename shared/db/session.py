from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from shared.db.config import db_settings

_engine = create_async_engine(
    db_settings.database_url,
    pool_size=db_settings.database_pool_size,
    max_overflow=db_settings.database_max_overflow,
    pool_timeout=db_settings.database_pool_timeout,
    echo=db_settings.database_echo,
    future=True,
)

async_session_factory = async_sessionmaker(
    _engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI / dependency-injection friendly session provider."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


def get_engine():
    return _engine
