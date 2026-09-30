"""Import boundaries of the four layers (AGENTS.md "Architecture Rules",
spec §9), enforced rather than held by review (R1-48, fix wave F26).

An AST walk over every import in `src/graphrag_ui` — module level and
function level alike, since the graphrag imports are deferred inside
functions on purpose:

- `domain/` imports the standard library and other `domain` modules only;
- `services/` imports neither FastAPI/Starlette nor the `api` layer;
- `adapters/` never imports `services` or `api`;
- `graphrag*` is imported only by `adapters/graphrag_search.py` (the
  env-shielded in-process entry; indexing forks the CLI);
- `duckdb` is imported only under `adapters/`.
"""

import ast
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "graphrag_ui"
PKG = "graphrag_ui"

GRAPHRAG_SITES = {"adapters/graphrag_search.py"}


def imported_modules(source: str, module: str) -> set[str]:
    """Absolute dotted names of every import in `source`. `module` is the
    importing module's dotted name, used to resolve relative imports."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = module.split(".")[: -node.level]
                found.add(".".join([*base, node.module] if node.module else base))
            elif node.module:
                found.add(node.module)
    return found


def violations(rel: str, modules: set[str]) -> list[str]:
    """Layering rules broken by a file at `rel` (path under the package,
    posix separators) importing `modules`."""
    layer = rel.split("/", 1)[0] if "/" in rel else ""
    out: list[str] = []
    for mod in sorted(modules):
        top = mod.split(".")[0]
        own_layer = mod.split(".")[1] if top == PKG and "." in mod else ""
        if layer == "domain" and top != PKG and top not in sys.stdlib_module_names:
            out.append(f"{rel}: domain imports third-party {mod}")
        if layer == "domain" and top == PKG and own_layer != "domain":
            out.append(f"{rel}: domain imports {mod}")
        if layer == "services" and top in {"fastapi", "starlette"}:
            out.append(f"{rel}: services imports {mod}")
        if layer in {"services", "adapters"} and own_layer == "api":
            out.append(f"{rel}: {layer} imports {mod}")
        if layer == "adapters" and own_layer == "services":
            out.append(f"{rel}: adapters imports {mod}")
        if top.startswith("graphrag") and top != PKG and rel not in GRAPHRAG_SITES:
            out.append(f"{rel}: graphrag import {mod} outside {sorted(GRAPHRAG_SITES)}")
        if top == "duckdb" and layer != "adapters":
            out.append(f"{rel}: duckdb import outside adapters/")
    return out


def test_layering_rules_hold() -> None:
    found: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        dotted = ".".join([PKG, *rel.removesuffix(".py").split("/")])
        found += violations(rel, imported_modules(path.read_text(), dotted))
    assert found == []


def test_checker_catches_each_rule() -> None:
    # The scan above is only as good as the checker: every rule must fire.
    cases = {
        ("domain/x.py", "import yaml"): "third-party yaml",
        ("domain/x.py", "from graphrag_ui.adapters import db"): "imports graphrag_ui.adapters",
        ("domain/x.py", "from ..services import files"): "domain imports graphrag_ui.services",
        ("services/x.py", "from fastapi import HTTPException"): "services imports fastapi",
        ("services/x.py", "from starlette.routing import Match"): "services imports starlette",
        ("services/x.py", "from graphrag_ui.api import deps"): "services imports graphrag_ui.api",
        ("adapters/x.py", "from graphrag_ui.services import files"): "adapters imports",
        ("adapters/x.py", "def f():\n    from graphrag.api import query"): "graphrag import",
        ("services/x.py", "import graphrag_common"): "graphrag import",
        ("services/x.py", "import duckdb"): "duckdb import outside",
    }
    for (rel, source), expected in cases.items():
        dotted = ".".join([PKG, *rel.removesuffix(".py").split("/")])
        found = violations(rel, imported_modules(source, dotted))
        assert any(expected in v for v in found), (rel, source, found)
    allowed = ("adapters/graphrag_search.py", "from graphrag.api.query import local_search")
    assert violations(allowed[0], imported_modules(allowed[1], "graphrag_ui.adapters.x")) == []
