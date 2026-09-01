"""Settings.yaml read/write with hash-based optimistic locking and version
history (task brief 3).

The hash is sha256 of the file BYTES on disk (hex) — content is compared as
bytes so trailing-newline or encoding drift never fools the lock.
"""

import hashlib
import os
import string
import uuid

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import Project, SettingsVersion
from graphrag_ui.services.audit import audit
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
    (checked first — a stale editor must resync before any validation), and
    SettingsValidationError when the content exceeds MAX_CONTENT_BYTES, is
    not parseable YAML, or breaks graphrag's $ placeholder rules.
    """
    path = ws_path(project.id) / "settings.yaml"
    current_content, current_hash = read_settings(project)
    if current_hash != expected_hash:
        raise SettingsConflictError(current_content, current_hash)

    if len(content.encode()) > MAX_CONTENT_BYTES:
        raise SettingsValidationError("settings_too_large", "settings content too large")
    try:
        yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise SettingsValidationError(
            "settings_invalid_yaml", f"invalid yaml: {e}", {"reason": str(e)}
        ) from e

    # graphrag 3.1.0 runs STRICT string.Template substitution on settings.yaml
    # BEFORE parsing it (load_config.py): a lone "$" or an undefined
    # ${PLACEHOLDER} makes the CLI unable to load the workspace. Validate the
    # same way here — os.environ overlaid by the workspace .env KEY=VALUE
    # pairs, mirroring graphrag's env loading order.
    env = dict(os.environ)
    env_path = ws_path(project.id) / ".env"
    if env_path.exists():
        for raw in env_path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    try:
        string.Template(content).substitute(env)
    except (ValueError, KeyError) as e:
        # KeyError: ${X} with X in neither environ nor .env — equally
        # unloadable by the CLI; same 400 as an invalid "$".
        raise SettingsValidationError(
            "settings_invalid_placeholder", "invalid $ placeholder in settings"
        ) from e

    data = content.encode()
    new_hash = _hash_bytes(data)
    # atomic write: never leave a half-written settings.yaml behind a crash
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)

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
    await session.commit()
    return new_hash


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
