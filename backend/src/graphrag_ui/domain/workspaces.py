# backend/src/graphrag_ui/domain/workspaces.py
"""Pure workspace-path logic (spec A3).

Domain keeps no I/O and no config: this module only joins and lexically
validates. The service wrapper (services.projects.ws_path) owns
get_settings() and Path.resolve() — resolve() issues real syscalls and
can follow symlinks, so the post-resolve containment re-check lives there.
"""
import os
from pathlib import Path


def workspace_path(root: Path, project_id) -> Path:
    """workspace dir for `project_id` under `root`.

    Lexical containment only: the unresolved candidate must stay inside
    `root`. Callers that need the on-disk truth resolve afterwards and
    re-check containment against the resolved root.
    """
    candidate = root / str(project_id)
    # is_relative_to() keeps ".." as an ordinary component, so a raw check
    # would accept "../../etc": compare the lexically normalized form
    # instead. os.path.normpath is pure string math — no syscalls, no
    # symlink resolution — so domain purity holds.
    if not Path(os.path.normpath(candidate)).is_relative_to(root):
        msg = f"workspace path escapes workspaces dir: {candidate}"
        raise ValueError(msg)
    return candidate
