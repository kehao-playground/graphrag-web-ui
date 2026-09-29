"""Every problem code the backend can emit has an SPA catalog entry, and
every catalog entry is a code the backend emits (R1-57).

The emitted set is read from the source, not from a registry: a code is
born at a raise site, so an AST walk over src/ sees a new one the day it
is written. The shapes it recognises are the ones the codebase uses —
`ApiError(status, "<code>", …)`, `class …Error: code = "<code>"`,
`SomeError("<code>", "<detail>", …)`, `code="<code>"` keywords and
`{"code": "<code>"}` bodies."""

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "backend" / "src" / "graphrag_ui"
CATALOG = REPO / "frontend" / "src" / "i18n" / "locales" / "en-US.ts"

CODE = re.compile(r"^[a-z]+(_[a-z0-9]+)*$")
# A bare `SomeError("<str>", …)` counts only when the string looks like a
# code (snake_case with an underscore), not a one-word message.
POSITIONAL_CODE = re.compile(r"^[a-z]+(_[a-z0-9]+)+$")
# Internal codes that never reach the wire as themselves: QueryError's
# `config` is translated to `query_config_failed` by the query route.
INTERNAL = {"config"}


def _str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def emitted_codes() -> set[str]:
    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "code" for t in node.targets
            ):
                found.add(_str(node.value) or "")
            elif isinstance(node, ast.Call):
                name = _name(node.func)
                if name == "ApiError" and len(node.args) >= 2:
                    found.add(_str(node.args[1]) or "")
                elif name.endswith("Error") and len(node.args) >= 2:
                    first = _str(node.args[0]) or ""
                    if POSITIONAL_CODE.match(first):
                        found.add(first)
                for kw in node.keywords:
                    if kw.arg == "code":
                        found.add(_str(kw.value) or "")
            elif isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values, strict=True):
                    if _str(k) == "code":
                        found.add(_str(v) or "")
    return {c for c in found if CODE.match(c)} - INTERNAL


def catalog_codes() -> set[str]:
    text = CATALOG.read_text(encoding="utf-8")
    block = text.split("  errors: {", 1)[1].split("\n  },", 1)[0]
    return set(re.findall(r"^\s+([a-z_]+):", block, re.MULTILINE))


def test_every_emitted_code_has_a_catalog_entry():
    assert sorted(emitted_codes() - catalog_codes()) == []


def test_every_catalog_entry_is_emitted():
    assert sorted(catalog_codes() - emitted_codes()) == []


def test_the_scan_sees_each_raise_shape():
    # Guards the scanner itself: one code per recognised shape.
    codes = emitted_codes()
    assert {
        "health_too_many_ids",  # ApiError(status, code, detail)
        "job_conflict",  # class attribute
        "file_name_empty",  # SomeError(code, detail)
        "settings_conflict",  # code= keyword
        "validation_failed",  # {"code": …} body
    } <= codes
