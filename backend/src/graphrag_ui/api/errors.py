"""Stable machine error codes on the wire (i18n spec §4.1).

ApiError renders as {"detail": <legacy string>, "code": <stable code>,
"params": {...}?}. detail stays byte-identical to the pre-i18n contract
(tests pin it); code/params are additive so unknown-code clients keep
working. Every user-visible raise site in api/ uses ApiError; the three
JSONResponse exits (settings 409, must-change guard, upload size guard)
and the SSE error frame attach code by hand (spec §4.3/§4.4).
"""
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


class ApiError(HTTPException):
    def __init__(
        self, status_code: int, code: str, detail: str,
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
