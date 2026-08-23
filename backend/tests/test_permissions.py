import pytest

from graphrag_ui.domain.permissions import Action, can


# Note: each case's expected is for a **regular** user; admin-passes-all and
# disabled-account-rejects-all are covered uniformly by the last two lines of
# the test body — do not expand them into cases.
@pytest.mark.parametrize("action,project_role,expected", [
    # Regular user + owner
    (Action.view_project, "owner", True),
    (Action.update_project, "owner", True),
    (Action.delete_project, "owner", True),
    (Action.edit_content, "owner", True),
    (Action.manage_members, "owner", True),
    (Action.manage_users, "owner", False),
    # editor
    (Action.view_project, "editor", True),
    (Action.update_project, "editor", True),
    (Action.edit_content, "editor", True),
    (Action.delete_project, "editor", False),
    (Action.manage_members, "editor", False),
    # viewer
    (Action.view_project, "viewer", True),
    (Action.update_project, "viewer", False),
    (Action.edit_content, "viewer", False),
    (Action.manage_members, "viewer", False),
    # Non-member
    (Action.view_project, None, False),
    # Create project: any active user
    (Action.create_project, None, True),
])
def test_matrix(action, project_role, expected):
    assert can("user", True, action, project_role) is expected
    # Admin: everything allowed
    assert can("admin", True, action, project_role) is True
    # Disabled account: everything denied
    assert can("admin", False, action, project_role) is False
