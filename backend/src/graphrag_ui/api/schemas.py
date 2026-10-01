import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    model_validator,
)

from graphrag_ui.services.file_preview import PASSAGE_MAX_BYTES

# pydantic 2 does not coerce UUID to str, and the ORM ids are UUIDs: every
# response id field is declared UuidStr so the wire type stays a plain
# string (types.generated.ts keeps `string`) without a validator per model.
UuidStr = Annotated[str, BeforeValidator(lambda v: str(v) if isinstance(v, UUID) else v)]


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class RoleOut(BaseModel):
    """One role catalog entry; user_count/member_count are populated only
    by GET /api/admin/roles (spec §7)."""

    model_config = ConfigDict(from_attributes=True)

    id: UuidStr
    scope: str
    name: str
    description: str
    permissions: list[str]
    is_system: bool
    user_count: int | None = None
    member_count: int | None = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UuidStr
    email: EmailStr
    display_name: str
    roles: list[RoleOut] = []
    permissions: list[str] = []  # union of roles' atoms (spec §7)
    is_active: bool
    must_change_password: bool


class _UserLike(Protocol):
    """The shape user_out() needs. A Protocol rather than the ORM class:
    schemas stay free of ORM imports (the original reason this was typed
    `object`), but the attribute reads are checked instead of unchecked.
    Both User and api.deps.Principal satisfy it."""

    @property
    def id(self) -> UUID: ...
    @property
    def email(self) -> str: ...
    @property
    def display_name(self) -> str: ...
    @property
    def is_active(self) -> bool: ...
    @property
    def must_change_password(self) -> bool: ...


def user_out(user: _UserLike, roles: Sequence) -> UserOut:
    """Build UserOut from a User row plus its loaded global roles."""
    role_outs = [RoleOut.model_validate(r) for r in roles]
    perms: set[str] = set()
    for r in roles:
        perms.update(r.permissions or [])
    return UserOut(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        roles=role_outs,
        permissions=sorted(perms),
        is_active=user.is_active,
        must_change_password=user.must_change_password,
    )


class UserBriefOut(BaseModel):
    """Narrow list shown to every logged-in user (for picking users in member
    management). Deliberately omits admin fields like roles / permissions /
    must_change_password."""

    model_config = ConfigDict(from_attributes=True)

    id: UuidStr
    email: EmailStr
    display_name: str
    is_active: bool


class LoginOut(BaseModel):
    access_token: str
    refresh_token: str
    user: UserOut


class RefreshOut(BaseModel):
    access_token: str
    refresh_token: str


class AuthConfigOut(BaseModel):
    """Runtime auth mode for SPA boot detection (spec §5.3)."""

    auth_mode: Literal["local", "proxy"]


class LivenessOut(BaseModel):
    """GET /api/health: the process answers. Named apart from the
    per-project HealthOut, which is the knowledge-base aggregate."""

    status: Literal["ok"]


class ReadyOut(BaseModel):
    """Readiness report (spec §8.2 / §10). Served with 200 when every check
    passes and 503 otherwise — the Helm readinessProbe is status-code based,
    so a failing check has to change the code, not only the body."""

    db: Literal["ok", "error"]
    graphrag: str  # installed version, or "not-installed" (detected once at startup)
    disk_free_mb: int
    disk_ok: bool  # disk_free_mb >= DISK_WATERMARK_MB

    @property
    def ready(self) -> bool:
        return self.db == "ok" and self.disk_ok and self.graphrag != "not-installed"


class JobCreateIn(BaseModel):
    type: Literal["index", "update"]
    method: Literal["standard", "fast"]


class JobProgressOut(BaseModel):
    """Progress of a running job: for a test run, `done` of `total`
    questions answered (spec §5.4); for index and update, `done` of `total`
    graphrag workflows finished, read from stats.json (spec §6.3)."""

    done: int
    total: int


class JobOut(BaseModel):
    """API contract for a job row (spec §6.1). argv included so the UI can
    show the exact CLI invocation; progress is null for jobs that report
    none."""

    id: str
    project_id: str
    type: str
    method: str
    status: str
    display_status: str
    cancel_requested_at: datetime | None
    exit_code: int | None
    error: str | None
    stats: dict | None
    queued_by: str
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    argv: list[str]
    progress: JobProgressOut | None


class JobPageOut(BaseModel):
    """One page of a project's jobs, newest first (decision D1). total
    counts every job matching the type filter, ignoring limit/offset."""

    items: list[JobOut]
    total: int


class CancelOut(BaseModel):
    detail: str


class LastRunOut(BaseModel):
    type: str
    status: str
    finished_at: datetime | None
    total_runtime_seconds: float | None
    num_documents: int | None
    update_documents: int | None


class PreflightOut(BaseModel):
    active_job: JobOut | None
    last_run: LastRunOut | None
    cache_bytes: int
    cache_quota_mb: int
    # input/ + output/ against PROJECT_QUOTA_MB; enqueue refuses above it.
    usage_bytes: int
    project_quota_mb: int
    disk_free_mb: int
    disk_watermark_mb: int
    # /api/ready's value (installed version or "not-installed"), so the
    # launch dialog can refuse a job the CLI cannot run (spec §10).
    graphrag: str


class CacheClearOut(BaseModel):
    """What POST /projects/{pid}/cache:clear deleted."""

    freed_bytes: int


class AuditEntryOut(BaseModel):
    """One audit row, with the actor resolved to an email.

    actor_id/actor_email are both null for rows the system wrote with no
    signed-in actor (bootstrap admin creation), and actor_email alone is
    null when the actor's user row is gone — the history stays readable
    either way.
    """

    id: int
    actor_id: UUID | None
    actor_email: str | None
    action: str
    target_type: str
    target_id: str
    payload: dict | None
    created_at: datetime


class AuditPageOut(BaseModel):
    """One page of audit rows plus the total ignoring limit/offset."""

    rows: list[AuditEntryOut]
    total: int


class CitationEntryOut(BaseModel):
    id: int
    # null when the cited id is absent from the frame (the LLM cites ids
    # that were never indexed)
    text: str | None
    # Resolved WITH the answer (spec §7.4); null when the generation guard
    # withheld links, the title mapped to nothing, or the label is not
    # "Sources". Defaulted: result rows stored before the field existed
    # carry no key.
    source_name: str | None = None


class CitationOut(BaseModel):
    """One `[Data: <label> (ids)]` group of an answer. The same shape is
    the SSE `citations` event payload and a stored test result's
    citations."""

    label: str
    ids: list[int]
    entries: list[CitationEntryOut]


class QueryTimingsOut(BaseModel):
    """Also the SSE `done` event payload."""

    frames_ms: float
    search_ms: float
    citations_ms: float
    total_ms: float


class ContextFrameOut(BaseModel):
    name: str
    rows: int


class QueryOut(BaseModel):
    answer: str
    context: list[ContextFrameOut]
    citations: list[CitationOut]
    timings: QueryTimingsOut


# Mirrors domain.artifacts.TABLES (test_explore_api pins the two equal):
# spelled out so the generated SPA types carry the union.
ArtifactTableName = Literal[
    "entities", "relationships", "communities", "community_reports", "text_units", "documents"
]


class ArtifactTableOut(BaseModel):
    """One Explore table: the list projection and the filters it offers."""

    name: ArtifactTableName
    columns: list[str]
    type_filter: bool
    community_filter: bool


class ArtifactTablesOut(BaseModel):
    tables: list[ArtifactTableOut]


class ArtifactPageOut(BaseModel):
    """One page of a parquet table. Rows are projections of the table's
    list columns, so their keys vary per table."""

    rows: list[dict[str, Any]]
    total: int
    # a job is queued or running: the parquet files may be mid-rewrite
    stale: bool


class ArtifactDetailOut(BaseModel):
    row: dict[str, Any]
    stale: bool


class GraphNodeOut(BaseModel):
    hrid: int
    title: str
    type: str  # "" when the entity has none
    degree: int
    frequency: int
    # the node's community at the requested level; null when it has none
    community: int | None


class GraphEdgeOut(BaseModel):
    # entity titles, matching GraphNodeOut.title
    source: str
    target: str
    weight: float


class GraphOut(BaseModel):
    level: int
    levels: list[int]
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]
    # GRAPH_NODE_LIMIT capped the response: the highest-degree nodes were
    # kept and edges to cut nodes went with them. node_limit is null when
    # uncapped.
    truncated: bool
    node_limit: int | None
    stale: bool


class ApiErrorOut(BaseModel):
    """Every 4xx body except 422 (i18n spec §4.1). code is the stable
    machine code the SPA localizes from, params its interpolation values;
    detail is an English developer-facing string, never shown when the
    client knows the code."""

    detail: str
    code: str
    params: dict[str, str | int] | None = None


class ValidationIssueOut(BaseModel):
    """One rejected field. The submitted value (`input`, `ctx`) is never
    echoed back — it may be a password or a .env secret (R1-80)."""

    type: str
    loc: list[str | int]
    msg: str


class ValidationErrorOut(BaseModel):
    """A 422: the request did not match the documented schema."""

    detail: list[ValidationIssueOut]
    code: Literal["validation_failed"]


class SettingsConflictOut(BaseModel):
    """The settings PUT 409: settings.yaml changed since the client read
    it. Flat, so the conflict dialog can show the text now on disk and
    retry against its hash."""

    detail: str
    code: Literal["settings_conflict"]
    current_content: str
    current_hash: str


# Project input files (api/files_routes.py, spec §6.3).


class FileOut(BaseModel):
    name: str
    size: int


class FileEntryOut(BaseModel):
    name: str
    # Nullable because a `removed` row has no file behind it (spec 6.1).
    # Inventing a zero size or the deletion timestamp would let the UI sort
    # and total them as if they were files.
    size: int | None
    modified_at: str | None
    sha256: str | None
    index_state: str
    tags: list[str] = []


class FileListOut(BaseModel):
    files: list[FileEntryOut]
    usage_bytes: int
    quota_bytes: int
    # UPLOAD_MAX_FILE_MB in bytes: the SPA checks a file against it before
    # sending, and names the limit in the uploader (the server stays the
    # authority — this only saves a doomed round trip).
    max_file_bytes: int
    # Whether `skipped` can be emitted at all, and why not. On the response,
    # not on each row: it is a property of the artifacts, and repeating it
    # per file would invite the UI to render it per file (spec 6.3).
    ingest_check: str
    has_baseline: bool


# Tags are metadata, not input (spec 8): a tag body is curated vocabulary,
# so each tag is a non-empty bounded string rather than free text.
_TAG = Annotated[str, StringConstraints(min_length=1, max_length=50)]


class TagsIn(BaseModel):
    # extra="forbid": a body with unknown keys is a caller bug, not a
    # silently ignored field (same posture as every other body here).
    model_config = ConfigDict(extra="forbid")

    tags: list[_TAG] = Field(min_length=1)


class BulkDeleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 500 names is the bulk ceiling (spec 8); each name is validated against
    # the project's whitelist in the service, like every other filename.
    names: list[str] = Field(min_length=1, max_length=500)


class TagOut(BaseModel):
    name: str
    count: int


class TagCatalogOut(BaseModel):
    tags: list[TagOut]


class BulkDeleteOut(BaseModel):
    deleted: int
    bytes: int
    # Names whose unlink failed: their rows and audit stay, the rest commit.
    failed: list[str]


class PreviewOut(BaseModel):
    text: str
    offset: int
    total_size: int
    match: bool


class PreviewIn(BaseModel):
    """Exactly one locator form. A partially specified locator is a caller
    bug, and guessing an interpretation is how the bindings in
    resolve_stored_passage get bypassed by accident (spec 7.4)."""

    model_config = ConfigDict(extra="forbid")

    result_id: uuid.UUID | None = None
    entry_id: int | None = None
    passage: str | None = None

    @model_validator(mode="after")
    def _exactly_one_form(self) -> "PreviewIn":
        historic = self.result_id is not None and self.entry_id is not None
        half = (self.result_id is None) != (self.entry_id is None)
        adhoc = self.passage is not None
        if half or (historic and adhoc) or not (historic or adhoc):
            raise ValueError("provide either {result_id, entry_id} or {passage}")
        if adhoc:
            if not self.passage:
                raise ValueError("passage must not be empty")
            # The bound is on BYTES: pydantic's string max_length counts
            # characters, so a CJK passage would pass a character check at
            # three times the byte budget.
            if len(self.passage.encode("utf-8")) > PASSAGE_MAX_BYTES:
                raise ValueError(f"passage exceeds {PASSAGE_MAX_BYTES} bytes")
        return self
