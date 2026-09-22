"""In-process config loading is confined to the workspace (R2-01 (2), R2-02,
R2-06, R4-42): `${VAR}` resolves from the workspace .env only, nothing is
merged into os.environ, the process cwd never moves, relative paths resolve
against the workspace root, and a rotated key is the key in use on the very
next load. Real graphrag: these run wherever the pinned package imports."""

import os
from pathlib import Path

import pytest

from graphrag_ui.adapters.graphrag_search import ConfigLoadError, load_config

# The shape `graphrag init` writes (init_content.INIT_YAML), trimmed to what
# GraphRagConfig requires plus the path-bearing sections under test.
_SETTINGS = """\
completion_models:
  default_completion_model:
    model_provider: openai
    model: gpt-4.1
    auth_method: api_key
    api_key: ${GRAPHRAG_API_KEY}
embedding_models:
  default_embedding_model:
    model_provider: openai
    model: text-embedding-3-large
    auth_method: api_key
    api_key: ${GRAPHRAG_API_KEY}
input:
  type: text
  file_pattern: .*\\.txt$$
output_storage:
  type: file
  base_dir: output
cache:
  type: json
  storage:
    type: file
    base_dir: cache
vector_store:
  type: lancedb
  db_uri: output/lancedb
local_search:
  prompt: prompts/local_search_system_prompt.txt
"""


def _workspace(root: Path, key: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "settings.yaml").write_text(_SETTINGS)
    (root / ".env").write_text(f"GRAPHRAG_API_KEY={key}\n")
    return root


def _api_key(config) -> str:
    return config.completion_models["default_completion_model"].api_key


def test_placeholders_resolve_from_the_workspace_env_only(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "process-secret")
    root = _workspace(tmp_path / "ws", "sk-workspace")
    assert _api_key(load_config(root)) == "sk-workspace"

    (root / "settings.yaml").write_text(_SETTINGS.replace("${GRAPHRAG_API_KEY}", "${JWT_SECRET}"))
    with pytest.raises(ConfigLoadError, match="JWT_SECRET"):
        load_config(root)


def test_load_leaves_os_environ_and_cwd_alone(tmp_path):
    root = _workspace(tmp_path / "ws", "sk-workspace")
    before_env, before_cwd = dict(os.environ), os.getcwd()
    load_config(root)
    assert os.getcwd() == before_cwd  # R2-06: no os.chdir into the workspace
    assert dict(os.environ) == before_env  # R2-02: no load_dotenv into the process


def test_projects_do_not_share_keys_and_rotation_is_immediate(tmp_path):
    a = _workspace(tmp_path / "a", "sk-a")
    b = _workspace(tmp_path / "b", "sk-b")
    assert _api_key(load_config(a)) == "sk-a"
    assert _api_key(load_config(b)) == "sk-b"  # R2-02: no first-project bleed
    (a / ".env").write_text("GRAPHRAG_API_KEY=sk-a-rotated\n")
    assert _api_key(load_config(a)) == "sk-a-rotated"  # R4-42: the saved key is the key in use


def test_relative_paths_resolve_against_the_workspace(tmp_path):
    root = _workspace(tmp_path / "ws", "sk").resolve()
    config = load_config(root)
    assert config.vector_store.db_uri == str(root / "output" / "lancedb")
    assert config.output_storage.base_dir == str(root / "output")
    assert config.cache.storage.base_dir == str(root / "cache")
    assert config.local_search.prompt == str(root / "prompts" / "local_search_system_prompt.txt")
    # the input file_pattern's escaped "$$" comes back as the real regex
    assert config.input.file_pattern == r".*\.txt$"


def test_missing_env_file_and_bad_yaml_are_config_errors(tmp_path):
    root = _workspace(tmp_path / "ws", "sk")
    (root / ".env").unlink()
    with pytest.raises(ConfigLoadError, match="GRAPHRAG_API_KEY"):
        load_config(root)
    (root / ".env").write_text("GRAPHRAG_API_KEY=sk\n")
    (root / "settings.yaml").write_text("completion_models: [\n")
    with pytest.raises(ConfigLoadError):
        load_config(root)
