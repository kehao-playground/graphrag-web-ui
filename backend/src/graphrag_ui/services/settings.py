"""Settings.yaml read/write with hash-based optimistic locking and version
history (task brief 3).

The hash is sha256 of the file BYTES on disk (hex) — content is compared as
bytes so trailing-newline or encoding drift never fools the lock.
"""

import hashlib
import uuid
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import Project, SettingsVersion
from graphrag_ui.adapters.workspace_env import read_workspace_env, substitute_placeholders
from graphrag_ui.domain.settings_confinement import confinement_violations, input_pin_violations
from graphrag_ui.services.audit import audit
from graphrag_ui.services.project_lock import input_mutation
from graphrag_ui.services.projects import ws_path

# Versions list endpoint caps the returned rows (display history, not an archive)
VERSIONS_PAGE_CAP = 50

# Cap on editor-submitted content: settings.yaml is a hand-maintained config
# and every write also snapshots a settings_versions row — anything beyond
# 1 MiB can only be a mistake or abuse, never a real configuration.
MAX_CONTENT_BYTES = 1024 * 1024


class SettingsConflictError(Exception):
    """expected_hash does not match the file on disk — routes map to 409 with
    the current content/hash so the frontend can offer a diff."""

    def __init__(self, current_content: str, current_hash: str):
        self.current_content = current_content
        self.current_hash = current_hash
        super().__init__("settings hash mismatch")


class SettingsValidationError(ValueError):
    """Content-level settings rejection — routes map to 400 (spec §4.2).
    Subclasses ValueError (historical contract)."""

    def __init__(self, code: str, detail: str, params: dict[str, str] | None = None) -> None:
        super().__init__(detail)
        self.code = code
        self.params = params


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_settings(project: Project) -> tuple[str, str]:
    """(content, sha256-hex of the bytes on disk)."""
    data = (ws_path(project.id) / "settings.yaml").read_bytes()
    return data.decode(), _hash_bytes(data)


async def write_settings(
    session: AsyncSession, project: Project, content: str, expected_hash: str, actor_id: uuid.UUID
) -> str:
    """Optimistic-lock write; returns the new hash.

    Raises SettingsConflictError when the disk hash differs from expected_hash
    (checked first — a stale editor must resync before any validation — and
    again inside the project lock, where a concurrent writer is visible),
    SettingsValidationError when the content exceeds MAX_CONTENT_BYTES, is
    not parseable YAML, breaks graphrag's $ placeholder rules, or would
    take graphrag outside the workspace (validate_settings_content), and
    ProjectIndexingError when an index/update job holds the project — the
    re-check runs inside the project lock in _commit_settings, so a frozen
    write never touches the file on disk.
    """
    current_content, current_hash = read_settings(project)
    if current_hash != expected_hash:
        raise SettingsConflictError(current_content, current_hash)

    if len(content.encode()) > MAX_CONTENT_BYTES:
        raise SettingsValidationError("settings_too_large", "settings content too large")
    validate_settings_content(ws_path(project.id), content, project.input_file_type)

    new_hash = _hash_bytes(content.encode())
    await _commit_settings(session, project, content, new_hash, expected_hash, actor_id)
    return new_hash


def validate_settings_content(root: Path, content: str, input_file_type: str) -> None:
    """Everything that makes a settings.yaml unloadable or unsafe for the
    workspace at `root`, as SettingsValidationError: YAML syntax, the $
    placeholder rules, and the workspace confinement of R2-03.

    graphrag runs STRICT string.Template substitution on settings.yaml
    BEFORE parsing it: a lone "$" or an undefined ${PLACEHOLDER} makes the
    workspace unloadable. Placeholders resolve against the workspace .env
    ALONE — the adapter substitutes from the same source (R2-01), so a
    placeholder the API process could resolve (${JWT_SECRET}) is just as
    invalid here. The confinement rules run on the SUBSTITUTED document,
    since `${DIR}` can spell `../` just as well as the literal.
    """
    try:
        yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise SettingsValidationError(
            "settings_invalid_yaml", f"invalid yaml: {e}", {"reason": str(e)}
        ) from e
    try:
        substituted = substitute_placeholders(content, read_workspace_env(root))
    except (ValueError, KeyError) as e:
        raise SettingsValidationError(
            "settings_invalid_placeholder", "invalid $ placeholder in settings"
        ) from e
    try:
        data = yaml.safe_load(substituted)
    except yaml.YAMLError as e:
        raise SettingsValidationError(
            "settings_invalid_yaml", f"invalid yaml: {e}", {"reason": str(e)}
        ) from e
    escapes = confinement_violations(data)
    if escapes:
        field = ", ".join(escapes)
        raise SettingsValidationError(
            "settings_path_escape",
            f"settings point graphrag outside the project workspace: {field}",
            {"field": field},
        )
    pins = input_pin_violations(data, input_file_type)
    if pins:
        field = ", ".join(pins)
        raise SettingsValidationError(
            "settings_input_locked",
            f"the input format is fixed at project creation: {field}",
            {"field": field},
        )


def check_workspace_settings(project: Project) -> None:
    """Re-validate the settings.yaml ON DISK against the current .env before
    anything hands it to graphrag (enqueue, dry-run). The write-side check
    is not enough on its own: the file may predate the validator, and a
    `${DIR}` path that was confined when written moves with the .env.
    Sync file reads — callers run it in a thread."""
    content, _ = read_settings(project)
    validate_settings_content(ws_path(project.id), content, project.input_file_type)


async def _commit_settings(
    session: AsyncSession,
    project: Project,
    content: str,
    new_hash: str,
    expected_hash: str,
    actor_id: uuid.UUID,
) -> None:
    """The committing transaction (input_mutation): lock, freeze re-check,
    hash re-check, version row + audit, flush, atomic write, commit.

    The hash is compared again INSIDE the lock (R1-04): two writers holding
    the same expected_hash both pass write_settings' early check, and only
    this one tells the second of them that the first has landed. The rows
    flush before the file is written (R1-05), so a database refusal leaves
    settings.yaml untouched.

    Barrier seam (same shape as files._commit_upload): the config-freeze
    test parks here BEFORE the lock is taken, so an enqueue racing the
    write wins and the re-check inside the lock refuses it.
    """
    async with input_mutation(session, project.id) as m:
        current_content, current_hash = read_settings(project)
        if current_hash != expected_hash:
            raise SettingsConflictError(current_content, current_hash)
        session.add(
            SettingsVersion(
                project_id=project.id, content=content, content_hash=new_hash, saved_by=actor_id
            )
        )
        await audit(
            session,
            actor_id,
            "settings.updated",
            "project",
            str(project.id),
            {"content_hash": new_hash},
        )
        await m.apply(lambda: _atomic_write(ws_path(project.id) / "settings.yaml", content))


def _atomic_write(path: Path, content: str) -> None:
    # never leave a half-written settings.yaml behind a crash
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(content.encode())
    tmp.replace(path)


async def list_versions(session: AsyncSession, project: Project) -> list[SettingsVersion]:
    """Newest first, capped at VERSIONS_PAGE_CAP rows."""
    stmt = (
        select(SettingsVersion)
        .where(SettingsVersion.project_id == project.id)
        .order_by(SettingsVersion.created_at.desc(), SettingsVersion.id.desc())
        .limit(VERSIONS_PAGE_CAP)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_version(
    session: AsyncSession, project: Project, version_id: int
) -> SettingsVersion | None:
    stmt = select(SettingsVersion).where(
        SettingsVersion.id == version_id, SettingsVersion.project_id == project.id
    )
    return (await session.execute(stmt)).scalar_one_or_none()
