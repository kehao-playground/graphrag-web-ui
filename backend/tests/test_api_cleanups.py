"""Fix wave F30: the api layer's leftovers after F13 — one pipeline-step
wrap (R1-76), one service-level not-indexed error (R1-42), pure ASGI
middleware and no must-change middleware (R1-106), no role re-fetch after
set_member (R1-105)."""

import logging
import uuid

import pytest
from starlette.middleware.base import BaseHTTPMiddleware

from graphrag_ui.adapters.models import Project
from graphrag_ui.services import explore as explore_service
from graphrag_ui.services import query as query_service
from graphrag_ui.services.errors import NotIndexedError, ServicePipelineError, pipeline_step


class _StepError(ServicePipelineError):
    pass


class _Passthrough(RuntimeError):
    pass


def test_pipeline_step_logs_and_wraps_with_the_tail(caplog):
    with (
        caplog.at_level(logging.ERROR),
        pytest.raises(_StepError) as exc,
        pipeline_step(_StepError, "search", "search failed (project %s)", "p1"),
    ):
        raise ValueError("x" * 600 + "END")
    assert exc.value.code == "search"
    assert len(exc.value.detail) == 500 and exc.value.detail.endswith("END")
    assert isinstance(exc.value.__cause__, ValueError)
    assert "search failed (project p1)" in caplog.text


def test_pipeline_step_passes_listed_errors_through(caplog):
    with (
        caplog.at_level(logging.ERROR),
        pytest.raises(_Passthrough),
        pipeline_step(_StepError, "list", "list failed", passthrough=(_Passthrough,)),
    ):
        raise _Passthrough("as-is")
    assert caplog.text == ""


def _project() -> Project:
    return Project(id=uuid.uuid4(), name="p", slug="p", owner_id=uuid.uuid4())


async def test_query_on_an_unindexed_workspace_raises_the_service_error(app, monkeypatch):
    monkeypatch.setattr(query_service, "load_config", lambda root: object())
    with pytest.raises(NotIndexedError):
        await query_service.prepare_query(_project(), "basic")


async def test_explore_on_an_unindexed_workspace_raises_the_service_error(app, db_session):
    with pytest.raises(NotIndexedError):
        await explore_service.list_artifacts(db_session, _project(), "entities", limit=10, offset=0)


def test_no_base_http_middleware(app):
    # Every BaseHTTPMiddleware adds a task group and a memory-stream hop
    # to each request and each streamed chunk (SSE, uploads).
    assert all(m.cls is not BaseHTTPMiddleware for m in app.user_middleware)


async def test_must_change_token_on_an_unrouted_path_is_a_plain_404(client):
    login = await client.post(
        "/api/auth/login", json={"email": "admin@test.local", "password": "admin-pass-123"}
    )
    hdr = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await client.get("/api/no-such-route", headers=hdr)).status_code == 404
    # A mounted route still enforces the forced change.
    r = await client.get("/api/projects", headers=hdr)
    assert r.status_code == 403
    assert r.json()["code"] == "auth_must_change_password"


async def test_set_member_hands_back_the_role_it_validated(app, db_session):
    from graphrag_ui.adapters.models import User
    from graphrag_ui.domain.role_catalog import ROLE_ID_VIEWER
    from graphrag_ui.services.projects import set_member

    owner = User(email="o@test.local", display_name="o", password_hash="x")
    member = User(email="m@test.local", display_name="m", password_hash="x")
    db_session.add_all([owner, member])
    await db_session.flush()
    project = Project(name="p", slug="p-set-member", owner_id=owner.id, input_file_type="text")
    db_session.add(project)
    await db_session.flush()
    row, role = await set_member(db_session, project, member.id, ROLE_ID_VIEWER, actor_id=None)
    assert row.role_id == role.id == ROLE_ID_VIEWER
    assert role.name == "viewer"
