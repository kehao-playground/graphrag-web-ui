"""Coded service errors and the one app-level mapper (R1-11, R1-27, R1-30,
R1-32), the 422 envelope that never echoes input (R1-80), and the auth
boundary as the only activeness check (R1-88)."""

import importlib
import pkgutil
import uuid

import pytest

import graphrag_ui.services as services_pkg
from graphrag_ui.api import env_routes
from graphrag_ui.api.errors import SERVICE_ERROR_STATUS
from graphrag_ui.services.errors import CodedServiceError
from graphrag_ui.services.questions import QuestionSetNotFoundError
from tests.test_env import SECRET, _alice, _make_project, _set
from tests.test_projects import _activate, _login


def _all_subclasses(cls: type) -> set[type]:
    out: set[type] = set()
    for sub in cls.__subclasses__():
        out |= {sub} | _all_subclasses(sub)
    return out


def test_every_coded_service_error_has_an_http_status():
    # Import every service module so each subclass is registered.
    for mod in pkgutil.iter_modules(services_pkg.__path__):
        importlib.import_module(f"{services_pkg.__name__}.{mod.name}")
    unmapped = {
        c.__name__
        for c in _all_subclasses(CodedServiceError)
        if not any(base in SERVICE_ERROR_STATUS for base in c.__mro__)
    }
    assert unmapped == set()


def test_not_found_errors_are_lookup_errors():
    for cls in _all_subclasses(CodedServiceError):
        if cls.__name__.endswith("NotFoundError"):
            assert issubclass(cls, LookupError), cls.__name__


async def test_coded_error_renders_through_the_app_handler(client, app):
    @app.get("/api/__coded")
    async def coded():
        raise QuestionSetNotFoundError()

    r = await client.get("/api/__coded")
    assert r.status_code == 404
    assert r.json() == {"detail": "question set not found", "code": "question_set_not_found"}


async def test_unrelated_key_error_in_env_delete_is_not_a_404(client, app, monkeypatch):
    """A KeyError bug inside the service must surface as a 500, never be
    reported as "key not found" (R1-27)."""
    alice = await _alice(client, app)
    pid = await _make_project(client, alice)

    async def broken(*_a, **_k):
        raise KeyError("unrelated")

    monkeypatch.setattr(env_routes, "delete_env_key", broken)
    with pytest.raises(KeyError):
        await client.delete(f"/api/projects/{pid}/env/GRAPHRAG_API_KEY", headers=alice)


async def test_422_does_not_echo_the_submitted_password(client):
    admin = await _activate(client, "admin@test.local", "admin-pass-123", "admin-new-1")
    r = await client.post(
        "/api/auth/change-password",
        headers=admin,
        json={"current_password": "admin-new-1", "new_password": "tiny-pw"},
    )
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "validation_failed"
    assert "tiny-pw" not in r.text and "admin-new-1" not in r.text
    assert all(set(e) <= {"type", "loc", "msg"} for e in body["detail"])
    assert body["detail"][0]["loc"] == ["body", "new_password"]


async def test_env_patch_oversized_value_is_422_without_the_value(client, app):
    alice = await _alice(client, app)
    pid = await _make_project(client, alice)
    value = "v" * (64 * 1024 + 1)
    r = await _set(client, alice, pid, "GRAPHRAG_API_KEY", value)
    assert r.status_code == 422, r.text[:200]
    assert r.json()["code"] == "validation_failed"
    assert value not in r.text


async def test_env_patch_missing_key_is_422_without_the_value(client, app):
    alice = await _alice(client, app)
    pid = await _make_project(client, alice)
    r = await client.patch(f"/api/projects/{pid}/env", headers=alice, json={"value": SECRET})
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "validation_failed"
    assert SECRET not in r.text


async def test_disabled_user_token_is_refused_at_the_auth_boundary(client, app):
    """can() no longer takes is_active: an access token issued before the
    account was disabled must still be refused, by get_current_user."""
    alice = await _alice(client, app)
    admin = await _login(client, "admin@test.local", "admin-new-1")
    pid = await _make_project(client, alice)
    users = (await client.get("/api/admin/users", headers=admin)).json()
    alice_id = next(u["id"] for u in users if u["email"] == "alice@test.local")
    r = await client.patch(f"/api/admin/users/{alice_id}", headers=admin, json={"is_active": False})
    assert r.status_code == 200, r.text
    r = await client.get(f"/api/projects/{pid}", headers=alice)
    assert r.status_code == 401


async def test_project_routes_404_before_403(client, app):
    alice = await _alice(client, app)
    r = await client.get(f"/api/projects/{uuid.uuid4()}/settings", headers=alice)
    assert r.status_code == 404
    assert r.json()["code"] == "project_not_found"
