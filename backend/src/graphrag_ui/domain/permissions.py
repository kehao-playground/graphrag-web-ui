"""Permission atoms (spec §4.1). Pure: no I/O, no ORM, frozensets only."""
from enum import StrEnum


class Atom(StrEnum):
    users_manage = "users:manage"
    projects_view_any = "projects:view_any"
    projects_act_any = "projects:act_any"
    projects_create = "projects:create"
    project_view = "project:view"
    project_edit_content = "project:edit_content"
    project_run_jobs = "project:run_jobs"
    project_edit_settings = "project:edit_settings"
    project_manage = "project:manage"


GLOBAL_ATOMS: frozenset[Atom] = frozenset({
    Atom.users_manage, Atom.projects_view_any, Atom.projects_act_any,
    Atom.projects_create,
})
PROJECT_ATOMS: frozenset[Atom] = frozenset(
    {a for a in Atom if a not in GLOBAL_ATOMS})


def can(global_perms: frozenset[str], is_active: bool, action: Atom,
        member_perms: frozenset[str] | None = None) -> bool:
    """Effective-permission check. `global_perms` is the union of the
    actor's global-role atoms; `member_perms` the member-role atoms for
    the project in question (None = not a member)."""
    if not is_active:
        return False
    if action is Atom.projects_create:
        return True  # baseline for every active user (spec §4.1)
    if action in GLOBAL_ATOMS:
        return action in global_perms
    # act_any implies every project atom AND view_any (spec §4.1); a
    # custom role holding only act_any must still see the project list
    if Atom.projects_act_any in global_perms:
        return True
    if action is Atom.project_view and Atom.projects_view_any in global_perms:
        return True
    return member_perms is not None and action in member_perms


def effective_project_perms(
    global_perms: frozenset[str],
    member_perms: frozenset[str] | None,
) -> frozenset[str]:
    """The caller's atom set for ONE project (ProjectOut.my_permissions,
    spec §7): act_any expands to every project atom; view_any at least to
    project:view; otherwise the member-role atoms."""
    if Atom.projects_act_any in global_perms:
        return frozenset(a.value for a in PROJECT_ATOMS)
    perms = frozenset(member_perms or ())
    if Atom.projects_view_any in global_perms:
        perms |= {Atom.project_view.value}
    return perms
