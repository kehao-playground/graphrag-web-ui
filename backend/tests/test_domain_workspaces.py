# backend/tests/test_domain_workspaces.py
from pathlib import Path

import pytest

from graphrag_ui.domain.workspaces import workspace_path


def test_workspace_path_joins_root():
    root = Path("/srv/ws")
    assert workspace_path(root, "9ba2c483-773c-4ba2-a4b8-71f457e9c13d") == \
        Path("/srv/ws/9ba2c483-773c-4ba2-a4b8-71f457e9c13d")


def test_workspace_path_rejects_lexical_escape():
    with pytest.raises(ValueError, match="escapes"):
        workspace_path(Path("/srv/ws"), "../../etc")
