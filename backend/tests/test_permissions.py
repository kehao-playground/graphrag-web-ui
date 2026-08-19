import pytest

from graphrag_ui.domain.permissions import Action, can


# 注意:每個 case 的 expected 是**一般使用者**的預期值;
# admin 全通過與停用帳號全拒絕由測試本體最後兩行統一覆蓋,不要再展開成 case。
@pytest.mark.parametrize("action,project_role,expected", [
    # 一般使用者 + owner
    (Action.view_project, "owner", True),
    (Action.update_project, "owner", True),
    (Action.delete_project, "owner", True),
    (Action.manage_members, "owner", True),
    (Action.manage_users, "owner", False),
    # editor
    (Action.view_project, "editor", True),
    (Action.update_project, "editor", True),
    (Action.delete_project, "editor", False),
    (Action.manage_members, "editor", False),
    # viewer
    (Action.view_project, "viewer", True),
    (Action.update_project, "viewer", False),
    (Action.manage_members, "viewer", False),
    # 非成員
    (Action.view_project, None, False),
    # 建專案:任何 active 使用者
    (Action.create_project, None, True),
])
def test_matrix(action, project_role, expected):
    assert can("user", True, action, project_role) is expected
    # admin 全部允許
    assert can("admin", True, action, project_role) is True
    # 停用帳號全部拒絕
    assert can("admin", False, action, project_role) is False
