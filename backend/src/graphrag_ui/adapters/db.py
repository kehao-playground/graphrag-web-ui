from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from graphrag_ui.config import get_settings

_engine = None
_factory = None


def make_engine(url: str | None = None):
    """顯式 URL 用(測試 fixture、一次性腳本)。"""
    return create_async_engine(url or get_settings().database_url, pool_pre_ping=True)


def make_session_factory(engine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


def get_session_factory() -> async_sessionmaker:
    """應用用的共享 factory — **必須 lazy**。

    模組級直接建 engine 會在 import 時就讀 get_settings(),
    早於測試 fixture 設定 DATABASE_URL,導致整個測試連到錯的 DB。
    """
    global _engine, _factory
    if _factory is None:
        _engine = make_engine()
        _factory = make_session_factory(_engine)
    return _factory


async def reset_engine() -> None:
    """測試用:環境變數變更後丟棄快取的 engine(記得 dispose,否則連線池會累積)。"""
    global _engine, _factory
    if _engine is not None:
        await _engine.dispose()
    _engine, _factory = None, None
