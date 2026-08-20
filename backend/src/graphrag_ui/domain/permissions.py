from enum import StrEnum


class Action(StrEnum):
    manage_users = "manage_users"
    create_project = "create_project"
    view_project = "view_project"
    update_project = "update_project"
    delete_project = "delete_project"
    manage_members = "manage_members"
    edit_content = "edit_content"


_PROJECT_ACTIONS: dict[Action, set[str]] = {
    Action.view_project: {"owner", "editor", "viewer"},
    Action.update_project: {"owner", "editor"},
    Action.delete_project: {"owner"},
    Action.manage_members: {"owner"},
    Action.edit_content: {"owner", "editor"},
}


def can(user_role: str, is_active: bool, action: Action,
        project_role: str | None = None) -> bool:
    if not is_active:
        return False
    if user_role == "admin":
        return True
    if action is Action.manage_users:
        return False
    if action is Action.create_project:
        return True
    allowed = _PROJECT_ACTIONS.get(action)
    return allowed is not None and project_role in allowed
