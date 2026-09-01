"""ApiError envelope (i18n spec §4.1): additive code/params, legacy
detail untouched, plain HTTPException renders without code."""

from fastapi import HTTPException

from graphrag_ui.api.errors import ApiError


async def test_api_error_renders_detail_code_and_params(client, app):
    @app.get("/api/__boom")
    async def boom():
        raise ApiError(
            413, "file_too_large", "file exceeds the 50 MiB upload limit", {"max_mb": 50}
        )

    r = await client.get("/api/__boom")
    assert r.status_code == 413
    body = r.json()
    assert body["detail"] == "file exceeds the 50 MiB upload limit"
    assert body["code"] == "file_too_large"
    assert body["params"] == {"max_mb": 50}


async def test_api_error_omits_empty_params(client, app):
    @app.get("/api/__plain")
    async def plain():
        raise ApiError(409, "job_conflict", "this project already has an indexing job in progress")

    r = await client.get("/api/__plain")
    # New test pinning the envelope: dict equality is the point here.
    assert r.json() == {
        "detail": "this project already has an indexing job in progress",
        "code": "job_conflict",
    }


async def test_plain_http_exception_has_no_code(client, app):
    @app.get("/api/__legacy")
    async def legacy():
        raise HTTPException(400, "old style")

    r = await client.get("/api/__legacy")
    assert r.json() == {"detail": "old style"}
