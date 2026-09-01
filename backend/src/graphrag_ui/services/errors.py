"""Shared base for service pipeline errors (spec A7).

code names the failing step; detail is server-log-only material — routes
return fixed zh-TW messages, never these strings.
"""

INTERRUPTED_DETAIL = "查詢中斷"


class ServicePipelineError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail
