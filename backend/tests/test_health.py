from httpx import ASGITransport, AsyncClient

from graphrag_ui.main import create_app


async def test_health():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_ready_with_db(client):
    r = await client.get("/api/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["db"] == "ok"
    assert body["graphrag"]  # version string cached at startup
