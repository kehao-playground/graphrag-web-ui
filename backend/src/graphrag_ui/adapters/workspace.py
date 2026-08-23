import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Protocol

import yaml

# Key names verified against graphrag source: the format key is input.type
# (InputConfig.type; there is no file_type field), and the storage backend is
# a separate top-level section input_storage.type. file_pattern is a regex
# (TextFileReader default r".*\.txt$"), not a glob. text covers txt+md
# (spec §2 allowlist).
_FILE_PATTERNS = {"text": r".*\.(txt|md)$", "csv": r".*\.csv$", "json": r".*\.json$"}
_ALLOWED = set(_FILE_PATTERNS)

# graphrag init's --model/--embedding declare prompt= in typer: with a non-TTY
# stdin (subprocess/container) click reads EOF and aborts outright (verified
# empirically). Values must be passed explicitly for non-interactive runs;
# they equal the graphrag 3.1.0 graphrag.config.defaults (gpt-4.1 /
# text-embedding-3-large).
_INIT_MODEL = "gpt-4.1"

_INIT_EMBEDDING = "text-embedding-3-large"

# graphrag_common.load_config runs string.Template(text).substitute(os.environ)
# over the whole settings.yaml BEFORE parsing it: any literal "$" that is not a
# "${VAR}" placeholder must be escaped as "$$" or config loading dies with
# "Invalid placeholder in string" (verified against graphrag 3.1.0). Our
# file_pattern regexes end in "$", so they must be escaped on disk; graphrag
# un-escapes them back to the real regex.
#
# graphrag 3.1.0 index --dry-run without --skip-validation calls
# validate_config_names(), which fires REAL completion/embedding requests
# (80s+, always fails without a working API key). The endpoint's contract is
# offline settings/schema validation (spec §6.2), so dry_run passes
# --skip-validation; YAML/schema errors still surface as a non-zero exit.
_DRY_RUN_TIMEOUT = 180
_OUTPUT_TAIL_CHARS = 20000


def _escaped_pattern(input_file_type: str) -> str:
    """file_pattern as it must appear IN settings.yaml (see $-escape note)."""
    return _FILE_PATTERNS[input_file_type].replace("$", "$$")


_logger = logging.getLogger(__name__)


class WorkspaceInitError(RuntimeError):
    """graphrag init failed. The api layer converts to HTTP — services must not import FastAPI."""


class WorkspaceInitializer(Protocol):
    async def init(self, root: Path, input_file_type: str) -> None: ...


class GraphragInitInitializer:
    async def init(self, root: Path, input_file_type: str) -> None:
        if input_file_type not in _ALLOWED:
            msg = f"unsupported input_file_type: {input_file_type}"
            raise ValueError(msg)
        # subprocess.run blocks; calling it directly in an async route would
        # stall the whole event loop (single-replica deploy = entire service
        # frozen for seconds)
        await asyncio.to_thread(self._run, root, input_file_type)

    def _run(self, root: Path, input_file_type: str) -> None:
        root.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                ["graphrag", "init", "--root", str(root),
                 "--model", _INIT_MODEL, "--embedding", _INIT_EMBEDDING],
                # ~7s measured (~10s under load); 300s still safe for load spikes
                # and also covers a hung CLI
                check=True, capture_output=True, timeout=300)
        except subprocess.CalledProcessError as e:
            # str(e) carries only the exit code; the real root cause is on
            # stderr — log it for diagnosis (HTTP stays a detail-free 500 so
            # internals never leak to clients)
            _logger.error("graphrag init failed (exit %s): %s", e.returncode,
                          (e.stderr or b"").decode(errors="replace").strip())
            raise WorkspaceInitError(str(e)) from e
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            _logger.error("graphrag init failed: %r", e)  # FileNotFoundError = CLI not on PATH
            raise WorkspaceInitError(str(e)) from e
        settings_path = root / "settings.yaml"
        data = yaml.safe_load(settings_path.read_text())
        input_cfg = data.setdefault("input", {})
        input_cfg["type"] = input_file_type
        input_cfg["file_pattern"] = _escaped_pattern(input_file_type)
        settings_path.write_text(yaml.safe_dump(data, sort_keys=False))
        # InputConfig is extra="allow": wrong key names are silently ignored.
        # Read back and assert after writing so a future graphrag version that
        # renames keys fails loudly here instead of breaking silently.
        check_input = yaml.safe_load(settings_path.read_text()).get("input", {})
        if (check_input.get("type") != input_file_type
                or check_input.get("file_pattern") != _escaped_pattern(input_file_type)):
            msg = f"settings.yaml input patch failed: {check_input}"
            raise WorkspaceInitError(msg)


class FakeInitializer:
    """Unit tests: create the dir and a minimal settings.yaml, no CLI fork.
    Writes the same $-escaped file_pattern as the real initializer so the
    real CLI can still load the workspace."""

    async def init(self, root: Path, input_file_type: str) -> None:
        (root / "input").mkdir(parents=True, exist_ok=True)
        (root / "settings.yaml").write_text(yaml.safe_dump(
            {"input": {"type": input_file_type,
                       "file_pattern": _escaped_pattern(input_file_type)}}))


async def dry_run(root: Path) -> dict:
    """`graphrag index --root <root> --dry-run` via asyncio.to_thread, timeout 180s.

    Returns {"ok": bool, "output": str(stdout + stderr tail, last 20000 chars)}.
    TimeoutExpired → {"ok": False, "output": ... + "\\n[dry-run timed out after 180s]"}.
    FileNotFoundError (CLI missing) → WorkspaceInitError (route → 500).
    """
    return await asyncio.to_thread(_dry_run, root)


def _tail(*streams: bytes | None) -> str:
    """stdout+stderr concatenated, decoded leniently, last 20 000 chars."""
    text = "".join((s or b"").decode(errors="replace") for s in streams)
    return text[-_OUTPUT_TAIL_CHARS:]


def _dry_run(root: Path) -> dict:
    try:
        proc = subprocess.run(
            ["graphrag", "index", "--root", str(root), "--dry-run",
             "--skip-validation"],
            # non-zero exit is DATA (ok=False), never an exception here
            check=False,
            capture_output=True, timeout=_DRY_RUN_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        # POSIX run() already captured partial output into the exception
        return {"ok": False,
                "output": _tail(e.stdout, e.stderr)
                + f"\n[dry-run timed out after {_DRY_RUN_TIMEOUT}s]"}
    except FileNotFoundError as e:
        _logger.error("graphrag dry-run failed: %r", e)  # CLI not on PATH
        raise WorkspaceInitError(str(e)) from e
    return {"ok": proc.returncode == 0, "output": _tail(proc.stdout, proc.stderr)}
