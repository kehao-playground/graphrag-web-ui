"""Every action the backend audits has an SPA label, and every labelled
action is one the backend writes (decision D3, fix wave F24).

Same approach as test_error_catalog: the written set is read from the
source by an AST walk over `audit(session, actor, "<action>", …)` calls,
so a new action is seen the day it is written."""

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "backend" / "src" / "graphrag_ui"
LABELS = REPO / "frontend" / "src" / "components" / "labels.ts"


def _written_actions() -> set[str]:
    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name != "audit":
                continue
            action = node.args[2] if len(node.args) > 2 else None
            assert isinstance(action, ast.Constant) and isinstance(action.value, str), (
                f"{path.name}:{node.lineno}: audit action must be a string literal"
            )
            found.add(action.value)
    return found


def _labelled_actions() -> set[str]:
    block = re.search(r"AUDIT_ACTIONS = \[(.*?)\] as const", LABELS.read_text(), re.DOTALL)
    assert block is not None, "AUDIT_ACTIONS not found in labels.ts"
    return set(re.findall(r'"([a-z_]+\.[a-z_]+)"', block.group(1)))


def test_every_audited_action_is_labelled_and_vice_versa():
    written, labelled = _written_actions(), _labelled_actions()
    assert written, "the AST walk found no audit() calls"
    assert written - labelled == set(), "audited but missing from AUDIT_ACTIONS"
    assert labelled - written == set(), "in AUDIT_ACTIONS but never audited"
