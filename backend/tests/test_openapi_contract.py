"""Pin the endpoints that still answer without a response_model (spec
A5.2 ratchet): the set may only shrink. New endpoints MUST declare one.

FastAPI 0.141 keeps lazily-included routers as _IncludedRouter placeholders
in app.routes, so the walk uses iter_route_contexts() — the same iterator
the OpenAPI generator itself uses."""

import uuid

from fastapi.routing import APIRoute, iter_route_contexts

from graphrag_ui.api.schemas import ApiErrorOut, ValidationErrorOut
from graphrag_ui.main import create_app
from tests.test_jobs_api import _setup_users

KNOWN_UNTYPED = {
    "DELETE /api/admin/roles/{role_id}",
    "DELETE /api/projects/{pid}/env/{key}",
    "DELETE /api/projects/{pid}/files/{filename}",
    # kb slice 1: the tag routes answer 204 No Content; OpenAPI forbids a
    # response body on 204, so they join the debt set like every other
    # no-content endpoint rather than declaring a bogus response_model.
    "DELETE /api/projects/{pid}/files/{filename}/tags",
    # kb slice 2: same 204-no-content reason as the tag routes above.
    "DELETE /api/projects/{pid}/question-sets/{sid}",
    "DELETE /api/projects/{pid}/question-sets/{sid}/questions/{qid}",
    "DELETE /api/projects/{pid}",
    "DELETE /api/projects/{pid}/members/{user_id}",
    "GET /api/jobs/{job_id}/logs",
    "GET /api/projects/{pid}/query/stream",
    "PATCH /api/projects/{pid}/env",
    "POST /api/admin/users/{user_id}/reset-password",
    "POST /api/auth/change-password",
    "POST /api/auth/logout",
    "POST /api/projects/{pid}/files/{filename}/tags",
}


def test_untyped_endpoints_ratchet():
    app = create_app()
    untyped = {
        f"{min(rc.methods - {'HEAD'})} {rc.path}"
        for rc in iter_route_contexts(app.routes)
        if isinstance(rc.original_route, APIRoute) and rc.response_model is None
    }
    assert untyped == KNOWN_UNTYPED, f"response_model debt changed: {untyped ^ KNOWN_UNTYPED}"


# R1-44: operationIds are the handler names, so the names are contract.
# HTTP-verb prefixes repeat the method; a bare noun says nothing.
_HTTP_VERB_PREFIXES = ("post_", "put_", "patch_")
_READ_PREFIXES = ("get_", "list_")


def test_operation_ids_are_verb_resource_handler_names():
    schema = create_app().openapi()
    ids: list[str] = []
    for name, op in _operations(schema):
        method, _ = name.split(" ", 1)
        op_id = op["operationId"]
        ids.append(op_id)
        assert "_api_" not in op_id, f"{name}: path leaked into operationId {op_id}"
        assert "_" in op_id, f"{name}: {op_id} is not <verb>_<resource>"
        assert not op_id.startswith(_HTTP_VERB_PREFIXES), f"{name}: {op_id} names the HTTP verb"
        if op_id.startswith(_READ_PREFIXES):
            assert method == "GET", f"{name}: {op_id} reads but is not a GET"
    assert len(ids) == len(set(ids)), sorted(i for i in ids if ids.count(i) > 1)


def _operations(schema: dict):
    for path, item in schema["paths"].items():
        for method, op in item.items():
            yield f"{method.upper()} {path}", op


def _ref(response: dict) -> str:
    return response["content"]["application/json"]["schema"]["$ref"]


def test_every_operation_documents_the_error_envelope():
    """R3-32: every client error an operation can answer is in the contract.
    4XX is the ApiError envelope; a 422 is its own shape (detail is a list)
    and replaces FastAPI's HTTPValidationError, whose `input` field the
    handler never sends (R1-80)."""
    schema = create_app().openapi()
    components = schema["components"]["schemas"]
    assert "ApiErrorOut" in components and "ValidationErrorOut" in components
    assert "HTTPValidationError" not in components
    assert "ValidationError" not in components
    for name, op in _operations(schema):
        responses = op["responses"]
        assert _ref(responses["4XX"]) == "#/components/schemas/ApiErrorOut", name
        if "422" in responses:
            assert _ref(responses["422"]) == "#/components/schemas/ValidationErrorOut", name
    # route-specific shapes win over the range
    put_settings = schema["paths"]["/api/projects/{pid}/settings"]["put"]["responses"]
    assert _ref(put_settings["409"]) == "#/components/schemas/SettingsConflictOut"


async def test_error_bodies_match_the_documented_models(client, app):
    """The documented models describe what the handlers actually send."""
    r = await client.post("/api/auth/login", json={})
    assert r.status_code == 422
    ValidationErrorOut.model_validate(r.json())

    _, alice, _ = await _setup_users(client, app)
    r = await client.get(f"/api/jobs/{uuid.uuid4()}", headers=alice)
    assert r.status_code == 404
    body = ApiErrorOut.model_validate(r.json())
    assert body.code == "job_not_found"
