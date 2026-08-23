from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from graphrag_ui.config import get_settings

_engine = None
_factory = None


def make_engine(url: str | None = None):
    """For an explicit URL (test fixtures, one-off scripts)."""
    return create_async_engine(url or get_settings().database_url, pool_pre_ping=True)


def make_session_factory(engine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


def get_session_factory() -> async_sessionmaker:
    """Shared factory for the app — **must be lazy**.

    A module-level engine would call get_settings() at import time, before
    test fixtures set DATABASE_URL, sending every test to the wrong DB.
    """
    global _engine, _factory
    if _factory is None:
        _engine = make_engine()
        _factory = make_session_factory(_engine)
    return _factory


async def reset_engine() -> None:
    """Tests: drop the cached engine after env-var changes (dispose it, or pools accumulate)."""
    global _engine, _factory
    if _engine is not None:
        await _engine.dispose()
    _engine, _factory = None, None
