"""Task 3: GET /api/projects/{pid}/artifacts/* — browse, detail, graph.

Integration tests against the real app + Postgres + the real duckdb
adapter: proves the registry projection end-to-end (list rows carry no
``description``), keyword/type/community filter and pagination
round-trips, route precedence (/artifacts/graph must NOT bind to
{table}), the stale flag fed by an active job row, and the zh-TW error
contract (404 unknown table / 404 missing row / 409 not indexed).
Parquet shape mirrors the Task 2 adapter fixture (spec §13 probe)."""

import uuid

import pandas as pd

from graphrag_ui.adapters.db import get_session_factory
from graphrag_ui.adapters.jobs_repo import insert_job
from graphrag_ui.adapters.workspace import FakeInitializer
from graphrag_ui.api.projects_routes import get_initializer
from graphrag_ui.services.projects import ws_path


def _write_artifacts(pid: str) -> None:
    """Seed the project workspace with §13-shaped parquet (Task 2 fixture)."""
    out = ws_path(uuid.UUID(pid)) / "output"
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "id": ["e1", "e2", "e3"], "human_readable_id": [1, 2, 3],
        "title": ["Alan Turing", "Analytical Engine", "Ada Lovelace"],
        "type": ["PERSON", "ARTIFACT", "PERSON"],
        "text_unit_ids": [["a"], ["b"], ["c"]],
        "frequency": [3, 2, 2], "degree": [2, 1, 0],
        "description": ["computed", "machine", "first programmer"],
    }).to_parquet(out / "entities.parquet")
    pd.DataFrame({
        "id": ["r1", "r2"], "human_readable_id": [1, 2],
        "source": ["Alan Turing", "Ada Lovelace"],
        "target": ["Ada Lovelace", "Ghost Entity"],
        "weight": [4.0, 1.0], "combined_degree": [2, 0],
        "text_unit_ids": [["a"], []],
        "description": ["correspondence", "dangling"],
    }).to_parquet(out / "relationships.parquet")
    pd.DataFrame({
        "id": ["c1", "c2"], "human_readable_id": [0, 1], "community": [0, 1],
        "level": [0, 1], "parent": [-1, 0], "children": [[1], []],
        "title": ["C0", "C1"],
        "entity_ids": [["e1", "e2"], ["e3"]],
        "relationship_ids": [["r1"], []], "text_unit_ids": [[], []],
        "period": ["2026-08-22"] * 2, "size": [2, 1],
    }).to_parquet(out / "communities.parquet")


async def _login(client, email, password):
    r = await client.post("/api/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _activate(client, email, initial_pw, new_pw):
    """Every new account (incl. the bootstrap admin) has must_change_password=True — usable only after the change."""
    hdr = await _login(client, email, initial_pw)
    await client.post(
        "/api/auth/change-password",
        headers=hdr,
        json={"current_password": initial_pw, "new_password": new_pw},
    )
    return await _login(client, email, new_pw)


async def _setup_users(client, app):
    """admin + alice (owner) + bob (added as viewer later) + carol (non-member)."""
    app.dependency_overrides[get_initializer] = FakeInitializer
    admin = await _activate(client, "admin@test.local", "admin-pass-123", "admin-new-1")
    for email, pw, name in [
        ("alice@test.local", "alice-pass-1", "Alice"),
        ("bob@test.local", "bob-pass-1234", "Bob"),
        ("carol@test.local", "carol-pass-1", "Carol"),
    ]:
        r = await client.post(
            "/api/admin/users", headers=admin,
            json={"email": email, "display_name": name, "password": pw},
        )
        assert r.status_code == 201, r.text
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    bob = await _activate(client, "bob@test.local", "bob-pass-1234", "bob-pass-5678")
    carol = await _activate(client, "carol@test.local", "carol-pass-1", "carol-pass-2")
    return admin, alice, bob, carol


async def _project(client, alice, name="X3"):
    r = await client.post(
        "/api/projects", headers=alice,
        json={"name": name, "description": None, "input_file_type": "text"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _add_viewer(client, alice, pid, email):
    users = (await client.get("/api/users", headers=alice)).json()
    vid = next(u["id"] for u in users if u["email"] == email)
    r = await client.put(
        f"/api/projects/{pid}/members/{vid}", headers=alice, json={"role": "viewer"}
    )
    assert r.status_code in (200, 201), r.text


async def _indexed_project(client, app):
    """alice (owner) + bob (viewer) + carol (non-member) and a workspace with parquet."""
    _, alice, bob, carol = await _setup_users(client, app)
    pid = await _project(client, alice)
    await _add_viewer(client, alice, pid, "bob@test.local")
    _write_artifacts(pid)
    return pid, alice, bob, carol


async def _seed_queued_job(client, pid, headers):
    """A queued job row (runner loop disabled in tests → stays queued)."""
    me = (await client.get("/api/auth/me", headers=headers)).json()["id"]
    async with get_session_factory()() as s:
        await insert_job(
            s, project_id=uuid.UUID(pid), type="index", method="fast",
            argv=["index", "--root", "/ws", "--method", "fast"],
            queued_by=uuid.UUID(me),
        )
        await s.commit()


async def test_list_envelope_projection_and_order(client, app):
    pid, alice, _, _ = await _indexed_project(client, app)
    r = await client.get(f"/api/projects/{pid}/artifacts/entities", headers=alice)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"rows", "total", "stale"}
    assert body["total"] == 3 and body["stale"] is False
    assert [row["human_readable_id"] for row in body["rows"]] == [1, 2, 3]
    # registry projection holds end-to-end: big columns never leave the server
    assert set(body["rows"][0]) == {
        "human_readable_id", "title", "type", "frequency", "degree",
    }


async def test_viewer_200_non_member_403(client, app):
    pid, _, bob, carol = await _indexed_project(client, app)
    r = await client.get(f"/api/projects/{pid}/artifacts/entities", headers=bob)
    assert r.status_code == 200
    r = await client.get(f"/api/projects/{pid}/artifacts/entities", headers=carol)
    assert r.status_code == 403
    assert r.json() == {"detail": "forbidden"}


async def test_keyword_type_community_filters(client, app):
    pid, alice, _, _ = await _indexed_project(client, app)
    base = f"/api/projects/{pid}/artifacts/entities"

    r = await client.get(base, headers=alice, params={"q": "turing"})
    assert r.status_code == 200 and r.json()["total"] == 1  # ILIKE is case-insensitive

    r = await client.get(base, headers=alice, params={"type": "PERSON"})
    assert r.status_code == 200 and r.json()["total"] == 2

    # entities carry no community column — resolved via a communities (level=MAX) join
    r = await client.get(base, headers=alice, params={"community": 1})
    assert r.status_code == 200 and r.json()["total"] == 1


async def test_unsupported_filter_422(client, app):
    """API honesty: relationships take no type filter and documents take no
    community filter -> 422 (entities?type= returning 200 is already proven
    by test_keyword_type_community_filters)."""
    pid, alice, _, _ = await _indexed_project(client, app)
    r = await client.get(
        f"/api/projects/{pid}/artifacts/relationships",
        headers=alice, params={"type": "x"},
    )
    assert r.status_code == 422
    assert r.json() == {"detail": "此資料表不支援該篩選條件"}
    r = await client.get(
        f"/api/projects/{pid}/artifacts/documents",
        headers=alice, params={"community": 0},
    )
    assert r.status_code == 422
    assert r.json() == {"detail": "此資料表不支援該篩選條件"}


async def test_limit_offset_returns_exactly_second_row(client, app):
    pid, alice, _, _ = await _indexed_project(client, app)
    r = await client.get(
        f"/api/projects/{pid}/artifacts/entities",
        headers=alice, params={"limit": 1, "offset": 1},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3  # total stays unpaginated
    assert len(body["rows"]) == 1
    assert body["rows"][0]["human_readable_id"] == 2
    assert body["rows"][0]["title"] == "Analytical Engine"


async def test_limit_offset_bounds_are_422(client, app):
    pid, alice, _, _ = await _indexed_project(client, app)
    base = f"/api/projects/{pid}/artifacts/entities"
    assert (await client.get(base, headers=alice, params={"limit": 0})).status_code == 422
    assert (await client.get(base, headers=alice, params={"limit": 201})).status_code == 422
    assert (await client.get(base, headers=alice, params={"offset": -1})).status_code == 422


async def test_active_job_marks_stale_on_all_three(client, app):
    pid, alice, _, _ = await _indexed_project(client, app)
    await _seed_queued_job(client, pid, alice)
    base = f"/api/projects/{pid}/artifacts"
    r = await client.get(f"{base}/entities", headers=alice)
    assert r.json()["stale"] is True
    r = await client.get(f"{base}/entities/1", headers=alice)
    assert r.json()["stale"] is True
    r = await client.get(f"{base}/graph", headers=alice)
    assert r.json()["stale"] is True


async def test_graph_route_precedence_and_shape(client, app):
    """/graph must be registered first — otherwise "graph" binds to {table} -> 404 unknown table."""
    pid, alice, _, _ = await _indexed_project(client, app)
    r = await client.get(f"/api/projects/{pid}/artifacts/graph", headers=alice)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"level", "levels", "nodes", "edges", "stale"}
    assert body["levels"] == [0, 1]
    assert body["level"] == 1  # default = deepest level present
    assert len(body["nodes"]) == 3
    assert body["edges"] == [
        {"source": "Alan Turing", "target": "Ada Lovelace", "weight": 4.0}
    ]
    assert body["stale"] is False


async def test_graph_explicit_level(client, app):
    pid, alice, _, _ = await _indexed_project(client, app)
    r = await client.get(
        f"/api/projects/{pid}/artifacts/graph", headers=alice, params={"level": 0}
    )
    assert r.status_code == 200, r.text
    assert r.json()["level"] == 0


async def test_detail_full_row_including_description(client, app):
    pid, alice, _, _ = await _indexed_project(client, app)
    r = await client.get(f"/api/projects/{pid}/artifacts/entities/1", headers=alice)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"row", "stale"}
    assert body["row"]["description"] == "computed"  # full columns on detail
    assert body["row"]["text_unit_ids"] == ["a"]
    assert body["stale"] is False


async def test_detail_missing_row_404(client, app):
    pid, alice, _, _ = await _indexed_project(client, app)
    r = await client.get(f"/api/projects/{pid}/artifacts/entities/99", headers=alice)
    assert r.status_code == 404
    assert r.json() == {"detail": "找不到該筆資料"}


async def test_unknown_table_404_list_and_detail(client, app):
    pid, alice, _, _ = await _indexed_project(client, app)
    base = f"/api/projects/{pid}/artifacts"
    r = await client.get(f"{base}/bogus", headers=alice)
    assert r.status_code == 404
    assert r.json() == {"detail": "未知的資料表"}
    r = await client.get(f"{base}/bogus/1", headers=alice)
    assert r.status_code == 404
    assert r.json() == {"detail": "未知的資料表"}


async def test_not_indexed_409_list_and_graph(client, app):
    """FakeInitializer's workspace has no output/ — same 409 message as the query path."""
    _, alice, _, _ = await _setup_users(client, app)
    pid = await _project(client, alice)
    base = f"/api/projects/{pid}/artifacts"
    for path in ("entities", "graph"):
        r = await client.get(f"{base}/{path}", headers=alice)
        assert r.status_code == 409, r.text
        assert r.json() == {"detail": "尚未建立索引,請先執行索引任務"}


async def test_must_change_password_token_403(client, app):
    """Forced-change token: the same global 403 shape as other APIs; leaks no route info."""
    app.dependency_overrides[get_initializer] = FakeInitializer
    admin = await _activate(client, "admin@test.local", "admin-pass-123", "admin-new-1")
    r = await client.post(
        "/api/admin/users", headers=admin,
        json={"email": "dave@test.local", "display_name": "Dave",
              "password": "dave-pass-1"},
    )
    assert r.status_code == 201, r.text
    dave = await _login(client, "dave@test.local", "dave-pass-1")
    r = await client.get("/api/projects/00000000-0000-0000-0000-000000000000"
                         "/artifacts/entities", headers=dave)
    assert r.status_code == 403
    assert r.json() == {"detail": "password change required"}


async def test_corrupt_parquet_502_list(client, app):
    """entities.parquet is garbage bytes -> ExploreReadError -> 502, completing the 404/409/403/502 error matrix."""
    pid, alice, _, _ = await _indexed_project(client, app)
    (ws_path(uuid.UUID(pid)) / "output" / "entities.parquet").write_bytes(b"not parquet")
    r = await client.get(f"/api/projects/{pid}/artifacts/entities", headers=alice)
    assert r.status_code == 502
    assert r.json() == {"detail": "讀取索引輸出失敗"}
