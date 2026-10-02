"""adapters/workspace.py against a fake `graphrag` CLI (R2-36): the init and
dry-run failure branches without forking the real CLI, so they are covered
by the fast suite instead of only by the key-gated slow tests."""

import subprocess
from pathlib import Path

import pytest
import yaml

from graphrag_ui.adapters import workspace
from graphrag_ui.adapters.workspace import GraphragInitInitializer, WorkspaceInitError


def _fake_run(monkeypatch, behaviour):
    """Route subprocess.run to `behaviour(argv, **kwargs)`; record argv."""
    calls: list[list[str]] = []

    def fake(argv, **kwargs):
        calls.append(argv)
        return behaviour(argv, **kwargs)

    monkeypatch.setattr(workspace.subprocess, "run", fake)
    return calls


def _completed(argv, returncode=0, stdout=b"", stderr=b""):
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


def test_init_patches_the_input_section_the_cli_wrote(tmp_path, monkeypatch):
    root = tmp_path / "ws"

    def init_writes_settings(argv, **kwargs):
        (root / "settings.yaml").write_text(
            yaml.safe_dump({"models": {}, "input": {"type": "text", "file_pattern": ".*"}})
        )
        return _completed(argv)

    calls = _fake_run(monkeypatch, init_writes_settings)
    GraphragInitInitializer()._run(root, "csv")
    assert calls[0][:4] == ["graphrag", "init", "--root", str(root)]
    assert "--model" in calls[0] and "--embedding" in calls[0]  # non-TTY needs both
    data = yaml.safe_load((root / "settings.yaml").read_text())
    assert data["input"] == {"type": "csv", "file_pattern": workspace._escaped_pattern("csv")}
    assert data["models"] == {}  # the rest of the CLI's file is kept


@pytest.mark.parametrize(
    "error",
    [
        subprocess.CalledProcessError(2, ["graphrag"], stderr=b"bad flag"),
        subprocess.TimeoutExpired(["graphrag"], 300),
        FileNotFoundError("graphrag"),
    ],
    ids=["non-zero exit", "timeout", "cli missing"],
)
def test_init_failures_raise_workspace_init_error(tmp_path, monkeypatch, error):
    def fail(argv, **kwargs):
        raise error

    _fake_run(monkeypatch, fail)
    with pytest.raises(WorkspaceInitError):
        GraphragInitInitializer()._run(tmp_path / "ws", "text")


async def test_init_refuses_an_unknown_input_type_before_forking(tmp_path, monkeypatch):
    calls = _fake_run(monkeypatch, lambda argv, **kw: _completed(argv))
    with pytest.raises(ValueError, match="unsupported input_file_type"):
        await GraphragInitInitializer().init(tmp_path / "ws", "pdf")
    assert calls == []


def test_dry_run_exit_status_is_data(tmp_path: Path, monkeypatch):
    calls = _fake_run(
        monkeypatch, lambda argv, **kw: _completed(argv, 1, b"loading\n", b"bad yaml\n")
    )
    assert workspace._dry_run(tmp_path) == {"ok": False, "output": "loading\nbad yaml\n"}
    assert calls[0][-2:] == ["--dry-run", "--skip-validation"]  # offline: no model calls


def test_dry_run_output_is_the_tail(tmp_path, monkeypatch):
    big = b"x" * (workspace._OUTPUT_TAIL_CHARS + 50) + b"END"
    _fake_run(monkeypatch, lambda argv, **kw: _completed(argv, 0, big))
    out = workspace._dry_run(tmp_path)
    assert out["ok"] is True
    assert len(out["output"]) == workspace._OUTPUT_TAIL_CHARS
    assert out["output"].endswith("END")


def test_dry_run_timeout_keeps_partial_output_and_says_so(tmp_path, monkeypatch):
    def hang(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"], output=b"step 1\n", stderr=b"")

    _fake_run(monkeypatch, hang)
    out = workspace._dry_run(tmp_path)
    assert out["ok"] is False
    assert out["output"].startswith("step 1\n")
    assert out["output"].endswith(f"[dry-run timed out after {workspace._DRY_RUN_TIMEOUT}s]")


def test_dry_run_without_the_cli_raises_workspace_init_error(tmp_path, monkeypatch):
    def missing(argv, **kwargs):
        raise FileNotFoundError("graphrag")

    _fake_run(monkeypatch, missing)
    with pytest.raises(WorkspaceInitError):
        workspace._dry_run(tmp_path)
