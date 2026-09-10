"""Historic preview locator bindings (spec 7.4). Every failure is a 404."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import Project
from graphrag_ui.adapters.workspace import FakeInitializer
from graphrag_ui.api.projects_routes import get_initializer
from graphrag_ui.services.projects import ws_path
from tests.citation_fixtures import UNIT_TEXT, stored_results
from tests.test_projects import _activate, _setup_two_users

FILE_A = "file-a.md"
FILE_B = "file-b.md"


async def _login_alice(client, app) -> dict:
    """Activated alice; FakeInitializer keeps project creation off the FS
    initializer path (the rows are what these tests exercise)."""
    app.dependency_overrides[get_initializer] = FakeInitializer
    await _setup_two_users(client)
    return await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")


async def _new_project(client, alice, *, name: str) -> str:
    r = await client.post(
        "/api/projects", headers=alice, json={"name": name, "input_file_type": "text"}
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _project_row(db_session: AsyncSession, pid: str) -> Project:
    project = await db_session.get(Project, uuid.UUID(pid))
    assert project is not None
    return project


def _seed_input(pid: str, *names: str) -> None:
    """Every named input file carries the cited passage, so a locator that
    slipped past a binding and reached the WRONG file would still find the
    passage there — the negative tests below then fail for the right
    reason instead of 404-ing by accident."""
    for name in names:
        (ws_path(uuid.UUID(pid)) / "input" / name).write_text(
            f"{UNIT_TEXT} and the rest of the document"
        )


def _sources_citation(entry_id: int, source_name: str | None) -> dict:
    """The stored citation shape T2 persists: only Sources entries carry a
    resolved source_name."""
    return {
        "label": "Sources",
        "ids": [entry_id],
        "entries": [{"id": entry_id, "text": UNIT_TEXT, "source_name": source_name}],
    }


def _entity_citation(entry_id: int) -> dict:
    return {
        "label": "Entities",
        "ids": [entry_id],
        "entries": [{"id": entry_id, "text": "Entity Seven", "source_name": None}],
    }


@pytest.fixture
async def run_with_citations(client, app, db_session):
    """A stored result citing Sources (1) -> file-a.md, with the passage on
    disk in BOTH input files; yields (alice, pid, result_id, entry_id)."""
    alice = await _login_alice(client, app)
    pid = await _new_project(client, alice, name="LC")
    project = await _project_row(db_session, pid)
    _seed_input(pid, FILE_A, FILE_B)
    result = (await stored_results(db_session, project, [[_sources_citation(1, FILE_A)]]))[0]
    return alice, pid, str(result.id), 1


@pytest.fixture
async def two_projects_with_runs(client, app, db_session):
    """Alice owns A and B; both carry the same VALID citation shape, so a
    locator for B's result posted against A's path fails binding 1 and
    nothing else. Yields (alice_a, pid_a, result_id_b, entry_id_b)."""
    alice = await _login_alice(client, app)
    pid_a = await _new_project(client, alice, name="PA")
    pid_b = await _new_project(client, alice, name="PB")
    _seed_input(pid_a, FILE_A)
    _seed_input(pid_b, FILE_A)
    project_b = await _project_row(db_session, pid_b)
    result_b = (await stored_results(db_session, project_b, [[_sources_citation(1, FILE_A)]]))[0]
    return alice, pid_a, str(result_b.id), 1


@pytest.fixture
async def two_results(client, app, db_session):
    """ONE run, two results: A cites Sources (1), B cites Sources (2). Entry
    ids name entries OF THAT RESULT, not of the run; yields
    (alice, pid, result_a_id, entry_id_of_b)."""
    alice = await _login_alice(client, app)
    pid = await _new_project(client, alice, name="TR")
    project = await _project_row(db_session, pid)
    _seed_input(pid, FILE_A, FILE_B)
    results = await stored_results(
        db_session,
        project,
        [[_sources_citation(1, FILE_A)], [_sources_citation(2, FILE_B)]],
    )
    return alice, pid, str(results[0].id), 2


@pytest.fixture
async def run_with_entity_citation(client, app, db_session):
    """A result citing Sources (1) AND Entities (7): the entity entry id can
    never be a valid locator because only Sources entries carry a
    source_name at all; yields (alice, pid, result_id, entities_entry_id)."""
    alice = await _login_alice(client, app)
    pid = await _new_project(client, alice, name="EN")
    project = await _project_row(db_session, pid)
    _seed_input(pid, FILE_A)
    result = (
        await stored_results(
            db_session, project, [[_sources_citation(1, FILE_A), _entity_citation(7)]]
        )
    )[0]
    return alice, pid, str(result.id), 7


async def test_historic_locator_opens_the_stored_passage(client, run_with_citations):
    alice, pid, result_id, entry_id = run_with_citations  # source_name == file-a.md
    r = await client.post(
        f"/api/projects/{pid}/files/{FILE_A}/preview",
        headers=alice,
        json={"result_id": result_id, "entry_id": entry_id},
    )
    assert r.status_code == 200 and r.json()["match"] is True


async def test_a_result_id_from_another_project_is_404(client, two_projects_with_runs):
    """Binding 1. project:view on the PATH project says nothing about which
    project that row belongs to."""
    alice_a, pid_a, result_id_b, entry_id_b = two_projects_with_runs
    r = await client.post(
        f"/api/projects/{pid_a}/files/{FILE_A}/preview",
        headers=alice_a,
        json={"result_id": result_id_b, "entry_id": entry_id_b},
    )
    assert r.status_code == 404
    assert r.json()["code"] != "forbidden"


async def test_an_entry_from_a_different_result_is_404(client, two_results):
    """Binding 2: entry_id must name an entry OF THAT RESULT."""
    alice, pid, result_a, entry_id_of_b = two_results
    r = await client.post(
        f"/api/projects/{pid}/files/{FILE_B}/preview",
        headers=alice,
        json={"result_id": result_a, "entry_id": entry_id_of_b},
    )
    assert r.status_code == 404


async def test_an_entry_of_a_non_sources_citation_is_404(client, run_with_entity_citation):
    """Binding 2, other half: only Sources entries carry a source_name at
    all, so an Entities entry id can never be a valid locator."""
    alice, pid, result_id, entities_entry_id = run_with_entity_citation
    r = await client.post(
        f"/api/projects/{pid}/files/{FILE_A}/preview",
        headers=alice,
        json={"result_id": result_id, "entry_id": entities_entry_id},
    )
    assert r.status_code == 404


async def test_a_valid_result_paired_with_another_filename_is_404(client, run_with_citations):
    """Binding 3, the confused deputy: without it a caller with legitimate
    rights could pair a real result_id with any filename and have the server
    search a different document for the stored passage. file-b.md carries
    the passage too, so a missing binding 3 would return 200, not 404."""
    alice, pid, result_id, entry_id = run_with_citations  # source_name == file-a.md
    r = await client.post(
        f"/api/projects/{pid}/files/{FILE_B}/preview",
        headers=alice,
        json={"result_id": result_id, "entry_id": entry_id},
    )
    assert r.status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"result_id": "..."},  # half the pair
        {"entry_id": 1},  # the other half
        {"passage": "x", "result_id": "...", "entry_id": 1},  # mixed
        {"passage": "x", "entry_id": 1},  # mixed
        {},  # neither
    ],
)
async def test_partial_or_mixed_locators_are_422(client, run_with_citations, body):
    alice, pid, result_id, entry_id = run_with_citations
    filled = {
        k: (result_id if k == "result_id" else entry_id if k == "entry_id" else v)
        for k, v in body.items()
    }
    r = await client.post(f"/api/projects/{pid}/files/{FILE_A}/preview", headers=alice, json=filled)
    assert r.status_code == 422


async def test_get_still_returns_the_head_window(client, run_with_citations):
    """The GET form is unchanged by this task."""
    alice, pid, _, _ = run_with_citations
    r = await client.get(f"/api/projects/{pid}/files/{FILE_A}/preview", headers=alice)
    assert r.status_code == 200 and r.json()["offset"] == 0 and r.json()["match"] is False


async def test_a_stored_source_name_whose_file_was_deleted_is_a_clean_404(
    client, run_with_citations
):
    """The drawer renders it as disabled rather than 404-ing open — but the
    endpoint's contract is still a 404, and the frontend must not call it."""
    alice, pid, result_id, entry_id = run_with_citations
    assert (
        await client.delete(f"/api/projects/{pid}/files/{FILE_A}", headers=alice)
    ).status_code == 204
    r = await client.post(
        f"/api/projects/{pid}/files/{FILE_A}/preview",
        headers=alice,
        json={"result_id": result_id, "entry_id": entry_id},
    )
    assert r.status_code == 404
