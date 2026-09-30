"""ASGI middleware. Pure ASGI on purpose (R1-106): Starlette's
BaseHTTPMiddleware adds a task group and a memory-stream hop to every
request and every streamed chunk, SSE and uploads included."""

import re

from fastapi import status
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from graphrag_ui.config import get_settings
from graphrag_ui.services.files import max_file_bytes

# POST /api/projects/{pid}/files — the only upload endpoint (pid is a path
# segment, so [^/]+ cannot over-match into deeper routes).
_UPLOAD_PATH = re.compile(r"^/api/projects/[^/]+/files$")

# Multipart framing (boundary + part headers + trailing CRLF) inflates
# Content-Length slightly beyond the payload; tolerate it so an
# exactly-at-cap file is not falsely rejected by the early check. The
# authoritative cap is the streaming limit in save_file.
_DECLARED_LENGTH_SLACK = 64 * 1024


class UploadSizeGuard:
    """Early 413 on a declared-oversized upload, before the body is read.

    FastAPI parses the whole multipart body ahead of endpoint code, so a
    check inside upload_file would fire only after a multi-GB body had been
    spooled and parsed. Rejecting here keeps a hostile POST this cheap:
    header read, response, done. The header is advisory (may be absent or
    malformed — chunked uploads fall through); the streaming cap in
    save_file remains the authoritative limit.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] == "POST"
            and _UPLOAD_PATH.match(scope["path"])
        ):
            declared = Headers(scope=scope).get("content-length", "")
            if declared.isdigit() and int(declared) > max_file_bytes() + _DECLARED_LENGTH_SLACK:
                max_mb = get_settings().upload_max_file_mb
                response = JSONResponse(
                    {
                        "detail": f"file exceeds the {max_mb} MiB upload limit",
                        "code": "file_too_large",
                        "params": {"max_mb": max_mb},
                    },
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
