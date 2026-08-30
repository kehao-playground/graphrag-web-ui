"""Fixed built-in role ids (spec §4.2).

The literals are duplicated verbatim in the R1 migration (migrations never
import app modules); changing one side without the other breaks the seed.
Custom roles get random UUIDs at creation time and never appear here.
"""
import uuid

ROLE_ID_USER_ADMIN = uuid.UUID("00000000-0000-4000-8000-000000000001")
ROLE_ID_OPS = uuid.UUID("00000000-0000-4000-8000-000000000002")
ROLE_ID_VIEWER = uuid.UUID("00000000-0000-4000-8000-000000000003")
ROLE_ID_MAINTAINER = uuid.UUID("00000000-0000-4000-8000-000000000004")
ROLE_ID_EDITOR = uuid.UUID("00000000-0000-4000-8000-000000000005")
ROLE_ID_OWNER = uuid.UUID("00000000-0000-4000-8000-000000000006")

GLOBAL_BUILTIN_ROLE_IDS = frozenset({ROLE_ID_USER_ADMIN, ROLE_ID_OPS})
PROJECT_BUILTIN_ROLE_IDS = frozenset({
    ROLE_ID_VIEWER, ROLE_ID_MAINTAINER, ROLE_ID_EDITOR, ROLE_ID_OWNER})
