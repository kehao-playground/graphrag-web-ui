"""Per-key workspace .env management with masked reads (task brief 4).

Values in .env are secrets: list_env returns masked forms only, and
error messages must never include a value (routes rely on that).
"""

import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import Project
from graphrag_ui.services.audit import audit
from graphrag_ui.services.projects import ws_path

# dotenv keys we manage: UPPER_SNAKE (graphrag's GRAPHRAG_API_KEY etc.)
_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


class EnvValidationError(ValueError):
    """Key/value-level .env rejection — routes map to 400 (spec §4.2).
    Subclasses ValueError (historical contract)."""

    def __init__(self, code: str, detail: str, params: dict[str, str] | None = None) -> None:
        super().__init__(detail)
        self.code = code
        self.params = params


def _mask(value: str) -> str:
    return (value[:2] + "****") if len(value) >= 6 else "****"


def _env_path(project: Project):
    return ws_path(project.id) / ".env"


def _read_lines(project: Project) -> list[str]:
    # graphrag init creates the .env; a workspace without one reads as empty
    path = _env_path(project)
    return path.read_text().splitlines() if path.exists() else []


def _key_of(line: str) -> str:
    return line.partition("=")[0].strip()


def _atomic_write(project: Project, lines: list[str]) -> None:
    # atomic write (same pattern as settings.py): a crash mid-write never
    # leaves a half-written .env behind
    path = _env_path(project)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(path)


def list_env(project: Project) -> list[dict]:
    """[{key, masked}] from the workspace .env; missing file → []."""
    entries = []
    for raw in _read_lines(project):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        entries.append({"key": key, "masked": _mask(value)})
    return entries


def _validate(key: str, value: str) -> None:
    """Key/shape checks shared by set_env_key's callers; raises
    EnvValidationError with messages that never contain the value (routes
    echo str(e))."""
    if not _KEY_RE.fullmatch(key):
        raise EnvValidationError("env_invalid_key", f"invalid key: {key}", {"key": key})
    if "\n" in value or "\r" in value:
        raise EnvValidationError("env_value_single_line", "value must be a single line")


def _upsert_lines(project: Project, key: str, value: str) -> list[str]:
    """key=value on one line; all other lines keep their order."""
    out, replaced = [], False
    for line in _read_lines(project):
        if "=" in line and _key_of(line) == key:
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={value}")
    return out


def _remove_lines(project: Project, key: str) -> list[str]:
    """Lines minus the key's line; missing key → KeyError (route maps 404)."""
    lines = _read_lines(project)
    out = [line for line in lines if not ("=" in line and _key_of(line) == key)]
    if len(out) == len(lines):
        raise KeyError(key)
    return out


async def set_env_key(
    session: AsyncSession, project: Project, key: str, value: str, actor_id: uuid.UUID | None
) -> None:
    """Upsert `key=value` AND audit it, one transaction (spec A1).

    Payload-known-first shape: the audit row is added and flushed BEFORE
    the external .env write, so a failed write rolls the flushed row back —
    no env.key_set row without the real change. EnvValidationError (bad key /
    multi-line value) is raised before any row or write.
    """
    _validate(key, value)
    lines = _upsert_lines(project, key, value)
    try:
        await audit(session, actor_id, "env.key_set", "project", str(project.id), {"key": key})
        await session.flush()
        _atomic_write(project, lines)
        await session.commit()
    except Exception:
        await session.rollback()
        raise


async def delete_env_key(
    session: AsyncSession, project: Project, key: str, actor_id: uuid.UUID | None
) -> None:
    """Remove the key's line AND audit it, one transaction (spec A1).

    Same payload-known-first shape: audit+flush, then the .env write, then
    commit. Missing key → KeyError, raised before any row or write.
    """
    lines = _remove_lines(project, key)
    try:
        await audit(session, actor_id, "env.key_deleted", "project", str(project.id), {"key": key})
        await session.flush()
        _atomic_write(project, lines)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
