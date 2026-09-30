"""Stable machine error codes on the wire (i18n spec §4.1).

ApiError renders as {"detail": <legacy string>, "code": <stable code>,
"params": {...}?}. detail stays byte-identical to the pre-i18n contract
(tests pin it); code/params are additive so unknown-code clients keep
working. Every user-visible raise site in api/ uses ApiError; the two
JSONResponse exits (settings 409, api/middleware.UploadSizeGuard) and the
SSE error frames attach code by hand (spec §4.3/§4.4).

Service errors reach the wire through ONE table (R1-11): every
CodedServiceError subclass has a row in SERVICE_ERROR_STATUS, and the
app-level handler renders it with the same envelope. Routes do not catch
them; only route-specific shapes (the settings 409 body, dry-run's
"failure is data") keep a local except.
"""

import logging
from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from graphrag_ui.services.env_file import EnvKeyNotFoundError, EnvValidationError
from graphrag_ui.services.errors import (
    CodedServiceError,
    JobConflictError,
    NotIndexedError,
    ProjectIndexingError,
)
from graphrag_ui.services.explore import UnknownTableError, UnsupportedFilterError
from graphrag_ui.services.file_preview import LocatorMismatchError
from graphrag_ui.services.files import (
    FileServiceError,
    FileTooLargeError,
    InputFileNotFoundError,
    QuotaExceededError,
)
from graphrag_ui.services.jobs import DiskWatermarkError, ProjectOverQuotaError
from graphrag_ui.services.projects import (
    MemberNotFoundError,
    MemberOwnerProtectedError,
    ProjectInitError,
)
from graphrag_ui.services.questions import (
    QuestionNotFoundError,
    QuestionSetNotFoundError,
    QuestionSetTooLargeError,
)
from graphrag_ui.services.rate_limit import QueryRateLimitedError
from graphrag_ui.services.roles import (
    LastUserManagerError,
    RoleInUseError,
    RoleIsSystemError,
    RoleNameTakenError,
    RoleNotFoundError,
    RolePermissionsInvalidError,
    RoleScopeMismatchError,
)
from graphrag_ui.services.settings import DryRunFailedError, SettingsValidationError
from graphrag_ui.services.test_runs import EmptyQuestionSetError
from graphrag_ui.services.users import SelfRoleChangeError, UserNotFoundError

logger = logging.getLogger(__name__)


class ApiError(HTTPException):
    def __init__(
        self,
        status_code: int,
        code: str,
        detail: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.code = code
        self.params = params


async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
    body: dict[str, Any] = {"detail": exc.detail, "code": exc.code}
    if exc.params:
        body["params"] = exc.params
    return JSONResponse(status_code=exc.status_code, content=body)


# class -> (HTTP status, fixed detail). A None detail sends the exception's
# own message: those messages are written for the client (they name the
# rule, never a secret). A fixed detail is used where the message carries
# internals (ids, scopes) or where one wording is the pinned contract.
SERVICE_ERROR_STATUS: dict[type[CodedServiceError], tuple[int, str | None]] = {
    # 400 — the request broke a rule
    FileServiceError: (status.HTTP_400_BAD_REQUEST, None),
    SettingsValidationError: (status.HTTP_400_BAD_REQUEST, None),
    EnvValidationError: (status.HTTP_400_BAD_REQUEST, None),
    RoleIsSystemError: (status.HTTP_400_BAD_REQUEST, "built-in roles are immutable"),
    RoleScopeMismatchError: (status.HTTP_400_BAD_REQUEST, None),
    RolePermissionsInvalidError: (status.HTTP_400_BAD_REQUEST, None),
    LastUserManagerError: (
        status.HTTP_400_BAD_REQUEST,
        "cannot remove the last active user manager",
    ),
    SelfRoleChangeError: (
        status.HTTP_400_BAD_REQUEST,
        "cannot change your own role or active status",
    ),
    MemberOwnerProtectedError: (status.HTTP_400_BAD_REQUEST, None),
    QuestionSetTooLargeError: (status.HTTP_400_BAD_REQUEST, None),
    EmptyQuestionSetError: (status.HTTP_400_BAD_REQUEST, "question set has no questions"),
    # 404 — the addressed thing does not exist
    InputFileNotFoundError: (status.HTTP_404_NOT_FOUND, "file not found"),
    # One fixed message for all three locator bindings: distinguishing
    # them would leak which one failed (spec 7.4).
    LocatorMismatchError: (status.HTTP_404_NOT_FOUND, "citation not found"),
    EnvKeyNotFoundError: (status.HTTP_404_NOT_FOUND, "key not found"),
    RoleNotFoundError: (status.HTTP_404_NOT_FOUND, "role not found"),
    UserNotFoundError: (status.HTTP_404_NOT_FOUND, "user not found"),
    MemberNotFoundError: (status.HTTP_404_NOT_FOUND, "member not found"),
    QuestionSetNotFoundError: (status.HTTP_404_NOT_FOUND, "question set not found"),
    QuestionNotFoundError: (status.HTTP_404_NOT_FOUND, "question not found"),
    UnknownTableError: (status.HTTP_404_NOT_FOUND, "unknown table"),
    # 409 — the project's state forbids it right now
    ProjectIndexingError: (status.HTTP_409_CONFLICT, None),
    NotIndexedError: (status.HTTP_409_CONFLICT, "not indexed yet — run an indexing job first"),
    JobConflictError: (status.HTTP_409_CONFLICT, "this project already has a job in progress"),
    DiskWatermarkError: (status.HTTP_409_CONFLICT, "not enough free disk space"),
    # The upload quota error at enqueue (R3-25): same code, but the project's
    # state is what refuses, not the request size.
    ProjectOverQuotaError: (status.HTTP_409_CONFLICT, None),
    RoleNameTakenError: (status.HTTP_409_CONFLICT, "a role with that name already exists"),
    RoleInUseError: (status.HTTP_409_CONFLICT, "role is still granted; unassign it first"),
    # 413 — single-file cap and project quota alike (spec §9)
    FileTooLargeError: (status.HTTP_413_CONTENT_TOO_LARGE, None),
    QuotaExceededError: (status.HTTP_413_CONTENT_TOO_LARGE, None),
    UnsupportedFilterError: (
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "this table does not support that filter",
    ),
    QueryRateLimitedError: (
        status.HTTP_429_TOO_MANY_REQUESTS,
        "too many queries — please retry later",
    ),
    # 500 — graphrag's CLI could not run (not installed, crashed on init)
    ProjectInitError: (status.HTTP_500_INTERNAL_SERVER_ERROR, "graphrag init failed"),
    DryRunFailedError: (status.HTTP_500_INTERNAL_SERVER_ERROR, "graphrag dry-run failed"),
}


def api_error_for(exc: CodedServiceError) -> ApiError | None:
    """The ApiError for a coded service error; None if its class has no row
    (a programming error — test_service_errors keeps the table complete)."""
    for cls in type(exc).__mro__:
        row = SERVICE_ERROR_STATUS.get(cls)
        if row is not None:
            status_code, detail = row
            return ApiError(status_code, exc.code, detail or str(exc), exc.params)
    return None


async def coded_error_handler(request: Request, exc: CodedServiceError) -> JSONResponse:
    err = api_error_for(exc)
    if err is None:
        logger.error("unmapped service error %s", type(exc).__name__, exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal Server Error"},
        )
    return await api_error_handler(request, err)


# The only keys of a pydantic error that are safe to return: `input` is the
# submitted value (a password, a .env secret) and `ctx` can carry it too, so
# neither ever leaves the server (R1-80).
_VALIDATION_KEYS = ("type", "loc", "msg")


async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [{k: e[k] for k in _VALIDATION_KEYS if k in e} for e in exc.errors()]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": errors, "code": "validation_failed"},
    )
