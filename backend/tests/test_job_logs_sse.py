"""Task 4: SSE job-log streaming — full stream, live following, Last-Event-ID
/ ?offset= resume, no-log-file done-only, unknown job 404.

Auth helpers copied from test_jobs_api.py (bootstrap admin activation).
pytest-asyncio auto mode (no pytestmark needed, conftest/pyproject)."""

import asyncio
import json
import uuid

from graphrag_ui.adapters.db import get_session_factory
from graphrag_ui.adapters.index_runner import log_path_for
from graphrag_ui.adapters.jobs_repo import finish
from graphrag_ui.adapters.workspace import FakeInitializer
from graphrag_ui.api.projects_routes import get_initializer
from graphrag_ui.services.projects import ws_path


async def _login(client, email, password):
    r = await client.post("/api/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _activate(client, email, initial_pw, new_pw):
    """所有新帳號(含 bootstrap admin)must_change_password=True — 換完密碼才可用。"""
    hdr = await _login(client, email, initial_pw)
    await client.post(
        "/api/auth/change-password",
        headers=hdr,
        json={"current_password": initial_pw, "new_password": new_pw},
    )
    return await _login(client, email, new_pw)


async def _owner(client, app):
    app.dependency_overrides[get_initializer] = FakeInitializer
    return await _activate(client, "admin@test.local", "admin-pass-123", "admin-new-1")


async def _queued_job(client, hdr, name):
    pid = (
        await client.post(
            "/api/projects",
            headers=hdr,
            json={"name": name, "description": None, "input_file_type": "text"},
        )
    ).json()["id"]
    job = (
        await client.post(
            f"/api/projects/{pid}/jobs",
            headers=hdr,
            json={"type": "index", "method": "fast"},
        )
    ).json()
    return pid, job


def _log_of(pid: str, job_id: str):
    # ws_path is what the route uses — same resolution rules, no /tmp symlink drift
    return log_path_for(ws_path(uuid.UUID(pid)), uuid.UUID(job_id))


async def _finish(job_id: str, status: str = "succeeded"):
    async with get_session_factory()() as s:
        await finish(s, uuid.UUID(job_id), status, exit_code=0)


async def _stream_body(client, url, headers=None):
    async with client.stream("GET", url, headers=headers) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert resp.headers["cache-control"] == "no-cache"
        assert resp.headers["x-accel-buffering"] == "no"
        body = ""
        async for chunk in resp.aiter_text():
            body += chunk
    return body


async def test_sse_streams_then_done(client, app):
    hdr = await _owner(client, app)
    pid, job = await _queued_job(client, hdr, "SSE")
    _log_of(pid, job["id"]).write_bytes(b"line1\nline2\n")
    await _finish(job["id"])

    body = await _stream_body(client, f"/api/jobs/{job['id']}/logs", headers=hdr)
    assert "event: log" in body
    # 單一 data 行,chunk 以 JSON 字串編碼(newline 已轉義,不會斷行)
    assert json.dumps("line1\nline2\n") in body
    assert "id: 12" in body  # 結束時的位元組 offset = 檔案大小
    assert "event: done" in body
    assert '"status": "succeeded"' in body


async def test_sse_resume_from_last_event_id(client, app):
    hdr = await _owner(client, app)
    pid, job = await _queued_job(client, hdr, "SSER")
    _log_of(pid, job["id"]).write_bytes(b"0123456789")
    await _finish(job["id"])

    body = await _stream_body(
        client, f"/api/jobs/{job['id']}/logs", headers={**hdr, "Last-Event-ID": "4"}
    )
    # 只送出 offset 4 之後的位元組:單一 log event,data 為 "456789"
    logs = [ln for ln in body.splitlines() if ln.startswith("data: ") and '"456789"' in ln]
    assert logs, body
    assert "01234" not in body
    assert "event: done" in body
    assert '"offset": 10' in body


async def test_sse_live_follows_file_until_terminal(client, app):
    """串流啟動後才寫入的資料也要送達;job 進入終態且檔案讀完後串流結束。"""
    hdr = await _owner(client, app)
    pid, job = await _queued_job(client, hdr, "SSEL")
    log = _log_of(pid, job["id"])
    log.write_bytes(b"first\n")

    task = asyncio.create_task(_stream_body(client, f"/api/jobs/{job['id']}/logs", headers=hdr))
    await asyncio.sleep(0.3)  # let the stream deliver the first chunk
    with log.open("ab") as fh:
        fh.write(b"second\n")
    await _finish(job["id"])
    body = await asyncio.wait_for(task, timeout=10)
    assert json.dumps("first\n") in body
    assert json.dumps("second\n") in body
    assert "event: done" in body
    assert '"offset": 13' in body


async def test_sse_resume_from_offset_query(client, app):
    hdr = await _owner(client, app)
    pid, job = await _queued_job(client, hdr, "SSEQ")
    _log_of(pid, job["id"]).write_bytes(b"0123456789")
    await _finish(job["id"])

    body = await _stream_body(client, f"/api/jobs/{job['id']}/logs?offset=4", headers=hdr)
    assert [ln for ln in body.splitlines() if ln.startswith("data: ") and '"456789"' in ln], body
    assert "01234" not in body
    assert "event: done" in body


async def test_sse_missing_log_file_streams_done_only(client, app):
    hdr = await _owner(client, app)
    _, job = await _queued_job(client, hdr, "SSEN")
    await _finish(job["id"])  # 從未啟動 → log 檔不存在

    body = await _stream_body(client, f"/api/jobs/{job['id']}/logs", headers=hdr)
    assert "event: log" not in body
    assert "event: done" in body
    assert '"offset": 0' in body


async def test_sse_unknown_job_404(client, app):
    hdr = await _owner(client, app)
    r = await client.get(f"/api/jobs/{uuid.uuid4()}/logs", headers=hdr)
    assert r.status_code == 404


async def test_sse_token_query_param_streams(client, app):
    """?token= auth (EventSource cannot send headers) yields the same stream."""
    hdr = await _owner(client, app)
    pid, job = await _queued_job(client, hdr, "sse-query-token")
    token = hdr["Authorization"].split(" ", 1)[1]
    _log_of(pid, job["id"]).write_text("token path\n", encoding="utf-8")
    await _finish(job["id"])
    body = await _stream_body(client, f"/api/jobs/{job['id']}/logs?token={token}")
    assert "token path" in body
    assert "event: done" in body


async def test_sse_token_query_param_invalid_401(client, app):
    hdr = await _owner(client, app)
    _, job = await _queued_job(client, hdr, "sse-bad-token")
    r = await client.get(f"/api/jobs/{job['id']}/logs?token=not-a-token")
    assert r.status_code == 401


async def test_sse_token_must_change_password_403(client, app):
    """must-change-password user with a valid ?token= is still 403: the
    _sse_user mirror is the only guard on this path (the global middleware
    only inspects Authorization headers). The user is a project VIEWER, so
    without this gate the stream would be 200 — the test discriminates."""
    hdr = await _owner(client, app)
    pid, job = await _queued_job(client, hdr, "sse-token-must-change")
    r = await client.post("/api/admin/users", headers=hdr,
                          json={"email": "mcp-user@example.com", "display_name": "mcp",
                                "password": "mcp-pass-123"})
    assert r.status_code == 201, r.text
    viewer_id = r.json()["id"]
    r = await client.put(f"/api/projects/{pid}/members/{viewer_id}",
                         headers=hdr, json={"role": "viewer"})
    assert r.status_code in (200, 201), r.text
    r = await client.post("/api/auth/login",
                          json={"email": "mcp-user@example.com", "password": "mcp-pass-123"})
    token = r.json()["access_token"]
    r = await client.get(f"/api/jobs/{job['id']}/logs?token={token}")
    assert r.status_code == 403
    assert r.json()["detail"] == "password change required"
