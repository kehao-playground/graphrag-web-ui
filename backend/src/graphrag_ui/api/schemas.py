from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr, Field

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
    """Batch progress the test-run worker ticks between questions (spec
    §5.4): `done` of `total` questions answered."""

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
    disk_free_mb: int
    disk_watermark_mb: int
    # /api/ready's value (installed version or "not-installed"), so the
    # launch dialog can refuse a job the CLI cannot run (spec §10).
    graphrag: str


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
