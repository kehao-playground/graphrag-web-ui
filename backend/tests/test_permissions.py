"""Atom model (spec §4.1): union resolution, implications, baseline,
scope isolation, is_active short-circuit."""
import pytest

from graphrag_ui.domain.permissions import Atom, can, effective_project_perms

MANAGER = frozenset({"users:manage"})
OPS = frozenset({"projects:view_any", "projects:act_any"})
AUDITOR = frozenset({"projects:view_any"})
EMPTY = frozenset()

VIEWER = frozenset({"project:view"})
MAINTAINER = frozenset({"project:view", "project:edit_content",
                        "project:run_jobs"})
EDITOR = MAINTAINER | {"project:edit_settings"}
OWNER = EDITOR | {"project:manage"}

GLOBAL = {Atom.users_manage, Atom.projects_view_any, Atom.projects_act_any,
          Atom.projects_create}
PROJECT_ATOMS = {a for a in Atom if a not in GLOBAL}


def test_create_project_is_baseline_for_every_active_user():
    assert can(EMPTY, True, Atom.projects_create) is True
    assert can(EMPTY, True, Atom.projects_create, None) is True


def test_disabled_account_short_circuits_everything():
    for a in Atom:
        assert can(MANAGER | OPS, False, a, OWNER) is False


def test_global_atoms_check_global_membership_only():
    assert can(MANAGER, True, Atom.users_manage) is True
    assert can(OPS, True, Atom.users_manage) is False


def test_scope_isolation():
    # project atoms never satisfy a global check (spec §9)
    assert can(OWNER, True, Atom.users_manage) is False
    # global perms never imply project atoms except via implications
    assert can(MANAGER, True, Atom.project_view, None) is False


@pytest.mark.parametrize("member_perms,action,expected", [
    (VIEWER, Atom.project_view, True),
    (VIEWER, Atom.project_edit_content, False),
    (VIEWER, Atom.project_run_jobs, False),
    (VIEWER, Atom.project_edit_settings, False),
    (VIEWER, Atom.project_manage, False),
    (MAINTAINER, Atom.project_view, True),
    (MAINTAINER, Atom.project_edit_content, True),
    (MAINTAINER, Atom.project_run_jobs, True),
    # the maintainer boundary (spec decision 1): no settings, no keys
    (MAINTAINER, Atom.project_edit_settings, False),
    (MAINTAINER, Atom.project_manage, False),
    (EDITOR, Atom.project_edit_settings, True),
    (EDITOR, Atom.project_manage, False),
    (OWNER, Atom.project_manage, True),
    (None, Atom.project_view, False),
])
def test_project_matrix(member_perms, action, expected):
    assert can(EMPTY, True, action, member_perms) is expected


def test_act_any_implies_every_project_atom():
    for a in PROJECT_ATOMS:
        assert can(OPS, True, a, None) is True


def test_view_any_implies_view_only():
    assert can(AUDITOR, True, Atom.project_view, None) is True
    # member_perms=None on purpose: view_any alone grants nothing beyond
    # project:view. (Passing MAINTAINER here would assert False against a
    # member who legitimately holds edit_content.)
    assert can(AUDITOR, True, Atom.project_edit_content, None) is False


def test_effective_project_perms_for_my_permissions():
    assert effective_project_perms(OPS, None) == frozenset(
        a.value for a in PROJECT_ATOMS)
    assert effective_project_perms(AUDITOR, MAINTAINER) == \
        MAINTAINER | {"project:view"}
    assert effective_project_perms(EMPTY, None) == frozenset()
