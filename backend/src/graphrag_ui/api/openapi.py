"""Error responses in the generated contract (R3-32).

FastAPI documents only its own 422 (HTTPValidationError, whose `input`
field the validation handler strips) and nothing else non-2xx. This
post-processor attaches the envelopes the handlers in api/errors.py
actually send, so openapi.json — and the SPA's generated types — carry
them:

- `4XX` on every operation: ApiErrorOut ({detail, code, params?}).
- `422` wherever FastAPI documents one: ValidationErrorOut.

A route with its own shape for one status (the settings 409) declares it
with `responses=`; an explicit status wins over the range.
"""

from typing import Any

from fastapi import FastAPI
from pydantic.json_schema import models_json_schema

from graphrag_ui.api.schemas import ApiErrorOut, ValidationErrorOut

_REF = "#/components/schemas/{model}"


def _response(model: type, description: str) -> dict[str, Any]:
    ref = _REF.format(model=model.__name__)
    return {"description": description, "content": {"application/json": {"schema": {"$ref": ref}}}}


def install_error_schema(app: FastAPI) -> None:
    generate = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        # FastAPI caches the dict it returns on app.openapi_schema; editing
        # it in place keeps that cache the edited one.
        schema = generate()
        _, defs = models_json_schema(
            [(ApiErrorOut, "serialization"), (ValidationErrorOut, "serialization")],
            ref_template=_REF,
        )
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        components.pop("HTTPValidationError", None)
        components.pop("ValidationError", None)
        components.update(defs["$defs"])
        for path_item in schema["paths"].values():
            for op in path_item.values():
                responses = op["responses"]
                if "422" in responses:
                    responses["422"] = _response(ValidationErrorOut, "Validation Error")
                responses["4XX"] = _response(ApiErrorOut, "Client Error")
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
