from pathlib import Path

import pytest

from graphrag_ui.domain.jobs import (
    JOB_METHODS,
    JOB_TYPES,
    TERMINAL_STATUSES,
    build_argv,
    display_status,
    error_annotation,
)


def test_build_argv_matrix():
    root = Path("/ws/x")
    assert build_argv("index", "standard", root) == [
        "index", "--root", "/ws/x", "--method", "standard"]
    assert build_argv("index", "fast", root) == [
        "index", "--root", "/ws/x", "--method", "fast"]
    assert build_argv("update", "standard", root) == [
        "update", "--root", "/ws/x", "--method", "standard"]
    assert build_argv("update", "fast", root) == [
        "update", "--root", "/ws/x", "--method", "fast"]


@pytest.mark.parametrize("t,m", [("bogus", "standard"), ("index", "turbo")])
def test_build_argv_rejects_unknown(t, m):
    with pytest.raises(ValueError):
        build_argv(t, m, Path("/ws"))


def test_oom_annotation():
    assert "OOM" in error_annotation(137)
    assert error_annotation(0) is None
    assert error_annotation(1) is None


def test_display_status_cancelling():
    assert display_status("running", True) == "cancelling"
    assert display_status("running", False) == "running"
    assert display_status("queued", True) == "queued"
    assert display_status("succeeded", False) == "succeeded"
    assert set(JOB_TYPES) == {"index", "update"}
    assert set(JOB_METHODS) == {"standard", "fast"}
    assert "failed(interrupted)" in TERMINAL_STATUSES
