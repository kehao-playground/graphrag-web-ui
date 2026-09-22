"""Workspace .env as the ONLY placeholder source and the subprocess env
allowlist (R2-01, R2-02). The API process environment — JWT_SECRET,
DATABASE_URL, bootstrap credentials — must be unreachable from a project's
settings.yaml and invisible to its graphrag subprocess."""

import os

import pytest

from graphrag_ui.adapters.workspace_env import (
    read_workspace_env,
    subprocess_env,
    substitute_placeholders,
)


def test_read_workspace_env_parses_dotenv_and_skips_comments(tmp_path):
    (tmp_path / ".env").write_text(
        "# graphrag init\nGRAPHRAG_API_KEY=sk-123456\n\nTITLE_COLUMN=title\nQUOTED='a b'\n"
    )
    assert read_workspace_env(tmp_path) == {
        "GRAPHRAG_API_KEY": "sk-123456",
        "TITLE_COLUMN": "title",
        # python-dotenv unquotes, exactly as graphrag's own loader would
        "QUOTED": "a b",
    }


def test_read_workspace_env_missing_file_is_empty(tmp_path):
    assert read_workspace_env(tmp_path) == {}


def test_read_workspace_env_drops_reserved_keys(tmp_path):
    # a .env written before the validator existed may still carry them
    (tmp_path / ".env").write_text("HTTPS_PROXY=http://evil\nGRAPHRAG_API_KEY=sk-1\nPATH=/x\n")
    assert read_workspace_env(tmp_path) == {"GRAPHRAG_API_KEY": "sk-1"}


def test_substitute_placeholders_uses_only_the_given_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "process-secret")
    assert substitute_placeholders("k: ${KEY}\n", {"KEY": "v"}) == "k: v\n"
    with pytest.raises(KeyError):
        substitute_placeholders("k: ${JWT_SECRET}\n", {"KEY": "v"})
    with pytest.raises(ValueError):  # lone "$" — graphrag's strict Template rules
        substitute_placeholders('x: "a$"\n', {})
    assert substitute_placeholders("re: ^.*\\.txt$$\n", {}) == "re: ^.*\\.txt$\n"


def test_subprocess_env_is_an_allowlist_plus_workspace_pairs(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "process-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/x")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "hunter2")
    monkeypatch.setenv("PROXY_AUTH_SECRET", "p" * 32)
    monkeypatch.setenv("HTTPS_PROXY", "http://corp-proxy:3128")
    monkeypatch.setenv("NLTK_DATA", "/usr/local/share/nltk_data")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    monkeypatch.delenv("LITELLM_LOG", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)  # docker's own proxy config sets it
    (tmp_path / ".env").write_text("GRAPHRAG_API_KEY=sk-1\nHTTP_PROXY=http://evil\n")

    env = subprocess_env(tmp_path)

    for secret in ("JWT_SECRET", "DATABASE_URL", "BOOTSTRAP_ADMIN_PASSWORD", "PROXY_AUTH_SECRET"):
        assert secret not in env
    assert env["PATH"] == os.environ["PATH"]
    assert env["HTTPS_PROXY"] == "http://corp-proxy:3128"  # operator passthrough
    assert env["NLTK_DATA"] == "/usr/local/share/nltk_data"
    assert env["LC_ALL"] == "C.UTF-8"
    assert env["GRAPHRAG_API_KEY"] == "sk-1"
    assert "HTTP_PROXY" not in env  # reserved: the workspace cannot set it
    assert env["LITELLM_LOG"] == "ERROR"


def test_subprocess_env_workspace_never_overrides_operator_values(tmp_path, monkeypatch):
    # an operator's explicit LITELLM_LOG=DEBUG wins over the default, and the
    # workspace cannot shadow an allowlisted name the operator set
    monkeypatch.setenv("LITELLM_LOG", "DEBUG")
    monkeypatch.setenv("NLTK_DATA", "/opt/nltk")
    (tmp_path / ".env").write_text("LITELLM_LOG=TRACE\nNLTK_DATA=/tmp/evil\n")
    env = subprocess_env(tmp_path)
    assert env["LITELLM_LOG"] == "DEBUG"
    assert env["NLTK_DATA"] == "/opt/nltk"
