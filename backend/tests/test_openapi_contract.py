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
    "DELETE /api/projects/{project_id}",
    "DELETE /api/projects/{project_id}/members/{user_id}",
    "GET /api/health",
    "GET /api/jobs/{job_id}/logs",
    "GET /api/projects/{pid}/artifacts/graph",
    "GET /api/projects/{pid}/artifacts/{table}",
    "GET /api/projects/{pid}/artifacts/{table}/{hrid}",
    "GET /api/projects/{pid}/query/stream",
    "GET /api/ready",
    "PATCH /api/projects/{pid}/env",
    "POST /api/admin/users/{user_id}/reset-password",
    "POST /api/auth/change-password",
    "POST /api/auth/logout",
    "POST /api/jobs/{job_id}/cancel",
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
