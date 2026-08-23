# backend/tests/test_comment_language.py
"""Comment-language scanner (spec B4): CJK in code comments fails the build.

Escape hatch: a comment whose first non-space text is `zh-TW:` is
deliberate (e.g. quoting a UI string) and passes. String literals are
never scanned — only comment syntax is. Known dumb-scanner tradeoff: a
`//` inside a string literal followed by CJK would false-positive; the
repo has no such case today and the zh-TW: escape covers it.
Stdlib only. `python backend/tests/test_comment_language.py --list`
prints violations (file:line: comment) — the authoritative sweep list.
"""
import ast
import io
import re
import sys
import tokenize
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CJK = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff01-\uff60]")
ESCAPE = re.compile(r"^\s*zh-TW:")

ALLOWLIST_PATH = Path(__file__).resolve().parent / "comment_language_allowlist.txt"

# Scan roots (repo-relative). Only these trees and files are swept.
ROOTS = (
    "backend/src", "backend/tests", "backend/migrations", "backend/scripts",
    "backend/alembic.ini", "backend/Dockerfile", "backend/pyproject.toml",
    "frontend/src", "frontend/vite.config.ts", "frontend/Dockerfile",
    "frontend/index.html", "frontend/nginx.conf",
    "deploy", ".github/workflows", "docker-compose.yml", ".env.example",
)
# Generated artifacts and agent docs never count, even where the extension
# would otherwise be whitelisted (types.generated.ts is real .ts content).
EXCLUDED_PATHS = frozenset({"frontend/src/api/types.generated.ts", "openapi.json"})
EXCLUDED_PREFIXES = ("docs/superpowers/",)


# --- Step 2 implementation -------------------------------------------------


def python_comments(text: str) -> list[tuple[int, str]]:
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            out.append((tok.start[0], tok.string))
    return out


def python_docstrings(text: str) -> list[tuple[int, str]]:
    out = []
    for n in ast.walk(ast.parse(text)):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef,
                          ast.AsyncFunctionDef)):
            doc = ast.get_docstring(n)
            if doc:
                out.append((n.body[0].lineno, doc))
    return out


def marker_comments(text: str, marker: str) -> list[tuple[int, str]]:
    return [(i, ln.split(marker, 1)[1])
            for i, ln in enumerate(text.splitlines(), 1) if marker in ln]


def block_comments(text: str, start: str, end: str) -> list[tuple[int, str]]:
    pat = re.compile(re.escape(start) + r"(.*?)" + re.escape(end), re.S)
    return [(text[:m.start()].count("\n") + 1, m.group(1))
            for m in pat.finditer(text)]


def violates(comment_text: str) -> bool:
    """True when a comment carries CJK and is not zh-TW:-escaped."""
    if not CJK.search(comment_text):
        return False
    # `#` arrives inside python COMMENT tokens; marker_comments already
    # stripped the marker, so only the python flavor needs it removed
    # before the escape check.
    body = comment_text.lstrip().lstrip("#").lstrip()
    return not ESCAPE.match(body)


def _slash_comments(text: str) -> list[tuple[int, str]]:
    return marker_comments(text, "//") + block_comments(text, "/*", "*/")


def _hash_comments(text: str) -> list[tuple[int, str]]:
    return marker_comments(text, "#")


# Extension dispatch: the whitelist IS the table — files whose extension
# (or exact name) is absent are never scanned, so vendored tarballs,
# images, lockfiles, and bytecode drop out naturally. INI also allows `;`
# comments but alembic.ini uses none today; kept out to stay dumb.
EXT_DISPATCH = {
    ".py": lambda text: python_comments(text) + python_docstrings(text),
    ".ts": _slash_comments,
    ".tsx": _slash_comments,
    ".yml": _hash_comments,
    ".yaml": _hash_comments,
    ".toml": _hash_comments,
    ".conf": _hash_comments,
    ".ini": _hash_comments,
    ".css": lambda text: block_comments(text, "/*", "*/"),
    ".html": lambda text: block_comments(text, "<!--", "-->"),
    ".tpl": lambda text: block_comments(text, "{{/*", "*/}}"),
}
NAME_DISPATCH = {
    "Dockerfile": _hash_comments,
    ".env.example": _hash_comments,
}


def extractor_for(path: Path):
    if path.name in NAME_DISPATCH:
        return NAME_DISPATCH[path.name]
    return EXT_DISPATCH.get(path.suffix.lower())


def comments_for(path: Path, text: str) -> list[tuple[int, str]]:
    extract = extractor_for(path)
    if extract is None:
        return []
    try:
        return sorted(extract(text))
    except (SyntaxError, tokenize.TokenError):
        # A file that does not parse is out of scope for a comment sweep.
        return []


def load_allowlist(path: Path = ALLOWLIST_PATH) -> frozenset[str]:
    """Repo-relative paths allowed to keep CJK comments; missing file = none."""
    if not path.is_file():
        return frozenset()
    entries = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.split("#", 1)[0].strip()
        if entry:
            entries.add(entry)
    return frozenset(entries)


def _excluded(rel: str) -> bool:
    return rel in EXCLUDED_PATHS or rel.startswith(EXCLUDED_PREFIXES)


def iter_scan_files(repo: Path = REPO, roots=ROOTS):
    """Yield (path, repo-relative posix path) for every scannable file."""
    for root in roots:
        base = repo / root
        if base.is_file():
            files = [base]
        elif base.is_dir():
            files = sorted(p for p in base.rglob("*") if p.is_file())
        else:
            continue
        for path in files:
            rel = path.relative_to(repo).as_posix()
            if _excluded(rel):
                continue
            if extractor_for(path) is None:
                continue
            yield path, rel


def file_violations(path: Path, text: str) -> list[tuple[int, str]]:
    return [(lineno, comment)
            for lineno, comment in comments_for(path, text)
            if violates(comment)]


def scan_repo(repo: Path = REPO, roots=ROOTS, allowlist=None):
    """Yield (rel_path, lineno, comment) for every CJK comment violation."""
    allow = load_allowlist() if allowlist is None else allowlist
    for path, rel in iter_scan_files(repo, roots):
        if rel in allow:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, comment in file_violations(path, text):
            yield rel, lineno, comment


def list_lines(repo: Path = REPO, roots=ROOTS):
    """One `path:lineno: first comment line` string per violation."""
    for rel, lineno, comment in scan_repo(repo, roots):
        preview = comment.splitlines()[0].strip() if comment else ""
        yield f"{rel}:{lineno}: {preview}"


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if "--list" not in argv:
        print(__doc__)
        return 0
    count = 0
    for line in list_lines():
        print(line)
        count += 1
    # Summary goes to stderr so stdout stays a pure, parseable work order.
    print(f"total CJK-comment violations: {count}", file=sys.stderr)
    return 0


# --- Step 1 self-tests (pytest) ---------------------------------------------
# Every CJK sample is a string literal assigned to a variable: string
# content is never scanned, and a sample written as a test function's
# first triple-quoted string would become that function's docstring and
# flag this very file in --list output.

TS_CJK_LINE = "const a = 1; // 這是註解\nconst b = 2; // plain\n"
TS_ZH_TW_ESCAPE = "const a = 1; // zh-TW: 這是刻意保留的 UI 字串\n"
TS_CJK_IN_STRING = 'const label = "中文標籤"; // english note\nconst s2 = "另一段"; // also english\n'
PY_CJK_DOCSTRING = 'def f():\n    """中文說明"""\n    return 1\n'
PY_STRING_ONLY = 'LABEL = "中文標籤"\n\ndef g():\n    """English doc."""\n    return LABEL\n'
PY_ZH_TW_HASH = "# zh-TW: 刻意保留的舊介面字串\nX = 1\n"
YAML_CJK_HASH = "replicas: 2 # 副本數\nimage: api\n"
TOML_CJK_HASH = "timeout = 30 # 逾時秒數\n"
TPL_CJK = "apiVersion: v1\n{{/* 圖表說明\n第二行 */}}\nname: x\n"
HTML_CJK = "<p>hi</p>\n<!-- 中文註解 -->\n"
CSS_CJK = "body { margin: 0; }\n/* 邊距 */\n"


def test_cjk_line_comment_fails():
    hits = [(n, c) for n, c in marker_comments(TS_CJK_LINE, "//") if violates(c)]
    assert hits == [(1, " 這是註解")]


def test_zh_tw_escape_passes():
    assert [c for _, c in marker_comments(TS_ZH_TW_ESCAPE, "//") if violates(c)] == []


def test_cjk_inside_string_literal_passes():
    # Strings are never scanned: only the trailing english // comments exist.
    assert [c for _, c in marker_comments(TS_CJK_IN_STRING, "//") if violates(c)] == []
    assert not [c for _, c in python_comments(PY_STRING_ONLY) if violates(c)]
    assert not [c for _, c in python_docstrings(PY_STRING_ONLY) if violates(c)]


def test_tpl_block_comment_cjk_fails_cross_line():
    hits = block_comments(TPL_CJK, "{{/*", "*/}}")
    assert hits == [(2, " 圖表說明\n第二行 ")]
    assert violates(hits[0][1])


def test_fullwidth_and_cjk_punctuation_fail():
    for ch in "，、。「」《》！":
        assert violates(f"// delimiter {ch} here"), f"expected {ch!r} to fail"


def test_latin_symbols_pass():
    assert not violates("// spec §8.1 backstop")
    assert not violates("// latency — p95 ≥ 200ms")
    assert not violates("// path: /usr/share/nginx/html")


def test_py_docstring_cjk_fails():
    hits = python_docstrings(PY_CJK_DOCSTRING)
    assert hits == [(2, "中文說明")]
    assert violates(hits[0][1])


def test_hash_comment_yaml_toml_style_fails():
    assert [(n, c) for n, c in marker_comments(YAML_CJK_HASH, "#") if violates(c)] == [(1, " 副本數")]
    assert [(n, c) for n, c in marker_comments(TOML_CJK_HASH, "#") if violates(c)] == [(1, " 逾時秒數")]


def test_python_hash_zh_tw_escape_passes():
    assert [c for _, c in python_comments(PY_ZH_TW_HASH) if violates(c)] == []


def test_extension_dispatch():
    assert file_violations(Path("x.py"), PY_CJK_DOCSTRING) == [(2, "中文說明")]
    assert file_violations(Path("x.tsx"), TS_CJK_LINE) == [(1, " 這是註解")]
    assert file_violations(Path("x.yaml"), YAML_CJK_HASH) == [(1, " 副本數")]
    assert file_violations(Path("Dockerfile"), "FROM x # 基底映像\n") == [(1, " 基底映像")]
    assert file_violations(Path(".env.example"), "K=1 # 金鑰\n") == [(1, " 金鑰")]
    assert file_violations(Path("x.html"), HTML_CJK) == [(2, " 中文註解 ")]
    assert file_violations(Path("x.css"), CSS_CJK) == [(2, " 邊距 ")]
    assert file_violations(Path("x.tpl"), TPL_CJK) == [(2, " 圖表說明\n第二行 ")]
    # Extension whitelist: anything else is not scanned at all.
    assert file_violations(Path("x.json"), '{"a": "中文"}') == []
    assert file_violations(Path("x.svg"), "<!-- 中文 -->") == []


def test_allowlisted_path_passes(tmp_path):
    (tmp_path / "legacy.py").write_text(PY_CJK_DOCSTRING, encoding="utf-8")
    allow = tmp_path / "allowlist.txt"
    allow.write_text("# header comment\nlegacy.py # kept for history\n", encoding="utf-8")
    assert load_allowlist(allow) == frozenset({"legacy.py"})
    # Control: without the allowlist entry the violation is reported.
    assert [(rel, n) for rel, n, _ in scan_repo(tmp_path, ["legacy.py"], allowlist=frozenset())] == [("legacy.py", 2)]
    # With it, the whole path passes.
    assert list(scan_repo(tmp_path, ["legacy.py"], allowlist=load_allowlist(allow))) == []


def test_missing_allowlist_file_tolerated(tmp_path):
    assert load_allowlist(tmp_path / "absent.txt") == frozenset()
    (tmp_path / "legacy.py").write_text(PY_CJK_DOCSTRING, encoding="utf-8")
    hits = list(scan_repo(tmp_path, ["legacy.py"], allowlist=load_allowlist(tmp_path / "absent.txt")))
    assert [(rel, n) for rel, n, _ in hits] == [("legacy.py", 2)]


def test_excluded_paths_are_skipped(tmp_path):
    gen = tmp_path / "frontend/src/api/types.generated.ts"
    gen.parent.mkdir(parents=True)
    gen.write_text(TS_CJK_LINE, encoding="utf-8")
    (tmp_path / "openapi.json").write_text('{"x": "中文"}\n', encoding="utf-8")
    docs = tmp_path / "docs/superpowers/x.py"
    docs.parent.mkdir(parents=True)
    docs.write_text(PY_CJK_DOCSTRING, encoding="utf-8")
    keep = tmp_path / "frontend/src/keep.tsx"
    keep.write_text(TS_CJK_LINE, encoding="utf-8")
    hits = [rel for rel, _, _ in scan_repo(tmp_path, ["frontend/src", "openapi.json", "docs"])]
    assert hits == ["frontend/src/keep.tsx"]


def test_list_mode_prints_path_lineno(tmp_path):
    (tmp_path / "mod.py").write_text(PY_CJK_DOCSTRING, encoding="utf-8")
    assert list(list_lines(tmp_path, ["mod.py"])) == ["mod.py:2: 中文說明"]


if __name__ == "__main__":
    raise SystemExit(main())
