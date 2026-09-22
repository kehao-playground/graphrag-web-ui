"""Pin the endpoints that still answer without a response_model (spec
A5.2 ratchet): the set may only shrink. New endpoints MUST declare one.

FastAPI 0.141 keeps lazily-included routers as _IncludedRouter placeholders
in app.routes, so the walk uses iter_route_contexts() — the same iterator
the OpenAPI generator itself uses."""

from fastapi.routing import APIRoute, iter_route_contexts

from graphrag_ui.main import create_app

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
    "DELETE /api/projects/{project_id}",
    "DELETE /api/projects/{project_id}/members/{user_id}",
    "GET /api/health",
    "GET /api/jobs/{job_id}/logs",
    "GET /api/projects/{pid}/artifacts/graph",
    "GET /api/projects/{pid}/artifacts/{table}",
    "GET /api/projects/{pid}/artifacts/{table}/{hrid}",
    "GET /api/projects/{pid}/query/stream",
    "PATCH /api/projects/{pid}/env",
    "POST /api/admin/users/{user_id}/reset-password",
    "POST /api/auth/change-password",
    "POST /api/auth/logout",
    "POST /api/jobs/{job_id}/cancel",
    "POST /api/projects/{pid}/files/{filename}/tags",
    "POST /api/projects/{pid}/query",
}


def test_untyped_endpoints_ratchet():
    app = create_app()
    untyped = {
        f"{min(rc.methods - {'HEAD'})} {rc.path}"
        for rc in iter_route_contexts(app.routes)
        if isinstance(rc.original_route, APIRoute) and rc.response_model is None
    }
    assert untyped == KNOWN_UNTYPED, f"response_model debt changed: {untyped ^ KNOWN_UNTYPED}"
