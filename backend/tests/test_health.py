from httpx import ASGITransport, AsyncClient

from graphrag_ui.main import create_app


async def test_health():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_ready_reports_db_and_graphrag_keys():
    # ASGITransport 不跑 lifespan,graphrag_version 為 None — 只斷言 key 存在(Task 2 重構)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/ready")
    assert r.status_code == 200
    assert set(r.json()) == {"db", "graphrag"}
